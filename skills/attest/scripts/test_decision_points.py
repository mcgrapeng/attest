#!/usr/bin/env python3
"""判定点（Decision Point）推导的回归测试。

DP 是「源码里一个会改变行为的选择」—— 机器从代码反向推导出来的那张清单。
这份测试盯三件事：

1. **DP ID 不含行号** —— 代码增删几行，ID 不许漂移（规格 §2.3.1）。
2. **ID 的归一化与合法性** —— `x#f#drop` 与 `x#f#drop#1` 是同一个（规格 §2.3.2）。
3. **L2 词法推导的边界** —— 注释与字符串里的词不算；整词匹配；
   `camelCase` / `snake_case` 变体都命中；行号不漂。

    python3 skills/attest/scripts/test_decision_points.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import decision_points  # noqa: E402
from decision_points import (  # noqa: E402
    DpAnalysis,
    DecisionPoint,
    dp_key,
    lexical_analyze,
    normalize_dp_id,
)

FAILURES: list[str] = []


def expect(cond: bool, label: str) -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILURES.append(label)


def kinds_of(src: str) -> list[str]:
    return sorted(p.kind for p in lexical_analyze("src/a.ts", src))


def pairs_of(src: str) -> set[tuple[str, str]]:
    return {(p.anchor, p.kind) for p in lexical_analyze("src/a.ts", src)}


# ---------------------------------------------------------------- DP ID


def test_assign_ids_numbers_same_group() -> None:
    print("\nDP ID：同一函数同类判定点按序编号，唯一的那个不编号")
    pts = [DecisionPoint("src/a.ts", "f", "branch", 3, "", 1),
           DecisionPoint("src/a.ts", "f", "branch", 9, "", 1),
           DecisionPoint("src/a.ts", "g", "drop", 20, "", 1)]
    an = DpAnalysis(level="L2", backend="B3-lexical", backend_source="none",
                    note="", points=pts)
    an.assign_ids()
    got = [p.dp_id for p in an.points]
    expect(got == ["src/a.ts#f#branch#1", "src/a.ts#f#branch#2",
                   "src/a.ts#g#drop"], f"编号结果（实际 {got}）")


def test_assign_ids_ignores_line_numbers_in_id() -> None:
    print("\nDP ID：行号变了 ID 不变（代码增删几行不该让 ID 漂）")
    a = DecisionPoint("src/a.ts", "f", "drop", 3, "", 1)
    b = DecisionPoint("src/a.ts", "f", "drop", 300, "", 1)
    an = DpAnalysis(level="L2", backend="B3-lexical", backend_source="none",
                    note="", points=[a])
    an.assign_ids()
    first = a.dp_id
    an.points = [b]
    an.assign_ids()
    expect(first == b.dp_id == "src/a.ts#f#drop",
           f"同一个判定点挪行后 ID 不变（实际 {first} / {b.dp_id}）")


def test_by_id_and_by_kind() -> None:
    print("\n分组：by_id 按 ID 取回，by_kind 按类分组")
    pts = [DecisionPoint("src/a.ts", "f", "branch", 3, "", 1),
           DecisionPoint("src/a.ts", "f", "branch", 9, "", 1),
           DecisionPoint("src/a.ts", "g", "drop", 20, "", 1)]
    an = DpAnalysis(level="L2", backend="B3-lexical", backend_source="none",
                    note="", points=pts)
    an.assign_ids()
    expect(an.by_id()["src/a.ts#g#drop"] is pts[2], "by_id 能按 ID 取回判定点")
    expect(len(an.by_kind()["branch"]) == 2 and len(an.by_kind()["drop"]) == 1,
           "by_kind 按类分组")


def test_dp_key_treats_ordinal_one_as_default() -> None:
    print("\nDP ID 归一化：ordinal 可有可无，缺省当 1")
    expect(dp_key("src/a.ts#f#drop") == dp_key("src/a.ts#f#drop#1"),
           "x#f#drop 与 x#f#drop#1 是同一个")
    expect(dp_key("not-a-dp-id") is None, "不是 DP ID ⇒ None")
    expect(dp_key("src/a.ts#f") is None, "只有两段 ⇒ None")
    expect(dp_key("src/a.ts#f#drop#x") is None, "ordinal 不是数字 ⇒ None")


def test_dp_key_accepts_spec_regex_forms() -> None:
    print("\nDP ID 合法性：规格 §4.3 的正则")
    expect(dp_key("src/agent-memory/x.ts#insertRow#drop") is not None,
           "路径含 / 与 .")
    expect(dp_key("src/a-b.ts#close#error-path") is not None,
           "kind 含连字符")
    expect(dp_key("src/a.ts#f#drop#2") is not None, "带 ordinal")


def test_normalize_dp_id_lowercases_kind_only() -> None:
    print("\nDP ID 归一化：kind 小写，其余去空白")
    expect(normalize_dp_id(" src/a.ts # F # Drop ") == "src/a.ts#F#drop",
           f"只小写 kind（实际 {normalize_dp_id(' src/a.ts # F # Drop ')}）")
    expect(dp_key("src/a.ts#f#DROP") == dp_key("src/a.ts#f#drop"),
           "大写 kind 归一化后等价")


# ---------------------------------------------------------------- L2 词法


def test_lexical_finds_drop_and_resource() -> None:
    print("\nL2 词法：filter ⇒ drop，client.close() ⇒ resource")
    src = ("export function insertRow(rows) {\n"
           "  const kept = rows.filter((row) => row.id !== '');\n"
           "  client.close();\n"
           "}\n")
    pairs = pairs_of(src)
    expect(("insertRow", "drop") in pairs, f"filter ⇒ drop（实际 {sorted(pairs)}）")
    expect(("insertRow", "resource") in pairs,
           f"close ⇒ resource（实际 {sorted(pairs)}）")


def test_lexical_ignores_strings_and_comments() -> None:
    print("\nL2 词法：注释与字符串里的词不算")
    src = ("export function f() {\n"
           "  // client.close();\n"
           "  const s = 'client.close()';\n"
           "  if (a >= 1) { return b; }\n"
           "}\n")
    pts = lexical_analyze("src/a.ts", src)
    expect(not any(p.kind == "resource" for p in pts),
           "注释与字符串里的 close 不算资源判定点")
    expect(kinds_of(src) == ["boundary", "branch"],
           f"只有 if（分支）与 >=（阈值）（实际 {kinds_of(src)}）")


def test_anchor_falls_back_to_toplevel() -> None:
    print("\n锚点：不在任何具名函数内 ⇒ <toplevel>")
    src = "const x = client.close();\n"
    pts = lexical_analyze("src/a.ts", src)
    expect(len(pts) == 1 and pts[0].anchor == "<toplevel>",
           f"锚点是 <toplevel>（实际 {[p.anchor for p in pts]}）")
    expect(pts[0].kind == "resource", f"仍是 resource（实际 {pts[0].kind}）")


def test_anchor_is_nearest_enclosing_function() -> None:
    print("\n锚点：取最近的外层函数，函数已经闭合的 ⇒ <toplevel>")
    src = ("export function outer() {\n"
           "  function inner() {\n"
           "    client.close();\n"
           "  }\n"
           "}\n"
           "client.close();\n")
    pts = lexical_analyze("src/a.ts", src)
    expect(len(pts) == 2, f"两个资源判定点（实际 {len(pts)}）")
    expect(pts[0].anchor == "inner", f"内层函数（实际 {pts[0].anchor}）")
    expect(pts[1].anchor == "<toplevel>",
           f"函数外的那处是 <toplevel>（实际 {pts[1].anchor}）")


def test_whole_word_match_not_substring() -> None:
    print("\n词表口径：整词匹配，不是子串匹配")
    src = ("export function f() {\n"
           "  client.closedAt();\n"
           "  client.disclose();\n"
           "}\n")
    expect(not any(p.kind == "resource" for p in lexical_analyze("src/a.ts", src)),
           "closedAt / disclose 不算 close")


def test_variant_normalization() -> None:
    print("\n词表口径：camelCase / snake_case 变体都命中")
    src = ("export function f() {\n"
           "  svc.tearDown();\n"
           "  svc.tear_down();\n"
           "}\n")
    got = [p for p in lexical_analyze("src/a.ts", src) if p.kind == "resource"]
    expect(len(got) == 2, f"tearDown / tear_down 都命中（实际 {len(got)}）")


def test_line_numbers_survive_multiline_template() -> None:
    print("\n行号：多行模板串之后的代码行号不许漂")
    src = ("export function f() {\n"
           "  const msg = `\n"
           "    client.close()\n"
           "  `;\n"
           "  client.close();\n"
           "}\n")
    pts = lexical_analyze("src/a.ts", src)
    expect([p.line for p in pts] == [5],
           f"第 5 行的那处（实际 {[p.line for p in pts]}）")
    expect("client.close();" in src.split("\n")[pts[0].line - 1],
           "报出来的那一行确实是 close 调用（没被模板串顶偏）")


def test_class_method_anchor() -> None:
    print("\n锚点：类方法取方法名（不是类名）")
    src = ("class Store {\n"
           "  async close(): Promise<void> {\n"
           "    client.close();\n"
           "  }\n"
           "}\n")
    pts = lexical_analyze("src/a.ts", src)
    expect(len(pts) == 1 and pts[0].anchor == "close",
           f"锚点是方法名（实际 {[p.anchor for p in pts]}）")


def test_object_literal_method_anchor() -> None:
    print("\n锚点：对象字面量里的方法也取方法名")
    src = ("export function makeStore() {\n"
           "  return {\n"
           "    async close(): Promise<void> {\n"
           "      client.close();\n"
           "    },\n"
           "  };\n"
           "}\n")
    pts = lexical_analyze("src/a.ts", src)
    expect(len(pts) == 1 and pts[0].anchor == "close",
           f"锚点是方法名（实际 {[p.anchor for p in pts]}）")


def test_optional_chaining_call_is_resource() -> None:
    print("\nL2 词法：client.close?.() ⇒ resource")
    src = ("export async function shutdown() {\n"
           "  await client.close?.();\n"
           "}\n")
    expect(("shutdown", "resource") in pairs_of(src),
           f"close?.() 命中 resource（实际 {sorted(pairs_of(src))}）")


def test_method_declaration_is_not_a_call() -> None:
    print("\nL2 词法：方法声明不算调用（close() 的声明不是资源判定点）")
    src = ("class Store {\n"
           "  async close(): Promise<void> {\n"
           "    await client.close?.();\n"
           "  }\n"
           "}\n")
    got = [p for p in lexical_analyze("src/a.ts", src) if p.kind == "resource"]
    expect(len(got) == 1 and got[0].line == 3,
           f"只有那一处调用算 resource（实际 {[(p.line, p.detail) for p in got]}）")


def test_leading_dot_chain_is_drop() -> None:
    print("\nL2 词法：链式调用的续行（行首的 .filter）也算 drop")
    src = ("export function insertRows(rows) {\n"
           "  const kept = rows\n"
           "    .map((row) => row)\n"
           "    .filter((row) => row.id !== '');\n"
           "}\n")
    expect(("insertRows", "drop") in pairs_of(src),
           f".filter( 命中 drop（实际 {sorted(pairs_of(src))}）")


def test_folded_signature_anchor() -> None:
    print("\n锚点：签名折行好几行时仍取到函数名")
    src = ("export function resolveClassification(\n"
           "  metadata: Meta,\n"
           "  extra: string,\n"
           "): DataClassification {\n"
           "  if (!metadata) {\n"
           "    return 'unknown';\n"
           "  }\n"
           "  return 'public';\n"
           "}\n")
    anchors = {p.anchor for p in lexical_analyze("src/a.ts", src)}
    expect(anchors == {"resolveClassification"},
           f"全部落在函数名上（实际 {sorted(anchors)}）")


def test_type_literal_return_scope_keeps_function_name() -> None:
    print("\n锚点：返回类型是对象字面量时，函数体仍归该函数")
    src = ("export function scopeOf(filters: Filters): {\n"
           "  userId: string;\n"
           "} {\n"
           "  client.close();\n"
           "}\n")
    pts = lexical_analyze("src/a.ts", src)
    expect(len(pts) == 1 and pts[0].anchor == "scopeOf",
           f"锚点是函数名（实际 {[p.anchor for p in pts]}）")


def test_state_and_error_path() -> None:
    print("\nL2 词法：写状态字段 ⇒ state，throw ⇒ error-path")
    src = ("export function f(o) {\n"
           "  o.status = 'done';\n"
           "  throw new Error('boom');\n"
           "}\n")
    pairs = pairs_of(src)
    expect(("f", "state") in pairs, f"o.status ⇒ state（实际 {sorted(pairs)}）")
    expect(("f", "error-path") in pairs,
           f"throw ⇒ error-path（实际 {sorted(pairs)}）")


def test_concurrency_and_io() -> None:
    print("\nL2 词法：query ⇒ boundary-io，await / Promise.all ⇒ concurrency")
    src = ("export async function f(c) {\n"
           "  await c.query('select 1');\n"
           "  await Promise.all([p1(), p2()]);\n"
           "}\n")
    pairs = pairs_of(src)
    expect(("f", "boundary-io") in pairs,
           f"query ⇒ boundary-io（实际 {sorted(pairs)}）")
    expect(("f", "concurrency") in pairs,
           f"await ⇒ concurrency（实际 {sorted(pairs)}）")


def test_anchor_at_reports_enclosing_scope() -> None:
    print("\n锚点：给行号 ⇒ 该行所在的最内层具名作用域")
    src = ("export function outer() {\n"
           "  const x = 1;\n"
           "  return client.close();\n"
           "}\n")
    lines = src.split("\n")
    got = decision_points._anchor_at(lines, 3)
    expect(got == "outer", f"第 3 行归 outer（实际 {got}）")
    top = decision_points._anchor_at(lines, 1)
    expect(top == "<toplevel>", f"函数之外是 <toplevel>（实际 {top}）")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    test_assign_ids_numbers_same_group()
    test_assign_ids_ignores_line_numbers_in_id()
    test_by_id_and_by_kind()
    test_dp_key_treats_ordinal_one_as_default()
    test_dp_key_accepts_spec_regex_forms()
    test_normalize_dp_id_lowercases_kind_only()
    test_lexical_finds_drop_and_resource()
    test_lexical_ignores_strings_and_comments()
    test_anchor_falls_back_to_toplevel()
    test_anchor_is_nearest_enclosing_function()
    test_anchor_at_reports_enclosing_scope()
    test_whole_word_match_not_substring()
    test_variant_normalization()
    test_line_numbers_survive_multiline_template()
    test_class_method_anchor()
    test_object_literal_method_anchor()
    test_optional_chaining_call_is_resource()
    test_method_declaration_is_not_a_call()
    test_leading_dot_chain_is_drop()
    test_folded_signature_anchor()
    test_type_literal_return_scope_keeps_function_name()
    test_state_and_error_path()
    test_concurrency_and_io()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} 项失败:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
