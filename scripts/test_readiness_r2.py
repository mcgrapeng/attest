#!/usr/bin/env python3
"""round-2 回归测试：交付判定的正确性（readiness）。

覆盖 brief「G001 交付判定正确性与执行面收敛」的 17 条子项，每条至少
一条断言。能在干净临时目录里独立跑通（不依赖别的 skill 的绝对路径）。

    python3 scripts/test_readiness_r2.py
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

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "readiness.py"
FAILURES: list[str] = []
TMPDIRS: list[Path] = []

sys.path.insert(0, str(HERE))
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


def run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    e = {**os.environ, "PYTHONUTF8": "1"}
    if env:
        e.update(env)
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          env=e)


def line_of(out: str, needle: str) -> str:
    """报告里包含 needle 的那一行。"""
    for ln in out.splitlines():
        if needle in ln:
            return ln
    return ""



def _blocking(out: str) -> int:
    """结论行里的阻塞项数。

    结论措辞从「不可交付 / 达到交付条件」改成三档（阻塞项 / 待人工确认 / 无信号），
    所以断言改成数阻塞项，而不是匹配旧措辞。
    """
    m = re.search(r"结论：(\d+) 项阻塞", out)
    return int(m.group(1)) if m else -1

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    # ---------------------------------------------------------- 子项 1
    print("子项 1：零测试的模块不得判「达到交付条件」")
    zero = mkdtemp()
    write(zero / "package.json", json.dumps({"scripts": {"test": "node --test"}}))
    write(zero / "src" / "calc.ts",
          "export function add(a: number, b: number): number { return a + b }\n")
    r = run("src", "--repo", str(zero))
    expect("没有找到任何测试文件" in r.stdout,
           "零测试 ⇒ 测试全绿判 skipped 并说清为什么")
    expect("✓ 测试全绿" not in r.stdout, "零测试不得打 ✓（不空转 pass）")
    expect("达到交付条件" not in r.stdout,
           "零测试的整体结论不得是「达到交付条件」")
    expect(r.returncode != 0, "零测试 ⇒ 退出码非 0")

    # ---------------------------------------------------------- 子项 2
    print("\n子项 2：测试发现覆盖仓库级 test/ tests/ spec/")
    layout = mkdtemp()
    write(layout / "package.json", json.dumps(
        {"scripts": {"test": "node --test"}}))
    write(layout / "src" / "calc.js",
          "export function add(a, b) { return a + b }\n")
    write(layout / "test" / "calc.test.js",
          "import test from 'node:test';\n"
          "import { add } from '../src/calc.js';\n"
          "test('add', () => { if (add(1, 2) !== 3) throw new Error('x'); });\n")
    r2 = run("src", "--repo", str(layout))
    expect("单测 1 个" in r2.stdout,
           "仓库级 test/ 下的测试被算成这个模块的测试")
    expect("零引用" not in r2.stdout,
           "test/ 布局下不再报「导出零引用」（之前是 1/1 零引用）")
    expect(_blocking(r2.stdout) == 0, "test/ 布局的健康仓库不被判不可交付")

    print("\n子项 2：--test-dir 显式指定测试目录")
    qa = mkdtemp()
    write(qa / "package.json", json.dumps({"scripts": {"test": "node --test"}}))
    write(qa / "src" / "calc.ts",
          "export function add(a: number, b: number): number { return a + b }\n")
    write(qa / "qa" / "calc.test.js",
          "import test from 'node:test';\n"
          "import { add } from '../src/calc.js';\n"
          "test('add', () => { if (add(1, 2) !== 3) throw new Error('x'); });\n")
    r3 = run("src", "--repo", str(qa), "--test-dir", "qa")
    expect("单测 1 个" in r3.stdout, "--test-dir 指定的目录里的测试被找到")

    # ---------------------------------------------------------- 子项 3
    print("\n子项 3：覆盖率 include glob 按实际扩展名推导")
    m = readiness.Module(root=zero, path="src", base=zero / "src")
    m.sources = [zero / "src" / "a.js", zero / "src" / "b.js"]
    globs = readiness.coverage_globs(m, "src")
    expect(globs == ["src/**/*.js"], "只有 .js 时 include glob 是 src/**/*.js")
    m.sources = [zero / "src" / "a.ts", zero / "src" / "b.tsx"]
    globs2 = readiness.coverage_globs(m, "src")
    expect(globs2 == ["src/**/*.ts", "src/**/*.tsx"],
           "多种扩展名各给一条 glob")
    expect(not any("*.ts" == g.split("**/")[-1] for g in globs),
           "JavaScript 模块不再被写死成 **/*.ts")

    # ---------------------------------------------------------- 子项 4
    print("\n子项 4：依赖抽取覆盖 require / import / Python import")
    js_specs = readiness._import_specs(
        "const a = require('lodash');\nimport 'side-effect-pkg';\n"
        "import b from 'from-pkg';\n", ".js")
    expect("lodash" in js_specs, "require('x') 被认出来")
    expect("side-effect-pkg" in js_specs, "import 'x' 被认出来")
    expect("from-pkg" in js_specs, "from 'x' 被认出来")
    py_specs = readiness._import_specs(
        "import os\nimport requests\nfrom fastapi import FastAPI\n"
        "from . import local\n", ".py")
    expect("requests" in py_specs, "Python 的 import x 被认出来")
    expect("fastapi" in py_specs, "Python 的 from x import y 被认出来")
    expect("os" not in py_specs, "Python 标准库不算第三方依赖")
    expect("local" not in py_specs, "Python 相对导入不算第三方依赖")

    print("\n子项 4：Python 仓库不再报「模块没有第三方依赖」")
    pyrepo = mkdtemp()
    write(pyrepo / "pyproject.toml",
          "[tool.pytest.ini_options]\ntestpaths = [\"test\"]\n")
    write(pyrepo / "src" / "pkg" / "__init__.py", "")
    write(pyrepo / "src" / "pkg" / "mod.py",
          "import requests\n\n\ndef used_by_tests():\n    return 1\n")
    write(pyrepo / "test" / "test_mod.py",
          "from pkg.mod import used_by_tests\n")
    r4 = run("src", "--repo", str(pyrepo))
    expect("模块没有第三方依赖" not in r4.stdout,
           "Python 仓库不再误报「没有第三方依赖」")
    expect("requests" in r4.stdout, "Python 的第三方依赖被列出来")

    # ---------------------------------------------------------- 子项 5
    print("\n子项 5：Python 下导出判据不空转")
    r5 = run("src", "--repo", str(pyrepo))
    no_export_py = mkdtemp()
    write(no_export_py / "pyproject.toml",
          "[tool.pytest.ini_options]\ntestpaths = [\"test\"]\n")
    write(no_export_py / "src" / "pkg" / "__init__.py", "")
    write(no_export_py / "src" / "pkg" / "mod.py",
          "def _private():\n    return 1\n")
    write(no_export_py / "test" / "test_mod.py", "def test_x():\n    assert True\n")
    r5b = run("src", "--repo", str(no_export_py))
    expect("0 个导出符号都有多个调用点" not in r5b.stdout,
           "不过度抽象不得输出「0 个导出符号都有多个调用点」")
    expect("模块的导出符号在全仓都是独一份" not in r5b.stdout,
           "没有重复造轮子不得输出「导出符号在全仓都是独一份」")
    empty_exports = mkdtemp()
    write(empty_exports / "package.json", json.dumps({"scripts": {"test": "true"}}))
    write(empty_exports / "src" / "only.ts", "const x = 1;\n")
    r5c = run("src", "--repo", str(empty_exports))
    expect("0 个导出符号都有多个调用点" not in r5c.stdout,
           "零导出的模块也不打「都有多个调用点」")

    # ---------------------------------------------------------- 子项 6
    print("\n子项 6：表设计给租户列/唯一约束留豁免")
    facts = {"widgets": {
        "columns": ["id\x1fYES\x1finteger", "name\x1fYES\x1ftext"],
        "indexes": ["widgets_pkey"], "policies": [], "rls": []}}
    single = mkdtemp()
    m_single = readiness.Module(root=single, path="src", base=single / "src")
    m_single.sources = [write(single / "src" / "store.ts",
                               "export const widgets = 1;\n")]
    c = readiness.check_schema(m_single, facts)
    expect(c.status != "fail", "单租户模块不因缺租户列/唯一约束假红")
    expect(any("豁免" in e for e in c.evidence),
           "证据里说清查了哪几条、豁免了什么")
    multi = mkdtemp()
    m_multi = readiness.Module(root=multi, path="src", base=multi / "src")
    m_multi.sources = [write(multi / "src" / "store.ts",
                              "export const t = 'tenant_id';\n")]
    c2 = readiness.check_schema(m_multi, facts)
    expect(c2.status == "fail",
           "多租户模块（源码里有 tenant 字段）仍要求租户列")
    cfg = readiness.check_schema(
        m_single, {"app_config": {
            "columns": ["id\x1fYES\x1finteger"], "indexes": ["app_config_pkey"],
            "policies": [], "rls": []}})
    expect(cfg.status != "fail", "全局配置表不假红")

    # ---------------------------------------------------------- 子项 7
    print("\n子项 7：「具备扩展性」改名「不过度抽象」")
    r7 = run("src", "--repo", str(layout))
    expect("不过度抽象" in r7.stdout, "判据名改成「不过度抽象」")
    expect("具备扩展性" not in r7.stdout, "旧名不再出现")

    # ---------------------------------------------------------- 子项 8
    print("\n子项 8：对账层的漂移/租户/表头识别可配置")
    eng = mkdtemp()
    write(eng / "package.json", json.dumps({"scripts": {
        "test": "node --test",
        "data:reconcile": "node scripts/reconcile.js"}}))
    write(eng / "src" / "store.ts",
          "import { pgTable } from 'drizzle-orm/pg-core';\n"
          "export const widgets = pgTable('widgets', {});\n")
    write(eng / "src" / "calc.test.ts",
          "import test from 'node:test';\n"
          "test('ok', () => {});\n")
    # 英文输出的对账脚本（之前一律假绿）
    write(eng / "scripts" / "reconcile.js",
          "// reconcile widgets table against the vector store\n"
          "console.log('| tenant | missing | status |');\n"
          "console.log('| default | 0 | consistent |');\n"
          "console.log('| t1 | 3 | drift |');\n")
    r8 = run("src", "--repo", str(eng))
    expect(_blocking(r8.stdout) > 0, "英文输出的对账脚本不再假绿（漂移被报出来）")
    expect("漂移" in r8.stdout or "drift" in r8.stdout, "漂移行被列出来")

    print("\n子项 8：找不到可比对的 CSV 对 ⇒ 报「无法比对」")
    write(eng / "scripts" / "reconcile.js",
          "// reconcile widgets\n"
          "console.log('| default | 0 | 一致 |');\n")
    r8b = run("src", "--repo", str(eng))
    expect("无法比对" in r8b.stdout,
           "没有成对 CSV 时报「无法比对」，不报「0 对无差异」")
    expect("0 对无差异" not in r8b.stdout, "不得报「0 对无差异」")

    print("\n子项 8：测试租户前缀可配置")
    write(eng / "scripts" / "reconcile.js",
          "// reconcile widgets\n"
          "console.log('| qa-1 | 3 | ❌ 漂移 |');\n")
    r8c = run("src", "--repo", str(eng), "--test-tenant-prefix", "qa-")
    expect(_blocking(r8c.stdout) == 0,
           "--test-tenant-prefix 声明的前缀算测试租户（不阻塞交付）")

    print("\n子项 8：租户列的位置从表头认（不是写死第一列）")
    write(eng / "scripts" / "reconcile.js",
          "// reconcile widgets\n"
          "console.log('| store | tenant | missing |');\n"
          "console.log('| s1 | itest-x | 3 | ❌ 漂移 |');\n")
    r8e = run("src", "--repo", str(eng))
    expect(_blocking(r8e.stdout) == 0,
           "租户列不在第一列时也能认对（测试租户不阻塞交付）")

    print("\n子项 8：输出目录用工具自己的通用变量")
    write(eng / "scripts" / "reconcile.js",
          "// reconcile widgets\n"
          "const fs = require('fs');\n"
          "const dir = process.env.REQ_PLAN_RECONCILE_OUT_DIR;\n"
          "fs.mkdirSync(dir, { recursive: true });\n"
          "fs.writeFileSync(dir + '/pg.csv',"
          " 'id,tenant_id,score\\n\"m1\",\"t1\",1\\n');\n"
          "fs.writeFileSync(dir + '/vec.csv',"
          " 'id,tenant_id,score\\n\"m1\",\"t1\",2\\n');\n"
          "console.log('| default | 0 | 一致 |');\n")
    r8d = run("src", "--repo", str(eng))
    expect("字段不一致" in r8d.stdout,
           "对账脚本写到 REQ_PLAN_RECONCILE_OUT_DIR 的 CSV 也被比对")

    # ---------------------------------------------------------- 子项 9
    print("\n子项 9：CSV 按声明配对 + 排除时间戳/正文字段")
    csv_dir = mkdtemp()
    write(csv_dir / "pg.csv", "id,tenant_id,score,created_at\n"
                               "\"m1\",\"t1\",1,2020-01-01\n")
    write(csv_dir / "vec.csv", "id,tenant_id,score,created_at\n"
                               "\"m1\",\"t1\",2,2021-09-09\n")
    diffs, bodies, pairs, ts_n = readiness.compare_csv_fields(csv_dir)
    expect(pairs == 1, "一对 CSV 只比一次")
    expect(len(diffs) == 1 and diffs[0]["field"] == "score",
           "普通字段不一致被报出来")
    expect(ts_n == 1, "时间戳类字段被排除（两边天然不同）")
    expect(not bodies, "正文类字段单独归类")

    write(csv_dir / "pg.csv", "id,tenant_id,text\n\"m1\",\"t1\",\"hello\"\n")
    write(csv_dir / "vec.csv", "id,tenant_id,text\n\"m1\",\"t1\",\"H E L L O\"\n")
    diffs2, bodies2, pairs2, _ = readiness.compare_csv_fields(csv_dir)
    expect(not diffs2 and len(bodies2) == 1,
           "正文类字段不一致归到「需人工判断」，不自动判 fail")

    third = csv_dir / "other.csv"
    write(third, "id,region\n\"m1\",\"cn\"\n")
    diffs3, bodies3, pairs3, _ = readiness.compare_csv_fields(csv_dir)
    expect(pairs3 == 1, "第三个无关 CSV 不产生额外比较对（不是两两配对）")

    decl = mkdtemp()
    write(decl / "pg.csv", "id,tenant_id,score\n\"m1\",\"t1\",1\n")
    write(decl / "vec.csv", "id,tenant_id,score\n\"m1\",\"t1\",2\n")
    write(decl / "other.csv", "id,tenant_id,score\n\"m1\",\"t1\",3\n")
    dd, bb, pp, _ = readiness.compare_csv_fields(
        decl, declared=[("pg.csv", "vec.csv")])
    expect(pp == 1 and dd and "pg.csv ↔ vec.csv" in dd[0]["text"],
           "声明的对照关系优先（--csv-pair）")

    # ---------------------------------------------------------- 子项 10
    print("\n子项 10：通用阈值默认不判 fail")
    low = mkdtemp()
    write(low / "package.json", json.dumps(
        {"scripts": {"test": "node --test"}}))
    write(low / "src" / "calc.ts",
          "export function add(a: number, b: number): number { return a + b }\n"
          "export function neverCalled(a: number): number {\n"
          "  const x = a * 2;\n"
          "  const y = x + 1;\n"
          "  return y;\n"
          "}\n")
    write(low / "src" / "calc.test.ts",
          "import test from 'node:test';\n"
          "import { add } from './calc.ts';\n"
          "test('add', () => { if (add(1, 2) !== 3) throw new Error('x'); });\n")
    r10 = run("src", "--repo", str(low))
    cov_line = line_of(r10.stdout, "核心逻辑测试到位")
    expect("✗ 核心逻辑测试到位" not in r10.stdout,
           "没有仓库闸门时不用通用阈值判 fail")
    expect("缺少判据" in cov_line, "说清「仓库没有覆盖率闸门，缺少判据」")
    r10b = run("src", "--repo", str(low), "--min-line-coverage", "99")
    expect("✗ 核心逻辑测试到位" in r10b.stdout,
           "显式给 --min-line-coverage 时才按阈值判 fail")

    # ---------------------------------------------------------- 子项 11
    print("\n子项 11：Python 仓库读得到自己声明的闸门与阈值")
    py2 = mkdtemp()
    write(py2 / "pyproject.toml",
          "[tool.pytest.ini_options]\ntestpaths = [\"test\"]\n"
          "[tool.coverage.report]\nfail_under = 95\n")
    write(py2 / "src" / "pkg" / "__init__.py", "")
    write(py2 / "src" / "pkg" / "mod.py",
          "def used_by_tests():\n    return 1\n")
    write(py2 / "test" / "test_mod.py", "from pkg.mod import used_by_tests\n")
    thr_line, thr_branch = readiness.repo_coverage_threshold(py2)
    expect(thr_line == 95.0, "pyproject.toml 的 fail_under 被读成仓库阈值")
    write(py2 / "Makefile", "coverage:\n\tpython -m coverage report\n")
    gates = readiness._python_gate_scripts(py2)
    expect(any("coverage" in g[1] for g in gates),
           "Makefile 里的覆盖率闸门被认出来")
    write(py2 / "tox.ini", "[testenv:coverage]\ndeps = pytest-cov\n")
    gates2 = readiness._python_gate_scripts(py2)
    expect(any("tox" in g[1] for g in gates2), "tox.ini 的覆盖率环境被认出来")

    print("\n子项 11：Python 仓库自己的闸门会被驱动，判定看闸门")
    write(py2 / "Makefile", "coverage:\n\t@echo 'widgets 95%'\n")
    r11 = run("src", "--repo", str(py2))
    expect("仓库自己的覆盖率闸门全过" in r11.stdout,
           "Python 仓库的 Makefile 闸门被实跑并采信它的结论")
    write(py2 / "Makefile", "coverage:\n\t@echo 'widgets 40%'\n\texit 1\n")
    r11b = run("src", "--repo", str(py2))
    expect("✗ 核心逻辑测试到位" in r11b.stdout,
           "Python 仓库的闸门没过 ⇒ 判红（阈值归仓库所有）")
    r11c = run("src", "--repo", str(py2), "--dry-run")
    expect("make coverage" in r11c.stdout, "--dry-run 也列出 Python 闸门")

    print("\n子项 11：仓库自己的闸门红了要判红（TS 侧同一条）")
    ts_gate = mkdtemp()
    write(ts_gate / "package.json", json.dumps({"scripts": {
        "test": "node --test",
        "coverage": "node scripts/cov.js src"}}))
    write(ts_gate / "src" / "calc.ts",
          "export function add(a: number, b: number): number { return a + b }\n")
    write(ts_gate / "src" / "calc.test.ts",
          "import test from 'node:test';\ntest('ok', () => {});\n")
    write(ts_gate / "scripts" / "cov.js",
          "console.log('# tests 1');\nprocess.exit(1);\n")
    rg = run("src", "--repo", str(ts_gate))
    expect("✗ 核心逻辑测试到位" in rg.stdout,
           "仓库自己的覆盖率闸门红了 ⇒ 判红（不是「全过」）")
    expect("仓库自己的覆盖率闸门全过" not in rg.stdout,
           "闸门没过时不得说「全过」")

    print("\n子项 11b：Python 仓库的 Makefile 目标（对账 / 真后端）也要认")
    py2 = mkdtemp()
    write(py2 / "pyproject.toml", "[project]\nname='pkg'\n")
    write(py2 / "src" / "pkg" / "__init__.py", "")
    write(py2 / "src" / "pkg" / "store.py",
          "import psycopg\ndef upsert(x):\n    pass\n")
    write(py2 / "Makefile",
          "coverage:\n\tpytest --cov=pkg\n\n"
          "reconcile-store:\n\tpython -m pkg.reconcile src/pkg\n\n"
          "integration-store:\n\tpytest -m integration src/pkg\n")
    m2 = readiness.discover_module(py2, "src")
    expect(m2.lang == "py", "认出这是 Python 模块")
    gates2 = readiness.find_gate_scripts(m2, py2)
    expect(any("coverage" in g[1] for g in gates2),
           "Makefile 的覆盖率目标被认出来")
    recs2 = readiness.find_reconcilers(m2, py2)
    expect(any("reconcile" in r[1] for r in recs2),
           "Makefile 的对账目标被认出来（之前只扫 package.json ⇒ Python 仓库用不上）")
    lanes2 = readiness.find_itest_scripts(m2, py2)
    expect(any("integration" in l[1] for l in lanes2),
           "Makefile 的真后端目标被认出来")


    # ---------------------------------------------------------- 子项 12
    print("\n子项 12：真后端车道默认不跑")
    lane = mkdtemp()
    marker = lane / "ITEST_RAN"
    write(lane / "package.json", json.dumps({"scripts": {
        "test": "node --test",
        "test:integration:memory": "node scripts/lane.js src/orders/x.itest.ts"}}))
    write(lane / "src" / "store.ts",
          "import { pgTable } from 'drizzle-orm/pg-core';\n"
          "export const widgets = pgTable('widgets', {});\n")
    write(lane / "src" / "calc.test.ts",
          "import test from 'node:test';\ntest('ok', () => {});\n")
    write(lane / "scripts" / "lane.js",
          "const fs = require('fs');\n"
          "fs.writeFileSync('ITEST_RAN', 'yes');\n"
          "console.log('# tests 1');\nconsole.log('# pass 1');\n")
    r12 = run("src", "--repo", str(lane))
    expect(not marker.exists(), "默认不跑真后端车道")
    r12b = run("src", "--repo", str(lane), "--include-itest")
    expect(marker.exists(), "--include-itest 时才跑真后端车道")

    # ---------------------------------------------------------- 子项 13
    print("\n子项 13：脚本选取是显式白名单 + --dry-run")
    reset_marker = lane / "RESET_RAN"
    write(lane / "package.json", json.dumps({"scripts": {
        "test": "node --test",
        "reconcile:reset": "node scripts/reset.js",
        "coverage:whatever": "node scripts/reset.js"}}))
    write(lane / "scripts" / "reset.js",
          "const fs = require('fs');\n"
          "fs.writeFileSync('RESET_RAN', 'yes');\n")
    r13 = run("src", "--repo", str(lane))
    expect(not reset_marker.exists(),
           "名字带关键词但不在白名单的脚本不会被执行")
    r13b = run("src", "--repo", str(lane), "--dry-run")
    expect(r13b.returncode == 0, "--dry-run 正常退出")
    expect(not reset_marker.exists(), "--dry-run 不执行任何脚本")
    expect("将要执行的仓库脚本" in r13b.stdout, "--dry-run 列出将执行什么")

    # ---------------------------------------------------------- 子项 14
    print("\n子项 14：子进程环境白名单透传")
    secret_repo = mkdtemp()
    write(secret_repo / "package.json", json.dumps({"scripts": {
        "test": "node --test",
        "eval:baseline:check": "node scripts/baseline.js"}}))
    write(secret_repo / "src" / "calc.ts",
          "export function add(a: number, b: number): number { return a + b }\n")
    write(secret_repo / "src" / "calc.test.ts",
          "import test from 'node:test';\ntest('ok', () => {});\n")
    write(secret_repo / "scripts" / "baseline.js",
          "const fs = require('fs');\n"
          "fs.writeFileSync('ENV_CAPTURED',\n"
          "  'SECRET=' + (process.env.TOOL_R2_SECRET || '') + '\\n' +\n"
          "  'PATH=' + (process.env.PATH || '') + '\\n' +\n"
          "  'HOME=' + (process.env.HOME || '') + '\\n');\n")
    r14 = run("src", "--repo", str(secret_repo),
              env={"TOOL_R2_SECRET": "s3cret-value"})
    captured = ""
    cap_file = secret_repo / "ENV_CAPTURED"
    if cap_file.exists():
        captured = cap_file.read_text(encoding="utf-8")
    expect("s3cret-value" not in captured,
           "白名单外的环境变量不透传给目标仓库的脚本")
    expect("PATH=" in captured and "PATH=\n" not in captured,
           "PATH 保留（否则 node/pytest 找不到）")
    expect("HOME=" in captured, "HOME 保留")

    # ---------------------------------------------------------- 子项 15
    print("\n子项 15：psql 连接串不进 argv")
    env15 = readiness.pg_env("postgres://u:p%40ss@db.example:5433/mydb")
    expect(env15.get("PGHOST") == "db.example", "连接串解析出 PGHOST")
    expect(env15.get("PGPORT") == "5433", "连接串解析出 PGPORT")
    expect(env15.get("PGUSER") == "u", "连接串解析出 PGUSER")
    expect(env15.get("PGPASSWORD") == "p@ss", "口令走 PGPASSWORD")
    expect(env15.get("PGDATABASE") == "mydb", "连接串解析出 PGDATABASE")
    expect(not any("postgres://" in v for v in env15.values()),
           "连接串本身不出现在环境变量里")
    expect(readiness.pg_env("不是连接串") == {},
           "解析失败安全降级（不传口令、不猜）")
    expect(readiness.pg_env("") == {} or
           all(k.startswith("PG") for k in readiness.pg_env("")),
           "没有连接串时只用 PG* 环境变量")

    # ---------------------------------------------------------- 子项 16
    print("\n子项 16：判据条数与代码一致（字段级比对并入数据流一致）")
    r16 = run("src", "--repo", str(layout), "--json")
    data16 = json.loads(r16.stdout)
    keys16 = [c["key"] for c in data16["checks"]]
    expect(len(keys16) == 12, f"报告里有 12 条判据（实际 {len(keys16)}）")
    expect("field-compare" not in keys16,
           "字段级比对不是独立判据（并入数据流一致）")
    expect("data-flow" in keys16, "数据流一致这条在")
    expect("oracle" in keys16, "oracle 独立性这条在（G004 加的）")

    # ---------------------------------------------------------- 子项 17
    print("\n子项 17：每条判据带证据类别")
    for c in data16["checks"]:
        if c.get("evidence_kind") not in ("executed", "static", "untested"):
            expect(False, f"判据 {c.get('key')} 有 evidence_kind")
            break
    else:
        expect(True, "每条判据都有 evidence_kind（executed/static/untested）")
    kinds = {c["key"]: c["evidence_kind"] for c in data16["checks"]}
    expect(kinds.get("tests-green") in ("executed", "untested"),
           "测试全绿带证据类别")
    expect(kinds.get("extensibility") == "static",
           "静态信号（不过度抽象）标成 static")
    expect(readiness.Check("k", "t", "untested").mark == "○",
           "「未实测」状态有对应的记号（○）")

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
