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

import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import decision_points  # noqa: E402
from decision_points import (  # noqa: E402
    DpAnalysis,
    DecisionPoint,
    TsBackend,
    ast_analyze,
    detect_backend,
    dp_key,
    lexical_analyze,
    normalize_dp_id,
    python_ast_analyze,
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


# ------------------------------------------------------------ TS AST 后端


def _find_backend(prefer: str = "auto") -> TsBackend | None:
    """本机能不能拿到可用的 TS 后端：当前目录 + `ATTEST_TS_ROOTS` 里的根。

    环境变量用来在「attest 仓库自己没有 TypeScript」的机器上补一个搜索根
    （CI 装全局 typescript@5 时不需要它）。
    """
    roots = [Path.cwd().resolve()]
    for raw in os.environ.get("ATTEST_TS_ROOTS", "").split(os.pathsep):
        if raw.strip():
            roots.append(Path(raw.strip()).resolve())
    for root in roots:
        backend = detect_backend(root, prefer=prefer)
        if backend is not None:
            return backend
    return None


def _write_sample(root: Path, rel: str, src: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(src, encoding="utf-8")


SAMPLE_SRC = (
    "export async function insertRows(rows: any[], client: any, o: any) {\n"
    "  if (rows.length >= 1) {\n"
    "    throw new Error('empty');\n"
    "  }\n"
    "  o.status = 'done';\n"
    "  const kept = rows.filter((row) => row.id !== '');\n"
    "  try {\n"
    "    await client.query('select 1');\n"
    "  } catch (e) {\n"
    "    client.close();\n"
    "  }\n"
    "  return kept.slice(0, 10);\n"
    "}\n"
)


def test_detect_backend_returns_none_without_typescript() -> None:
    print("\n后端探测：目标里没有 TypeScript ⇒ None")
    with tempfile.TemporaryDirectory() as td:
        got = detect_backend(Path(td))
    expect(got is None, f"空目录 ⇒ None（实际 {got}）")


def test_detect_backend_l2_preference_skips_probing() -> None:
    print("\n后端探测：prefer=l2 ⇒ 不探测，直接 None")
    with tempfile.TemporaryDirectory() as td:
        expect(detect_backend(Path(td), prefer="l2") is None, "prefer=l2 ⇒ None")
    expect(detect_backend(HERE, prefer="l2") is None, "prefer=l2 ⇒ 永远 None")


def test_detect_backend_reports_a_usable_backend() -> None:
    print("\n后端探测：找到能真解析的 TypeScript（不是只看路径/版本）")
    backend = _find_backend()
    if backend is None:
        print("  skip 本机没有可用的 TypeScript 后端")
        return
    expect(backend.kind in ("B1", "B2"), f"kind 是 B1/B2（实际 {backend.kind}）")
    expect(backend.source in ("repo-local", "global-npm"),
           f"source 是 repo-local/global-npm（实际 {backend.source}）")
    expect(Path(backend.package_dir).is_absolute(),
           f"package_dir 是绝对路径（实际 {backend.package_dir}）")


def test_l3_finds_more_kinds_than_l2_on_same_file() -> None:
    print("\nL3 AST：锚点与种类都对得上")
    src = ("export function f(x: number) {\n"
           "  if (x >= 1) { throw new Error('e'); }\n"
           "  return x;\n"
           "}\n")
    l2 = {p.kind for p in lexical_analyze("src/a.ts", src)}
    expect("boundary" in l2, f"L2 也能看见 >=（实际 {sorted(l2)}）")
    backend = _find_backend()
    if backend is None:
        print("  skip 本机没有可用的 TypeScript 后端")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_sample(root, "src/a.ts", src)
        an = ast_analyze(root, ["src/a.ts"], backend)
    kinds = {p.kind for p in an.points}
    expect(an.level == "L3", f"level 是 L3（实际 {an.level}）")
    expect("boundary" in kinds, f"L3 看见 >=（实际 {sorted(kinds)}）")
    expect("branch" in kinds, f"L3 看见 if（实际 {sorted(kinds)}）")
    expect("error-path" in kinds, f"L3 看见 throw（实际 {sorted(kinds)}）")
    anchors = {p.anchor for p in an.points}
    expect(anchors == {"f"}, f"锚点是函数名 f（实际 {sorted(anchors)}）")


def test_cross_backend_l2_subset_of_l3() -> None:
    print("\nL2 ⊆ L3：L2 报出的每个种类，L3 也必须报得出")
    backend = _find_backend()
    if backend is None:
        print("  skip 本机没有可用的 TypeScript 后端")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_sample(root, "src/a.ts", SAMPLE_SRC)
        l2 = lexical_analyze("src/a.ts", SAMPLE_SRC)
        an = ast_analyze(root, ["src/a.ts"], backend)
    l3_kinds = {p.kind for p in an.points}
    missing = sorted({p.kind for p in l2} - l3_kinds)
    expect(not missing, f"L3 覆盖 L2 的全部种类（缺 {missing}）")
    expect("drop" in l3_kinds, f"L3 看见 filter/slice（实际 {sorted(l3_kinds)}）")
    expect("state" in l3_kinds, f"L3 看见 o.status（实际 {sorted(l3_kinds)}）")


def test_l3_and_l2_agree_on_dp_ids() -> None:
    print("\nDP ID 稳定：同一段代码，L2 与 L3 给出同一组 ID")
    backend = _find_backend()
    if backend is None:
        print("  skip 本机没有可用的 TypeScript 后端")
        return
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_sample(root, "src/a.ts", SAMPLE_SRC)
        l2 = lexical_analyze("src/a.ts", SAMPLE_SRC)
        an = ast_analyze(root, ["src/a.ts"], backend)
    l2_an = DpAnalysis(level="L2", backend="B3-lexical", backend_source="none",
                       points=l2)
    l2_an.assign_ids()
    # 按**可比键**比，不按字符串比：组里只有一个时 ID 不带 ordinal
    # （`x#f#drop`），两个以上才带（`x#f#drop#1`）—— 同一个键的两种写法。
    l2_keys = {dp_key(p.dp_id) for p in l2_an.points}
    l3_keys = {dp_key(p.dp_id) for p in an.points}
    only_l2 = sorted(k for k in l2_keys - l3_keys if k is not None)
    expect(not only_l2, f"L2 的判定点在 L3 里都找得到（L2 独有 {only_l2}）")


def test_both_backends_agree_on_the_same_points() -> None:
    print("\n跨后端：B1（TS5）与 B2（TS7）给出同一组判定点")
    b1 = _find_backend(prefer="b1")
    b2 = _find_backend(prefer="b2")
    if b1 is None or b2 is None:
        print(f"  skip 本机只有一种后端（B1={b1 is not None}, B2={b2 is not None}）")
        return
    keys = []
    for backend in (b1, b2):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_sample(root, "src/a.ts", SAMPLE_SRC)
            an = ast_analyze(root, ["src/a.ts"], backend)
        keys.append({dp_key(p.dp_id) for p in an.points})
    expect(keys[0] == keys[1],
           f"两个后端结果一致（B1 独有 {sorted(keys[0] - keys[1])}，"
           f"B2 独有 {sorted(keys[1] - keys[0])}）")


def test_large_module_output_is_not_truncated() -> None:
    print("\n大模块：判定点 JSON 超过管道缓冲也不能被截断")
    backend = _find_backend()
    if backend is None:
        print("  skip 本机没有可用的 TypeScript 后端")
        return
    # 40 文件 × 20 函数 × 3 个判定点 ≈ 2400 点 ⇒ JSON 约 190 KiB，
    # 远超管道缓冲（64 KiB）。macOS 上 stdout 是异步的：写完立刻 exit
    # 会把没刷完的数据丢掉 ⇒ JSON 半截 ⇒ 解析失败 ⇒ 静默退回 L2。
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rels = []
        for i in range(40):
            rel = f"src/mod{i}.ts"
            _write_sample(root, rel, "".join(
                f"export function f{j}(x: number) {{\n"
                f"  if (x >= {j}) {{ throw new Error('e'); }}\n"
                f"  return x;\n"
                f"}}\n" for j in range(20)))
            rels.append(rel)
        an = ast_analyze(root, rels, backend)
    expect(an.level == "L3", f"level 是 L3（实际 {an.level}：{an.note}）")
    expect(len(an.points) >= 2000,
           f"判定点不少于 2000 个（实际 {len(an.points)}）")


def test_ts_script_exits_only_after_stdout_flush() -> None:
    print("\nTS 后端脚本：只能在 stdout 刷完之后退出")
    src = decision_points._TS_SCRIPT
    exits = [m.start() for m in re.finditer(r"process\.exit\(", src)]
    expect(len(exits) == 1, f"脚本里只有一处 process.exit（实际 {len(exits)} 处）")
    if exits:
        before = src[max(0, exits[0] - 120):exits[0]]
        expect("=>" in before,
               f"process.exit 在写回调里（实际 {before.strip()[-60:]!r}）")


# ---------------------------------------------------------------- Python L3


def test_python_ast_finds_kinds() -> None:
    print("\nPython 标准库 ast：判定点种类")
    src = ("def load(rows):\n"
           "    if len(rows) >= 1:\n"
           "        raise ValueError('x')\n"
           "    try:\n"
           "        client.close()\n"
           "    except OSError:\n"
           "        pass\n"
           "    return [r for r in rows if r]\n")
    an = python_ast_analyze("a.py", src)
    expect(an.level == "L3", f"level 是 L3（实际 {an.level}）")
    expect(an.backend == "py-ast", f"backend 是 py-ast（实际 {an.backend}）")
    got = {(p.anchor, p.kind) for p in an.points}
    for kind in ("branch", "boundary", "error-path", "resource", "drop"):
        expect(("load", kind) in got,
               f"认出 {kind} 判定点（实际 {sorted(got)}）")
    expect(all(p.dp_id for p in an.points), "每个判定点都带 DP ID")


def test_python_ast_anchor_is_function_name() -> None:
    print("\nPython ast：锚点是最近的具名作用域")
    an = python_ast_analyze("a.py", "def f():\n    return client.close()\n")
    expect(len(an.points) == 1, f"只有 1 个判定点（实际 {len(an.points)}）")
    expect(an.points[0].anchor == "f",
           f"锚点是函数名 f（实际 {an.points[0].anchor}）")
    cls = python_ast_analyze(
        "a.py", "class S:\n    def close(self):\n        client.close()\n")
    got = sorted({p.anchor for p in cls.points})
    expect(got == ["close"], f"类方法锚点是方法名（实际 {got}）")
    top = python_ast_analyze("a.py", "status = 'ready'\n")
    expect(top.points[0].anchor == "<toplevel>",
           f"顶层锚点是 <toplevel>（实际 {top.points[0].anchor}）")
    expect(top.points[0].kind == "state",
           f"状态赋值是 state（实际 {top.points[0].kind}）")


def test_python_ast_concurrency_io_and_imports() -> None:
    print("\nPython ast：并发、I/O、被导入的符号")
    an = python_ast_analyze(
        "a.py", "async def go():\n    await asyncio.gather(a())\n")
    expect(any(p.kind == "concurrency" for p in an.points),
           f"await / asyncio.gather 是 concurrency（实际 {an.points}）")
    io = python_ast_analyze("a.py", "def q():\n    return db.query('x')\n")
    expect(any(p.kind == "boundary-io" for p in io.points),
           f"I/O 词表的调用是 boundary-io（实际 {io.points}）")
    imp = python_ast_analyze(
        "a.py",
        "from db import query_rows\n\ndef q():\n    return query_rows()\n")
    expect(any(p.kind == "boundary-io" for p in imp.points),
           f"被导入符号的调用是 boundary-io（实际 {imp.points}）")


def test_python_ast_parse_error_does_not_crash() -> None:
    print("\nPython ast：源码解析失败不崩、也不给一张空清单")
    bad = python_ast_analyze("a.py", "def f(:\n")
    expect(bad.level == "L1", f"解析失败降级到 L1（实际 {bad.level}）")
    expect("解析失败" in bad.note, f"写明解析失败（实际 {bad.note!r}）")
    expect(not bad.points, "解析失败不产判定点")


def test_python_ast_match_and_with() -> None:
    print("\nPython ast：match 分支与 with 语句")
    an = python_ast_analyze(
        "a.py",
        "def f(x):\n"
        "    match x:\n"
        "        case 1:\n"
        "            return 'a'\n"
        "        case _:\n"
        "            return 'b'\n")
    branches = [p for p in an.points if p.kind == "branch"]
    expect(len(branches) == 2,
           f"match 的每个 case 一条 branch（实际 {len(branches)}）")
    expect(all(p.line > 0 for p in an.points),
           f"判定点都有行号（实际 {[p.line for p in an.points]}）")
    w = python_ast_analyze(
        "a.py", "def f():\n    with open('x') as fh:\n        return fh.read()\n")
    kinds = {p.kind for p in w.points}
    expect("resource" in kinds, f"with 语句是 resource（实际 {sorted(kinds)}）")
    expect("boundary-io" in kinds, f"I/O 调用是 boundary-io（实际 {sorted(kinds)}）")


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
    test_detect_backend_returns_none_without_typescript()
    test_detect_backend_l2_preference_skips_probing()
    test_detect_backend_reports_a_usable_backend()
    test_l3_finds_more_kinds_than_l2_on_same_file()
    test_cross_backend_l2_subset_of_l3()
    test_l3_and_l2_agree_on_dp_ids()
    test_both_backends_agree_on_the_same_points()
    test_large_module_output_is_not_truncated()
    test_ts_script_exits_only_after_stdout_flush()
    test_python_ast_finds_kinds()
    test_python_ast_anchor_is_function_name()
    test_python_ast_concurrency_io_and_imports()
    test_python_ast_match_and_with()
    test_python_ast_parse_error_does_not_crash()
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
