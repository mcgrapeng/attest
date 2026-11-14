#!/usr/bin/env python3
"""回归测试：测试运行器的输出分类 —— 假绿是这里要挡住的东西。

独立复评抓到的一条：`_failed_tests` 原来只认 TAP 的 `not ok N -`，
而 **jest / vitest / mocha 都不吐 TAP** ⇒ 那些仓库里失败的用例一个都认不出来
⇒ `failed` 为空 ⇒ 报「✓ 测试全绿」、退出码 0。

实测复现（jest 风格、退出码 1、明确有失败用例）：

    ✓ 测试全绿 — 没有失败用例
    结论：0 项阻塞 …
    EXIT=0

一个**红着的**套件拿到绿色裁决 —— 对交付闸门是最坏的一类缺陷。

现在按「退出码 + 输出」一起判，三件事分开：
- 用例真失败 ⇒ 阻塞
- 环境没就绪 ⇒ 未实测
- **输出格式认不出** ⇒ 未实测（绝不判绿）

    python3 scripts/test_readiness_runners.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "readiness.py"
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


def repo_with_runner(script: str, runner_body: str,
                     test_body: str = "import test from 'node:test';\n"
                                     "test('x', () => {});\n") -> Path:
    """一个只有 1 个源文件 + 1 个测试文件、test 命令跑自定义脚本的小仓库。"""
    d = mkdtemp()
    write(d / "package.json", json.dumps({"scripts": {"test": script}}))
    write(d / "src" / "a.ts", "export const add = (a, b) => a + b;\n")
    write(d / "src" / "a.test.ts", test_body)
    if runner_body:
        write(d / "scripts" / "runner.js", runner_body)
    return d


JEST_LIKE = """console.log(" PASS  src/a.test.ts");
console.log(" FAIL  src/a.test.ts");
console.log("  \\u25cf add > \\u52a0\\u6cd5");
console.log("    expect(received).toBe(expected)");
console.log("    Expected: 3");
console.log("    Received: 4");
console.log("");
console.log("Tests:       1 failed, 1 passed, 2 total");
process.exitCode = 1;
"""

VITEST_LIKE = """console.log(" FAIL  src/a.test.ts > add > \\u52a0\\u6cd5");
console.log("  \\u00d7 \\u52a0\\u6cd5");
console.log("");
console.log(" Tests  1 failed | 1 passed (2)");
process.exitCode = 1;
"""

MOCHA_LIKE = """console.log("  1) add");
console.log("     \\u52a0\\u6cd5");
console.log("");
console.log("  1 passing");
console.log("  1 failing");
process.exitCode = 1;
"""

GARBAGE_FAIL = """console.log("something went sideways");
process.exit(1);
"""

QUIET_OK = """console.log("ok");
"""


def test_jest_like_failure_blocks() -> None:
    print("\njest 风格输出 + 失败用例 ⇒ 必须阻塞，不能判绿")
    d = repo_with_runner("node scripts/runner.js", JEST_LIKE)
    r = run("src", "--repo", str(d))
    expect("✓ 测试全绿" not in r.stdout, "不得报「✓ 测试全绿」")
    expect("测试全绿" in r.stdout, "有「测试全绿」这一条")
    expect(r.returncode != 0, "退出码非 0")
    expect("1 项阻塞" in r.stdout, "结论里算阻塞项")


def test_vitest_like_failure_blocks() -> None:
    print("\nvitest 风格输出 + 失败用例 ⇒ 必须阻塞")
    d = repo_with_runner("node scripts/runner.js", VITEST_LIKE)
    r = run("src", "--repo", str(d))
    expect("✓ 测试全绿" not in r.stdout, "不得报「✓ 测试全绿」")
    expect(r.returncode != 0, "退出码非 0")


def test_mocha_like_failure_blocks() -> None:
    print("\nmocha 风格输出 + 失败用例 ⇒ 必须阻塞")
    d = repo_with_runner("node scripts/runner.js", MOCHA_LIKE)
    r = run("src", "--repo", str(d))
    expect("✓ 测试全绿" not in r.stdout, "不得报「✓ 测试全绿」")
    expect(r.returncode != 0, "退出码非 0")


def test_tap_failure_still_blocks() -> None:
    print("\nTAP 风格（node --test）真失败 ⇒ 仍然阻塞，且点名用例")
    d = repo_with_runner("node --test src/a.test.ts", "",
                         test_body="import test from 'node:test';\n"
                                    "import assert from 'node:assert';\n"
                                    "test('\\u52a0\\u6cd5', () => {"
                                    " assert.equal(1 + 1, 3); });\n")
    r = run("src", "--repo", str(d))
    expect("✗ 测试全绿" in r.stdout, "报「✗ 测试全绿」")
    expect("1 个用例失败" in r.stdout, "说了几个用例失败")
    expect(r.returncode != 0, "退出码非 0")


def test_unparsable_output_never_green() -> None:
    print("\n输出格式认不出 ⇒ 判未实测，**绝不判绿**")
    for label, body in (("退出码 1", GARBAGE_FAIL), ("退出码 0", QUIET_OK)):
        d = repo_with_runner("node scripts/runner.js", body)
        r = run("src", "--repo", str(d))
        expect("✓ 测试全绿" not in r.stdout,
               f"{label}：不得报「✓ 测试全绿」")
        expect("○ 测试全绿" in r.stdout or "— 测试全绿" in r.stdout,
               f"{label}：判未实测（○ 或 —）")
        expect(r.returncode != 0,
               f"{label}：退出码非 0（关键判据没测到 ⇒ 不能算绿）")


def test_env_error_not_blocking() -> None:
    print("\n环境没就绪（连不上中间件）⇒ 未实测，不算阻塞项")
    d = repo_with_runner("node scripts/runner.js",
                         "console.log('not ok 1 - connect ECONNREFUSED 127.0.0.1:19530');\n")
    r = run("src", "--repo", str(d))
    expect("✓ 测试全绿" not in r.stdout, "不得报「✓ 测试全绿」")
    expect("0 项阻塞" in r.stdout, "环境问题不算阻塞项")
    expect("环境没就绪" in r.stdout, "说清是环境没就绪")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    test_jest_like_failure_blocks()
    test_vitest_like_failure_blocks()
    test_mocha_like_failure_blocks()
    test_tap_failure_still_blocks()
    test_unparsable_output_never_green()
    test_env_error_not_blocking()
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
