#!/usr/bin/env python3
"""变异算子（7 类）、严格用例匹配、作用域限定与解析检查的回归测试。

算子自证是这里的核心：**每个算子都要能杀死一个已知的坏实现**，
否则它就是"看着像变异、其实测不出东西"。
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
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


# TAP 的 "not ok N - 用例名" ⇒ 失败用例名。
_NOT_OK_RE = re.compile(r"^\s*not ok\s+\d+\s*-?\s*(.*)$", re.M)


def _classify(code: int, out: str, name: str) -> tuple[list[str], list[str]]:
    """三分口径的最小实现：真失败 ⇒ 失败用例名；认不出 ⇒ 判不了。"""
    fails = [m.group(1).strip() for m in _NOT_OK_RE.finditer(out)
             if m.group(1).strip()]
    if fails:
        return fails, []
    if code == 0:
        return [], []
    return [], [f"{name}: 认不出用例名"]


def node_repo(files: dict[str, str]) -> Path:
    """一个能用 `node --test` 跑起来的最小仓库。"""
    root = mkdtemp()
    write(root / "package.json",
          '{"name":"fixture","version":"1.0.0","scripts":{"test":"node --test"}}\n')
    for rel, text in files.items():
        write(root / rel, text)
    return root


def run_check(root: Path, source_rels: list[str], test_rels: list[str],
              **kw) -> dict:
    sys.path.insert(0, str(HERE))
    from mutation_check import run_mutation_check  # noqa: E402
    return run_mutation_check(
        repo=root, ws_rel=".", source_rels=source_rels, test_rels=test_rels,
        runner=["node", "--test"], classify=_classify, **kw)


# --------------------------------------------------------------- 算子表


def test_operator_table():
    print("test_operator_table")
    sys.path.insert(0, str(HERE))
    from mutation_check import (L2_OPERATORS, L3_OPERATORS,  # noqa: E402
                                OPERATORS)
    expect(OPERATORS == ("cmp-negate", "cmp-tighten", "in-to-hasown",
                         "drop-throw", "drop-call", "predicate-negate",
                         "catch-drop"),
           "OPERATORS 是规格里的 7 个算子、顺序不变")
    expect(L2_OPERATORS < set(OPERATORS), "L2 是 OPERATORS 的真子集")
    expect(L2_OPERATORS | L3_OPERATORS == set(OPERATORS),
           "L2 ∪ L3 = 全部算子")
    expect(not (L2_OPERATORS & L3_OPERATORS), "L2 与 L3 不重叠")


# --------------------------------------------------------------- 用例匹配


def test_match_case_is_strict():
    print("\ntest_match_case_is_strict")
    sys.path.insert(0, str(HERE))
    from mutation_check import match_case  # noqa: E402
    expect(match_case("failing case", "outer group > failing case"),
           "末尾一段相等 ⇒ 命中")
    expect(match_case("outer group > failing case",
                      "outer group > failing case"),
           "完全相等 ⇒ 命中")
    expect(not match_case("rejects a row",
                          "outer group > rejects a row without id"),
           "多出来的字 ⇒ 不命中")
    expect(not match_case("failing case", "failing case extra"),
           "用例名更长 ⇒ 不命中")
    expect(not match_case("a > b", "a > b > c"),
           "多一段 ⇒ 不命中")
    expect(not match_case("", "outer group > failing case"),
           "空用例名 ⇒ 不命中")
    expect(not match_case("failing", "a > b > c"),
           "段数不足 ⇒ 不命中")
    expect(match_case("outer group > failing case", "outer group > failing case"),
           "多段完全相等 ⇒ 命中")


# --------------------------------------------------------------- L2 算子


def test_cmp_negate_kills_a_bad_implementation():
    print("\ntest_cmp_negate_kills_a_bad_implementation")
    root = node_repo({
        "src/a.js": "function isOne(x) { return x === 1; }\n"
                    "module.exports = { isOne };\n",
        "src/a.test.js": (
            "const test = require('node:test');\n"
            "const assert = require('node:assert');\n"
            "const { isOne } = require('./a.js');\n"
            "test('isOne', () => {\n"
            "  assert.strictEqual(isOne(1), true);\n"
            "  assert.strictEqual(isOne(2), false);\n"
            "});\n"),
    })
    res = run_check(root, ["src/a.js"], ["src/a.test.js"],
                    ops=("cmp-negate",))
    expect(res["attempted"] >= 1, "至少试了一个变异点")
    expect(res["killed"] >= 1, "cmp-negate 杀死了坏实现")
    expect(not res["survivors"], "没有存活者")


def test_in_to_hasown_kills_prototype_chain_bug():
    print("\ntest_in_to_hasown_kills_prototype_chain_bug")
    # 原实现用 `in`（会看到原型链上的键）；测试把「in 能看到原型链」钉住 ⇒
    # 变异成 Object.hasOwn 后行为变了 ⇒ 杀死。
    root = node_repo({
        "src/a.js": "const MAP = Object.create({ inherited: 1 });\n"
                    "function has(map, key) { return key in map; }\n"
                    "module.exports = { MAP, has };\n",
        "src/a.test.js": (
            "const test = require('node:test');\n"
            "const assert = require('node:assert');\n"
            "const { MAP, has } = require('./a.js');\n"
            "test('has', () => {\n"
            "  assert.strictEqual(has(MAP, 'own'), false);\n"
            "  assert.strictEqual(has(MAP, 'inherited'), true);\n"
            "});\n"),
    })
    res = run_check(root, ["src/a.js"], ["src/a.test.js"],
                    ops=("in-to-hasown",))
    expect(res["attempted"] >= 1, "至少试了一个变异点")
    expect(res["killed"] >= 1, "in-to-hasown 杀死了原型链语义")


def test_in_to_hasown_skips_for_in():
    print("\ntest_in_to_hasown_skips_for_in")
    sys.path.insert(0, str(HERE))
    from mutation_check import find_mutations  # noqa: E402
    root = mkdtemp()
    p = write(root / "a.js",
              "for (const x in obj) { use(x); }\n"
              "if (k in obj) { use(k); }\n")
    muts = find_mutations(p)
    in_muts = [m for m in muts if m.op == "in-to-hasown"]
    expect(len(in_muts) == 1,
           f"只找到一个 in 运算符（for...in 那个不算）实际 {len(in_muts)}")
    expect(all(m.line == 2 for m in in_muts),
           "找到的那个 in 在第 2 行（if (k in obj)），不是第 1 行的 for...in")
    expect(all("for" not in m.original for m in in_muts),
           "for...in 不会被当成 in 运算符")


def test_mutation_line_number_survives_multiline_comments():
    """多行注释/字符串不能把后面变异点的行号顶掉。

    词法算子靠「在剥离后的代码里数换行」算行号 ⇒ 剥离字符串与注释时
    必须**保留换行**。早期实现把整段匹配替换成等长空格 ⇒ 多行注释里的
    换行被吃掉 ⇒ 该行之后的每个变异点都报错的行号（实测 lumen 的 7 个
    脚本：71 个词法变异点行号全错）。
    """
    print("\ntest_mutation_line_number_survives_multiline_comments")
    sys.path.insert(0, str(HERE))
    from mutation_check import find_mutations  # noqa: E402
    root = mkdtemp()
    src = ("/**\n"
           " * 多行文档注释。\n"
           " * 第二行。\n"
           " */\n"
           "function f(a, b) {\n"
           "  return a === b;\n"
           "}\n"
           "module.exports = { f };\n")
    p = write(root / "a.js", src)
    muts = [m for m in find_mutations(p, limit=None) if m.op == "cmp-negate"]
    expect(len(muts) == 1, f"找到 1 个 cmp-negate 变异点，实际 {len(muts)}")
    if not muts:
        return
    idx = src.index("===")
    want_line = src.count("\n", 0, idx) + 1
    want_col = idx - (src.rfind("\n", 0, idx) + 1) + 1
    expect(muts[0].line == want_line,
           f"行号是原文件里的真行号（{want_line}），实际 {muts[0].line}")
    expect(muts[0].col == want_col,
           f"列号是原文件里的真列号（{want_col}），实际 {muts[0].col}")


def test_mutation_symbol_is_enclosing_function():
    """变异点归属它所在的那个函数（作用域过滤按这个符号筛）。

    这条**不是**行号那条的附庸：行号被顶掉时作用域仍然是对的（实测 7 个
    真实脚本 71 个词法变异点，修复前后 symbol 零变化 —— 压缩多行注释后，
    变异点与所属函数的相对距离只会变短，不会跨出函数）。它钉的是另一件事：
    剥离字符串与注释不能把作用域结构改坏。
    """
    print("\ntest_mutation_symbol_is_enclosing_function")
    sys.path.insert(0, str(HERE))
    from mutation_check import find_mutations  # noqa: E402
    root = mkdtemp()
    src = ("/*\n"
           " * 多行注释\n"
           " */\n"
           "function alpha() {\n"
           "  return 0;\n"
           "}\n"
           "function beta(a, b) {\n"
           "  return a === b;\n"
           "}\n")
    p = write(root / "a.js", src)
    muts = [m for m in find_mutations(p, limit=None) if m.op == "cmp-negate"]
    expect(len(muts) == 1, f"找到 1 个 cmp-negate 变异点，实际 {len(muts)}")
    if not muts:
        return
    expect(muts[0].symbol == "beta",
           f"变异点归属 beta，实际 {muts[0].symbol!r}")


# --------------------------------------------------------------- L3 算子


def _structural(src: str, name: str = "a.js") -> list:
    sys.path.insert(0, str(HERE))
    from mutation_check import find_structural_mutations  # noqa: E402
    root = node_repo({f"src/{name}": src})
    return find_structural_mutations(root / "src" / name)


def test_drop_throw_replaces_not_deletes():
    print("\ntest_drop_throw_replaces_not_deletes")
    sys.path.insert(0, str(HERE))
    from mutation_check import _apply  # noqa: E402
    src = "function f() { throw new Error('x'); }\n"
    muts = _structural(src)
    hits = [m for m in muts if m.op == "drop-throw"]
    expect(bool(hits), "找到了 throw 变异点")
    if hits:
        expect(_apply(src, hits[0]) == "function f() { void new Error('x'); }\n",
               "throw 被**替换**成 void，不是删语句")


def test_drop_call_keeps_binding_defined():
    print("\ntest_drop_call_keeps_binding_defined")
    sys.path.insert(0, str(HERE))
    from mutation_check import _apply  # noqa: E402
    src = "function f() { const r = g(1); return r; }\n"
    muts = _structural(src)
    hits = [m for m in muts if m.op == "drop-call"]
    expect(bool(hits), "找到了调用变异点")
    if hits:
        out = _apply(src, hits[0])
        expect("const r = undefined;" in out, "绑定还在（const r = undefined;）")


def test_predicate_negate_flips_filter():
    print("\ntest_predicate_negate_flips_filter")
    sys.path.insert(0, str(HERE))
    from mutation_check import _apply  # noqa: E402
    src = "function f(rows) { return rows.filter((r) => r.ok); }\n"
    muts = _structural(src)
    hits = [m for m in muts if m.op == "predicate-negate"]
    expect(bool(hits), "找到了 filter 变异点")
    if hits:
        expect("!((r) => r.ok)" in _apply(src, hits[0]),
               "谓词被取反")


def test_catch_drop_skipped_when_binding_used_later():
    print("\ntest_catch_drop_skipped_when_binding_used_later")
    used = ("function f() {\n"
            "  try { risky(); } catch (e) { const reason = e.message; }\n"
            "  return reason;\n"
            "}\n")
    unused = ("function f() {\n"
              "  try { risky(); } catch (e) { log(e); }\n"
              "  return 1;\n"
              "}\n")
    expect(not [m for m in _structural(used) if m.op == "catch-drop"],
           "块里声明的名字在块外被引用 ⇒ 不生成该变异点")
    expect([m for m in _structural(unused) if m.op == "catch-drop"],
           "没有外部引用 ⇒ 生成 catch-drop 变异点")


def test_structural_points_need_no_typescript():
    print("\ntest_structural_points_need_no_typescript")
    sys.path.insert(0, str(HERE))
    from decision_points import detect_backend  # noqa: E402
    root = node_repo({"src/a.js": "function f() { return 1; }\n"})
    backend = detect_backend(root)
    expect(backend is not None,
           "本机能找到 TypeScript AST 后端（找不到则结构算子没法自测）")
    if backend is not None:
        from mutation_check import find_structural_mutations  # noqa: E402
        muts = find_structural_mutations(root / "src" / "a.js")
        expect(isinstance(muts, list), "结构算子能跑")


# --------------------------------------------------------------- 解析检查


def test_parse_check_failure_is_unknown_not_killed():
    print("\ntest_parse_check_failure_is_unknown_not_killed")
    root = node_repo({
        "src/a.js": "function isOne(x) { return x === 1; }\n"
                    "module.exports = { isOne };\n",
        "src/a.test.js": (
            "const test = require('node:test');\n"
            "const assert = require('node:assert');\n"
            "const { isOne } = require('./a.js');\n"
            "test('isOne', () => { assert.strictEqual(isOne(1), true); });\n"),
    })

    def broken(_path, _text) -> bool:
        return False

    res = run_check(root, ["src/a.js"], ["src/a.test.js"],
                    ops=("cmp-negate",), parse_check=broken)
    expect(res["attempted"] == 0, "解析不过 ⇒ 不计入分母")
    expect(res["killed"] == 0, "解析不过 ⇒ 不算杀死")
    expect(res["unknown"] >= 1, "解析不过 ⇒ 记成判不了")


def test_scope_symbols_limits_points():
    print("\ntest_scope_symbols_limits_points")
    src = ("function a(x) {\n"
           "  return x === 1;\n"
           "}\n"
           "function b(x) {\n"
           "  return x === 2;\n"
           "}\n")
    root = node_repo({
        "src/a.js": src + "module.exports = { a, b };\n",
        "src/a.test.js": (
            "const test = require('node:test');\n"
            "const assert = require('node:assert');\n"
            "const { a, b } = require('./a.js');\n"
            "test('a', () => { assert.strictEqual(a(1), true); });\n"
            "test('b', () => { assert.strictEqual(b(2), true); });\n"),
    })
    res_all = run_check(root, ["src/a.js"], ["src/a.test.js"],
                        ops=("cmp-negate",))
    res_b = run_check(root, ["src/a.js"], ["src/a.test.js"],
                      ops=("cmp-negate",), scope_symbols={"b"})
    expect(res_all["attempted"] == 2,
           f"不限定作用域 ⇒ 两个点都试（实际 {res_all['attempted']}）")
    expect(res_b["attempted"] == 1,
           f"限定 b ⇒ 只试 b 里那个（实际 {res_b['attempted']}）")
    where = [s["where"] for s in res_b["survivors"]]
    expect(all(w.endswith(":5") for w in where) if where else True,
           "只挑了 b 里的变异点（第 5 行），没碰 a（第 2 行）")


def test_case_name_judges_only_that_case():
    print("\ntest_case_name_judges_only_that_case")
    root = node_repo({
        "src/a.js": "function isOne(x) { return x === 1; }\n"
                    "module.exports = { isOne };\n",
        "src/a.test.js": (
            "const test = require('node:test');\n"
            "const assert = require('node:assert');\n"
            "const { isOne } = require('./a.js');\n"
            "test('other case', () => { assert.strictEqual(isOne(1), true); });\n"),
    })
    res = run_check(root, ["src/a.js"], ["src/a.test.js"],
                    ops=("cmp-negate",), case_name="isOne")
    expect(res["killed"] == 0, "钉的用例没红 ⇒ 不算杀死")
    expect(bool(res["survivors"]), "钉的用例没红 ⇒ 记成存活")
    res2 = run_check(root, ["src/a.js"], ["src/a.test.js"],
                     ops=("cmp-negate",), case_name="other case")
    expect(res2["killed"] >= 1, "钉的用例红了 ⇒ 算杀死")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    test_operator_table()
    test_match_case_is_strict()
    test_cmp_negate_kills_a_bad_implementation()
    test_in_to_hasown_kills_prototype_chain_bug()
    test_in_to_hasown_skips_for_in()
    test_drop_throw_replaces_not_deletes()
    test_drop_call_keeps_binding_defined()
    test_predicate_negate_flips_filter()
    test_catch_drop_skipped_when_binding_used_later()
    test_structural_points_need_no_typescript()
    test_parse_check_failure_is_unknown_not_killed()
    test_scope_symbols_limits_points()
    test_case_name_judges_only_that_case()
    test_mutation_line_number_survives_multiline_comments()
    test_mutation_symbol_is_enclosing_function()

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
