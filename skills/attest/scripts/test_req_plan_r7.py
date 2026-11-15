#!/usr/bin/env python3
"""round-7 回归测试：关掉 round-6 评审找到的 2 个 CRITICAL 绕过 + 3 个假阳性。"""
from __future__ import annotations

import json
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


def main() -> int:
    # C locale 下 stdout 默认 ascii，打中文会 UnicodeEncodeError
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    tmp = mkdtemp()
    write(tmp / "src" / "a.ts", "export const a = 1\n")
    write(tmp / "src" / "a.test.ts", "test('x', () => {})\n")
    write(tmp / "src" / "reconcile.test.ts", "test('r', () => {})\n")
    repo = FakeRepo(tmp)

    print("CRITICAL 1：一致性写成散文 ⇒ 跨存储检查仍要触发")
    p1 = write(tmp / "prose.md", HEAD + """
## 数据一致性

记忆行以 PG 为权威，Milvus 为派生层。

## 约束：不引入中间件

用某个消息队列保证最终一致。
""")
    fs1 = check_plan(parse_plan(p1), repo)
    expect(any("替代方案" in f.detail for f in fs1),
           "散文写的一致性 ⇒ 仍要求替代方案")
    expect(any("中间件" in f.detail for f in fs1),
           "散文写的一致性 ⇒ 仍拦中间件")

    print("\nCRITICAL 1b：只有约束节提到中间件（无一致性节）⇒ 也要拦")
    p1b = write(tmp / "constraint-only.md", HEAD + """
## 约束：不引入中间件

用某个消息队列保证最终一致。
""")
    fs1b = check_plan(parse_plan(p1b), repo)
    expect(any("中间件" in f.detail for f in fs1b),
           "只有约束节提到中间件 ⇒ 报红")

    print("\nCRITICAL 2：一个标题混两节 ⇒ 报红")
    p2 = write(tmp / "mixed.md", """# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/a.ts:1 |

## 覆盖矩阵与一致性

| 流程 | 测试落点 | 车道 |
| --- | --- | --- |
| 关键流程 | src/a.test.ts | test |

| 数据 | 权威源 | 镜像/派生 | 一致性怎么验 |
| --- | --- | --- | --- |
| 记忆行 | PG | Milvus | src/reconcile.test.ts |
""")
    fs2 = check_plan(parse_plan(p2), repo)
    expect(any("混了两节" in f.detail for f in fs2),
           "合并标题 ⇒ 报红")

    print("\n假阳性 3：真实的 pkg#script 车道不该被报提醒")
    d3 = mkdtemp()
    # 车道带 "#" 前缀的前提是 package.json 在子目录里（工具按工作区生成全名）
    write(d3 / "service" / "package.json", json.dumps({"scripts": {"test": "vitest"}}))
    write(d3 / "service" / "src" / "a.ts", "export const a = 1\n")
    write(d3 / "service" / "src" / "a.test.ts", "test('x', () => {})\n")
    r3 = subprocess.run(
        [sys.executable, str(HERE / "req_plan.py"), "new", "f",
         "--repo", str(d3), "--module", "service/src"],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    expect(r3.returncode == 0, "new 成功")
    plan3 = d3 / "docs" / "f-test-plan.md"
    txt = plan3.read_text(encoding="utf-8").replace(
        "| <待填：流程名> | <待填：src/x.test.ts> | <待填：单测> |",
        "| 关键流程 | service/src/a.test.ts | service#test |",
    )
    plan3.write_text(txt, encoding="utf-8")
    r3b = subprocess.run(
        [sys.executable, str(HERE / "req_plan.py"), "check", str(plan3),
         "--repo", str(d3)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    expect("不是项目里的车道" not in r3b.stdout,
           "service#test 这种带 # 的真实车道不被误报")

    print("\n假阳性 4：替代方案换词写（权威来源/比对/补齐）不该报红")
    p4 = write(tmp / "alt-words.md", HEAD + """
## 数据一致性

| 数据 | 权威源 | 镜像/派生 | 一致性怎么验 |
| --- | --- | --- | --- |
| 记忆行 | PG | 向量库 | src/reconcile.test.ts |

## 约束：不引入中间件

以 PG 为权威来源，向量库只存副本，每小时把两边比对一次并补齐差异。

对账/修复路径的测试落点：src/reconcile.test.ts
""")
    fs4 = check_plan(parse_plan(p4), repo)
    expect(not any("替代方案" in f.detail for f in fs4),
           "换词写替代方案 ⇒ 不报红")

    print("\n假阳性 5：场景描述里的 issue #123 不该被当车道")
    p5 = write(tmp / "hash-text.md", HEAD.replace(
        "| 关键流程 | src/a.test.ts | test |",
        "| 修复 issue #123 的流程 | src/a.test.ts | test |",
    ))
    fs5 = check_plan(parse_plan(p5), repo)
    expect(not any("issue #123" in f.detail for f in fs5),
           "描述里的 #123 不被当车道")

    print("\n落点必须是测试文件（源文件 ⇒ 提醒）")
    p6 = write(tmp / "src-landing.md", HEAD.replace(
        "| 关键流程 | src/a.test.ts | test |",
        "| 关键流程 | src/a.ts | test |",
    ))
    fs6 = check_plan(parse_plan(p6), repo)
    expect(any("落点不是测试文件" in f.detail for f in fs6),
           "落点指向源文件 ⇒ 提醒")

    print("\n描述列里的文件不能顶替落点")
    p7 = write(tmp / "desc-file.md", HEAD.replace(
        "| 关键流程 | src/a.test.ts | test |",
        "| src/a.ts 限流 |  | test |",
    ))
    fs7 = check_plan(parse_plan(p7), repo)
    expect(any("没有测试落点" in f.detail for f in fs7),
           "描述列有文件、落点列空 ⇒ 报「没有测试落点」")

    print("\n矩阵首行是数据行（含空格子）⇒ 不被吞")
    p8 = write(tmp / "matrix-first-data.md", """# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/a.ts:1 |

## 覆盖矩阵

| 关键流程一 |  | test |
| --- | --- | --- |
| 关键流程二 | src/a.test.ts | test |
""")
    p8p = parse_plan(p8)
    expect(len(p8p.matrix_rows) == 2, "矩阵首行数据没被吞")

    print("\n一致性单列行 ⇒ 报红（不能被跳过）")
    p9 = write(tmp / "consistency-one-col.md", HEAD + """
## 数据一致性

| 记忆行 |
| --- |
""")
    fs9 = check_plan(parse_plan(p9), repo)
    expect(any("没写权威源" in f.detail for f in fs9),
           "一致性单列行 ⇒ 报「没写权威源」")

    print("\n同一数据名大小写不同 ⇒ 算重复")
    p10 = write(tmp / "case-dup.md", HEAD + """
## 数据一致性

| 数据 | 权威源 | 镜像/派生 | 一致性怎么验 |
| --- | --- | --- | --- |
| Memory | PG | Milvus | src/reconcile.test.ts |
| memory | Milvus | PG | src/reconcile.test.ts |
""")
    fs10 = check_plan(parse_plan(p10), repo)
    expect(any("出现了 2 行" in f.detail for f in fs10),
           "大小写不同的同名数据 ⇒ 报重复")

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
