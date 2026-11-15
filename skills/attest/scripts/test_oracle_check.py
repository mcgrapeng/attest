#!/usr/bin/env python3
"""oracle 独立性启发式检查的回归测试。

钉住两件事：
  1. 三种"期望值取自被测对象自身"的形态都能被认出来（JS / Python / Rust / Go）。
  2. 宁漏勿噪 —— 期望值来自字面量、来自外部 API、来自另一个被测函数时，
     一律不报（误报会把真问题淹掉）。

能在干净临时目录里独立跑通（不依赖别的 skill 的绝对路径）。

    python3 scripts/test_oracle_check.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "oracle_check.py"
FAILURES: list[str] = []
TMPDIRS: list[Path] = []

sys.path.insert(0, str(HERE))
import oracle_check  # noqa: E402


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


def by_file(result: dict, name: str) -> list[dict]:
    return [f for f in result["findings"] if f["file"].endswith(name)]


SRC_TS = (
    "export function calc(n: number): number { return n * 2 }\n"
    "export function sum(a: number, b: number): number { return a + b }\n"
)
SRC_PY = "def add(a, b):\n    return a + b\n"
SRC_RS = "pub fn add(a: i32, b: i32) -> i32 { a + b }\n"
SRC_GO = "package main\n\nfunc add(a int, b int) int { return a + b }\n"


def make_repo() -> Path:
    """一个合成仓库：被测对象导出 calc/sum/add，四种语言的测试各一份。"""
    root = mkdtemp()
    write(root / "src" / "calc.ts", SRC_TS)
    write(root / "src" / "calc.py", SRC_PY)
    write(root / "src" / "calc.rs", SRC_RS)
    write(root / "src" / "calc.go", SRC_GO)

    write(root / "test" / "self.test.ts",
          "import { calc } from '../src/calc';\n"
          "test('self', () => {\n"
          "  expect(calc(1)).toBe(calc(1));\n"
          "});\n")
    write(root / "test" / "fromsut.test.ts",
          "import { calc } from '../src/calc';\n"
          "test('from sut', () => {\n"
          "  const expected = calc(2);\n"
          "  expect(calc(3)).toBe(expected);\n"
          "});\n")
    write(root / "test" / "reassigned.test.ts",
          "import { calc } from '../src/calc';\n"
          "test('reassigned', () => {\n"
          "  let expected = calc(2);\n"
          "  expected = 'literal';\n"
          "  expect(calc(3)).toBe(expected);\n"
          "});\n")
    write(root / "test" / "self.test.py",
          "from src.calc import add\n\n"
          "def test_add():\n"
          "    assert add(1, 2) == add(1, 2)\n")
    write(root / "test" / "self.test.rs",
          "#[test]\n"
          "fn add_works() {\n"
          "    assert_eq!(add(1, 2), add(1, 2));\n"
          "}\n")
    write(root / "test" / "self.test.go",
          "package main\n\n"
          'import "testing"\n\n'
          "func TestAdd(t *testing.T) {\n"
          "\tif add(1, 2) != add(1, 2) {\n"
          '\t\tt.Fatalf("bad")\n'
          "\t}\n"
          "}\n")
    write(root / "test" / "snapshot.test.ts",
          "import { calc } from '../src/calc';\n"
          "test('snapshot', () => {\n"
          "  expect(calc(1)).toMatchSnapshot();\n"
          "  expect(calc(1)).toBe(2);\n"
          "});\n")

    # 反例 —— 期望值不是取自被测对象，不该报
    write(root / "test" / "literal.test.ts",
          "import { calc } from '../src/calc';\n"
          "test('literal', () => {\n"
          "  expect(calc(1)).toBe(2);\n"
          "});\n")
    write(root / "test" / "external.test.ts",
          "import { calc } from '../src/calc';\n"
          "test('external', () => {\n"
          "  expect(calc(1)).toBe(api.fetchExpected());\n"
          "});\n")
    write(root / "test" / "twosut.test.ts",
          "import { sum } from '../src/calc';\n"
          "test('two sut', () => {\n"
          "  const a = 1, b = 2;\n"
          "  expect(sum(a, b)).toBe(add(a, b));\n"
          "});\n")
    write(root / "test" / "commented.test.ts",
          "import { calc } from '../src/calc';\n"
          "test('commented', () => {\n"
          "  // expect(calc(1)).toBe(calc(1));\n"
          "  expect(calc(1)).toBe(2);\n"
          "});\n")
    write(root / "test" / "stringy.test.ts",
          "import { calc } from '../src/calc';\n"
          "test('stringy', () => {\n"
          '  expect(calc(1)).toBe("calc(1)");\n'
          "});\n")
    return root


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    repo = make_repo()
    result = oracle_check.check_oracle_independence(repo, [])

    print("三种形态都能被认出来")
    self_js = by_file(result, "test/self.test.ts")
    expect(len(self_js) == 1 and self_js[0]["kind"] == "自比较",
           "detects JS self-comparison")
    expect(self_js and self_js[0]["line"] == 3,
           "JS self-comparison reports the assertion line")

    from_sut = by_file(result, "test/fromsut.test.ts")
    expect(len(from_sut) == 1 and from_sut[0]["kind"] == "期望值取自被测对象",
           "detects JS expected-from-SUT")
    expect(from_sut and from_sut[0]["line"] == 4,
           "expected-from-SUT reports the assertion line")

    self_py = by_file(result, "test/self.test.py")
    expect(len(self_py) == 1 and self_py[0]["kind"] == "自比较",
           "detects Python self-comparison")

    self_rs = by_file(result, "test/self.test.rs")
    expect(len(self_rs) == 1 and self_rs[0]["kind"] == "自比较",
           "detects Rust self-comparison")

    self_go = by_file(result, "test/self.test.go")
    expect(len(self_go) == 1 and self_go[0]["kind"] == "自比较",
           "detects Go self-comparison")

    snap = by_file(result, "test/snapshot.test.ts")
    expect(len(snap) == 1 and snap[0]["kind"] == "快照自比对",
           "detects snapshot self-comparison")

    print("\n反例不报（宁漏勿噪）")
    expect(by_file(result, "test/literal.test.ts") == [],
           "literal expectation is not flagged")
    expect(by_file(result, "test/external.test.ts") == [],
           "external source expectation is not flagged")
    expect(by_file(result, "test/twosut.test.ts") == [],
           "two different SUT calls are not flagged")
    expect(by_file(result, "test/commented.test.ts") == [],
           "commented-out assertion is not flagged")
    expect(by_file(result, "test/stringy.test.ts") == [],
           "string literal is not flagged")
    expect(by_file(result, "test/reassigned.test.ts") == [],
           "variable reassigned to a literal is no longer a SUT expectation")

    print("\n输出契约")
    expect(set(result) == {"scanned", "findings", "verdict"},
           "result has exactly scanned/findings/verdict")
    expect(result["scanned"] == 12,
           "scanned counts the test files it read")
    expect(result["verdict"].startswith(f"{len(result['findings'])} 处"),
           "verdict counts the findings")
    f = self_js[0]
    expect(set(f) == {"file", "line", "kind", "snippet", "why"},
           "finding has file/line/kind/snippet/why")
    expect(f["file"] == "test/self.test.ts",
           "finding file is relative to the repo root")
    expect("expect(calc(1)).toBe(calc(1))" in f["snippet"],
           "finding snippet carries the offending assertion")
    expect(len(f["why"]) > 0 and "期望值" in f["why"],
           "finding why explains the oracle gap in plain words")

    print("\nCLI")
    r = run("--repo", str(repo), "--json")
    expect(r.returncode == 0, "CLI exits 0 even when there are findings")
    data = json.loads(r.stdout)
    expect(set(data) == {"scanned", "findings", "verdict"},
           "CLI --json emits the documented shape")
    expect(len(data["findings"]) == len(result["findings"]),
           "CLI --json reports the same findings as the API")

    r2 = run(str(repo / "test" / "self.test.ts"), "--repo", str(repo))
    expect(r2.returncode == 0 and "自比较" in r2.stdout,
           "CLI accepts a single test file")
    expect("提醒级" in r2.stdout,
           "CLI says the check is a reminder, not a verdict")

    r3 = run("--repo", str(repo))
    expect(r3.returncode == 0 and "扫描" in r3.stdout,
           "CLI scans the whole repo when no path is given")

    print("\n只读")
    target = repo / "test" / "self.test.ts"
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    oracle_check.check_oracle_independence(repo, [target])
    after = hashlib.sha256(target.read_bytes()).hexdigest()
    expect(before == after, "scan does not touch the scanned files")

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
