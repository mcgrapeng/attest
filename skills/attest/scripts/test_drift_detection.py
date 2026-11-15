#!/usr/bin/env python3
"""回归测试：漂移识别的**表结构感知**。

审计发现（原实现：stdout 里任何含「漂移」的行都算漂移行，不认表结构）：

1. **假红**：报告的说明文字里带「漂移」二字（例
   `### 多余向量（2 条：PG 行还在但已非 active ⇒ 真漂移）`），而总览表里
   每个租户都是 ✅ 一致 ⇒ 判定器把那句说明当成一条漂移，报「1 个生产租户漂移」。
2. **双计数**：一个租户真漂移时，表里一行 `❌ 漂移` + 散文一行「真漂移」
   ⇒ 报「2 个生产租户漂移」，其中一行的租户归属还是错的。
3. **假绿**（文本抓取的天生边界，改不了）：报告把行级判据换成别的说法
   （既无 ❌ 也无「漂移」）⇒ 判定器看不见。

现在：报告是**表格形态**时，漂移只从表里的行认；没有表格的纯文本报告保持旧行为
（别让别人的报告认不出）。

    python3 scripts/test_drift_detection.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "readiness.py"
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


def repo_with_reconciler(report: str) -> Path:
    """一个会碰 PG + 向量库、且带对账脚本的小仓库。"""
    d = mkdtemp()
    write(d / "package.json", json.dumps({
        "name": "drift-audit",
        "scripts": {
            "test": "node --test",
            "data:reconcile:memory": "node scripts/reconcile.js src/pkg",
        },
    }))
    write(d / "src" / "pkg" / "store.ts",
          "import { pgTable } from 'drizzle-orm/pg-core';\n"
          "import { MilvusClient } from '@zilliz/milvus2-sdk-node';\n"
          "export const widgets = pgTable('widgets', {});\n"
          "export async function up(id: string) {\n"
          "  const c = new MilvusClient({ address: 'localhost:19530' });\n"
          "  return c.upsert({ collection_name: 'mem', data: [{ id }] });\n"
          "}\n")
    # 脚本正文要提到模块路径 —— 判定器靠它把脚本认成"这个模块的对账机制"
    write(d / "scripts" / "reconcile.js",
          "// reconcile src/pkg widgets\n" + report)
    return d


def run(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "src", "--repo", str(repo)],
                          capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONUTF8": "1"})


def line_of(out: str, needle: str) -> str:
    for ln in out.splitlines():
        if needle in ln:
            return ln
    return ""


TABLE_REPORT = """console.log('# 对账');
console.log('');
console.log('## 总览');
console.log('');
console.log('| 租户 | PG 总行 | 向量数 | 缺向量 | 正文不一致 | 判定 |');
console.log('|---|---|---|---|---|---|');
console.log('| default | 10 | 10 | 0 | 0 | {marker} |');
console.log('');
console.log('## 租户 default');
console.log('');
console.log('### 多余向量（2 条：PG 行还在但已非 active ⇒ 真漂移）');
console.log('');
console.log('| id | scope | PG 状态 |');
console.log('|---|---|---|');
console.log('| m1 | s | inactive |');
console.log('| m2 | s | inactive |');
"""


def test_prose_line_is_not_drift() -> None:
    print("\n散文里的「漂移」不是漂移行")
    repo = repo_with_reconciler(TABLE_REPORT.format(marker="✅ 一致"))
    r = run(repo)
    ln = line_of(r.stdout, "数据流一致（跨存储对账） —")
    expect("生产租户无漂移" in ln,
           "表里全是 ✅ 一致 ⇒ 判无漂移（不把散文标题当漂移）")
    expect("✗" not in ln, "不报红")


def test_one_drifted_tenant_counts_once() -> None:
    print("\n一个租户真漂移 ⇒ 只算一个")
    repo = repo_with_reconciler(TABLE_REPORT.format(marker="❌ 漂移"))
    r = run(repo)
    ln = line_of(r.stdout, "数据流一致（跨存储对账） —")
    expect("1 个**生产租户**的数据流漂移" in ln,
           "报 1 个生产租户（不是 2 个：表里一行 + 散文一行）")
    expect("2 个**生产租户**" not in ln, "没有把一次漂移数成两个")


def test_plain_text_report_still_detected() -> None:
    print("\n没有表格的纯文本报告 ⇒ 保持旧行为（别让别人的报告认不出）")
    plain = """console.log('default: 3 rows drifted');
console.log('qa-tenant: 0 rows drifted');
"""
    repo = repo_with_reconciler(plain)
    r = run(repo)
    ln = line_of(r.stdout, "数据流一致（跨存储对账） —")
    expect("生产租户" in ln and "无漂移" not in ln,
           "纯文本报告里的 drift 行仍被认出来")
    print("  （纯文本报告没有表结构可认，只能这么粗 —— 但不该更粗："
          "行里出现 tenant/租户/一致性 不再让整行被丢掉。）")


def test_plain_text_line_with_tenant_is_detected() -> None:
    print("\n纯文本报告里含 tenant 的漂移行 ⇒ 也要认出来（不能被当表头丢掉）")
    plain = """console.log('tenant default: 3 rows drifted');
console.log('tenant qa-tenant: 0 rows drifted');
"""
    repo = repo_with_reconciler(plain)
    r = run(repo)
    ln = line_of(r.stdout, "数据流一致（跨存储对账） —")
    expect("无漂移" not in ln,
           "含 tenant 的漂移行没被静默吞掉")
    expect("1 个" in ln, "报 1 个生产租户（qa-tenant 那个是测试租户，不阻塞）")


def test_data_row_with_wording_is_not_dropped() -> None:
    print("\n数据行里含「正文不一致」⇒ 不能被当成表头丢掉")
    # 这行含「一致性」—— 旧实现按子串匹配把它当表头排除了（真漂移被吞掉）
    report = """console.log('| 租户 | 正文不一致 | 判定 |');
console.log('|---|---|---|');
console.log('| default | 2 | 正文不一致 |');
console.log('| default | 2 | ❌ 漂移 |');
"""
    repo = repo_with_reconciler(report)
    r = run(repo)
    ln = line_of(r.stdout, "数据流一致（跨存储对账） —")
    expect("1 个**生产租户**的数据流漂移" in ln,
           "带「正文不一致」的漂移行被认出来，且只算 1 个租户")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    test_prose_line_is_not_drift()
    test_one_drifted_tenant_counts_once()
    test_plain_text_report_still_detected()
    test_plain_text_line_with_tenant_is_detected()
    test_data_row_with_wording_is_not_dropped()
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
