#!/usr/bin/env python3
"""round-11 回归测试：钉升级为「文件 + 用例名」。"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from req_plan import (  # noqa: E402
    case_matches, check_plan, parse_pins, parse_plan, test_case_names,
)

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


class FakeRepo:
    def __init__(self, root: Path):
        self.root = root
        self.packages = []
        self.scripts = {}


TS_TEST = """import { describe, it } from 'node:test';

describe('poolColumnFilter (记忆池的 PG 过滤谓词)', () => {
  it('maps a missing pool to the shared pool', () => {});
  it('maps a named pool to that pool only', () => {});
});
"""


def main() -> int:
    # C locale 下 stdout 默认 ascii，打中文会 UnicodeEncodeError
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    tmp = mkdtemp()
    write(tmp / "src" / "a.ts", "export const a = 1\n")
    write(tmp / "src" / "pool.test.ts", TS_TEST)
    write(tmp / "src" / "b.test.ts", "test('other case', () => {});\n")
    repo = FakeRepo(tmp)

    print("用例名提取")
    names = test_case_names(tmp, "src/pool.test.ts")
    expect("maps a missing pool to the shared pool" in names,
           "node:test 的 it() 用例被提取")
    expect("maps a named pool to that pool only" in names, "用例名正确")
    expect(any(" > " in n for n in names),
           "describe 嵌套的全名也提取了")
    expect(case_matches("maps a named pool to that pool only", names), "精确匹配")
    expect(case_matches("maps a named pool", names), "前缀/缩写也认")
    expect(not case_matches("完全不存在", names), "不存在的不认")

    print("\nPython / Go 用例名")
    write(tmp / "tests" / "test_a.py", "def test_alpha():\n    pass\n")
    write(tmp / "pkg" / "x_test.go", "func TestBeta(t *testing.T) {}\n")
    expect("test_alpha" in test_case_names(tmp, "tests/test_a.py"), "Python 用例名")
    expect("TestBeta" in test_case_names(tmp, "pkg/x_test.go"), "Go 用例名")

    print("\n钉的解析")
    expect(parse_pins("src/a.test.ts::maps a named pool") ==
           [("src/a.test.ts", "maps a named pool")], "两段式钉解析")
    expect(parse_pins("src/a.test.ts:123") == [("src/a.test.ts", None)], "行号钉解析")
    expect(parse_pins("src/a.test.ts") == [("src/a.test.ts", None)], "文件钉解析")

    print("\n钉到不存在的用例 ⇒ 报红")
    plan = write(tmp / "p.md", """# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 空池映射到共享池 | src/pool.test.ts::根本不存在的用例 |
""")
    fs = check_plan(parse_plan(plan), repo)
    expect(any("钉的用例" in f.detail and "不存在" in f.detail for f in fs),
           "钉的用例不存在 ⇒ 报红")

    print("\n钉到真实用例 ⇒ 不报红")
    plan2 = write(tmp / "p2.md", """# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 空池映射到共享池 | src/pool.test.ts::maps a named pool to that pool only |
""")
    fs2 = check_plan(parse_plan(plan2), repo)
    expect(not any("钉的用例" in f.detail for f in fs2), "钉到真实用例 ⇒ 不报红")

    print("\n只钉到文件 ⇒ 聚合提醒")
    plan3 = write(tmp / "p3.md", """# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 空池映射到共享池 | src/pool.test.ts |
""")
    fs3 = check_plan(parse_plan(plan3), repo)
    expect(any("没指到具体用例" in f.detail for f in fs3),
           "只钉到文件 ⇒ 提醒")
    expect(sum(1 for f in fs3 if "没指到具体用例" in f.detail) == 1,
           "多条弱钉聚合成一条提醒")

    print("\n被钉文件里没被任何不变式钉住的用例 ⇒ 聚合提醒")
    plan4 = write(tmp / "p4.md", """# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 空池映射到共享池 | src/pool.test.ts::maps a named pool to that pool only |
""")
    fs4 = check_plan(parse_plan(plan4), repo)
    expect(any("没有对应的不变式" in f.detail for f in fs4),
           "有没用例没被钉住 ⇒ 提醒")

    print("\n矩阵落点到不存在的用例 ⇒ 报红")
    plan5 = write(tmp / "p5.md", """# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/pool.test.ts::maps a named pool to that pool only |

## 覆盖矩阵

| 流程 | 测试落点 | 车道 |
| --- | --- | --- |
| 关键流程 | src/pool.test.ts::幽灵用例 | test |
""")
    fs5 = check_plan(parse_plan(plan5), repo)
    expect(any("落点的用例" in f.detail for f in fs5),
           "矩阵落点的用例不存在 ⇒ 报红")

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
