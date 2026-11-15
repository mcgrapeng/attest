#!/usr/bin/env python3
"""DPE（判定点引擎）接入 readiness 的回归测试。

三条新判据（规格 §7.1）：
- `plan-alignment` 计划与机器推导双向对齐（静态）
- `decision-coverage` 应有判定点覆盖到位（静态）
- `pin-discriminating` 每条不变式的钉有区分力（实测）

口径要点：
- 没有 `--plan` ⇒ 三条一律 skipped（证据类别 untested）⇒ 老 fixture 的退出码不变；
- 旧格式计划（模块级维度表）⇒ 三条一律 warn，文案写明「升级」；
- `--lenient` ⇒ 三条一律不报红；
- L2（词法回退）⇒ 三条一律不硬判（缺能力绝不报绿）；
- JSON 顶层多三段：`decision_points` / `alignment` / `discriminating`。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "readiness.py"
FAILURES: list[str] = []
TMPDIRS: list[Path] = []

sys.path.insert(0, str(HERE))
import readiness  # noqa: E402
import req_plan  # noqa: E402

PY_SRC = '''def divide(a, b):
    if b == 0:
        raise ValueError("zero")
    return a / b
'''

PY_TEST = '''from pkg.core import divide


def test_divide():
    assert divide(6, 3) == 2
'''

#: 同一个函数里有两个同类判定点（两个 if）⇒ 机器的 DP ID 带 ordinal
PY_SRC_TWO_BRANCH = '''def divide(a, b):
    if b == 0:
        raise ValueError("zero")
    if a < 0:
        raise ValueError("negative")
    return a / b
'''

PYPROJECT = """[project]
name = "dpe-fixture"
version = "0.0.0"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
"""

TS_SRC = """export function divide(a: number, b: number): number {
  if (b === 0) {
    throw new Error('zero');
  }
  return a / b;
}
"""

TS_TEST = """import { test } from 'node:test';
import assert from 'node:assert';
import { divide } from './calc.ts';
test('divides', () => { assert.equal(divide(6, 3), 2); });
"""

#: 判定点粒度（v2）的计划：三条判定点全部归属到 I1
V2_PLAN = """# 测试计划

目标模块：`src`

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 除数为零必须报错 | tests/test_core.py::test_divide |

## 核心逻辑细节

| 维度 | DP ID | 判定点 | 归属的不变式 | 落点（文件::用例名） | 豁免理由 |
| --- | --- | --- | --- | --- | --- |
| 分支 | src/pkg/core.py#divide#branch | if b == 0 | I1 | tests/test_core.py::test_divide | |
| 边界 | src/pkg/core.py#divide#boundary | b == 0 | I1 | tests/test_core.py::test_divide | |
| 错误路径 | src/pkg/core.py#divide#error-path | raise ValueError | I1 | tests/test_core.py::test_divide | |
"""

#: 只列了一条判定点 ⇒ 机器推导出的另外两条在计划里没有
V2_PLAN_PARTIAL = """# 测试计划

目标模块：`src`

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 除数为零必须报错 | tests/test_core.py::test_divide |

## 核心逻辑细节

| 维度 | DP ID | 判定点 | 归属的不变式 | 落点（文件::用例名） | 豁免理由 |
| --- | --- | --- | --- | --- | --- |
| 分支 | src/pkg/core.py#divide#branch | if b == 0 | I1 | tests/test_core.py::test_divide | |
"""

#: 计划里写的是不带 ordinal 的 `...#branch` —— 它就是机器的 branch#1
V2_PLAN_ORDINAL = """# 测试计划

目标模块：`src`

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 除数为零必须报错 | tests/test_core.py::test_divide |

## 核心逻辑细节

| 维度 | DP ID | 判定点 | 归属的不变式 | 落点（文件::用例名） | 豁免理由 |
| --- | --- | --- | --- | --- | --- |
| 分支 | src/pkg/core.py#divide#branch | if b == 0 | I1 | tests/test_core.py::test_divide | |
"""

#: 旧的模块级维度表（v1）—— 没有 DP 列
V1_PLAN = """# 测试计划

目标模块：`src`

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 除数为零必须报错 | tests/test_core.py::test_divide |

## 核心逻辑细节

| 维度 | 落点 / 不适用理由 |
| --- | --- |
| 错误路径 | tests/test_core.py::test_divide |
"""

#: 钉指向的用例跟目标模块没关系（不 import 目标模块的任何东西）
V2_PLAN_ZERO = """# 测试计划

目标模块：`src`

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 除数为零必须报错 | tests/other_test.py::test_other |

## 核心逻辑细节

| 维度 | DP ID | 判定点 | 归属的不变式 | 落点（文件::用例名） | 豁免理由 |
| --- | --- | --- | --- | --- | --- |
| 分支 | src/pkg/core.py#divide#branch | if b == 0 | I1 | tests/other_test.py::test_other | |
| 边界 | src/pkg/core.py#divide#boundary | b == 0 | I1 | tests/test_core.py::test_divide | |
| 错误路径 | src/pkg/core.py#divide#error-path | raise ValueError | I1 | tests/test_core.py::test_divide | |
"""

#: 同样的计划，但高危那一行给了豁免理由 ⇒ 零覆盖可豁免
V2_PLAN_ZERO_WAIVED = V2_PLAN_ZERO.replace(
    "| 分支 | src/pkg/core.py#divide#branch | if b == 0 | I1 | "
    "tests/other_test.py::test_other | |",
    "| 分支 | src/pkg/core.py#divide#branch | if b == 0 | I1 | "
    "tests/other_test.py::test_other | 经间接调用覆盖 |")

TS_V2_PLAN = """# 测试计划

目标模块：`src`

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 除数为零必须报错 | src/calc.test.ts::divides |

## 核心逻辑细节

| 维度 | DP ID | 判定点 | 归属的不变式 | 落点（文件::用例名） | 豁免理由 |
| --- | --- | --- | --- | --- | --- |
| 分支 | src/calc.ts#divide#branch | if b === 0 | I1 | src/calc.test.ts::divides | |
"""


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


def py_repo() -> tuple[Path, readiness.Module]:
    root = mkdtemp()
    write(root / "pyproject.toml", PYPROJECT)
    write(root / "src" / "pkg" / "__init__.py", "")
    write(root / "src" / "pkg" / "core.py", PY_SRC)
    write(root / "tests" / "test_core.py", PY_TEST)
    m = readiness.discover_module(root, "src", None)
    return root, m


def ts_repo() -> tuple[Path, readiness.Module]:
    root = mkdtemp()
    write(root / "package.json", json.dumps(
        {"scripts": {"test": "node --test 'src/**/*.test.ts'"}}))
    write(root / "src" / "calc.ts", TS_SRC)
    write(root / "src" / "calc.test.ts", TS_TEST)
    m = readiness.discover_module(root, "src", None)
    return root, m


def three(root: Path, m: readiness.Module, plan: Path | None,
          dp, *, lenient: bool = False) -> list:
    return [
        readiness.check_plan_alignment(m, str(plan) if plan else None, dp,
                                       lenient=lenient),
        readiness.check_decision_coverage(m, str(plan) if plan else None, dp,
                                          lenient=lenient),
        readiness.check_pin_discriminating(m, str(plan) if plan else None, None,
                                           lenient=lenient),
    ]


# ------------------------------------------------------------------ 判据口径


def test_no_plan_means_new_criteria_skipped_not_fail():
    root, m = py_repo()
    dp = req_plan.derive_plan_analysis(root, ["src"])
    for c in three(root, m, None, dp):
        expect(c.status == "skipped", f"{c.key} 没计划 ⇒ skipped（不是 fail）")
        expect(c.evidence_kind == "untested",
               f"{c.key} 没计划 ⇒ 证据类别 untested")


def test_v1_plan_downgrades_new_criteria_to_warn():
    root, m = py_repo()
    plan = write(root / "docs" / "plan.md", V1_PLAN)
    dp = req_plan.derive_plan_analysis(root, ["src"])
    for c in three(root, m, plan, dp):
        expect(c.status == "warn", f"{c.key} 旧格式计划 ⇒ warn")
        expect("升级" in c.detail, f"{c.key} 文案写明「升级」")


def test_v2_plan_missing_machine_dps_fails_alignment():
    root, m = py_repo()
    plan = write(root / "docs" / "plan.md", V2_PLAN_PARTIAL)
    dp = req_plan.derive_plan_analysis(root, ["src"])
    c = readiness.check_plan_alignment(m, str(plan), dp)
    expect(c.status == "fail", "机器推导出、计划里没有 ⇒ 报红")
    expect(any("error-path" in e for e in c.evidence),
           "缺口清单点名了机器推导出的判定点")
    expect(all(c2.status != "fail" or c2.key == "plan-alignment"
               for c2 in three(root, m, plan, dp)),
           "缺口只报在对的那条判据上")


def test_dp_ordinal_is_identity_not_string():
    """计划写 `x#f#branch`、机器推导出 `x#f#branch#1` —— 同一个判定点。

    规格 §2.3.2：ordinal 段可写可不写，缺省当 1。两边按字符串比会把这
    一条判成「机器有、计划没有」＋「计划有、机器没有」两条假账。
    """
    root = mkdtemp()
    write(root / "pyproject.toml", PYPROJECT)
    write(root / "src" / "pkg" / "__init__.py", "")
    write(root / "src" / "pkg" / "core.py", PY_SRC_TWO_BRANCH)
    write(root / "tests" / "test_core.py", PY_TEST)
    plan_path = write(root / "docs" / "plan.md", V2_PLAN_ORDINAL)
    m = readiness.discover_module(root, "src", None)
    dp = req_plan.derive_plan_analysis(root, ["src"])
    ids = {p.dp_id for p in dp.points}
    expect("src/pkg/core.py#divide#branch#1" in ids,
           "机器推导出的是带 ordinal 的 branch#1")
    parsed = req_plan.parse_plan(plan_path)
    align = readiness._dpe_alignment(dp, parsed)
    expect(align["human_only"] == [],
           f"计划里的 #branch 就是机器的 branch#1，不该算「机器没推导出」"
           f"（实得 {align['human_only']}）")
    expect("src/pkg/core.py#divide#branch#1" not in align["machine_only"],
           f"branch#1 已被计划覆盖，不该算「机器推导出但计划里没有」"
           f"（实得 {align['machine_only']}）")
    acc = readiness._dpe_accounting(dp, parsed)
    expect(acc["attributed"] == 1 and acc["gaps"] == 5,
           f"branch#1 归到计划那一行，账上 1 条已归属、5 条缺口"
           f"（实得 attributed={acc['attributed']} gaps={acc['gaps']}）")
    c = readiness.check_plan_alignment(m, str(plan_path), dp)
    expect(not any("机器没推导出" in e for e in c.evidence),
           "对齐判据的证据里不再说「机器没推导出」")


def test_lenient_downgrades_fail_to_warn():
    root, m = py_repo()
    plan = write(root / "docs" / "plan.md", V2_PLAN_PARTIAL)
    dp = req_plan.derive_plan_analysis(root, ["src"])
    for c in three(root, m, plan, dp, lenient=True):
        expect(c.status != "fail", f"{c.key} --lenient ⇒ 不报红")
    c = readiness.check_plan_alignment(m, str(plan), dp, lenient=True)
    expect(c.status == "warn", "--lenient 把 fail 降成 warn")


def test_l2_level_never_hard_fails():
    root, m = ts_repo()
    plan = write(root / "docs" / "plan.md", TS_V2_PLAN)
    dp = req_plan.derive_plan_analysis(root, ["src"], prefer="l2")
    expect(dp.level == "L2", "指定 l2 ⇒ 推导等级 L2")
    for c in three(root, m, plan, dp):
        expect(c.status not in ("fail", "pass"),
               f"{c.key} L2 下不硬判（既不报红也不报绿）")


def test_pin_not_touching_module_is_reported():
    root, m = py_repo()
    write(root / "tests" / "other_test.py",
          "def test_other():\n    assert True\n")
    plan = write(root / "docs" / "plan.md", V2_PLAN_ZERO)
    dp = req_plan.derive_plan_analysis(root, ["src"])
    c = readiness.check_plan_alignment(m, str(plan), dp)
    expect(c.status == "fail", "钉不碰目标模块 ⇒ 报红")
    expect(any("目标模块" in e for e in c.evidence),
           "证据里写明「不直接引用目标模块」")


def test_zero_coverage_can_be_waived():
    root, m = py_repo()
    write(root / "tests" / "other_test.py",
          "def test_other():\n    assert True\n")
    plan = write(root / "docs" / "plan.md", V2_PLAN_ZERO_WAIVED)
    dp = req_plan.derive_plan_analysis(root, ["src"])
    c = readiness.check_plan_alignment(m, str(plan), dp)
    expect(c.status != "fail", "给了豁免理由 ⇒ 零覆盖不报红")


# ------------------------------------------------------------------ 输出契约


def test_json_has_three_new_top_level_segments():
    root, _ = py_repo()
    plan = write(root / "docs" / "plan.md", V2_PLAN)
    r = run("src", "--repo", str(root), "--plan", str(plan), "--json")
    expect(r.returncode in (0, 1), "命令跑完了")
    try:
        data = json.loads(r.stdout)
    except ValueError:
        expect(False, f"输出是合法 JSON（stderr: {r.stderr[-300:]}）")
        return
    for key in ("decision_points", "alignment", "discriminating"):
        expect(key in data, f"JSON 顶层有 {key}")
    seg = data.get("decision_points") or {}
    for f in ("analysis_level", "analysis_backend", "analysis_backend_source"):
        expect(f in seg, f"decision_points 带 {f}")
    expect(seg.get("analysis_level") == "L3",
           "Python 走标准库 ast ⇒ analysis_level = L3")
    expect(seg.get("total") == 3, "判定点总数 3")
    expect(data.get("alignment", {}).get("machine_only") == [],
           "计划齐全 ⇒ machine_only 为空")


def test_text_output_has_decision_point_section():
    root, _ = py_repo()
    plan = write(root / "docs" / "plan.md", V2_PLAN)
    r = run("src", "--repo", str(root), "--plan", str(plan))
    expect("判定点覆盖" in r.stdout, "文本输出有「判定点覆盖」节")
    expect("判定点总数 3" in r.stdout, "节里写了判定点总数")


def test_pytest_passing_output_is_understood():
    """pytest 的「1 passed」必须读得出来——否则钉住用例一跑绿就判不了。"""
    fails, setup = readiness._classify_run(0, "1 passed in 0.01s\n", "变异")
    expect(not fails and not setup,
           f"pytest 全绿 ⇒ 读得出用例结果（实际 fails={fails} setup={setup}）")


def test_python_dpsp_lane_produces_signal():
    """readiness 这条链上，Python 的 DPSP 必须真判出结论。

    之前的 Python 路径恒「不适用」⇒ 判据恒绿（橡皮章）；后来又恒「判不了」
    （pytest 的汇总读不出来、变异标记被捕获吞掉）⇒ 还是恒绿。
    """
    root, m = py_repo()
    plan_path = write(root / "docs" / "plan.md", V2_PLAN)
    plan = req_plan.parse_plan(plan_path)
    dp = req_plan.derive_plan_analysis(root, ["src"])
    dpsp = readiness._dpe_run_dpsp(m, root, plan, dp, "quick")
    expect(dpsp is not None, "Python 的 DPSP 跑起来了")
    if dpsp is None:
        return
    verdicts = dpsp.get("verdicts") or []
    expect(bool(verdicts), "出了一份结论")
    expect(all(v.get("status") != "not-applicable" for v in verdicts),
           f"没有恒「不适用」（实际 {[v.get('status') for v in verdicts]}）")
    c = readiness.check_pin_discriminating(m, str(plan_path), dpsp)
    expect(c.status == "pass",
           f"强断言的钉 ⇒ 判据 pass（实际 {c.status}：{c.detail}）")


def test_not_judged_gained_two_entries():
    expect("并发正确性" in readiness.NOT_JUDGED,
           "「本工具不判定」清单含「并发正确性」")
    expect("容量与规模行为" in readiness.NOT_JUDGED,
           "「本工具不判定」清单含「容量与规模行为」")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    test_no_plan_means_new_criteria_skipped_not_fail()
    test_v1_plan_downgrades_new_criteria_to_warn()
    test_v2_plan_missing_machine_dps_fails_alignment()
    test_dp_ordinal_is_identity_not_string()
    test_lenient_downgrades_fail_to_warn()
    test_l2_level_never_hard_fails()
    test_pin_not_touching_module_is_reported()
    test_zero_coverage_can_be_waived()
    test_json_has_three_new_top_level_segments()
    test_text_output_has_decision_point_section()
    test_pytest_passing_output_is_understood()
    test_python_dpsp_lane_produces_signal()
    test_not_judged_gained_two_entries()

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
