#!/usr/bin/env python3
"""round-15 回归测试：判定点粒度的计划格式（v2）、格式识别与豁免。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import req_plan  # noqa: E402
SCRIPT = HERE / "req_plan.py"
FAILURES: list[str] = []
TMPDIRS: list[Path] = []


def expect(cond: bool, label: str) -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILURES.append(label)


def mkdtemp() -> Path:
    p = Path(tempfile.mkdtemp())
    TMPDIRS.append(p)
    return p


def write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONUTF8": "1"})


SRC = """export function insertRow(rows: string[]) {
  if (rows.length > 100) throw new Error('too many');
  if (!rows.length) throw new Error('empty');
  return rows;
}
"""
TEST_SRC = "test('rejects a row', () => {});\n"

V2_HEADER = ("| 维度 | DP ID | 判定点 | 归属的不变式 | 落点（文件::用例名） | 豁免理由 |\n"
             "| --- | --- | --- | --- | --- | --- |")

V1_PLAN = """# 测试计划

目标模块：`src`

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 缺 id 的行必须报错 | src/a.test.ts::rejects a row |

## 核心逻辑细节

| 维度 | 落点 / 不适用理由 |
| --- | --- |
| 错误路径 | src/a.test.ts::rejects a row |
"""


def mk_repo() -> Path:
    root = mkdtemp()
    write(root / "src" / "a.ts", SRC)
    write(root / "src" / "a.test.ts", TEST_SRC)
    return root


def plan_text(rows: str, extra: str = "") -> str:
    return f"""# 测试计划

目标模块：`src`

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 缺 id 的行必须报错 | src/a.test.ts::rejects a row |

## 核心逻辑细节

{V2_HEADER}
{rows}
{extra}
"""


def check(root: Path, text: str, name: str = "plan.md") -> str:
    plan = write(root / "docs" / name, text)
    r = run("check", str(plan), "--repo", str(root))
    return r.stdout + r.stderr


def has_mark(out: str, needle: str, mark: str) -> bool:
    """报告里有没有一条 needle 命中、且级别是 mark 的发现。"""
    return any(ln.lstrip().startswith(mark)
               for ln in out.splitlines() if needle in ln)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    sys.path.insert(0, str(HERE))
    from req_plan import (PLAN_FORMAT_V1, PLAN_FORMAT_V2, Plan,  # noqa: E402
                          detect_plan_format, detail_pin_index, parse_plan)

    print("test_detect_format_v1_and_v2")
    v1 = Plan(path=Path("p.md"), detail_header=["维度", "落点"],
              detail_rows=[["边界", "src/a.test.ts"]])
    expect(detect_plan_format(v1) == PLAN_FORMAT_V1, "v1 计划识别为 v1")
    v2 = Plan(path=Path("p.md"),
              detail_header=["维度", "DP ID", "判定点", "归属的不变式",
                             "落点", "豁免理由"],
              detail_rows=[["错误路径", "src/a.ts#f#drop", "缺 id 报错",
                            "I1", "src/a.test.ts::rejects a row", ""]])
    expect(detect_plan_format(v2) == PLAN_FORMAT_V2, "表头含 DP ID ⇒ v2")

    print("\ntest_format_detected_from_data_row_not_header")
    deg = Plan(path=Path("p.md"), detail_header=["维度", "判定点", "归属", "落点"],
               detail_rows=[["src/a.ts#f#drop", "缺 id 报错", "I1",
                             "src/a.test.ts::x"]])
    expect(detect_plan_format(deg) == PLAN_FORMAT_V2,
           "表头没写 DP ID，但数据行第 1 列能解析成 DP ID ⇒ v2")

    print("\ntest_v1_plan_pins_still_read_column_one")
    expect(detail_pin_index(v1) == 1, "v1 的落点列 = 1")

    print("\ntest_v2_plan_pins_read_column_four")
    expect(detail_pin_index(v2) == 4, "v2 的落点列 = 4")

    root = mk_repo()

    print("\ntest_check_reports_unattributed_high_risk_dp")
    out = check(root, plan_text(
        "| 错误路径 | src/a.ts#insertRow#drop | 缺 id 报错 |  |  |  |"))
    expect(has_mark(out, "src/a.ts#insertRow#drop", "✗"),
           "高危判定点既没归属也没豁免 ⇒ 报红")

    print("\ntest_check_reports_unknown_invariant_id")
    out = check(root, plan_text(
        "| 错误路径 | src/a.ts#insertRow#drop | 缺 id 报错 | I9 | "
        "src/a.test.ts::rejects a row |  |"))
    expect(has_mark(out, "I9", "✗"), "归属的不变式编号不存在 ⇒ 报红")

    print("\ntest_check_reports_dp_file_not_exists")
    out = check(root, plan_text(
        "| 错误路径 | src/missing.ts#insertRow#drop | 缺 id 报错 | I1 | "
        "src/a.test.ts::rejects a row |  |"))
    expect(has_mark(out, "src/missing.ts", "✗"),
           "DP ID 指向的文件不存在 ⇒ 报红")

    print("\ntest_check_waiver_requires_reason")
    out = check(root, plan_text(
        "| 错误路径 | src/a.ts#insertRow#drop | 缺 id 报错 |  |  | 不适用 |"))
    expect(has_mark(out, "豁免", "·"), "豁免理由只有「不适用」⇒ 提醒")

    print("\ntest_html_comment_waiver_is_accepted")
    out = check(root, plan_text(
        "| 错误路径 | src/a.ts#insertRow#drop | 缺 id 报错 |  |  |  |",
        extra='<!-- attest:waive src/a.ts#insertRow#drop reason="上游已校验" -->'))
    expect("高危判定点" not in out, "正文注释豁免 ⇒ 该 DP 不算缺口")
    out2 = check(root, plan_text(
        "| 错误路径 | src/a.ts#insertRow#drop | 缺 id 报错 |  |  |  |",
        extra='<!-- attest:waive src/a.ts#insertRow#drop reason="" -->'),
        name="plan2.md")
    expect(has_mark(out2, "豁免 src/a.ts#insertRow#drop", "✗"),
           "注释豁免没给理由 ⇒ 报红")

    print("\ntest_waiver_with_unknown_dp_id_is_reported")
    out = check(root, plan_text(
        "| 错误路径 | src/a.ts#insertRow#drop | 缺 id 报错 | I1 | "
        "src/a.test.ts::rejects a row |  |",
        extra='<!-- attest:waive src/a.ts#nowhere#drop reason="写错了" -->'))
    expect(has_mark(out, "豁免 src/a.ts#nowhere#drop", "✗"),
           "豁免了一个表里没有的判定点 ⇒ 报红")

    print("\nv1 计划 ⇒ 三条新判据降级成提醒")
    out = check(root, V1_PLAN, name="v1.md")
    expect(has_mark(out, "旧的模块级维度表", "·"), "v1 计划 ⇒ 提醒去升级")

    print("\nupgrade：旧表留成附录，追加判定点表")
    root2 = mk_repo()
    plan_path = write(root2 / "docs" / "u.md", V1_PLAN)
    r = run("upgrade", str(plan_path), "--repo", str(root2))
    expect(r.returncode == 0, f"upgrade 成功（{r.stderr.strip()[:80]}）")
    new = plan_path.read_text(encoding="utf-8")
    expect("DP ID" in new, "新表有 DP ID 列")
    expect("src/a.ts#insertRow#" in new, "DP ID 是机器推导出来的")
    expect("附录" in new, "旧表留成附录")
    p = parse_plan(plan_path)
    expect(detect_plan_format(p) == PLAN_FORMAT_V2, "升级后的计划识别为 v2")
    expect(len(p.detail_rows) > 0, "新表的数据行被解析到")
    expect(all(len(r_) >= 5 for r_ in p.detail_rows),
           "旧表的数据行没混进新表")

    print("\ntest_module_path_outside_repo_is_ignored")
    outside = mkdtemp()
    write(outside / "leak.ts",
          "export function leak() { if (x === 1) return 1; return 2; }\n")
    repo3 = mkdtemp()
    write(repo3 / "package.json", json.dumps({"scripts": {"test": "vitest"}}))
    write(repo3 / "src" / "a.ts", SRC)
    r = run("new", "esc", "--repo", str(repo3),
            "--module", f"../{outside.name}")
    expect(r.returncode == 0, "new 成功（模块路径越界不该崩）")
    plan3 = repo3 / "docs" / "esc-test-plan.md"
    text3 = plan3.read_text(encoding="utf-8") if plan3.exists() else ""
    expect("leak" not in text3, "仓库外的模块不被读进计划")

    print("\ntest_delivery_precondition_sections")
    # 「确认」节：确认者可以是人，也可以是 agent —— 记下是谁
    ok_sec = """
## 确认（人 或 agent 都可以）

| 审什么 | 谁确认 | 什么时候 | 结论 |
| --- | --- | --- | --- |
| 不变式清单与需求一致 | claude-opus-5 | 2026-11-17 | 一致 |
"""
    p_ok = parse_plan(write(mkdtemp() / "p.md", plan_text("", ok_sec)))
    expect(p_ok.reviewed, "「确认」节填了 ⇒ 算确认过")
    expect(p_ok.review_by == "claude-opus-5", "确认者被记下来（人 or agent）")
    p_ph = parse_plan(write(mkdtemp() / "p.md", plan_text(
        "", "\n## 确认（人 或 agent 都可以）\n\n| 审什么 | 谁确认 | 什么时候 | 结论 |\n"
           "| --- | --- | --- | --- |\n| 不变式清单 | <待填> | <待填> | <待填> |\n")))
    expect(not p_ph.reviewed, "「确认」节还是占位 ⇒ 不算确认过")

    # 「本工具不判定项的归属」节：八项各由谁判、依据是什么
    from req_plan import NOT_JUDGED_ITEMS  # noqa: E402
    rows = "\n".join(f"| {i} | 人 | 评审记录 |" for i in NOT_JUDGED_ITEMS)
    nj = f"\n## 本工具不判定项的归属\n\n| 项 | 由谁判 | 依据 |\n| --- | --- | --- |\n{rows}\n"
    p_nj = parse_plan(write(mkdtemp() / "p.md", plan_text("", nj)))
    expect(len(p_nj.not_judged_owners) == len(NOT_JUDGED_ITEMS),
           "归属表的行被解析到")
    expect(not p_nj.not_judged_missing, "八项都有人认领 ⇒ 没有缺口")
    half = "\n".join(f"| {i} | 人 | 评审记录 |" for i in NOT_JUDGED_ITEMS[:4])
    p_half = parse_plan(write(mkdtemp() / "p.md", plan_text(
        "", "\n## 本工具不判定项的归属\n\n| 项 | 由谁判 | 依据 |\n| --- | --- | --- |\n"
           f"{half}\n| <待填> | <待填> | <待填> |\n")))
    expect(len(p_half.not_judged_missing) == len(NOT_JUDGED_ITEMS) - 4,
           "没认领的项被数出来")
    out_nj = check(mk_repo(), plan_text("", nj))
    expect("没人认领" not in out_nj, "八项都认领了 ⇒ check 不提醒")
    out_missing = check(mk_repo(), plan_text("", ""))
    expect("没人认领" in out_missing,
           "八项没人认领 ⇒ check 提醒（不报红）")

    print("\ntest_reconcile_generates_script_from_the_plan")
    rec_plan = write(mkdtemp() / "rec.md", """# 测试计划

## 数据一致性

| 数据 | 权威源 | 镜像/派生 | 一致性怎么验（钉） |
| --- | --- | --- | --- |
| 记忆事实 | PG `agent_memory`（`src/models.ts`） | Milvus 集合 `lumen_mem_<租户>` | src/x.itest.ts |
| 记忆正文 | PG `agent_memory.text` | Milvus `text` 字段 | src/x.itest.ts |
""")
    out_ts = mkdtemp() / "scripts" / "reconcile-memory.ts"
    r_rc = run("reconcile", str(rec_plan), "--repo", str(mkdtemp()),
               "--out", str(out_ts))
    expect(r_rc.returncode == 0, "reconcile 生成成功")
    src = out_ts.read_text(encoding="utf-8") if out_ts.exists() else ""
    expect("pgTable: 'agent_memory'" in src, "表名从计划里抽出来了")
    expect("bodyColumn: 'text'" in src,
           "计划里写了「表.列」⇒ 那一列直接填成要比对的正文列")
    expect("collection: 'lumen_mem_<租户>'" in src, "集合名从计划里抽出来了")
    expect("<待填" in src, "抽不出来的留成占位")
    expect("拒绝运行" in src, "没填完时脚本拒绝运行（不静默绿）")
    expect("计划原文" in src, "生成物里保留计划原文，agent 填的时候看得见")
    r_rc2 = run("reconcile", str(rec_plan), "--repo", str(mkdtemp()),
                "--out", str(out_ts))
    expect(r_rc2.returncode == 1, "已存在的文件不覆盖（没有 --force）")

    print("\ntest_case_names_see_template_literal_cases")
    # 用例名写成模板字符串（`it(\`...\${x}...\`)`）在 TS 里很常见（把
    # method/url 插进用例名）⇒ 用例名解析器不认它 ⇒ 这类用例没法被计划钉住
    # （落点写不出「文件::用例名」⇒ 计划只能整条豁免）。
    tpl_repo = mkdtemp()
    write(tpl_repo / "src" / "a.test.ts",
          "it(`rejects ${request.method} ${request.url} without credentials (401)`, () => {});\n"
          "it('plain case', () => {});\n")
    names = req_plan.test_case_names(tpl_repo, "src/a.test.ts")
    expect(any("rejects" in n and "without credentials" in n for n in names),
           "模板字符串写的用例名也能被解析出来")
    expect("plain case" in names, "普通引号用例名照旧解析得到")

    print("\ntest_baseline_generates_script_from_the_plan")
    base_plan = write(mkdtemp() / "base.md", """# 测试计划

## 数据一致性

| 数据 | 权威源 | 镜像/派生 | 一致性怎么验（钉） |
| --- | --- | --- | --- |
| 记忆事实 | PG `agent_memory`（`src/models.ts`） | Milvus 集合 `lumen_mem_<租户>` | `scripts/memory-baseline-check.ts` 基线快照 `fixtures/baseline.json` |
""")
    out_base = mkdtemp() / "scripts" / "baseline-memory.ts"
    r_bl = run("baseline", str(base_plan), "--repo", str(mkdtemp()),
               "--out", str(out_base))
    expect(r_bl.returncode == 0, "baseline 生成成功")
    bsrc = out_base.read_text(encoding="utf-8") if out_base.exists() else ""
    expect("pgTable: 'agent_memory'" in bsrc, "表名从计划里抽出来了")
    expect("baselinePath: 'fixtures/baseline.json'" in bsrc,
           "基线快照路径从「一致性怎么验」列里抽出来了")
    expect("<待填" in bsrc, "抽不出来的留成占位")
    expect("拒绝运行" in bsrc, "没填完时脚本拒绝运行（不静默绿）")
    expect("计划原文" in bsrc, "生成物里保留计划原文，agent 填的时候看得见")
    expect("baseline:check" in bsrc,
           "生成物里给出 package.json 的脚本名（readiness 靠它认出这条车道）")
    r_bl2 = run("baseline", str(base_plan), "--repo", str(mkdtemp()),
                "--out", str(out_base))
    expect(r_bl2.returncode == 1, "已存在的文件不覆盖（没有 --force）")
    r_bl3 = run("baseline", str(base_plan), "--repo", str(mkdtemp()),
                "--out", str(out_base), "--force")
    expect(r_bl3.returncode == 0, "--force 可以覆盖")

    print()
    for d in TMPDIRS:
        shutil.rmtree(d, ignore_errors=True)
    if FAILURES:
        print(f"{len(FAILURES)} 项失败:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
