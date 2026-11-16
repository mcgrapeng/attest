#!/usr/bin/env python3
"""DPSP（区分力自证）的回归测试。

一句话：**这条钉能不能发现这类缺陷**。
办法：把生产代码改坏（变异），跑测试，看钉住的那**一个**用例会不会红。

四档结论（规格 §6.1）：pass / fail / untested / not-applicable。
只有「全部存活」那一档报红。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
FAILURES: list[str] = []
TMPDIRS: list[Path] = []

sys.path.insert(0, str(HERE))
from discriminate import (  # noqa: E402
    match_case, parse_imports, runtime_symbols, run_dpsp)

ENGINE_TS = """export const a = 1;
export const b = 2;
export function classify(n: number): string {
  if (n === 0) { return 'zero'; }
  return 'other';
}
"""

CALC_TS = """export function classify(n: number): string {
  if (n === 0) { return 'zero'; }
  return 'other';
}
"""

#: 钉住的用例碰到「n === 0」这一支 ⇒ 变异能被它发现 ⇒ pass
COVERED_TS = CALC_TS
COVERED_TEST = """import { test } from 'node:test';
import assert from 'node:assert';
import { classify } from './calc.ts';
test('classifies zero', () => { assert.equal(classify(0), 'zero'); });
"""

#: 钉住的用例**调用了**函数，但断言与判定结果无关 ⇒ 改坏判定它也发现不了 ⇒ fail
WEAK_ASSERT_TEST = """import { test } from 'node:test';
import assert from 'node:assert';
import { classify } from './calc.ts';
test('classifies zero', () => { assert.equal(typeof classify(0), 'string'); });
"""

#: 纯计算函数：没有比较 / throw ⇒ 零变异点 ⇒ not-applicable
NO_POINT_TS = "export function double(n: number): number { return n * 2; }\n"
NO_POINT_TEST = """import { test } from 'node:test';
import assert from 'node:assert';
import { double } from './calc.ts';
test('doubles', () => { assert.equal(double(2), 4); });
"""

#: 被测函数压根没被调用 ⇒ 变异没生效 ⇒ not-applicable（不是「存活」）
UNCALLED_TS = """export function neverCalled(n: number): string {
  if (n === 0) { return 'zero'; }
  return 'other';
}
"""
UNCALLED_TEST = """import { test } from 'node:test';
import assert from 'node:assert';
import { neverCalled } from './calc.ts';
test('does nothing', () => { assert.equal(1, 1); });
"""

#: 经路径别名可达 ⇒ 副本不密闭 ⇒ 不变异 ⇒ not-applicable
ALIAS_TEST = """import { test } from 'node:test';
import assert from 'node:assert';
import { classify } from '@/calc.ts';
test('classifies zero', () => { assert.equal(classify(0), 'zero'); });
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


def node_repo(files: dict[str, str]) -> Path:
    """一个能用 `node --test` 跑起来的最小仓库。"""
    root = mkdtemp()
    write(root / "package.json",
          '{"name":"fixture","version":"1.0.0","scripts":{"test":"node --test"}}\n')
    for rel, text in files.items():
        write(root / rel, text)
    return root


def dpsp(root: Path, src: str, test: str, extra: dict | None = None,
         case: str = "classifies zero") -> dict:
    """跑一轮 DPSP：钉在 `src/calc.test.ts` 的那个用例上。

    `root` 就是被测仓库（`node_repo` 建的），tsconfig 之类的附加文件留在里面。
    """
    write(root / "src/calc.ts", src)
    write(root / "src/calc.test.ts", test)
    for rel, text in (extra or {}).items():
        write(root / rel, text)
    return run_dpsp(root, ".", [("INV-1", f"src/calc.test.ts::{case}", "钉住的不变式")],
                    ["node", "--test"], ["src/calc.test.ts"],
                    source_rels=["src/calc.ts"], level="L2")


# ---------------------------------------------------------------- 导入解析


def test_import_parser_finds_dynamic_import() -> None:
    root = node_repo({"src/mem0-engine.ts": ENGINE_TS})
    src = "const { a, b } = await import('./mem0-engine.ts');\n"
    refs = parse_imports(src, "src/x.test.ts", root)
    expect([r.symbols for r in refs] == [("a", "b")],
           "动态 import 的解构符号被认出")
    expect(bool(refs) and refs[0].target == "src/mem0-engine.ts",
           "动态 import 的目标解析成仓库相对路径")


def test_import_parser_marks_type_only() -> None:
    root = node_repo({"src/mem0-engine.ts": ENGINE_TS})
    src = "import type { EngineHandle } from './mem0-engine.ts';\n"
    refs = parse_imports(src, "src/x.test.ts", root)
    expect(bool(refs) and refs[0].type_only, "纯类型 import 标成 type_only")
    expect(runtime_symbols(refs) == set(), "纯类型 import 不算运行时覆盖")


def test_inline_import_type_is_not_a_runtime_reference() -> None:
    """`type X = import('./a.ts').Y` 是类型位置 ⇒ 运行时被擦除（规格 §5.1.1）。"""
    root = node_repo({"src/mem0-engine.ts": ENGINE_TS})
    src = "type Mem0Api = import('./mem0-engine.ts').Row;\n"
    refs = parse_imports(src, "src/x.test.ts", root)
    expect(bool(refs) and refs[0].type_only,
           "类型位置的 import() 标成 type_only")
    expect(runtime_symbols(refs) == set(),
           "类型位置的 import() 不算运行时引用")


def test_type_only_specifier_is_not_a_runtime_symbol() -> None:
    """`import { type Row } from './a.ts'` 整条都是类型位置 ⇒ 不算引用。"""
    root = node_repo({"src/mem0-engine.ts": ENGINE_TS})
    src = "import { type Row } from './mem0-engine.ts';\n"
    refs = parse_imports(src, "src/x.test.ts", root)
    expect(bool(refs) and refs[0].type_only,
           "全是 type specifier 的 import 标成 type_only")
    expect(runtime_symbols(refs) == set(),
           "全是 type specifier 的 import 不算运行时引用")


def test_mixed_specifier_keeps_only_the_value_one() -> None:
    """`import { type Row, insertRow }` ⇒ 模块是运行时加载的，但 Row 不是运行时符号。"""
    root = node_repo({"src/mem0-engine.ts": ENGINE_TS})
    src = "import { type Row, insertRow } from './mem0-engine.ts';\n"
    refs = parse_imports(src, "src/x.test.ts", root)
    expect(bool(refs) and not refs[0].type_only,
           "混着值 specifier 的 import 不算纯类型")
    expect(runtime_symbols(refs) == {"insertRow"},
           f"只有值 specifier 算运行时符号（实得 {runtime_symbols(refs)}）")


def test_import_parser_namespace_level() -> None:
    root = node_repo({"src/mem0-engine.ts": ENGINE_TS})
    src = "const mod = await import('./mem0-engine.ts');\n"
    refs = parse_imports(src, "src/x.test.ts", root)
    expect(bool(refs) and refs[0].namespace_level,
           "无解构的动态 import ⇒ 命名空间级")
    expect(bool(refs) and set(refs[0].symbols) >= {"a", "b"},
           "命名空间级 ⇒ 该模块的全部导出符号都算引用")


def test_mock_module_counts_as_reference() -> None:
    """mock.module('./x.ts') 点名了那个模块 ⇒ 算引用（规格 §5.1.1）。"""
    root = node_repo({"src/mem0-engine.ts": ENGINE_TS})
    src = "mock.module('./mem0-engine.ts');\n"
    refs = parse_imports(src, "src/x.test.ts", root)
    expect(bool(refs), "mock.module 产出引用")
    expect(bool(refs) and refs[0].target == "src/mem0-engine.ts",
           "mock.module 的目标解析对了")


def test_import_parser_resolves_relative_and_alias() -> None:
    root = node_repo({
        "src/mem0-engine.ts": ENGINE_TS,
        "src/other.ts": "export const other = 1;\n",
        "tsconfig.json": json.dumps(
            {"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}),
    })
    src = ("import { a } from './mem0-engine.ts';\n"
           "import { other } from '@/other.ts';\n")
    refs = parse_imports(src, "src/x.test.ts", root)
    by_target = {r.target: r for r in refs}
    expect("src/mem0-engine.ts" in by_target,
           "相对路径解析成仓库相对路径")
    expect("src/mem0-engine.ts" in by_target and by_target["src/mem0-engine.ts"].relative,
           "相对 specifier 的 relative 标志为 True")
    expect("src/other.ts" in by_target,
           "路径别名按仓库 tsconfig 的 paths 解析")
    expect("src/other.ts" in by_target and not by_target["src/other.ts"].relative,
           "别名 specifier 的 relative 标志为 False")


# ---------------------------------------------------------------- 四档结论


def test_verdict_not_applicable_when_no_mutation_points() -> None:
    res = dpsp(node_repo({"src/calc.ts": NO_POINT_TS,
                           "src/calc.test.ts": NO_POINT_TEST}),
               NO_POINT_TS, NO_POINT_TEST, case="doubles")
    expect(bool(res["verdicts"]), "有一份结论")
    expect(res["verdicts"][0]["status"] == "not-applicable",
           "零变异点 ⇒ not-applicable（不是 fail）")
    expect(res["verdicts"][0]["status"] != "fail",
           "零变异点不报红")


def test_verdict_fail_when_all_mutants_survive() -> None:
    """钉住的用例调了函数，但断言与判定结果无关 ⇒ 改坏判定它也绿 ⇒ fail。"""
    res = dpsp(node_repo({"src/calc.ts": COVERED_TS,
                           "src/calc.test.ts": WEAK_ASSERT_TEST}),
               COVERED_TS, WEAK_ASSERT_TEST)
    expect(res["verdicts"][0]["status"] == "fail",
           "变异全部存活 ⇒ fail")
    expect(bool(res["survivors"]), "存活点被记录下来")


def test_verdict_pass_when_one_mutant_killed() -> None:
    res = dpsp(node_repo({"src/calc.ts": COVERED_TS,
                           "src/calc.test.ts": COVERED_TEST}),
               COVERED_TS, COVERED_TEST)
    expect(res["verdicts"][0]["status"] == "pass",
           "有变异被钉住的用例杀死 ⇒ pass")
    expect(res["verdicts"][0]["killed"] >= 1, "杀死数被记录下来")
    expect(res["killed"] == 1, "顶层的 killed 数的是**不变式**（规格 §7.4）")
    expect(res["verdicts"][0]["invariant_id"] == "INV-1",
           "结论能对回它的那条不变式")


#: 钉住的那条用例**不碰**任何有变异点的符号（同文件另一个函数才有）
#: ⇒ 变异点杀不死这条钉是必然的 ⇒ 判「判不了」，不是「这条钉没区分力」
OUT_OF_CASE_TS = """export function passthrough(n: number): number { return n; }

export function classify(n: number): string {
  if (n === 0) { return 'zero'; }
  return 'other';
}
"""

OUT_OF_CASE_TEST = """import { test } from 'node:test';
import assert from 'node:assert';
import { passthrough, classify } from './calc.ts';
test('classifies zero', () => { assert.equal(passthrough(1), 1); });
test('classifies', () => { assert.equal(classify(0), 'zero'); });
"""


def test_mutants_outside_the_pinned_case_are_untested() -> None:
    """变异点全在钉住那条用例没提到的符号上 ⇒ 判不了，不是 fail。

    否则「同文件里毫不相干的函数吃掉名额」会被说成「这条钉发现不了这类缺陷」
    —— 那是假的：那些变异点本来就跟这条钉无关。
    """
    res = dpsp(node_repo({"src/calc.ts": OUT_OF_CASE_TS,
                           "src/calc.test.ts": OUT_OF_CASE_TEST}),
               OUT_OF_CASE_TS, OUT_OF_CASE_TEST, case="classifies zero")
    expect(res["verdicts"][0]["status"] == "untested",
           "变异点在用例范围外 ⇒ untested（不是 fail）")
    expect(any("没提到任何可变异符号" in n for n in res["notes"]),
           "notes 里说清「变异点只能取自别的符号」")


def test_mutant_marker_makes_unloaded_code_not_applicable() -> None:
    """被测代码压根没被调用 ⇒ not-applicable，不是「存活」。"""
    res = dpsp(node_repo({"src/calc.ts": UNCALLED_TS,
                           "src/calc.test.ts": UNCALLED_TEST}),
               UNCALLED_TS, UNCALLED_TEST, case="does nothing")
    expect(res["verdicts"][0]["status"] == "not-applicable",
           "变异没生效 ⇒ not-applicable")
    expect(not res["survivors"], "没生效的变异不算存活")
    expect(res["not_applicable"] >= 1, "没生效的变异被单独计数")


def test_package_reachable_file_is_not_mutated() -> None:
    """经包名/别名可达的文件 ⇒ 不变异（副本不密闭）。"""
    res = dpsp(node_repo({
        "src/calc.ts": COVERED_TS,
        "src/calc.test.ts": ALIAS_TEST,
        "tsconfig.json": json.dumps(
            {"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}),
    }), COVERED_TS, ALIAS_TEST)
    expect(res["verdicts"][0]["status"] == "not-applicable",
           "别名可达 ⇒ not-applicable")
    expect(any("包路径" in n for n in res["notes"]),
           "别名可达会写明「副本不密闭」")


def test_verdict_untested_when_pin_is_broken() -> None:
    """钉指向的文件不存在 ⇒ 判不了（不是 fail：跑不起来 ≠ 不达标）。"""
    repo = node_repo({"src/calc.ts": COVERED_TS, "src/calc.test.ts": COVERED_TEST})
    res = run_dpsp(repo, ".", [("INV-1", "src/nope.test.ts::classifies zero", "x")],
                   ["node", "--test"], ["src/calc.test.ts"],
                   source_rels=["src/calc.ts"], level="L2")
    expect(res["verdicts"][0]["status"] == "untested", "钉的文件不存在 ⇒ untested")
    expect(res["verdicts"][0]["status"] != "fail", "判不了不报红")


def test_monorepo_pin_resolves_against_repo_root() -> None:
    """monorepo：钉的基准是仓库根，变异检查跑在工作区 ⇒ 两边必须对齐。

    基准错位的后果不是「判红」而是「全判不了」：钉指向的文件在
    `<仓库根>/service/src/...`，按工作区相对找就是 `<仓库根>/service/service/src/...`
    ⇒ 每条钉都报「文件不存在」⇒ 区分力一条也测不出来。
    """
    root = node_repo({"service/package.json":
                      '{"name":"svc","version":"1.0.0"}\n'})
    write(root / "service/src/calc.ts", COVERED_TS)
    write(root / "service/src/calc.test.ts", COVERED_TEST)
    res = run_dpsp(root, "service",
                   [("INV-1", "service/src/calc.test.ts::classifies zero", "x")],
                   ["node", "--test"], ["src/calc.test.ts"],
                   source_rels=["src/calc.ts"], level="L2")
    expect(res["verdicts"][0]["status"] == "pass",
           "仓库根相对的钉在 monorepo 里也能判出区分力"
           f"（实际 {res['verdicts'][0]['status']}：{res['verdicts'][0]['detail']}）")
    expect(res["verdicts"][0]["test"] == "src/calc.test.ts",
           "结论里记的是工作区相对路径")


def test_pin_outside_workspace_is_untested() -> None:
    """钉指向工作区之外的文件 ⇒ 副本里跑不到 ⇒ 判不了，不报红。"""
    root = node_repo({"service/package.json":
                      '{"name":"svc","version":"1.0.0"}\n',
                      "other/x.test.ts": COVERED_TEST})
    res = run_dpsp(root, "service",
                   [("INV-1", "other/x.test.ts::classifies zero", "x")],
                   ["node", "--test"], ["src/calc.test.ts"],
                   source_rels=["src/calc.ts"], level="L2")
    expect(res["verdicts"][0]["status"] == "untested",
           "工作区外的钉 ⇒ untested")
    expect(res["verdicts"][0]["status"] != "fail", "判不了不报红")


def test_not_applicable_verdict_still_reports_its_pin() -> None:
    """不适用也必须说清「是哪条钉、哪个测试文件」。

    少了这两个字段，报告里就只剩一句「不适用」⇒ 看的人无从判断
    是钉指错了还是这条本来就不在判定范围。
    """
    repo = node_repo({"src/calc.ts": COVERED_TS, "src/calc.test.ts": COVERED_TEST})
    # 源文件不可达 ⇒ 走「没有相对路径可达的源文件」那一档
    res = run_dpsp(repo, ".", [("INV-2", "src/calc.test.ts::classifies zero", "x")],
                   ["node", "--test"], ["src/calc.test.ts"],
                   source_rels=["src/other.ts"], level="L2")
    v = res["verdicts"][0]
    expect(v["status"] == "not-applicable", "源文件不可达 ⇒ not-applicable")
    expect(v["pin"] == "src/calc.test.ts::classifies zero",
           "不适用也报得出钉")
    expect(v["test"] == "src/calc.test.ts", "不适用也报得出测试文件")


def test_match_case_is_the_mutation_check_one() -> None:
    import mutation_check
    expect(match_case is mutation_check.match_case,
           "match_case 是同一个函数对象（不重写）")


def test_budget_reports_sampled_ratio() -> None:
    """超过预算 ⇒ 抽样，但必须写明判了多少 / 一共多少（自证不许藏缺口）。"""
    repo = node_repo({"src/calc.ts": COVERED_TS,
                       "src/calc.test.ts": COVERED_TEST})
    invs = [(f"INV-{i}", "src/calc.test.ts::classifies zero", "x")
            for i in range(25)]
    res = run_dpsp(repo, ".", invs, ["node", "--test"], ["src/calc.test.ts"],
                   source_rels=["src/calc.ts"], level="L2")
    expect(res["total"] == 25, "total 是全部不变式数")
    expect(res["checked"] < res["total"], "超预算 ⇒ 抽样")
    expect(res["checked"] <= 20, "抽样后不超过预算")
    expect(bool(res.get("budget_note")), "抽样比例写进 budget_note")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    test_import_parser_finds_dynamic_import()
    test_import_parser_marks_type_only()
    test_inline_import_type_is_not_a_runtime_reference()
    test_type_only_specifier_is_not_a_runtime_symbol()
    test_mixed_specifier_keeps_only_the_value_one()
    test_import_parser_namespace_level()
    test_mock_module_counts_as_reference()
    test_import_parser_resolves_relative_and_alias()
    test_verdict_not_applicable_when_no_mutation_points()
    test_verdict_fail_when_all_mutants_survive()
    test_verdict_pass_when_one_mutant_killed()
    test_mutant_marker_makes_unloaded_code_not_applicable()
    test_mutants_outside_the_pinned_case_are_untested()
    test_package_reachable_file_is_not_mutated()
    test_verdict_untested_when_pin_is_broken()
    test_monorepo_pin_resolves_against_repo_root()
    test_pin_outside_workspace_is_untested()
    test_not_applicable_verdict_still_reports_its_pin()
    test_match_case_is_the_mutation_check_one()
    test_budget_reports_sampled_ratio()

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
