#!/usr/bin/env python3
"""round-8 回归测试：解析器的结构性问题（表头词 / 多节标题 / 形状识别 / 否定语境）。"""
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

    print("表头用别的词 ⇒ 不该被当数据行")
    for header in (
        "| 场景 | 测试文件 | 车道 |",
        "| 用例 | 落点 | 车道 |",
        "| 流程 | 测试落点 | 车道 | 优先级 |",
    ):
        plan = write(tmp / "h.md", """# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/a.ts:1 |

## 覆盖矩阵

""" + header + """
| --- | --- | --- |
| 关键流程 | src/a.test.ts | test |
""")
        fs = check_plan(parse_plan(plan), repo)
        expect(not any(f.level == "error" for f in fs),
               f"表头 {header!r} 被认成表头（不报红）")

    print("\n不变式表头用别的词 ⇒ 不该报「没有钉」")
    plan = write(tmp / "h2.md", """# p

## 不变式清单

| 行为契约 | 描述 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/a.ts:1 |

## 覆盖矩阵

| 流程 | 测试落点 | 车道 |
| --- | --- | --- |
| 关键流程 | src/a.test.ts | test |
""")
    fs = check_plan(parse_plan(plan), repo)
    expect(not any(f.level == "error" for f in fs),
           "不变式表头用别的词 ⇒ 不报红")

    print("\n合并标题 ⇒ 两张表都要被收（不吞）")
    plan = write(tmp / "mixed.md", """# p

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
| 记忆行 | PG | Milvus | src/ghost.test.ts |
""")
    p = parse_plan(plan)
    expect(len(p.matrix_rows) == 1, "矩阵行被收")
    expect(len(p.consistency_rows) == 1, "一致性行也被收（没被吞）")
    fs = check_plan(p, repo)
    expect(any("ghost.test.ts" in f.detail for f in fs),
           "被吞的一致性表里的坏钉照样报红")

    print("\n一致性节标题换个说法 ⇒ 仍能按表形状认出")
    plan = write(tmp / "alt-title.md", """# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/a.ts:1 |

## 覆盖矩阵

| 流程 | 测试落点 | 车道 |
| --- | --- | --- |
| 关键流程 | src/a.test.ts | test |

## 跨存储核对

| 数据 | 权威源 | 镜像/派生 | 一致性怎么验 |
| --- | --- | --- | --- |
| 记忆行 | PG | Milvus | src/ghost.test.ts |
""")
    p = parse_plan(plan)
    expect(len(p.consistency_rows) == 1, "按表形状认出一致性行")
    fs = check_plan(p, repo)
    expect(any("ghost.test.ts" in f.detail for f in fs),
           "换个标题的坏钉照样报红")

    print("\n约束节标题不含「约束」二字 ⇒ 内容不能丢")
    plan = write(tmp / "constraint-title.md", """# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/a.ts:1 |

## 覆盖矩阵

| 流程 | 测试落点 | 车道 |
| --- | --- | --- |
| 关键流程 | src/a.test.ts | test |

## 不引入中间件

用某个消息队列保证最终一致。
""")
    fs = check_plan(parse_plan(plan), repo)
    expect(any("中间件" in f.detail for f in fs),
           "「## 不引入中间件」节里的消息队列被拦下")

    print("\n否定语境不算「用了中间件」")
    plan = write(tmp / "negated.md", """# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/a.ts:1 |

## 覆盖矩阵

| 流程 | 测试落点 | 车道 |
| --- | --- | --- |
| 关键流程 | src/a.test.ts | test |

## 约束：不引入中间件

本项目不引入消息队列，以 PG 为权威来源，向量库只存副本，每小时比对一次并补齐差异。

对账/修复路径的测试落点：src/reconcile.test.ts
""")
    fs = check_plan(parse_plan(plan), repo)
    expect(not any("中间件" in f.detail for f in fs),
           "「本项目不引入消息队列」不误报")

    print("\n同时写了替代方案也不能豁免中间件")
    plan = write(tmp / "both.md", """# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/a.ts:1 |

## 覆盖矩阵

| 流程 | 测试落点 | 车道 |
| --- | --- | --- |
| 关键流程 | src/a.test.ts | test |

## 数据一致性

| 数据 | 权威源 | 镜像/派生 | 一致性怎么验 |
| --- | --- | --- | --- |
| 记忆行 | PG | Milvus | src/reconcile.test.ts |

## 约束：不引入中间件

用消息队列保证最终一致；同时以 PG 为权威来源，每小时比对一次并补齐差异。

对账/修复路径的测试落点：src/reconcile.test.ts
""")
    fs = check_plan(parse_plan(plan), repo)
    expect(any("中间件" in f.detail for f in fs),
           "写了替代方案也拦「用了消息队列」")

    print("\n一致性表首列为空 ⇒ 报红")
    plan = write(tmp / "empty-name.md", """# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/a.ts:1 |

## 覆盖矩阵

| 流程 | 测试落点 | 车道 |
| --- | --- | --- |
| 关键流程 | src/a.test.ts | test |

## 数据一致性

|  | PG | Milvus | src/ghost.test.ts |
""")
    fs = check_plan(parse_plan(plan), repo)
    expect(any("数据名是空的" in f.detail for f in fs),
           "首列为空 ⇒ 报红")

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
