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


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    test_big_stdout_is_not_truncated()
    test_scope_relevant_mutation_is_selectable()
    test_parse_check_judges_the_mutation_not_the_file()

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
