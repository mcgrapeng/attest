#!/usr/bin/env python3
"""round-4 回归测试：round-3 引入的两个新错误路径 + 收尾。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
AUDIT = Path("/Users/zhangpeng/.agents/skills/ai-testing-system/scripts/"
             "testkit_audit.py")
FIX = Path("/Users/zhangpeng/.agents/skills/ai-testing-system/scripts/"
           "testkit_fix.py")

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


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8",
                          env={**os.environ, "PYTHONUTF8": "1"})


def main() -> int:
    # C locale 下 stdout 默认 ascii，打中文会 UnicodeEncodeError
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    print("缺陷 1：不可读目录让审计直接崩")
    d = mkdtemp()
    write(d / "package.json", json.dumps({"scripts": {"test": "vitest"}}))
    write(d / "src" / "a.ts", "export const a = 1\n")
    write(d / "src" / "a.test.ts", "test('x', () => {})\n")
    locked = d / "locked"
    locked.mkdir()
    os.chmod(locked, 0o000)
    r = run([sys.executable, str(AUDIT), str(d)])
    expect("Traceback" not in r.stderr, "不可读目录不崩")
    os.chmod(locked, 0o755)

    print("\n缺陷 2：worktrees/ 里的 manifest 被当成主仓配置")
    d2 = mkdtemp()
    write(d2 / "package.json", json.dumps({"scripts": {"test": "vitest"}}))
    write(d2 / "src" / "a.ts", "export const a = 1\n")
    write(d2 / "src" / "a.test.ts", "test('x', () => {})\n")
    # 代理工作树副本里有 @axe-core/playwright —— 主仓没有
    write(d2 / "worktrees" / "feat-x" / "package.json",
          json.dumps({"devDependencies": {"@axe-core/playwright": "^4"}}))
    r2 = run([sys.executable, str(AUDIT), str(d2)])
    expect("a11y" in r2.stdout, "a11y 项被检查了")
    expect("a11y 回归          ✓" not in r2.stdout
           and "✓ a11y 回归" not in r2.stdout,
           "worktrees/ 里的 axe 依赖没被算成主仓的（a11y 仍是缺口）")

    print("\n缺陷 3：target-repro-c/ 里的 manifest 被当成主仓配置")
    d3 = mkdtemp()
    write(d3 / "package.json", json.dumps({"scripts": {"test": "vitest"}}))
    write(d3 / "src" / "a.ts", "export const a = 1\n")
    write(d3 / "src" / "a.test.ts", "test('x', () => {})\n")
    write(d3 / "target-repro-c" / "vendored" / "package.json",
          json.dumps({"devDependencies": {"@axe-core/playwright": "^4"}}))
    r3 = run([sys.executable, str(AUDIT), str(d3)])
    expect("✓ a11y 回归" not in r3.stdout,
           "target-repro-c/ 里的 axe 依赖没被算成主仓的")

    print("\n缺陷 4：两列的不变式行 —— 钉在第 2 列被误报「没有钉」")
    sys.path.insert(0, str(HERE))
    from req_plan import check_plan, parse_plan  # noqa: E402

    class FakeRepo:
        def __init__(self, root: Path):
            self.root = root
            self.packages = []
            self.scripts = {}

    d4 = mkdtemp()
    write(d4 / "src" / "a.ts", "export const a = 1\n")
    plan = write(d4 / "two-col.md", """# p

## 不变式清单

| 不变式 | 钉在哪 |
| --- | --- |
| I1 | src/a.ts:1 |
""")
    p4 = parse_plan(plan)
    expect(len(p4.invariants) == 1, "两列行被认出来")
    expect(p4.invariants[0][2] == "src/a.ts:1", "钉在第 2 列被认出来")
    fs4 = check_plan(p4, FakeRepo(d4))
    expect(not any("没有钉" in f.detail for f in fs4),
           "两列且钉在第 2 列 ⇒ 不误报「没有钉」")

    print("\n缺陷 5：两列行真的没钉 ⇒ 仍要报红")
    plan5 = write(d4 / "two-col-nopin.md", """# p

## 不变式清单

| 不变式 | 说明 |
| --- | --- |
| I1 | 这条没钉 |
""")
    fs5 = check_plan(parse_plan(plan5), FakeRepo(d4))
    expect(any("没有钉" in f.detail for f in fs5),
           "两列且没钉 ⇒ 报「没有钉」（假全绿被堵住）")

    print("\n缺陷 6：首行是数据（带编号）+ 第二行是分隔行 ⇒ 首行被丢掉")
    plan6 = write(d4 / "first-row-data.md", """# p

## 不变式清单

| I1 | 没钉的不变式 | |
| --- | --- | --- |
| I2 | 有钉 | src/a.ts:1 |
""")
    p6 = parse_plan(plan6)
    expect(len(p6.invariants) == 2, "首行数据没被当表头丢掉")
    fs6 = check_plan(p6, FakeRepo(d4))
    expect(any("没有钉" in f.detail for f in fs6),
           "首行那条没钉的照样报红")

    print("\n缺陷 7：嵌套解构导出产生垃圾符号名")
    from req_plan import module_helpers  # noqa: E402
    d7 = mkdtemp()
    write(d7 / "src" / "x.ts", "export const { a: { b } } = obj;\n")
    names = " ".join(module_helpers(d7, ["src"]).get("src", []))
    expect("{ b" not in names, "嵌套解构没产生垃圾符号名")

    print("\n缺陷 8：testkit-fix 对双语言仓重复生成同一条命令")
    d8 = mkdtemp()
    write(d8 / "package.json", json.dumps({
        "scripts": {"test": "vitest"},
        "dependencies": {"fastapi": "^0.111"},
    }))
    write(d8 / "src" / "a.ts", "export const a = 1\n")
    write(d8 / "src" / "a.test.ts", "test('x', () => {})\n")
    write(d8 / "pyproject.toml", "[project]\nname='x'\n")
    r8 = run([sys.executable, str(FIX), "--project", str(d8)])
    # 只数真正的命令行（脚本里每条命令前面还有一行 echo '> …'）
    cmds8 = [l.strip() for l in r8.stdout.splitlines()
             if l.strip().startswith(("eval-gate", "npm", "npx", "cargo",
                                      "gotestsum"))]
    scaffold = [c for c in cmds8 if c.startswith("eval-gate")]
    expect(len(scaffold) == 1,
           f"eval-gate scaffold 只生成一条命令（实际 {len(scaffold)} 条）")
    expect(any(c.startswith("npm i -D @axe-core/playwright") for c in cmds8),
           "TS 侧的补齐命令没被 py 段的同 item 吞掉")

    print("\n缺陷 9：Rust 仓的补齐命令不能被别语言的同 item 吞掉")
    d9 = mkdtemp()
    write(d9 / "Cargo.toml", '[package]\nname="c"\n')
    write(d9 / "src.rs", "#[test]\nfn t(){}\n")
    r9 = run([sys.executable, str(FIX), "--project", str(d9)])
    cmds9 = [l.strip() for l in r9.stdout.splitlines()
             if l.strip().startswith("cargo")]
    expect(any(c.startswith("cargo mutants") for c in cmds9),
           "cargo mutants 在补齐脚本里")
    expect(any(c.startswith("cargo deny") for c in cmds9),
           "cargo deny 在补齐脚本里")

    print()
    for d in TMPDIRS:
        try:
            os.chmod(d, 0o755)
        except OSError:
            pass
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
