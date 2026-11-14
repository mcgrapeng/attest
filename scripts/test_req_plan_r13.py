#!/usr/bin/env python3
"""round-13 回归测试：生成测试工具包 + 四性（严谨/逻辑/复用/扩展）。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "req_plan.py"
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


PLAN = """# 测试计划：feat

- 目标模块：`src/feature`

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 阈值恒为 1 | src/feature/threshold.test.ts::keeps threshold at one |

## 覆盖矩阵

| 流程 | 测试落点 | 车道 |
| --- | --- | --- |
| 阈值校验 | src/feature/threshold.test.ts::keeps threshold at one | test |
"""


def main() -> int:
    # C locale 下 stdout 默认 ascii，打中文会 UnicodeEncodeError
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    tmp = mkdtemp()
    write(tmp / "package.json", json.dumps({
        "scripts": {"test": "vitest", "test:e2e": "playwright test"}}))
    write(tmp / "src" / "feature" / "a.ts", "export const t = 1;\n")
    write(tmp / "src" / "feature" / "existing.test.ts",
          "test('old case', () => {});\n")
    write(tmp / "docs" / "f-test-plan.md", PLAN)

    print("约定探测")
    sys.path.insert(0, str(HERE))
    from req_plan import detect_conventions  # noqa: E402
    conv = detect_conventions(tmp, near="src/feature")
    expect(conv.framework in ("node:test", "vitest"), f"框架认出来: {conv.framework}")
    expect(conv.assertion in ("node:assert", "expect"),
           f"断言风格认出来: {conv.assertion}")

    print("\n生成：不存在的文件 ⇒ 生成骨架")
    r = run("scaffold", str(tmp / "docs" / "f-test-plan.md"), "--repo", str(tmp))
    expect(r.returncode == 0, "scaffold 成功")
    gen = tmp / "src" / "feature" / "threshold.test.ts"
    expect(gen.exists(), "新测试文件生成了")
    if gen.exists():
        text = gen.read_text(encoding="utf-8")
        expect("I1" in text and "阈值恒为 1" in text,
               "文件头声明了钉的不变量（逻辑性载体）")
        expect("keeps threshold at one" in text, "用例名来自计划")
        expect("node:test" in text or "vitest" in text, "import 跟随仓库框架")
        expect("assert" in text or "expect" in text, "断言库跟随仓库风格")
        expect("TODO" in text, "断言处留了 TODO（要人填真断言）")

    print("\n生成：已存在的文件 ⇒ 不覆盖，只列要加的用例")
    before = (tmp / "src" / "feature" / "existing.test.ts").read_text(encoding="utf-8")
    r2 = run("scaffold", str(tmp / "docs" / "f-test-plan.md"), "--repo", str(tmp))
    after = (tmp / "src" / "feature" / "existing.test.ts").read_text(encoding="utf-8")
    expect(before == after, "既有测试文件没被改")

    print("\n严谨性：测试文件里没有断言 ⇒ 报红")
    write(tmp / "docs" / "g-test-plan.md", PLAN.replace(
        "src/feature/threshold.test.ts::keeps threshold at one",
        "src/feature/noassert.test.ts::some case").replace(
        "| I1 | 阈值恒为 1 | src/feature/noassert.test.ts::some case |",
        "| I1 | 阈值恒为 1 | src/feature/noassert.test.ts::some case |"))
    write(tmp / "src" / "feature" / "noassert.test.ts",
          "test('some case', () => {});\n")
    r3 = run("check", str(tmp / "docs" / "g-test-plan.md"), "--repo", str(tmp))
    expect("没有断言" in r3.stdout, "没有断言的测试文件 ⇒ 报红")

    print("\n严谨性：断言是同义反复也算没验东西")
    write(tmp / "src" / "feature" / "noassert.test.ts",
          "test('some case', () => { assert.strictEqual(1, 1); });\n")
    r4 = run("check", str(tmp / "docs" / "g-test-plan.md"), "--repo", str(tmp))
    expect("没有断言" not in r4.stdout, "有断言就不算没断言（不误报）")

    print("\n逻辑性：文件头没声明不变量 ⇒ 提醒")
    write(tmp / "src" / "feature" / "noassert.test.ts",
          "test('some case', () => { assert.ok(true); });\n")
    r5 = run("check", str(tmp / "docs" / "g-test-plan.md"), "--repo", str(tmp))
    expect("没声明钉了哪些不变量" in r5.stdout, "文件头缺声明 ⇒ 提醒")

    print("\n扩展性：多个结构雷同的用例 ⇒ 提醒")
    write(tmp / "src" / "feature" / "noassert.test.ts",
          "test('c1', () => { assert.strictEqual(a, b); });\n"
          "test('c2', () => { assert.strictEqual(a, b); });\n"
          "test('c3', () => { assert.strictEqual(a, b); });\n")
    r6 = run("check", str(tmp / "docs" / "g-test-plan.md"), "--repo", str(tmp))
    expect("结构完全相同" in r6.stdout, "雷同用例 ⇒ 提醒参数化")

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
