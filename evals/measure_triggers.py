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

每个查询跑一次 `claude -p`（--max-turns 1：只看它要不要加载 skill，不看它后面
干了什么 —— 那样更便宜，也更接近"会不会触发"这个问题本身）。

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


def run_query(query: str, timeout: int, max_turns: int
               ) -> tuple[bool, str, float]:
    work = Path(tempfile.mkdtemp(prefix="trig-"))
    try:
        cmd = ["claude", "-p", query, "--output-format", "stream-json",
               "--verbose", "--max-turns", str(max_turns)]
        try:
            proc = subprocess.run(cmd, cwd=work, capture_output=True,
                                  text=True, encoding="utf-8",
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, "超时（模型没在时限内决定）", 0.0
        hit, names = triggered_in_stream(proc.stdout)
        note = f"调用了 {names}" if hit else "没有调用 Skill 工具"
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
        return hit, note, cost
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=int, default=None)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-turns", type=int, default=3,
                    help="给模型几轮：1 会让它来不及加载 skill（测量偏保守）")
    args = ap.parse_args()

    queries = json.loads(EVAL_SET.read_text(encoding="utf-8"))
    if args.only is not None:
        queries = [q for q in queries
                   if queries.index(q) == args.only]
    results = []
    for i, q in enumerate(queries, 1):
        hit, note, cost = run_query(q["query"], args.timeout, args.max_turns)
        want = bool(q["should_trigger"])
        ok = hit == want
        results.append({"i": i, "query": q["query"], "should_trigger": want,
                        "triggered": hit, "pass": ok, "note": note,
                        "cost_usd": round(cost, 4)})
        if not args.json:
            mark = "ok  " if ok else "FAIL"
            print(f"  {mark} [{i:2d}] 应触发={str(want):5} 实测={str(hit):5} "
                  f"{q['query'][:46]}")
            if not ok:
                print(f"        {note}")

    tp = sum(1 for r in results if r["should_trigger"])
    fp = sum(1 for r in results if r["triggered"] and not r["should_trigger"])
    fn = sum(1 for r in results if not r["triggered"] and r["should_trigger"])
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    summary = {"total": len(results),
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
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
