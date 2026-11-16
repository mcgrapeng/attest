#!/usr/bin/env python3
"""判定点引擎三个 P0 缺陷的回归测试（先红后绿）。

A. **大 stdout 不能被截断**：macOS 上管道写是异步的，脚本写完就
   `process.exit()` ⇒ 超过管道缓冲（64KB）的尾巴当场丢掉 ⇒ AST 推导
   静默退回 L2，整套判定点引擎在真实仓库上失效。
B. **作用域过滤之后仍要能挑到变异点**：截断发生在过滤之前时，作用域外
   的点会先把名额占光，作用域内的点压根到不了 ⇒ 假的「不适用」。
C. **解析闸不能把原文就不标准的真代码判成「解析不过」**：正则字面量里的
   括号是合法的，旧闸门拿括号配平当绝对标准 ⇒ 能判的变异点全被推成
   「判不了」。
"""
from __future__ import annotations

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


def node_repo(files: dict[str, str]) -> Path:
    """一个能用 `node --test` 跑起来的最小仓库。"""
    root = mkdtemp()
    write(root / "package.json",
          '{"name":"fixture","version":"1.0.0","scripts":{"test":"node --test"}}\n')
    for rel, text in files.items():
        write(root / rel, text)
    return root


# --------------------------------------------------------------- A 大 stdout


def test_big_stdout_is_not_truncated():
    print("\ntest_big_stdout_is_not_truncated")
    sys.path.insert(0, str(HERE))
    from decision_points import _run_ts_script  # noqa: E402
    root = mkdtemp()
    # 20 万字节 ≫ macOS 管道缓冲（64KB）：写完就 exit 会丢掉没落盘的尾巴
    script = ("process.stdout.write('x'.repeat(200000));\n"
              "process.exit(0);\n")
    code, out, _err = _run_ts_script(script, "probe", str(root), {}, root)
    expect(code == 0, "大 stdout 的脚本退出码是 0")
    expect(len(out) == 200000,
           f"大 stdout 完整到达（实际 {len(out)} 字节，应当 200000）")


# ------------------------------------------------------- B 作用域内的点可选


def test_scope_relevant_mutation_is_selectable():
    print("\ntest_scope_relevant_mutation_is_selectable")
    sys.path.insert(0, str(HERE))
    from mutation_check import run_mutation_check  # noqa: E402
    src = ""
    for i, name in enumerate("abcd"):
        src += (f"function {name}(x) {{\n"
                f"  return x === {i + 1};\n"
                f"}}\n")
    root = node_repo({
        "src/a.js": src + "module.exports = { a, b, c, d };\n",
        "src/a.test.js": (
            "const test = require('node:test');\n"
            "const assert = require('node:assert');\n"
            "const { d } = require('./a.js');\n"
            "test('d', () => { assert.strictEqual(d(4), true); });\n"),
    })
    res = run_mutation_check(
        repo=root, ws_rel=".", source_rels=["src/a.js"],
        test_rels=["src/a.test.js"], runner=["node", "--test"],
        ops=("cmp-negate",), scope_symbols={"d"})
    expect(res["attempted"] == 1,
           f"作用域 d 里的变异点被挑中并试了（实际 {res['attempted']}）")
    expect(res["killed"] == 1,
           f"作用域 d 里的变异点被钉的用例杀死（实际 {res['killed']}）")


# ------------------------------------------------------------- C 解析闸判据


def test_parse_check_judges_the_mutation_not_the_file():
    print("\ntest_parse_check_judges_the_mutation_not_the_file")
    sys.path.insert(0, str(HERE))
    from discriminate import _default_parse_check  # noqa: E402
    # 原文括号就不配平：正则字面量里的括号是**合法**的
    src = ("const open = /\\(/g;\n"
           "const half = /x{2/;\n"
           "function isOne(x) { return x === 1; }\n")
    root = mkdtemp()
    path = write(root / "src" / "a.js", src)
    expect(_default_parse_check(path, src),
           "原文括号就不配平的真代码不被判成「解析不过」")
    # 变异真的把结构改坏了 ⇒ 仍然要判成解析不过
    broken = src.replace("function isOne(x) { return x === 1; }",
                         "function isOne(x) { return x === 1; ")
    expect(not _default_parse_check(path, broken),
           "结构被改坏的变异仍然判成「解析不过」")


# ------------------------------------------------------- D 解析闸用真解析器


def _ts_backend(root: Path):
    """这台机器能不能拿到可用的 TS 后端（拿不到 ⇒ 相关断言跳过）。"""
    sys.path.insert(0, str(HERE))
    from decision_points import detect_backend  # noqa: E402
    return detect_backend(root)


def test_parse_gate_uses_a_real_parser_not_bracket_balance():
    print("\ntest_parse_gate_uses_a_real_parser_not_bracket_balance")
    sys.path.insert(0, str(HERE))
    from discriminate import _default_parse_check  # noqa: E402
    src = ("export function isOne(x) {\n"
           "  return x === 1;\n"
           "}\n")
    root = node_repo({"src/a.ts": src})
    path = root / "src" / "a.ts"
    if _ts_backend(root) is None:
        print("  skip 本机没有可用的 TypeScript 后端")
        return
    # 括号配平、语法坏了：老闸门（括号配平）看不见 ⇒ 变异体被当成「杀死」⇒ 假绿
    broken = src.replace("x === 1", "x === ;")
    expect(not _default_parse_check(path, broken),
           "括号配平但语法坏了的变异判成「解析不过」")
    # 正则字面量里的括号是合法的：老闸门把它当结构差异 ⇒ 合法变异被误杀
    re_src = ("const re = /)/;\n"
              "export function isOne(x) {\n"
              "  return x === 1;\n"
              "}\n")
    re_path = write(root / "src" / "b.ts", re_src)
    re_mut = re_src.replace("/)/;", "/(/;")
    expect(_default_parse_check(re_path, re_mut),
           "正则字面量里的括号变化不误判合法变异")


# --------------------------------------------------- E 跨行的动态 import


def test_dynamic_import_across_lines_is_found():
    print("\ntest_dynamic_import_across_lines_is_found")
    sys.path.insert(0, str(HERE))
    from discriminate import parse_imports, runtime_symbols  # noqa: E402
    root = mkdtemp()
    write(root / "src" / "a.ts", "export const a = 1;\n")
    src = ("const mod = await import(\n"
           "  './a.ts'\n"
           ");\n")
    path = write(root / "src" / "b.ts", src)
    refs = parse_imports(src, "src/b.ts", root)
    expect(any(r.spec == "./a.ts" for r in refs),
           "说明符写在下一行的动态 import 被认出来")
    expect("a" in runtime_symbols(refs),
           "跨行动态 import 的导出符号算运行时引用")
    expect(path.is_file(), "夹具写盘成功")


# ------------------------------------- F 顶层 const 对象字面量的作用域名


def test_toplevel_const_object_keeps_its_export_name():
    print("\ntest_toplevel_const_object_keeps_its_export_name")
    sys.path.insert(0, str(HERE))
    from decision_points import scope_stacks  # noqa: E402
    src = ("export const node: Handler = {\n"
           "  async run(ctx) {\n"
           "    if (ctx.flag === false) return;\n"
           "  },\n"
           "};\n")
    stacks = scope_stacks(src.split("\n"))
    expect(stacks[2][:1] == ("node",),
           f"顶层 const 对象字面量的作用域名是 node（实际 {stacks[2]}）")
    expect("run" in stacks[2],
           f"对象里的方法仍是内层作用域（实际 {stacks[2]}）")


def test_const_object_method_is_reachable_for_scope_filter():
    print("\ntest_const_object_method_is_reachable_for_scope_filter")
    sys.path.insert(0, str(HERE))
    from mutation_check import run_mutation_check  # noqa: E402
    src = ("export const node = {\n"
           "  async run(ctx) {\n"
           "    if (ctx.flag === false) return;\n"
           "  },\n"
           "};\n")
    root = node_repo({
        "src/a.ts": src,
        "src/a.test.ts": (
            "import test from 'node:test';\n"
            "import assert from 'node:assert';\n"
            "import { node } from './a.ts';\n"
            "test('run', async () => {\n"
            "  const ctx = { flag: false };\n"
            "  await node.run(ctx);\n"
            "  assert.strictEqual(ctx.seen, undefined);\n"
            "});\n"),
    })
    res = run_mutation_check(
        repo=root, ws_rel=".", source_rels=["src/a.ts"],
        test_rels=["src/a.test.ts"], runner=["node", "--test"],
        ops=("cmp-negate",), scope_symbols={"node"})
    expect(res["attempted"] >= 1,
           f"const 对象里的方法被作用域过滤放行（实际 {res['attempted']}）")


# ----------------------------------------------- G 抽样按钉住用例排序


def test_preferred_symbols_are_sampled_first():
    print("\ntest_preferred_symbols_are_sampled_first")
    sys.path.insert(0, str(HERE))
    from mutation_check import run_mutation_check  # noqa: E402
    src = ""
    for i, name in enumerate("abcd"):
        src += (f"function {name}(x) {{\n"
                f"  return x === {i + 1};\n"
                f"}}\n")
    root = node_repo({
        "src/a.js": src + "module.exports = { a, b, c, d };\n",
        "src/a.test.js": (
            "const test = require('node:test');\n"
            "const assert = require('node:assert');\n"
            "const { d } = require('./a.js');\n"
            "test('d', () => { assert.strictEqual(d(4), true); });\n"),
    })
    # 不给优先符号 ⇒ 抽到文件里第一个（a）⇒ 用例照样绿 ⇒ 存活
    blind = run_mutation_check(
        repo=root, ws_rel=".", source_rels=["src/a.js"],
        test_rels=["src/a.test.js"], runner=["node", "--test"],
        ops=("cmp-negate",), scope_symbols={"a", "b", "c", "d"},
        max_mutants=1)
    expect(blind["killed"] == 0 and blind["survivors"],
           "没有优先符号时抽到不相干的函数 ⇒ 判成杀不死")
    # 给了优先符号 ⇒ 抽到用例真正提到的 d ⇒ 被杀死
    res = run_mutation_check(
        repo=root, ws_rel=".", source_rels=["src/a.js"],
        test_rels=["src/a.test.js"], runner=["node", "--test"],
        ops=("cmp-negate",), scope_symbols={"a", "b", "c", "d"},
        prefer_symbols={"d"}, max_mutants=1)
    expect(res["attempted"] == 1, "只试了一个变异点")
    expect(res["killed"] == 1,
           f"抽到的是用例真正提到的 d（killed={res['killed']}）")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    test_big_stdout_is_not_truncated()
    test_scope_relevant_mutation_is_selectable()
    test_parse_check_judges_the_mutation_not_the_file()
    test_parse_gate_uses_a_real_parser_not_bracket_balance()
    test_dynamic_import_across_lines_is_found()
    test_toplevel_const_object_keeps_its_export_name()
    test_const_object_method_is_reachable_for_scope_filter()
    test_preferred_symbols_are_sampled_first()

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
