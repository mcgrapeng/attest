#!/usr/bin/env python3
"""回归测试：真实仓库布局下的三个误报。

这三个缺陷不是推演出来的，是 attest 拿 5 个公开仓库自测时实测到的
（docs/self-test-report.md）：

1. **测试文件无后缀不被发现** —— chalk / express 把测试放在 `test/basic.js`
   这种没有 `.test.`/`.spec.` 后缀的文件里，工具只认带后缀的 ⇒ 判成
   「没有测试」⇒ 于是「导出的逻辑都有测试」把每个导出都报成零引用
   ⇒ **误报阻塞项**（chalk 实测 4/4）。
2. **逐文件命令追加到复合命令末尾** —— chalk 的 test 脚本是
   `xo && c8 node --test test/[!_]*.js && tsc --noEmit`，文件被追加到末尾 ⇒
   变成 `tsc` 的参数 ⇒ runner 根本没跑。
3. **`.d.ts` / `.js` 被当成派生存储** —— 命中的是源码注释里的 GitHub 链接
   `github.com/chalk/chalk/blob/main/readme.md` 里的 "blob"；click 那边命中的是
   Python 标准库 textwrap.py 里的一句 "blob" ⇒ 两个仓库都被误判成
   「有派生存储却没有对账机制」⇒ **误报阻塞项**。

    python3 scripts/test_repo_layout.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import readiness as R  # noqa: E402

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


def test_test_file_discovery() -> None:
    print("\n#1 测试目录里的无后缀文件也算测试（辅助子目录不算）")
    cases = [
        ("test/basic.js", "basic.js", True, "test/ 里的普通 js 是测试"),
        ("test/colors.js", "colors.js", True, "test/ 里的普通 js 是测试"),
        ("test/foo.test.js", "foo.test.js", True, "带 .test. 后缀照旧算"),
        ("test/support/env.js", "support/env.js", False, "support/ 里的是 helper"),
        ("test/fixtures/a.js", "fixtures/a.js", False, "fixtures/ 里的是 fixture"),
        ("test/helpers/x.js", "helpers/x.js", False, "helpers/ 里的是 helper"),
        ("tests/conftest.py", "conftest.py", False, "conftest.py 不是测试"),
        ("tests/setup.js", "setup.js", False, "setup.js 不是测试"),
        ("tests/test_basic.py", "test_basic.py", True, "python 的 test_*.py 照旧算"),
        ("tests/utils.py", "utils.py", False, "python 侧维持旧口径"),
    ]
    for full, rel, want, label in cases:
        got = R._looks_like_test_file(Path(full), Path(rel))
        expect(got == want, f"{label}（{full} → {got}）")
    expect(R._looks_like_test_file(Path("src/index.js")) is False,
           "不在测试目录里的文件不算测试")

    print("\n#1b 端到端：仓库级测试发现（chalk 布局）")
    root = mkdtemp()
    (root / "test" / "support").mkdir(parents=True)
    (root / "source").mkdir()
    (root / "test" / "basic.js").write_text("import '../source/index.js';\n",
                                            encoding="utf-8")
    (root / "test" / "support" / "env.js").write_text("module.exports = {};\n",
                                                       encoding="utf-8")
    (root / "source" / "index.js").write_text("export const Chalk = 1;\n",
                                              encoding="utf-8")
    m = R.Module(root=root, path="source", base=root / "source",
                 sources=[root / "source" / "index.js"], workspace=root)
    all_files, attributed = R.repo_level_tests(root, m)
    names = sorted(p.name for p in all_files)
    expect(names == ["basic.js"], f"只认 test/basic.js，不认 support/env.js（{names}）")
    expect([p.name for p in attributed] == ["basic.js"],
           "basic.js 提到 source/ ⇒ 归属这个模块")


def test_script_split_and_runner() -> None:
    print("\n#2 复合 test 脚本要拆开，并认出跑测试的那一段")
    parts = R._split_script("xo && c8 node --test test/[!_]*.js && tsc --noEmit")
    expect(len(parts) == 3, f"拆成 3 段（实际 {len(parts)}）")
    expect(parts[1] == ["c8", "node", "--test", "test/[!_]*.js"],
           f"第二段是覆盖率包装的 runner（{parts[1] if len(parts) > 1 else None}）")
    quoted = R._split_script("echo 'a && b'")
    expect(len(quoted) == 1 and quoted[0] == ["echo", "a && b"],
           "引号里的 && 不算分隔符")

    print("\n#2b runner 识别")
    cases = [
        (["c8", "node", "--test", "test/x.js"], True),
        (["mocha", "--reporter", "spec"], True),
        (["vitest", "run"], True),
        (["npx", "mocha"], True),
        (["xo"], False),
        (["tsc", "--noEmit"], False),
        (["node", "build.js"], False),
        (["npm", "run", "test:unit"], False),
    ]
    for parts, want in cases:
        got = R._runner_component(parts)
        expect((got is not None) == want, f"{parts} → {'runner' if got else '不是 runner'}")

    print("\n#2c 端到端：chalk 式的复合 test 脚本")
    root = mkdtemp()
    (root / "package.json").write_text(json.dumps({
        "scripts": {"test": "xo && c8 node --test test/[!_]*.js && tsc --noEmit"}
    }), encoding="utf-8")
    m = R.Module(root=root, path="source", base=root / "source", workspace=root)
    cmd, key = R.project_test_cmd(m)
    expect(key == "test", f"用的是仓库自己的 test 脚本（{key}）")
    expect(cmd == ["c8", "node", "--test"],
           f"落到 runner 那一段并去掉 glob（{cmd}）")
    expect("tsc" not in cmd, "tsc 不在逐文件命令里（文件不会变成 tsc 的参数）")

    print("\n#2d express 式的命令：去掉末尾的目录参数")
    root2 = mkdtemp()
    (root2 / "package.json").write_text(json.dumps({
        "scripts": {"test": "mocha --require test/support/env --reporter spec "
                            "--check-leaks test/ test/acceptance/"}
    }), encoding="utf-8")
    m2 = R.Module(root=root2, path="lib", base=root2 / "lib", workspace=root2)
    cmd2, _ = R.project_test_cmd(m2)
    expect(cmd2 == ["mocha", "--require", "test/support/env", "--reporter", "spec",
                    "--check-leaks"],
           f"目录参数去掉了，runner 参数留着（{cmd2}）")


def test_store_detection() -> None:
    print("\n#3 URL 里的 blob / 标准库里的 blob 不算派生存储")
    root = mkdtemp()
    src = root / "source"
    src.mkdir()
    dts = src / "index.d.ts"
    dts.write_text(
        "export declare class Chalk {}\n"
        "// see https://github.com/chalk/chalk/blob/main/readme.md\n",
        encoding="utf-8")
    impl = src / "index.js"
    impl.write_text("export class Chalk {}\n", encoding="utf-8")
    m = R.Module(root=root, path="source", base=src, sources=[dts, impl], workspace=root)
    derived, side = R.detect_stores(m)
    expect(derived == [], f"没有派生存储（实际 {derived}）")
    expect(side == [], f"没有旁路存储（实际 {side}）")

    print("\n#3b 真的向量库还是要认出来")
    root2 = mkdtemp()
    src2 = root2 / "source"
    src2.mkdir()
    f = src2 / "mem.py"
    f.write_text("from pymilvus import MilvusClient\n"
                 "client.createCollection('mem')\n", encoding="utf-8")
    m2 = R.Module(root=root2, path="source", base=src2, sources=[f], workspace=root2)
    derived2, _ = R.detect_stores(m2)
    expect(derived2 == ["mem.py"], f"Milvus 认得出来（实际 {derived2}）")

    print("\n#3c 子串不算证据（InputObject 里藏着 putObject）")
    root3 = mkdtemp()
    src3 = root3 / "source"
    src3.mkdir()
    g = src3 / "exact.d.ts"
    g.write_text("type ExactObject<InputType, InputObject> = never;\n"
                 "type X = RequireNone<Exclude<KeysOfUnion<InputObject>>>;\n",
                 encoding="utf-8")
    m3 = R.Module(root=root3, path="source", base=src3, sources=[g], workspace=root3)
    derived3, _ = R.detect_stores(m3)
    expect(derived3 == [], f"InputObject 不算 putObject（实际 {derived3}）")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    test_test_file_discovery()
    test_script_split_and_runner()
    test_store_detection()
    print()
    for d in TMPDIRS:
        shutil.rmtree(d, ignore_errors=True)
    if FAILURES:
        print(f"失败 {len(FAILURES)} 条：")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
