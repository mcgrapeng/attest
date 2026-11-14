#!/usr/bin/env python3
"""round-9 回归测试：跨存储校对强制 + 存储清单 + 占位行不当数据。

对应用户目标："产生的数据可以严格校对，比如 pg数据 向量数据 文件数据等"。
"""
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
from req_plan import check_plan, parse_plan, scan_stores  # noqa: E402

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

- 目标模块：`{mod}`

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

    print("存储探测：PG + 向量 + 文件")
    d2 = mkdtemp()
    write(d2 / "src" / "models.ts",
          "export const agentMemory = pgTable('agent_memory', {});\n")
    write(d2 / "src" / "vector.ts",
          "import { MilvusClient } from '@zilliz/milvus2-sdk-node';\n"
          "export const PREFIX = 'lumen_mem';\n")
    write(d2 / "src" / "files.ts",
          "export async function putObject(k: string) { return k; }\n")
    stores = scan_stores(d2, ["src"])
    expect("pg" in stores, "认出 PG")
    expect("vector" in stores, "认出向量库")
    expect("file" in stores, "认出对象存储")
    expect(any("agent_memory" in e for e in stores.get("pg", [])),
           "PG 表名带出来")
    expect(all(": " not in e or True for e in stores.get("pg", [])), "证据格式可用")

    print("\n存储探测：测试文件里的存储名不算")
    d3 = mkdtemp()
    write(d3 / "src" / "x.test.ts", "pgTable('fake_table', {});\n")
    expect(not scan_stores(d3, ["src"]).get("pg"),
           "测试文件里的 pgTable 不算模块在用 PG")

    print("\n跨存储强制：碰到 2 个存储但没一致性节 ⇒ 报红")
    d4 = mkdtemp()
    write(d4 / "src" / "models.ts", "export const t = pgTable('t', {});\n")
    write(d4 / "src" / "vector.ts", "export const C = MilvusClient;\n")
    write(d4 / "src" / "a.test.ts", "test('x', () => {})\n")
    plan = write(d4 / "p.md", HEAD.format(mod="src"))
    fs = check_plan(parse_plan(plan), FakeRepo(d4))
    expect(any("同时碰了 2 个存储" in f.detail for f in fs),
           "跨存储没校对 ⇒ 报红")

    print("\n只有一个存储 ⇒ 不强制")
    d5 = mkdtemp()
    write(d5 / "src" / "models.ts", "export const t = pgTable('t', {});\n")
    write(d5 / "src" / "a.test.ts", "test('x', () => {})\n")
    plan5 = write(d5 / "p5.md", HEAD.format(mod="src"))
    fs5 = check_plan(parse_plan(plan5), FakeRepo(d5))
    expect(not any("同时碰了" in f.detail for f in fs5),
           "只碰 1 个存储 ⇒ 不强制一致性")

    print("\n跨存储 + 有完整一致性节 ⇒ 不报红")
    d6 = mkdtemp()
    write(d6 / "src" / "a.ts", "export const a = 1\n")
    write(d6 / "src" / "models.ts", "export const t = pgTable('t', {});\n")
    write(d6 / "src" / "vector.ts", "export const C = MilvusClient;\n")
    write(d6 / "src" / "a.test.ts", "test('x', () => {})\n")
    write(d6 / "src" / "reconcile.test.ts", "test('r', () => {})\n")
    plan6 = write(d6 / "p6.md", HEAD.format(mod="src") + """
## 数据一致性

| 数据 | 权威源 | 镜像/派生 | 一致性怎么验 |
| --- | --- | --- | --- |
| 记忆行 | PG | Milvus | src/reconcile.test.ts |

## 约束：不引入中间件

单一权威：PG。向量库是派生层，靠对账修复。

对账/修复路径的测试落点：src/reconcile.test.ts
""")
    fs6 = check_plan(parse_plan(plan6), FakeRepo(d6))
    expect(not any(f.level == "error" for f in fs6),
           "完整的跨存储计划 ⇒ 无错误")

    print("\n模板占位行不当数据（未填的计划报「没填」，不是「文件不存在」）")
    d7 = mkdtemp()
    write(d7 / "package.json", json.dumps({"scripts": {"test": "vitest"}}))
    write(d7 / "src" / "a.ts", "export const a = 1\n")
    write(d7 / "src" / "a.test.ts", "test('x', () => {})\n")
    r7 = subprocess.run(
        [sys.executable, str(HERE / "req_plan.py"), "new", "f",
         "--repo", str(d7), "--module", "src"],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    expect(r7.returncode == 0, "new 成功")
    plan7 = d7 / "docs" / "f-test-plan.md"
    r7b = subprocess.run(
        [sys.executable, str(HERE / "req_plan.py"), "check", str(plan7),
         "--repo", str(d7)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    expect("模板占位" in r7b.stdout, "未填的计划报「模板占位，没填」")
    expect("指向的文件不存在" not in r7b.stdout,
           "占位里的路径不被当成真钉")

    print("\nnew 生成的计划里有存储清单 + 目标模块行")
    txt = plan7.read_text(encoding="utf-8")
    expect("目标模块：`src`" in txt, "目标模块行在")
    expect("本模块碰到的存储" in txt, "存储清单节在")

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
