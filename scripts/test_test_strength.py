#!/usr/bin/env python3
"""两条新判据的回归：测试强度（变异测试）与向量集合属性自洽。

覆盖：
    test-strength  —— 把源码改坏，测试会不会红（默认不跑，开了才测）
    vector-schema  —— 同一集合的属性在调用点之间是否自相矛盾

能在干净临时目录里独立跑通（不依赖别的 skill 的绝对路径）。

    python3 scripts/test_test_strength.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "readiness.py"
FAILURES: list[str] = []
TMPDIRS: list[Path] = []

sys.path.insert(0, str(HERE))
import mutation_check  # noqa: E402
import readiness  # noqa: E402


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


def ts_repo(src: str, test: str) -> Path:
    d = mkdtemp()
    write(d / "package.json", json.dumps({"scripts": {"test": "node --test"}}))
    write(d / "src" / "calc.ts", src)
    write(d / "src" / "calc.test.ts", test)
    return d


def check_of(out: str, key: str) -> dict:
    data = json.loads(out)
    for c in data["checks"]:
        if c["key"] == key:
            return c
    return {}


# ---------------------------------------------------------------- 变异点


def test_mutation_points() -> None:
    print("\n变异点：只认代码，不碰字符串与注释")
    d = mkdtemp()
    p = write(d / "a.ts",
              "export const a = (x: number) => x >= 1;\n"
              "// 注释里的 == 不算\n"
              "const s = \"a == b\";\n"
              "export const t = s;\n"
              "export const b = (x: number) => x === 2;\n")
    muts = mutation_check.find_mutations(p)
    lines = sorted({m.line for m in muts})
    expect(lines == [1, 5], f"只找到代码里的两处（实际 {lines}）")
    expect(any(m.original == "===" for m in muts), "JS 严格相等 === 也在内")
    py = write(d / "b.py",
               "def f(x):\n    return x >= 1  # 注释里的 ==\n\n"
               "S = \"a == b\"\n\ndef g(x):\n    return x == 2\n")
    pmuts = mutation_check.find_mutations(py)
    expect(sorted({m.line for m in pmuts}) == [2, 7],
           f"Python 同理（实际 {sorted({m.line for m in pmuts})}）")


# ---------------------------------------------------------------- 变异检查


def test_strength() -> None:
    print("\n测试强度：改了源码，测试会不会红")
    # 默认不开 ⇒ 未实测
    d = ts_repo("export const isBig = (n: number) => n >= 10;\n",
                "import test from 'node:test';\nimport assert from 'node:assert';\n"
                "import { isBig } from './calc.ts';\n"
                "test('边界', () => { assert.equal(isBig(10), true); });\n")
    r = run("src", "--repo", str(d), "--json")
    c = check_of(r.stdout, "test-strength")
    expect(c.get("status") == "skipped", "默认不开 ⇒ 未实测")
    expect(c.get("evidence_kind") == "untested", "标成 untested（不是静态信号）")

    # 开了：变异被测试杀死 ⇒ pass
    r2 = run("src", "--repo", str(d), "--mutation-check", "--json")
    c2 = check_of(r2.stdout, "test-strength")
    expect(c2.get("status") == "pass",
           f"测到的变异被杀死 ⇒ pass（实际 {c2.get('status')}）")
    expect(c2.get("evidence_kind") == "executed", "是实测，不是静态信号")
    expect("样本有限" in (c2.get("detail") or ""),
           "说清样本有限 ⇒ 不等于“这套测试够强”")

    # 开了：全部杀不死 ⇒ fail
    d2 = ts_repo("export const untested = (n: number) => n >= 10;\n",
                 "import test from 'node:test';\ntest('占位', () => {});\n")
    r3 = run("src", "--repo", str(d2), "--mutation-check", "--json")
    c3 = check_of(r3.stdout, "test-strength")
    expect(c3.get("status") == "fail",
           f"杀不死任何变异 ⇒ fail（实际 {c3.get('status')}）")
    expect("杀不死" in (c3.get("detail") or ""), "说清是杀不死")

    # 开了：一半杀不死 ⇒ warn + 位置
    d3 = ts_repo(
        "export const isBig = (n: number) => n >= 10;\n"
        "export const untested = (n: number) => n <= 0;\n",
        "import test from 'node:test';\nimport assert from 'node:assert';\n"
        "import { isBig } from './calc.ts';\n"
        "test('边界', () => { assert.equal(isBig(10), true); });\n")
    r4 = run("src", "--repo", str(d3), "--mutation-check", "--json")
    c4 = check_of(r4.stdout, "test-strength")
    expect(c4.get("status") == "warn",
           f"部分杀不死 ⇒ warn（实际 {c4.get('status')}）")
    expect(any("calc.ts:" in e for e in c4.get("evidence") or []),
           "列出杀不死的位置")
    expect("门槛归项目" in (c4.get("detail") or ""),
           "不发明分数线（杀不杀得死多少算够归项目）")

    # 原仓库不能被碰
    src = d3 / "src" / "calc.ts"
    expect("n >= 10" in src.read_text(encoding="utf-8")
           and "n <= 0" in src.read_text(encoding="utf-8"),
           "原仓库的源码没被改坏")


# ---------------------------------------------------------------- 向量集合


def vector_repo(body: str) -> Path:
    d = mkdtemp()
    write(d / "package.json", json.dumps({"scripts": {"test": "node --test"}}))
    write(d / "src" / "mem.ts", body)
    write(d / "src" / "mem.test.ts",
          "import test from 'node:test';\ntest('占位', () => {});\n")
    return d


def test_vector_schema() -> None:
    print("\n向量集合：属性在调用点之间自不自洽")
    head = ("import { MilvusClient, DataType } from '@zilliz/milvus2-sdk-node';\n"
            "const client = new MilvusClient({ address: 'localhost:19530' });\n"
            "const COLLECTION = 'memories';\n")

    # 没有向量调用 ⇒ skipped
    d0 = vector_repo("export const x = 1;\n")
    c0 = check_of(run("src", "--repo", str(d0), "--json").stdout, "vector-schema")
    expect(c0.get("status") == "skipped", "没有向量调用 ⇒ skipped")

    # 维度不一致 ⇒ fail
    d1 = vector_repo(head + """
export async function ensure() {
  await client.createCollection({
    collection_name: COLLECTION, dimension: 1536, metric_type: 'COSINE',
    partition_key_field: 'tenant_id',
    fields: [{ fieldName: 'vector', dataType: DataType.FLOAT_VECTOR }],
  });
}
export async function search(v: number[]) {
  return client.search({ collection_name: COLLECTION, data: [v], dimension: 768 });
}
""")
    c1 = check_of(run("src", "--repo", str(d1), "--json").stdout, "vector-schema")
    expect(c1.get("status") == "fail", f"维度不一致 ⇒ fail（实际 {c1.get('status')}）")
    expect("维度" in " ".join(c1.get("evidence") or []), "点明是维度不一致")
    expect("memories" in " ".join(c1.get("evidence") or []),
           "常量形式的集合名被解析成真值")

    # 分区键指向向量字段 ⇒ fail
    d2 = vector_repo(head + """
export async function ensure() {
  await client.createCollection({
    collection_name: COLLECTION, dimension: 1536,
    partition_key_field: 'vector',
    fields: [{ fieldName: 'vector', dataType: DataType.FLOAT_VECTOR }],
  });
}
""")
    c2 = check_of(run("src", "--repo", str(d2), "--json").stdout, "vector-schema")
    expect(c2.get("status") == "fail", "分区键是向量字段 ⇒ fail")
    expect("向量字段" in " ".join(c2.get("evidence") or []), "说清是向量字段")

    # 写读集合不同 ⇒ warn
    d3 = vector_repo(head + """
export async function write(v: number[]) {
  await client.upsert({ collection_name: COLLECTION, data: [{ id: '1', vector: v }] });
}
export async function read(v: number[]) {
  return client.search({ collection_name: 'memory_index', data: [v] });
}
""")
    c3 = check_of(run("src", "--repo", str(d3), "--json").stdout, "vector-schema")
    expect(c3.get("status") == "warn",
           f"写读集合不同 ⇒ warn（实际 {c3.get('status')}）")
    expect("memory_index" in " ".join(c3.get("evidence") or []), "列出两个集合")

    # 属性一致 ⇒ pass
    d4 = vector_repo(head + """
export async function ensure() {
  await client.createCollection({
    collection_name: COLLECTION, dimension: 1536, metric_type: 'COSINE',
    partition_key_field: 'tenant_id',
    fields: [{ fieldName: 'vector', dataType: DataType.FLOAT_VECTOR }],
  });
}
export async function write(v: number[]) {
  await client.upsert({ collection_name: COLLECTION, data: [{ id: '1', vector: v }] });
}
""")
    c4 = check_of(run("src", "--repo", str(d4), "--json").stdout, "vector-schema")
    expect(c4.get("status") == "pass",
           f"属性一致 ⇒ pass（实际 {c4.get('status')}）")
    expect(c4.get("evidence_kind") == "static",
           "静态信号（不能单独支撑「通过」）")


def main() -> int:
    test_mutation_points()
    test_strength()
    test_vector_schema()
    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 条：")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        for d in TMPDIRS:
            import shutil
            shutil.rmtree(d, ignore_errors=True)
    sys.exit(code)
