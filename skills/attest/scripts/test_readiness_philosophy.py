#!/usr/bin/env python3
"""哲学达标的回归测试：输出契约（R1/R4）与本轮复核中发现的缺陷。

覆盖 brief「G004 哲学达标 R1-R4」的 R1（输出契约）与 R4（证据类别分层），
以及集成复核中发现的四处缺陷：漂移表头被算成漂移、readiness 侧钉越界仍会读
仓库外的文件、Python 的 colocated 测试没被算进导出引用、--exec 是空标志。

能在干净临时目录里独立跑通（不依赖别的 skill 的绝对路径）。

    python3 scripts/test_readiness_philosophy.py
"""
from __future__ import annotations

import json
import os
import re
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


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONUTF8": "1"})


def _blocking(out: str) -> int:
    m = re.search(r"结论：(\d+) 项阻塞", out)
    return int(m.group(1)) if m else -1


def _block(out: str, title: str) -> str:
    """取某条判据自己的那一段（到下一条判据或下一节为止）。"""
    if title not in out:
        return ""
    rest = out.split(title, 1)[1]
    m = re.search(r"\n\s{2}[✓✗·—○]|\n──", rest)
    return rest[:m.start()] if m else rest


def ts_repo(tests: bool = True) -> Path:
    """一个最小的 TS 仓库（测试 colocated）。"""
    d = mkdtemp()
    write(d / "package.json", json.dumps({"scripts": {"test": "node --test"}}))
    write(d / "src" / "calc.ts",
          "export function add(a: number, b: number): number { return a + b }\n")
    if tests:
        write(d / "src" / "calc.test.ts",
              "import test from 'node:test';\n"
              "import assert from 'node:assert/strict';\n"
              "import { add } from './calc.ts';\n"
              "test('add', () => { assert.equal(add(1, 2), 3); });\n")
    return d


# ---------------------------------------------------------------- R1 输出契约

def test_r1_output_contract() -> None:
    print("\nR1：输出契约 —— 证据分层 + 三档结论 + 显式不判定清单")
    repo = ts_repo()
    r = run("src", "--repo", str(repo))

    expect("── 已实测 " in r.stdout, "报告有「已实测」分节")
    expect("── 静态信号" in r.stdout, "报告有「静态信号」分节")
    expect("── 未实测 " in r.stdout, "报告有「未实测」分节")
    expect("── 本工具不判定 " in r.stdout, "报告有「本工具不判定」分节")
    for item in readiness.NOT_JUDGED:
        expect(item in r.stdout, f"不判定清单里有「{item}」")
    expect("仍要人判断" in r.stdout, "说了结论只是测试就绪度、决策仍归人")

    expect(re.search(r"结论：\d+ 项阻塞 · \d+ 项待人工确认 · \d+ 项无信号",
                     r.stdout) is not None,
           "结论是三档格式（阻塞 / 待人工确认 / 无信号）")
    expect("达到交付条件" not in r.stdout, "不再出现「达到交付条件」这种裁决措辞")
    expect("不可交付" not in r.stdout, "不再出现「不可交付」这种裁决措辞")
    expect("退出码 1 =" in r.stdout, "说清了退出码的含义")

    # 静态信号不能单独支撑"通过"
    expect("不能单独支撑" in r.stdout, "静态信号标了不能单独支撑通过")

    print("\nR1：JSON 输出带结论段")
    rj = run("src", "--repo", str(repo), "--json")
    try:
        data = json.loads(rj.stdout)
    except json.JSONDecodeError:
        data = {}
    concl = data.get("conclusion") or {}
    expect(bool(concl), "JSON 里有 conclusion 段")
    for k in ("blocking", "needs_human", "no_signal", "not_judged", "exit_rule"):
        expect(k in concl, f"conclusion 段里有 {k}")
    expect(list(concl.get("not_judged") or []) == list(readiness.NOT_JUDGED),
           "JSON 的 not_judged 与代码常量一致")


def test_r1_exit_codes() -> None:
    print("\nR1：退出码语义")
    healthy = ts_repo()
    r_ok = run("src", "--repo", str(healthy))
    expect(r_ok.returncode == 0, "健康仓库（有测试、关键判据已测到）⇒ 退出码 0")

    zero = ts_repo(tests=False)
    r_zero = run("src", "--repo", str(zero))
    expect(r_zero.returncode != 0, "零测试 ⇒ 退出码非 0（关键判据没测到）")
    expect("没有找到任何测试文件" in r_zero.stdout, "说清为什么判不了")
    expect("○ 测试全绿" in r_zero.stdout,
           "测试全绿落在「未实测」节（○），不是判成不通过（✗）")
    expect("✗ 测试全绿" not in r_zero.stdout,
           "零测试不是「测试红」")


# ---------------------------------------------------------------- R4 证据类别

def test_r4_evidence_kinds() -> None:
    print("\nR4：判据带证据类别")
    repo = ts_repo()
    r = run("src", "--repo", str(repo), "--json")
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        data = {}
    checks = data.get("checks") or []
    expect(bool(checks), "JSON 里有判据列表")
    kinds = {c.get("evidence_kind") for c in checks}
    expect(kinds <= {"executed", "static", "untested"},
           f"证据类别取值合法（实际：{kinds}）")
    expect("executed" in kinds, "有实测类判据")
    by_key = {c.get("key"): c for c in checks}
    expect(by_key.get("tests-green", {}).get("evidence_kind") == "executed",
           "测试全绿是实测类")
    expect(by_key.get("extensibility", {}).get("evidence_kind") == "static",
           "不过度抽象是静态类")
    expect("oracle" in by_key, "有 oracle 独立性判据")
    expect(by_key.get("oracle", {}).get("evidence_kind") == "static",
           "oracle 判据是静态类（提醒不阻塞）")


def test_oracle_criterion_wired() -> None:
    print("\nR3：oracle 判据接进报告，并能报出自比较")
    d = mkdtemp()
    write(d / "package.json", json.dumps({"scripts": {"test": "node --test"}}))
    write(d / "src" / "calc.ts",
          "export function add(a: number, b: number): number { return a + b }\n")
    write(d / "src" / "calc.test.ts",
          "import test from 'node:test';\n"
          "import assert from 'node:assert/strict';\n"
          "import { add } from './calc.ts';\n"
          "test('自比较', () => { assert.equal(add(1, 2), add(1, 2)); });\n")
    r = run("src", "--repo", str(d))
    block = _block(r.stdout, "断言的期望值独立")
    expect("断言的期望值独立" in r.stdout, "报告里有 oracle 这条判据")
    expect("同义反复" in block, "恒真的自比较被点名")
    expect("可能来自被测对象自身" in block,
           "措辞是「可能来自被测对象自身」（不写成断言不独立）")
    expect(_blocking(r.stdout) == 0, "oracle 只是提醒，不算阻塞项")


def test_selfcheck_wired() -> None:
    print("\nR2：--self-check 接线 —— 注入已知缺陷，判据要翻红")
    d = ts_repo()
    r = run("src", "--repo", str(d), "--self-check")
    expect("── 反向验证 " in r.stdout, "报告里有反向验证一节")
    expect("反向验证通过" in r.stdout, "结论是「反向验证通过」")
    expect("pass → fail" in r.stdout or "pass → warn" in r.stdout,
           "打出了翻红前后的对照")
    expect("uncovered-exports" in r.stdout, "翻红的是 uncovered-exports 判据")
    expect("hygiene" in r.stdout, "inject-sleep 翻红的是 hygiene 判据")
    # 仓库没被改
    expect((d / "src" / "calc.test.ts").read_text(encoding="utf-8")
           .count("add(1, 2)") == 1, "用户仓库的测试文件没被注入改动")


# ------------------------------------------------- 缺陷 1：漂移表头被算成漂移

def test_defect_drift_header_not_counted() -> None:
    print("\n缺陷 1：英文对账表头里的 drift 字样不得被算成一条漂移")
    d = mkdtemp()
    write(d / "package.json", json.dumps(
        {"scripts": {"test": "node --test",
                     "reconcile": "node scripts/reconcile.js src"}}))
    write(d / "src" / "store.ts",
          "import { MilvusClient } from '@zilliz/milvus2-sdk-node';\n"
          "export async function upsertVector(id: string) {\n"
          "  const c = new MilvusClient({ address: 'localhost:19530' });\n"
          "  return c.upsert({ collection_name: 'mem', data: [{ id }] });\n"
          "}\n")
    # 表头含 drift；只有 default 一行是真漂移
    write(d / "scripts" / "reconcile.js",
          "// reconcile src\n"
          "console.log('| tenant | drift | status |');\n"
          "console.log('|---|---|---|');\n"
          "console.log('| default | 3 rows | MISMATCH |');\n")
    r = run("src", "--repo", str(d))
    block = _block(r.stdout, "数据流一致（跨存储对账）")
    expect("1 个" in block, f"只算 1 条漂移（实际：{block[:70].strip()}）")
    expect("2 个" not in block, "表头行没被算成第二条漂移")
    expect("| default | 3 rows | MISMATCH |" in r.stdout, "真漂移行被列出来")


# --------------------------------------------------- 缺陷 2：钉越界仍会被读取

def test_defect_out_of_bounds_pin_not_read() -> None:
    print("\n缺陷 2：计划里越界的钉不得被读取")
    d = ts_repo()
    outside = d.parent / "outside_pin.py"
    outside.write_text("def used(): return 1\n", encoding="utf-8")
    plan = write(d / "docs" / "plan.md",
                 "# 测试计划：p\n\n"
                 "## 不变式清单\n\n"
                 "| # | 不变式 | 钉在哪 |\n"
                 "| --- | --- | --- |\n"
                 "| I1 | 越界的钉 | ../outside_pin.py::used |\n\n"
                 "## 覆盖矩阵\n\n"
                 "| 流程 | 测试文件 |\n"
                 "| --- | --- |\n"
                 "| 主流程 | ../outside_pin.py::used |\n")
    r = run("src", "--repo", str(d), "--plan", str(plan))
    expect("钉指向仓库外" in r.stdout, "越界的钉被报出来")
    expect(_blocking(r.stdout) > 0, "越界的钉算阻塞项")
    expect("用例不存在" not in r.stdout,
           "没有去读仓库外的文件（否则会报「用例不存在」）")


# ------------------------------------------- 缺陷 3：Python colocated 测试没被算

def test_defect_python_colocated_tests_counted() -> None:
    print("\n缺陷 3：Python colocated 的 test_*.py 要算进导出引用")
    d = mkdtemp()
    write(d / "package.json", json.dumps({"scripts": {"test": "pytest"}}))
    write(d / "src" / "pkg" / "__init__.py", "")
    write(d / "src" / "pkg" / "mod.py",
          "def used_by_tests(a):\n    return a + 1\n\n\n"
          "def never_tested(b):\n    return b + 2\n")
    write(d / "src" / "pkg" / "test_mod.py",
          "from pkg.mod import used_by_tests\n"
          "def test_used():\n    assert used_by_tests(1) == 2\n")
    r = run("src", "--repo", str(d))
    block = _block(r.stdout, "导出的逻辑都有测试")
    expect("never_tested" in block, "零引用的 def 被点名")
    expect("used_by_tests" not in block,
           "有引用的 def 不被点名（colocated 测试被算进去了）")


# -------------------------------------- 缺陷 4：--exec 是空标志 / 脚本执行回显

def test_defect_exec_flag_and_script_echo() -> None:
    print("\n缺陷 4：--exec 是空标志 ⇒ 去掉；正常模式回显将执行的脚本")
    d = mkdtemp()
    write(d / "package.json", json.dumps(
        {"scripts": {"test": "node --test",
                     "coverage": "node scripts/cov.js src"}}))
    write(d / "src" / "a.ts", "export const a = 1;\n")
    write(d / "src" / "a.test.ts",
          "import test from 'node:test';\n"
          "import assert from 'node:assert/strict';\n"
          "test('a', () => { assert.equal(1, 1); });\n")
    write(d / "scripts" / "cov.js", "process.exit(0);\n")

    r_exec = run("src", "--repo", str(d), "--exec")
    expect(r_exec.returncode == 2, "--exec 不再是合法参数（argparse 拒绝）")
    expect("--exec" in (r_exec.stderr + r_exec.stdout),
           "argparse 的报错里点名了 --exec")

    r = run("src", "--repo", str(d))
    expect("将执行的仓库脚本" in r.stdout, "正常模式回显将执行的仓库脚本")
    expect("coverage" in r.stdout, "回显里含白名单命中的脚本名")

    r_dry = run("src", "--repo", str(d), "--dry-run")
    expect(r_dry.returncode == 0, "--dry-run 退出码 0")
    expect("不执行" in r_dry.stdout, "--dry-run 明说不执行")
    expect("$ node scripts/cov.js src" in r_dry.stdout, "--dry-run 列出确切命令")


# ------------------------------------------------------------ 死代码清理

def test_dead_code_removed() -> None:
    print("\n收尾：check_reconcile 是死代码 ⇒ 已删")
    src = SCRIPT.read_text(encoding="utf-8")
    expect("def check_reconcile" not in src, "readiness.py 里没有 check_reconcile")
    expect("pin_violation" in src,
           "readiness.py 用的是 req_plan 的钉越界校验")
    expect("_pin_outside_repo" not in src,
           "readiness.py 不再自带一份钉越界校验（两处口径必须一致）")



def test_plan_review_gate() -> None:
    print("\n计划的「人工确认」：钉得实 ≠ 理解得对")
    d = mkdtemp()
    write(d / "package.json", json.dumps({"scripts": {"test": "node --test"}}))
    write(d / "src" / "calc.ts", "export const add = (a, b) => a + b;\n")
    write(d / "src" / "calc.test.ts",
          "import test from 'node:test';\n"
          "import assert from 'node:assert';\n"
          "test('加法', () => { assert.equal(add(1, 2), 3); });\n")
    plan_body = ("# 测试计划：加法\n\n"
                 "## 不变式清单\n\n"
                 "| # | 不变式 | 钉在哪 |\n"
                 "| --- | --- | --- |\n"
                 "| I1 | 加法返回两数之和 | src/calc.test.ts::加法 |\n\n"
                 "## 覆盖矩阵\n\n"
                 "| 流程 | 落点 |\n"
                 "| --- | --- |\n"
                 "| 主流程 | src/calc.test.ts::加法 |\n\n"
                 "## 人工确认\n\n"
                 "| 审什么 | 谁 | 什么时候 | 结论 |\n"
                 "| --- | --- | --- | --- |\n"
                 "| 不变式清单与需求一致 | {who} | {when} | {verdict} |\n")
    plan = write(d / "docs" / "plan.md",
                 plan_body.format(who="<待填：谁读的>", when="<待填：日期>",
                                   verdict="<待填：一致 / 有出入>"))
    r = run("src", "--repo", str(d), "--plan", str(plan))
    expect("计划未经人工确认" in r.stdout,
           "没人工确认时明说（不能被一句「✓ 需求覆盖到位」盖过去）")
    expect("✓ 需求覆盖到位" not in r.stdout,
           "没人工确认时不当成完全通过")
    expect("需求是否被正确理解" in r.stdout,
           "说清这一项是人的判断，工具不判")

    write(d / "docs" / "plan.md",
          plan_body.format(who="张三", when="2026-11-15", verdict="一致"))
    r2 = run("src", "--repo", str(d), "--plan", str(plan))
    expect("✓ 需求覆盖到位" in r2.stdout, "人工确认填了才给 ✓")
    expect("计划已有人工确认" in r2.stdout, "确认过就写明")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    test_r1_output_contract()
    test_r1_exit_codes()
    test_r4_evidence_kinds()
    test_oracle_criterion_wired()
    test_selfcheck_wired()
    test_plan_review_gate()
    test_defect_drift_header_not_counted()
    test_defect_out_of_bounds_pin_not_read()
    test_defect_python_colocated_tests_counted()
    test_defect_exec_flag_and_script_echo()
    test_dead_code_removed()
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
