#!/usr/bin/env python3
"""round-10 回归测试：核心逻辑细节清单 + 不过度设计/不重复造轮子。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from req_plan import check_plan, parse_plan  # noqa: E402

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


HEAD = """# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/a.ts:1 |

## 覆盖矩阵

| 流程 | 测试落点 | 车道 |
| --- | --- | --- |
| 关键流程 | src/a.test.ts | test |
"""

DETAIL_FULL = """
## 核心逻辑细节

| 维度 | 落点 / 不适用理由 |
| --- | --- |
| 边界 | src/a.test.ts |
| 错误路径 | 不适用：本需求无外部输入失败路径 |
| 并发 | 不适用：纯函数无共享状态 |
| 重试/幂等 | src/retry.test.ts |
| 降级/超时 | src/a.test.ts |
| 跨租户隔离 | 不适用：不碰租户数据 |
| 权限 | 不适用：无权限点 |
| 资源上限 | src/a.test.ts |
"""


def main() -> int:
    # C locale 下 stdout 默认 ascii，打中文会 UnicodeEncodeError
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    tmp = mkdtemp()
    write(tmp / "src" / "a.ts", "export const a = 1\n")
    write(tmp / "src" / "a.test.ts", "test('x', () => {})\n")
    write(tmp / "src" / "retry.test.ts", "test('r', () => {})\n")
    repo = FakeRepo(tmp)

    print("细节清单：每维度有落点或「不适用」+理由 ⇒ 不报红")
    plan = write(tmp / "d.md", HEAD + DETAIL_FULL)
    fs = check_plan(parse_plan(plan), repo)
    expect(not any(f.level == "error" for f in fs),
           "完整的细节清单 ⇒ 无错误")
    expect(not any("没考虑到的维度" in f.detail for f in fs),
           "维度齐全 ⇒ 不提醒")

    print("\n细节清单：缺维度 ⇒ 提醒")
    plan2 = write(tmp / "d2.md", HEAD + """
## 核心逻辑细节

| 维度 | 落点 / 不适用理由 |
| --- | --- |
| 边界 | src/a.test.ts |
""")
    fs2 = check_plan(parse_plan(plan2), repo)
    expect(any("没考虑到的维度" in f.detail for f in fs2),
           "缺维度 ⇒ 提醒")

    print("\n细节清单：维度没落点也没说不适用 ⇒ 报红")
    plan3 = write(tmp / "d3.md", HEAD + """
## 核心逻辑细节

| 维度 | 落点 / 不适用理由 |
| --- | --- |
| 边界 |  |
""")
    fs3 = check_plan(parse_plan(plan3), repo)
    expect(any("没有测试落点" in f.detail for f in fs3),
           "维度空着 ⇒ 报红")

    print("\n细节清单：「不适用」没给理由 ⇒ 报红")
    plan4 = write(tmp / "d4.md", HEAD + """
## 核心逻辑细节

| 维度 | 落点 / 不适用理由 |
| --- | --- |
| 边界 | 不适用 |
""")
    fs4 = check_plan(parse_plan(plan4), repo)
    expect(any("没给理由" in f.detail for f in fs4),
           "「不适用」没理由 ⇒ 报红")

    print("\n细节清单：落点文件不存在 ⇒ 报红")
    plan5 = write(tmp / "d5.md", HEAD + """
## 核心逻辑细节

| 维度 | 落点 / 不适用理由 |
| --- | --- |
| 边界 | src/ghost.test.ts |
""")
    fs5 = check_plan(parse_plan(plan5), repo)
    expect(any("落点不存在" in f.detail for f in fs5),
           "落点不存在 ⇒ 报红")

    print("\n新建能力：符号全仓已存在 ⇒ 报红（别造轮子）")
    d6 = mkdtemp()
    write(d6 / "src" / "a.ts", "export const a = 1\n")
    write(d6 / "src" / "helper.ts", "export function computeScore() { return 1 }\n")
    write(d6 / "src" / "a.test.ts", "test('x', () => {})\n")
    plan6 = write(d6 / "p6.md", HEAD + """
## 新建能力（先论证不能复用）

| 能力 | 为什么不复用现成的 | 调用点 |
| --- | --- | --- |
| computeScore | 现有实现不支持新格式 | 2 |
""")
    fs6 = check_plan(parse_plan(plan6), FakeRepo(d6))
    expect(any("全仓已经存在" in f.detail for f in fs6),
           "同名导出已存在 ⇒ 报红")

    print("\n新建能力：调用点 ≤1 ⇒ 提醒（规则之三）")
    d7 = mkdtemp()
    write(d7 / "src" / "a.ts", "export const a = 1\n")
    write(d7 / "src" / "a.test.ts", "test('x', () => {})\n")
    plan7 = write(d7 / "p7.md", HEAD + """
## 新建能力（先论证不能复用）

| 能力 | 为什么不复用现成的 | 调用点 |
| --- | --- | --- |
| brandNewHelper | 现有 helper 都不适用 | 1 |
""")
    fs7 = check_plan(parse_plan(plan7), FakeRepo(d7))
    expect(any("规则之三" in (f.detail + f.fix) for f in fs7),
           "调用点 1 ⇒ 提醒过度设计")

    print("\n新建能力：没论证为什么不复用 ⇒ 提醒")
    plan8 = write(tmp / "d8.md", HEAD + """
## 新建能力（先论证不能复用）

| 能力 | 为什么不复用现成的 | 调用点 |
| --- | --- | --- |
| anotherHelper |  | 3 |
""")
    fs8 = check_plan(parse_plan(plan8), repo)
    expect(any("为什么不能复用" in f.detail for f in fs8),
           "没论证 ⇒ 提醒")

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
