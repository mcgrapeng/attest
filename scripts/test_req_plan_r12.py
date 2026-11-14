#!/usr/bin/env python3
"""round-12 回归测试：终审发现的 9 类缺陷（假阳性 6 + 假阴性 3）。

其中 B1/B2/B5 已在真实 Lumen 仓库复现过（退出码 1）—— 这里把形态固化。
"""
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


def plan_with(root: Path, pin: str, extra: str = "") -> Path:
    return write(root / "p.md", f"""# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 某条性质 | {pin} |

## 覆盖矩阵

| 流程 | 测试落点 | 车道 |
| --- | --- | --- |
| 关键流程 | src/pool.test.ts::maps a named pool to that pool only | test |
""" + extra)


def main() -> int:
    # C locale 下 stdout 默认 ascii，打中文会 UnicodeEncodeError
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    tmp = mkdtemp()
    write(tmp / "src" / "pool.test.ts", """import { describe, it, test } from 'node:test';

describe('pool', () => {
  it('maps a missing pool to the shared pool', () => {});
  it('maps a named pool to that pool only', () => {});
  it.each([[1]])('handles case %s', () => {});
  test.skip('skipped one', () => {});
  it.only('focused one', () => {});
  it('pdf.js item y is taken as-is (line-box top), never shifted down', () => {});
});
""")
    write(tmp / "src" / "async.test.py", "async def test_async_thing():\n    pass\n")
    write(tmp / "src" / "x_test.go", "func (s *Suite) TestGamma(t *testing.T) {}\n")
    write(tmp / "src" / "esc.test.ts",
          "it(\"doesn't fail\", () => {});\n")
    write(tmp / "src" / "cjk.test.ts", "it('中文用例名', () => {});\n")
    repo = FakeRepo(tmp)

    print("B2：it.each / test.skip / it.only 认得出")
    names = test_case_names(tmp, "src/pool.test.ts")
    expect(any("handles case" in n for n in names), "it.each 的用例名被提取")
    expect(any("skipped one" in n for n in names), "test.skip 的用例名被提取")
    expect(any("focused one" in n for n in names), "it.only 的用例名被提取")

    print("\nB3/B4：Python async def / Go receiver")
    expect("test_async_thing" in test_case_names(tmp, "src/async.test.py"),
           "Python async def 认得出")
    expect("TestGamma" in test_case_names(tmp, "src/x_test.go"),
           "Go 带 receiver 的测试方法认得出")

    print("\nB6：用例名含单引号")
    esc = test_case_names(tmp, "src/esc.test.ts")
    expect(any("doesn't fail" in n for n in esc),
           "双引号包裹的用例名（含撇号）完整提取")

    print("\nB1：一个单元格里两个钉")
    pins = parse_pins("src/pool.test.ts::maps a named, src/cjk.test.ts::中文用例名")
    expect(len(pins) == 2, "两个钉被拆成两条")
    expect(pins[0][1] == "maps a named", "第一个用例名没吞掉第二个钉")
    expect(pins[1] == ("src/cjk.test.ts", "中文用例名"), "第二个钉完整")

    print("\nB5：用例名里含路径状 token ⇒ 整个钉不被拆坏")
    plan = plan_with(tmp,
                      "src/pool.test.ts::pdf.js item y is taken as-is "
                      "(line-box top), never shifted down")
    fs = check_plan(parse_plan(plan), repo)
    expect(not any("文件不存在" in f.detail for f in fs),
           "用例名里的 pdf.js 没被当成第二个钉")
    expect(not any("钉的用例" in f.detail for f in fs),
           "用例名里的 pdf.js 没让钉判红")

    print("\nB5：真实仓库里复现过的那个用例名")
    plan2 = plan_with(tmp,
                       "src/pool.test.ts::pdf.js item y is taken as-is "
                       "(line-box top), never shifted down")
    fs2 = check_plan(parse_plan(plan2), repo)
    expect(not any(f.level == "error" for f in fs2), "整个计划无错误")

    print("\nC1：短钉名蒙混过关 ⇒ 必须报红")
    plan3 = plan_with(tmp, "src/pool.test.ts::a")
    fs3 = check_plan(parse_plan(plan3), repo)
    expect(any("钉的用例" in f.detail for f in fs3),
           "单字符钉名匹配不上任何用例 ⇒ 报红")

    print("\nC1：短钉名不该顺手关掉「未钉用例」提醒")
    expect(any("没有对应的不变式" in f.detail for f in fs3),
           "短钉名不会把未钉用例提醒一起关掉")

    print("\nC2：矩阵落点到不存在的用例 ⇒ 报红（不因 known_tests 跳过）")
    plan4 = write(tmp / "p4.md", """# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/pool.test.ts::maps a named pool to that pool only |

## 覆盖矩阵

| 流程 | 测试落点 | 车道 |
| --- | --- | --- |
| 关键流程 | src/plain_test.py::幽灵用例 | test |
""")
    write(tmp / "src" / "plain_test.py", "def test_ok():\n    pass\n")
    fs4 = check_plan(parse_plan(plan4), repo)
    expect(any("落点的用例" in f.detail for f in fs4),
           "非 known_tests 的文件也校验用例名")

    print("\nC3：矩阵落点文件里的用例没被认领 ⇒ 提醒")
    plan5 = write(tmp / "p5.md", """# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/cjk.test.ts::中文用例名 |

## 覆盖矩阵

| 流程 | 测试落点 | 车道 |
| --- | --- | --- |
| 关键流程 | src/pool.test.ts::maps a named pool to that pool only | test |
""")
    fs5 = check_plan(parse_plan(plan5), repo)
    expect(any("没有对应的不变式" in f.detail for f in fs5),
           "矩阵落点文件里没被认领的用例也提醒")

    print("\ndescribe 全名也能对上")
    plan6 = plan_with(tmp,
                       "src/pool.test.ts::pool > maps a named pool to that pool only")
    fs6 = check_plan(parse_plan(plan6), repo)
    expect(not any("钉的用例" in f.detail for f in fs6),
           "describe 全名形式的钉被接受")

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
