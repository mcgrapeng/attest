#!/usr/bin/env python3
"""round-16 回归测试：交付就绪度命令（readiness）。

重点：工具必须**实测**，且判据不能有假阳性。
"""
from __future__ import annotations

import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
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


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONUTF8": "1"})



def _blocking(out: str) -> int:
    """结论行里的阻塞项数。

    结论措辞从「不可交付 / 达到交付条件」改成三档（阻塞项 / 待人工确认 / 无信号），
    所以断言改成数阻塞项，而不是匹配旧措辞。
    """
    m = re.search(r"结论：(\d+) 项阻塞", out)
    return int(m.group(1)) if m else -1


def _block(out: str, title: str) -> str:
    """取某条判据自己的那一段（到下一条判据或下一节为止）。

    报告改成证据分节后，"标题后 200 字符"会跨节边界，把别的判据的内容算进来。
    """
    if title not in out:
        return ""
    rest = out.split(title, 1)[1]
    m = re.search(r"\n\s{2}[✓✗·—○]|\n──", rest)
    return rest[:m.start()] if m else rest

def main() -> int:
    # C locale 下 stdout 默认 ascii，打中文会 UnicodeEncodeError
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    tmp = mkdtemp()
    write(tmp / "package.json", json.dumps({
        "scripts": {"test": "node --test 'src/**/*.test.ts'"}}))
    write(tmp / "src" / "calc.ts",
          "export function add(a: number, b: number): number { return a + b }\n"
          "export const MAX = 100;\n")
    write(tmp / "src" / "calc.test.ts",
          "import test from 'node:test';\n"
          "import assert from 'node:assert/strict';\n"
          "import { add } from './calc.ts';\n"
          "test('add', () => { assert.equal(add(1, 2), 3); });\n")
    write(tmp / "src" / "helper.ts",
          "export function unusedHelper(): string { return 'x' }\n")

    print("基本跑通")
    r = run("src", "--repo", str(tmp))
    expect(r.returncode in (0, 1), "命令跑完了（有失败项时退出码 1）")
    expect("交付就绪度分析" in r.stdout, "输出了报告")
    expect("测试全绿" in r.stdout, "有「测试全绿」判据")

    print("\n测试失败 ⇒ 报红")
    write(tmp / "src" / "broken.test.ts",
          "import test from 'node:test';\n"
          "import assert from 'node:assert/strict';\n"
          "test('broken', () => { assert.equal(1, 2); });\n")
    r2 = run("src", "--repo", str(tmp))
    expect("测试全绿" in r2.stdout, "有失败用例时被判出来")
    expect(r2.returncode == 1, "有失败 ⇒ 退出码 1")
    (tmp / "src" / "broken.test.ts").unlink()

    print("\n覆盖率是实测的（不是编的）")
    r3 = run("src", "--repo", str(tmp))
    expect("行覆盖" in r3.stdout or "核心逻辑测试到位" in r3.stdout,
           "报了覆盖率")
    expect("用项目自己的 test 命令" in r3.stdout,
           "说明用了项目自己的测试命令（不是裸 node）")

    print("\n零引用的导出 ⇒ 报出来")
    expect("unusedHelper" in r3.stdout, "没人用的导出被点名")

    print("\n项目命令里没有可执行文件时也不崩")
    write(tmp / "package.json", json.dumps({
        "scripts": {"test": "definitely-not-a-real-runner --test"}}))
    r4 = run("src", "--repo", str(tmp))
    expect(r4.returncode in (0, 1), "命令跑完了（没崩）")
    expect("Traceback" not in r4.stderr, "没有 traceback")

    print("\n模块不存在 ⇒ 明确报错")
    r5 = run("src/nope", "--repo", str(tmp))
    expect(r5.returncode == 2, "模块不存在时退出码 2")

    print("\nJSON 输出可用")
    r6 = run("src", "--repo", str(tmp), "--json")
    expect(r6.returncode in (0, 1), "json 模式跑完")
    try:
        data = json.loads(r6.stdout)
        expect(isinstance(data.get("checks"), list) and len(data["checks"]) > 0,
               "json 里有判据列表")
        expect(all("key" in c and "status" in c for c in data["checks"]),
               "每条判据都有 key 与 status")
    except json.JSONDecodeError:
        expect(False, "json 能解析")

    print("\n覆盖率是确定的（同样命令跑两次，数字一样）")
    write(tmp / "package.json", json.dumps({
        "scripts": {"test": "node --test 'src/**/*.test.ts'"}}))
    ra = run("src", "--repo", str(tmp))
    rb = run("src", "--repo", str(tmp))

    def cov_of(proc: subprocess.CompletedProcess) -> str:
        for ln in proc.stdout.splitlines():
            if "核心逻辑测试到位" in ln:
                return ln.split("—")[1].strip() if "—" in ln else ln
        return ""

    expect(cov_of(ra) and cov_of(ra) == cov_of(rb),
           "两次跑的覆盖率结论一致")
    expect("平均行覆盖" in ra.stdout or "行覆盖" in ra.stdout,
           "报了覆盖率数字")

    print("\n数据流一致：实跑模块自己的对账脚本")
    repo = mkdtemp()
    write(repo / "package.json", json.dumps({"scripts": {
        "test": "node --test 'src/**/*.test.ts'",
        "data:reconcile": "node scripts/reconcile.js"}}))
    write(repo / "src" / "store.ts",
          "import { pgTable } from 'drizzle-orm/pg-core';\n"
          "export const widgets = pgTable('widgets', {});\n")
    write(repo / "src" / "calc.test.ts",
          "import test from 'node:test';\n"
          "import assert from 'node:assert/strict';\n"
          "test('ok', () => { assert.equal(1, 1); });\n")
    write(repo / "scripts" / "reconcile.js",
          "// 对账 widgets 表与向量库\n"
          "console.log('| 租户 | 缺向量 | 判定 |');\n"
          "console.log('|---|---:|---|');\n"
          "console.log('| default | 0 | ✅ 一致 |');\n"
          "console.log('| t1 | 3 | ❌ 漂移 |');\n")
    r7 = run("src", "--repo", str(repo))
    expect("数据流一致" in r7.stdout, "有「数据流一致」判据")
    expect("漂移" in r7.stdout, "漂移被报出来")
    expect("| default | 0 | ✅ 一致 |" in r7.stdout, "一致的租户也列出来（做上下文）")

    print("\n数据流一致：一致时通过")
    write(repo / "scripts" / "reconcile.js",
          "// 对账 widgets 表与向量库\n"
          "console.log('| default | 0 | ✅ 一致 |');\n")
    r8 = run("src", "--repo", str(repo))
    expect("❌ 漂移" not in r8.stdout, "没有漂移就不报漂移")
    expect("生产租户无漂移" in r8.stdout, "一致时判据通过（生产租户无漂移）")

    print("\n数据准确：没有产物 ≠ 指标不达标")
    write(repo / "package.json", json.dumps({"scripts": {
        "test": "node --test 'src/**/*.test.ts'",
        "eval:baseline:check": "node scripts/baseline.js"}}))
    write(repo / "scripts" / "baseline.js",
          "console.log('没有找到 artifacts/e2e-eval —— 先跑 eval 套件再对拍。');\n"
          "process.exit(1);\n")
    r9 = run("src", "--repo", str(repo))
    expect("指标不达标" not in r9.stdout, "缺产物没有被当成数据回归")
    expect("缺产物" in r9.stdout, "说明是缺产物")

    print("\n数据准确：真回归 ⇒ 报红")
    write(repo / "scripts" / "baseline.js",
          "console.log('recall@10 低于基线：0.42 < 0.61');\n"
          "process.exit(1);\n")
    r10 = run("src", "--repo", str(repo))
    expect("指标不达标" in r10.stdout, "真的指标回归被报红")

    print("\n跨存储字段级比对：同一个 id 的同一字段对不上 ⇒ 报出来")
    write(repo / "package.json", json.dumps({"scripts": {
        "test": "node --test 'src/**/*.test.ts'",
        "data:reconcile": "node scripts/reconcile.js"}}))
    write(repo / "scripts" / "reconcile.js",
          "// 对账 widgets 表与向量库\n"
          "const fs = require('fs');\n"
          "const dir = process.env.MEMORY_RECONCILE_OUT_DIR || '.';\n"
          "fs.mkdirSync(dir, { recursive: true });\n"
          "fs.writeFileSync(dir + '/pg.csv',\n"
          "  'id,tenant_id,scope,text\\n\"m1\",\"t1\",\"s1\",\"hello\"\\n');\n"
          "fs.writeFileSync(dir + '/vec.csv',\n"
          "  'id,tenant_id,scope,text\\n\"m1\",\"t1\",\"s1\",\"H E L L O\"\\n');\n"
          "console.log('| default | 0 | ✅ 一致 |');\n")
    r11 = run("src", "--repo", str(repo))
    expect("字段不一致" in r11.stdout, "字段级不一致被报出来")
    expect("m1" in r11.stdout, "点名了哪个 id 的哪个字段")

    print("\n只碰 PG 一个存储 ⇒ 不报「没有对账机制」")
    pg_only = mkdtemp()
    write(pg_only / "package.json", json.dumps({
        "scripts": {"test": "node --test 'src/**/*.test.ts'"}}))
    write(pg_only / "src" / "store.ts",
          "import { pgTable } from 'drizzle-orm/pg-core';\n"
          "export const widgets = pgTable('widgets', {});\n")
    write(pg_only / "src" / "calc.test.ts",
          "import test from 'node:test';\n"
          "import assert from 'node:assert/strict';\n"
          "test('ok', () => { assert.equal(1, 1); });\n")
    r12 = run("src", "--repo", str(pg_only))
    expect("只碰了 PG 一个存储" in r12.stdout, "单存储模块判 skipped")
    expect(_blocking(r12.stdout) == 0, "单存储模块不该被判不可交付")

    print("\n有派生存储（向量库）却没有对账机制 ⇒ 报红")
    write(pg_only / "src" / "vec.ts",
          "export function upsert(v: string) { return v }\n")
    write(pg_only / "src" / "vec-store.ts",
          "// MilvusClient createCollection upsert\n"
          "export const client = null as unknown as { search(q: string): void };\n")
    r13 = run("src", "--repo", str(pg_only))
    expect("派生存储却没有对账机制" in r13.stdout, "派生存储缺对账被报红")

    print("\n识别不出源码 ⇒ 退出码 2（不能给判定）")
    empty = mkdtemp()
    write(empty / "package.json", json.dumps({"scripts": {"test": "true"}}))
    (empty / "src").mkdir()
    r14 = run("src", "--repo", str(empty))
    expect(r14.returncode == 2, "没识别出源码时退出码 2")

    print("\nPython 工程：覆盖率与导出引用都能判")
    py = mkdtemp()
    write(py / "pyproject.toml",
          "[tool.pytest.ini_options]\ntestpaths = [\"test\"]\n")
    write(py / "src" / "pkg" / "__init__.py", "")
    write(py / "src" / "pkg" / "mod.py",
          "def used_by_tests():\n    return 1\n\n\n"
          "def never_tested():\n    return 2\n\n\n"
          "class PureData:\n    name: str\n")
    write(py / "test" / "test_mod.py", "from pkg.mod import used_by_tests\n")
    # 假 pytest：按 --cov-report=json: 落一份覆盖率
    fake = py / ".venv" / "bin" / "pytest"
    write(fake, "#!/bin/sh\n"
                "out=''\n"
                "for a in \"$@\"; do\n"
                "  case \"$a\" in --cov-report=json:*) "
                "out=\"${a#--cov-report=json:}\";; esac\n"
                "done\n"
                "mkdir -p \"$(dirname \"$out\")\"\n"
                "printf '%s' '{\"files\": {\"pkg/mod.py\": "
                "{\"summary\": {\"covered_lines\": 2, \"num_statements\": 2, "
                "\"percent_covered\": 100.0, \"num_branches\": 0, "
                "\"covered_branches\": 0}, \"missing_lines\": []}}}' > \"$out\"\n"
                "echo '1 passed'\n")
    fake.chmod(0o755)
    r15 = run("src", "--repo", str(py))
    expect("核心逻辑测试到位" in r15.stdout, "Python 有覆盖率判据")
    expect("never_tested" in r15.stdout, "Python 零引用的 def 被点名")
    expect("used_by_tests" not in _block(r15.stdout, "导出的逻辑都有测试"),
           "有引用的 def 不被点名")
    expect("PureData" not in r15.stdout, "纯数据类不当成待测导出")

    print("\n测试卫生：固定等待与不清理都被报出来")
    hyg = mkdtemp()
    write(hyg / "package.json", json.dumps({
        "scripts": {"test": "node --test 'src/**/*.test.ts'"}}))
    write(hyg / "src" / "store.ts",
          "import { pgTable } from 'drizzle-orm/pg-core';\n"
          "export const widgets = pgTable('widgets', {});\n")
    write(hyg / "src" / "dirty.test.ts",
          "import test from 'node:test';\n"
          "test('dirty', async () => {\n"
          "  await tx.insert(widgets).values({ id: '1' });\n"
          "  await sleep(1500);\n"
          "});\n")
    r16 = run("src", "--repo", str(hyg))
    expect("测试卫生" in r16.stdout, "有测试卫生判据")
    expect("固定等待" in r16.stdout, "固定等待被报出来")
    expect("残留" in r16.stdout, "不清理被报出来")

    print("\n测试卫生：显式声明 attest:no-db-writes 的文件不算写库残留")
    hyg2 = mkdtemp()
    write(hyg2 / "package.json", json.dumps({
        "scripts": {"test": "node --test 'src/**/*.test.ts'"}}))
    write(hyg2 / "src" / "store.ts",
          "import { pgTable } from 'drizzle-orm/pg-core';\n"
          "export const widgets = pgTable('widgets', {});\n")
    # 假 tx：每个用例重建，不写真库 ⇒ 声明豁免
    write(hyg2 / "src" / "faketx.test.ts",
          "import test from 'node:test';\n"
          "/* attest:no-db-writes reason=\"假 tx 每个用例重建，不写真库\" */\n"
          "test('fake tx', async () => {\n"
          "  await tx.insert(widgets).values({ id: '1' });\n"
          "});\n")
    r17 = run("src", "--repo", str(hyg2))
    expect("有写库动作" not in _block(r17.stdout, "测试卫生"),
           "声明了豁免 ⇒ 不报写库残留")
    expect("attest:no-db-writes" in r17.stdout, "豁免标记进报告（可评审）")
    expect("假 tx 每个用例重建" in r17.stdout, "豁免理由进报告")

    print("\n测试卫生：豁免没给理由 ⇒ 报错")
    hyg3 = mkdtemp()
    write(hyg3 / "package.json", json.dumps({
        "scripts": {"test": "node --test 'src/**/*.test.ts'"}}))
    write(hyg3 / "src" / "store.ts",
          "import { pgTable } from 'drizzle-orm/pg-core';\n"
          "export const widgets = pgTable('widgets', {});\n")
    write(hyg3 / "src" / "noreason.test.ts",
          "import test from 'node:test';\n"
          "<!-- attest:no-db-writes reason=\"\" -->\n"
          "test('no reason', async () => {\n"
          "  await tx.insert(widgets).values({ id: '1' });\n"
          "});\n")
    r18 = run("src", "--repo", str(hyg3))
    expect("没给理由" in r18.stdout, "豁免缺理由被报出来")
    expect("dirty.test.ts" in r16.stdout, "点名了哪个文件")

    print("\n真后端车道：环境没就绪 ≠ 用例失败")
    itst = mkdtemp()
    write(itst / "package.json", json.dumps({"scripts": {
        "test": "node --test 'src/**/*.test.ts'",
        "test:integration:memory": "node scripts/lane.js src/orders/x.itest.ts"}}))
    write(itst / "src" / "store.ts",
          "import { pgTable } from 'drizzle-orm/pg-core';\n"
          "export const widgets = pgTable('widgets', {});\n")
    write(itst / "src" / "orders" / "x.itest.ts", "export const x = 1;\n")
    write(itst / "scripts" / "lane.js",
          "// 假车道：报一个连不上 Milvus 的失败\n"
          "console.log('not ok 1 - 三方数据流对账 # SKIP');\n"
          "console.log('  ---');\n"
          "console.log(\"  error: '14 UNAVAILABLE: No connection established.\"\n"
          "  + \" Last error: Error: connect ECONNREFUSED 127.0.0.1:19999'\");\n"
          "console.log('# tests 1');\n"
          "console.log('# pass 0');\n"
          "console.log('# fail 1');\n"
          "console.log('# skipped 0');\n"
          "process.exit(1);\n")
    r17 = run("src", "--repo", str(itst), "--include-itest")
    expect("环境没就绪" in r17.stdout, "真后端车道的环境问题被认出来")
    expect("连不上" in r17.stdout, "说清是连不上 Milvus/PG")

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
