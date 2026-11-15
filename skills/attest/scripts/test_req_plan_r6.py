#!/usr/bin/env python3
"""round-6 回归测试：要求 1/3/4/5 的机械校验。

对应用户最初的五条要求：
  1 各类必要测试      → 矩阵行的车道必须是项目真实存在的
  3 每个关键流程细节  → 矩阵每行必须有测试落点（空行能过 = 假全绿）
  4 数据一致性准确性  → 一致性表的权威源/镜像/钉/数据名唯一
  5 不引入中间件      → 跨存储时必须有替代方案 + 对账落点；用中间件解一致性要报红
"""
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

    print("要求 3：矩阵每行必须有测试落点（空行不能过）")
    plan = write(tmp / "no-landing.md", """# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/a.ts:1 |

## 覆盖矩阵

| 流程 | 测试落点 | 车道 |
| --- | --- | --- |
| 关键流程一 |  | test |
| 关键流程二 | src/a.test.ts | test |
""")
    fs = check_plan(parse_plan(plan), repo)
    expect(any("没有测试落点" in f.detail for f in fs),
           "矩阵行没有测试落点 ⇒ 报红")

    print("\n要求 3：矩阵行引用的不存在的文件 ⇒ 报红")
    plan2 = write(tmp / "ghost.md", """# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/a.ts:1 |

## 覆盖矩阵

| 流程 | 测试落点 | 车道 |
| --- | --- | --- |
| 关键流程 | src/ghost.test.ts | test |
""")
    fs2 = check_plan(parse_plan(plan2), repo)
    expect(any("ghost.test.ts" in f.detail for f in fs2),
           "矩阵引用不存在的文件 ⇒ 报红")

    print("\n要求 4：一致性表缺权威源 / 缺钉 ⇒ 报红")
    plan3 = write(tmp / "consistency-bad.md", """# p

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
| 记忆行 |  |  | |
""")
    fs3 = check_plan(parse_plan(plan3), repo)
    expect(any("没写权威源" in f.detail for f in fs3), "缺权威源 ⇒ 报红")
    expect(any("没写镜像" in f.detail for f in fs3), "缺镜像/派生 ⇒ 报红")
    expect(any("一致性没有测试落点" in f.detail for f in fs3),
           "一致性没有测试落点 ⇒ 报红")

    print("\n要求 4：同一份数据列两行 ⇒ 报红（两个权威源）")
    plan4 = write(tmp / "consistency-dup.md", """# p

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
| 记忆行 | Milvus | PG | src/reconcile.test.ts |
""")
    fs4 = check_plan(parse_plan(plan4), repo)
    expect(any("出现了 2 行" in f.detail for f in fs4),
           "同一数据两行 ⇒ 报红")

    print("\n要求 5：跨存储但没给替代方案 ⇒ 报红")
    # 约束节要写点真实内容（不是模板占位）—— 占位会报"没填"而不是"缺替代方案"
    plan5 = write(tmp / "no-alt.md", """# p

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

先这样，回头补。
""")
    fs5 = check_plan(parse_plan(plan5), repo)
    expect(any("替代方案" in f.detail for f in fs5),
           "没给「单权威+镜像+对账」替代方案 ⇒ 报红")
    expect(any("对账/修复路径没有测试落点" in f.detail for f in fs5),
           "对账路径没有测试落点 ⇒ 报红")

    print("\n要求 5：用中间件解一致性且无替代方案 ⇒ 报红")
    plan6 = write(tmp / "middleware.md", """# p

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

用 Kafka 消息队列保证最终一致。
""")
    fs6 = check_plan(parse_plan(plan6), repo)
    expect(any("中间件" in f.detail for f in fs6),
           "用中间件解一致性且无替代方案 ⇒ 报红")

    print("\n要求 5：给了替代方案 + 对账落点 ⇒ 不报红")
    plan7 = write(tmp / "ok.md", """# p

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

单一权威：PG。向量库是派生层，靠对账修复。

对账/修复路径的测试落点：src/reconcile.test.ts
""")
    fs7 = check_plan(parse_plan(plan7), repo)
    expect(not any(f.level == "error" for f in fs7),
           "完整的跨存储计划 ⇒ 无错误")

    print("\n要求 5：「对账落点」这行不能冒充替代方案")
    plan7b = write(tmp / "ok-but-no-alt.md", """# p

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

用某个消息队列保证最终一致。

对账/修复路径的测试落点：src/reconcile.test.ts
""")
    fs7b = check_plan(parse_plan(plan7b), repo)
    expect(any("替代方案" in f.detail for f in fs7b),
           "只有落点行、没有替代方案 ⇒ 报红")
    expect(any("中间件" in f.detail for f in fs7b),
           "用消息队列解一致性 ⇒ 报红")

    print("\n要求 5：「最终一致」是性质不是中间件，不该被误报")
    plan7c = write(tmp / "eventual-consistency.md", """# p

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

单一权威：PG。向量库是派生层，最终一致由对账保证。

对账/修复路径的测试落点：src/reconcile.test.ts
""")
    fs7c = check_plan(parse_plan(plan7c), repo)
    expect(not any(f.level == "error" for f in fs7c),
           "用「最终一致」描述结果 ⇒ 不报红（不是中间件）")

    print("\n没有跨存储时：一致性/约束的检查不该触发")
    plan8 = write(tmp / "no-cross.md", """# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/a.ts:1 |

## 覆盖矩阵

| 流程 | 测试落点 | 车道 |
| --- | --- | --- |
| 关键流程 | src/a.test.ts | test |
""")
    fs8 = check_plan(parse_plan(plan8), repo)
    expect(not any("一致性" in f.item or "约束" in f.item for f in fs8),
           "无跨存储时一致性/约束不报红")

    print("\n要求 1：矩阵里的车道是编的 ⇒ 提醒")
    plan9 = write(tmp / "fake-lane.md", """# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/a.ts:1 |

## 覆盖矩阵

| 流程 | 测试落点 | 车道 |
| --- | --- | --- |
| 关键流程 | src/a.test.ts | service#test:made-up |
""")
    fs9 = check_plan(parse_plan(plan9), repo)
    expect(any(f.level == "warn" and "made-up" in f.detail for f in fs9),
           "编的车道 ⇒ 提醒")

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
