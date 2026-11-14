#!/usr/bin/env python3
"""测 description 的**真实触发**率：模型有没有去加载这个 skill。

    python3 evals/measure_triggers.py            # 跑全部查询
    python3 evals/measure_triggers.py --only 3   # 只跑某一条
    python3 evals/measure_triggers.py --json     # 机器可读

为什么不用 skill-creator 的 run_eval.py：它把 skill 做成**斜杠命令**塞进
.claude/commands/，然后检测模型有没有调用那个命令。而本机 `~/.claude/skills/`
里已经装着真的 req-test-plan ⇒ 模型直接加载真 skill，探针命令一次都没被调用
⇒ 实测率全是 0.00（实测过，2026-11-14）。那是评测环境被污染，不是真结果。

这个脚本测的是真机制：skill 装在 skills 目录里，模型看到 name+description，
决定用不用它 ⇒ 表现为 `Skill` 工具调用里 skill 名匹配。

**评测仓库不是空目录**：查询里提到的 `src/billing/`、`src/orders/`、
`docs/refund-test-plan.md` 在空目录里不存在 ⇒ 模型探一圈发现没东西可查就跳过
skill（实测：它在思考里提到了 req-test-plan，但没加载）。所以先造一个像个真
仓库的骨架，让"要不要用这个 skill"这个问题是真的。

每个查询跑一次 `claude -p`（--max-turns 3：够它探完再决定加载不加载；
1 轮会偏保守 —— 模型习惯先探索再加载 skill）。

退出码：0 = 全部符合预期；1 = 有不符合的。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL_SET = ROOT / "evals" / "trigger-evals.json"
SKILL_NAME = "req-test-plan"

#: 评测用的假仓库骨架 —— 查询里提到的路径都要存在，否则模型会认为没东西可查。
FIXTURE_FILES = {
    "package.json": json.dumps({
        "name": "shop-backend", "version": "1.0.0",
        "scripts": {"test": "node --test", "reconcile": "node scripts/reconcile.js"},
        "dependencies": {"pg": "^8.11.0"},
    }, indent=2) + "\n",
    "src/billing/billing.ts": (
        "export function computeInvoice(items: number[]): number {\n"
        "  return items.reduce((a, b) => a + b, 0);\n"
        "}\n"),
    "src/billing/invoice.test.ts": (
        "import test from 'node:test';\n"
        "import assert from 'node:assert/strict';\n"
        "import { computeInvoice } from './billing.ts';\n"
        "test('invoice total', () => { assert.equal(computeInvoice([1, 2]), 3); });\n"),
    "src/orders/export.ts": (
        "export function exportOrders(rows: string[]): string {\n"
        "  return rows.join('\\n');\n"
        "}\n"),
    "src/orders/export.test.ts": (
        "import test from 'node:test';\n"
        "import assert from 'node:assert/strict';\n"
        "import { exportOrders } from './export.ts';\n"
        "test('export', () => { assert.equal(exportOrders(['a']), 'a'); });\n"),
    "docs/refund-test-plan.md": (
        "# 测试计划：退款\n\n"
        "## 不变式清单\n\n"
        "| # | 不变式 | 钉在哪 |\n"
        "| --- | --- | --- |\n"
        "| I1 | 退款金额正确 | src/orders/refund.test.ts::退款金额正确 |\n\n"
        "## 覆盖矩阵\n\n"
        "| 流程 | 落点 |\n"
        "| --- | --- |\n"
        "| 主流程 | src/orders/refund.test.ts::退款金额正确 |\n"),
    "scripts/reconcile.js": (
        "// 对账 src/orders\n"
        "console.log('| tenant | drift | status |');\n"
        "console.log('| default | 0 | consistent |');\n"),
}


def build_fixture_repo(dst: Path) -> None:
    for rel, content in FIXTURE_FILES.items():
        p = dst / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def triggered_in_stream(stdout: str) -> tuple[bool, list[str]]:
    """流里有没有 Skill 工具调用命中了本 skill。"""
    hits: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "assistant":
            continue
        for c in (ev.get("message") or {}).get("content") or []:
            if not isinstance(c, dict) or c.get("type") != "tool_use":
                continue
            if c.get("name") != "Skill":
                continue
            name = str((c.get("input") or {}).get("skill") or "")
            if SKILL_NAME in name:
                hits.append(name)
    return bool(hits), hits


def considered_in_thinking(stdout: str) -> bool:
    """模型的**思考**里有没有把我们当候选。

    skill 列表一直��模型的上下文里，所以"没提到"不等于"没看见"。真正要问的是：
    描述够不够显眼，让我们在候选集里出现过一次。
    """
    markers = ("req-test-plan", "交付就绪度", "测试就绪度", "需求测试计划")
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "assistant":
            continue
        for c in (ev.get("message") or {}).get("content") or []:
            if isinstance(c, dict) and c.get("type") == "thinking":
                th = c.get("thinking") or ""
                if any(mk in th for mk in markers):
                    return True
    return False


def run_query(query: str, timeout: int, max_turns: int
               ) -> tuple[bool, str, float, list[str]]:
    work = Path(tempfile.mkdtemp(prefix="trig-"))
    try:
        build_fixture_repo(work)
        cmd = ["claude", "-p", query, "--output-format", "stream-json",
               "--verbose", "--max-turns", str(max_turns)]
        try:
            proc = subprocess.run(cmd, cwd=work, capture_output=True,
                                  text=True, encoding="utf-8",
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, "超时（模型没在时限内决定）", 0.0, [], False
        hit, names = triggered_in_stream(proc.stdout)
        considered = considered_in_thinking(proc.stdout)
        # 模型实际加载了哪些别的 skill —— 用来区分"我们没被选中"和
        # "我们被更强的候选挤掉了"。
        others: list[str] = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") != "assistant":
                continue
            for c in (ev.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use" \
                        and c.get("name") == "Skill":
                    nm = str((c.get("input") or {}).get("skill") or "")
                    if nm and nm != SKILL_NAME and nm not in others:
                        others.append(nm)
        note = f"调用了 {names}" if hit else "没有调用 Skill 工具"
        if others:
            note += f"；改用了 {others}"
        if proc.returncode != 0:
            # --max-turns 到了会报 error_max_turns（退出码 1）—— 那是**测量方式**
            # 造成的，不是模型失败，别当信号。
            note += f"；claude 退出码 {proc.returncode}（--max-turns 截断）"
        cost = 0.0
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "result":
                cost = float(ev.get("total_cost_usd") or 0.0)
        return hit, note, cost, others, considered
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=int, default=None)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--max-turns", type=int, default=3,
                    help="给模型几轮：1 会让它来不及加载 skill（测量偏保守）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    queries = json.loads(EVAL_SET.read_text(encoding="utf-8"))
    if args.only is not None:
        queries = [queries[args.only]]
    results = []
    for i, q in enumerate(queries, 1):
        hit, note, cost, others, considered = run_query(
            q["query"], args.timeout, args.max_turns)
        want = bool(q["should_trigger"])
        ok = hit == want
        results.append({"i": i, "query": q["query"], "should_trigger": want,
                        "triggered": hit, "pass": ok, "note": note,
                        "considered": considered,
                        "other_skills": others,
                        "cost_usd": round(cost, 4)})
        if not args.json:
            mark = "ok  " if ok else "FAIL"
            print(f"  {mark} [{i:2d}] 应触发={str(want):5} 实测={str(hit):5} "
                  f"{q['query'][:46]}")
            if not ok:
                print(f"        {note}")

    tp = sum(1 for r in results if r["should_trigger"] and r["triggered"])
    fp = sum(1 for r in results if r["triggered"] and not r["should_trigger"])
    fn = sum(1 for r in results if not r["triggered"] and r["should_trigger"])
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    considered_n = sum(1 for r in results if r.get("considered"))
    summary = {"total": len(results),
               "considered": considered_n,
               "cost_usd": round(sum(r.get("cost_usd", 0.0) for r in results), 4),
               "passed": sum(1 for r in results if r["pass"]),
               "precision": round(precision, 3), "recall": round(recall, 3),
               "f1": round(f1, 3), "false_positive": fp, "false_negative": fn}
    if args.json:
        print(json.dumps({"results": results, "summary": summary},
                         ensure_ascii=False, indent=2))
    else:
        print(f"\n共 {summary['total']} 条：符合预期 {summary['passed']}"
              f" · 精确率 {summary['precision']} · 召回率 {summary['recall']}"
              f" · F1 {summary['f1']}"
              f"（误触发 {fp}，漏触发 {fn}）"
              f" · 本次花销 ${summary['cost_usd']}")
        print(f"  模型在思考里把我们当候选的：{considered_n}/{len(results)}"
              f"（这是描述显眼度的指标 —— skill 列表本来就在上下文里）")
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
