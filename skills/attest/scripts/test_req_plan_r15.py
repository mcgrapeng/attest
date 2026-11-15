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
