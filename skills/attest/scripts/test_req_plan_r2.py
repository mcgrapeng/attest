#!/usr/bin/env python3
"""round-2 回归测试：把评审发现的每个缺陷都钉住。

每个用例都对应一条真实缺陷（评审 round 2 的发现），不是凑数。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from req_plan import (  # noqa: E402
    _is_lane, _PIN_RE, check_plan, module_helpers, parse_plan,
)

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


def main() -> int:
    # C locale 下 stdout 默认 ascii，打中文会 UnicodeEncodeError
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    print("缺陷 1：.tsx / .jsx 钉被截断（critical）")
    # 旧正则 (?:\.\.\.ts|tsx|...) 命中 ts 就停 ⇒ App.tsx 被截成 App.ts
    expect(_PIN_RE.findall("src/App.tsx:42") == ["src/App.tsx:42"],
           "App.tsx 钉不被截断")
    expect(_PIN_RE.findall("a.jsx") == ["a.jsx"], "a.jsx 钉不被截断")
    expect(_PIN_RE.findall("x.ts:1") == ["x.ts:1"], "x.ts 钉照常")
    expect(_PIN_RE.findall("a.py:3,4") == ["a.py:3,4"], "多行号锚点照常")

    print("\n缺陷 2：无表头表格首行被吞（critical）")
    tmp = mkdtemp()
    write(tmp / "src" / "a.ts", "export const a = 1\n")
    plan = write(tmp / "p.md", """# p

## 不变式清单

| I1 | 有钉 | src/nonexistent.ts:3 |
| I2 | 有钉 | src/a.ts:1 |
""")
    p = parse_plan(plan)
    expect(len(p.invariants) == 2, "无表头表格两行都在（没被当表头吞掉）")
    fs = check_plan(p, type("R", (), {"root": tmp, "packages": [], "scripts": {}})())
    expect(any("nonexistent" in f.detail for f in fs),
           "无表头表格里的坏钉照样报红")

    print("\n缺陷 3：### 小节把整节内容丢掉")
    plan2 = write(tmp / "p2.md", """# p2

## 不变式清单

### 逐条说明

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/a.ts:1 |
""")
    p2 = parse_plan(plan2)
    expect(len(p2.invariants) == 1, "### 小节下的表仍被认出来")

    print("\n缺陷 4：代码围栏里的示例表被当成真数据")
    plan3 = write(tmp / "p3.md", """# p3

## 覆盖矩阵

```markdown
| 流程 | 测试落点 | 车道 |
| --- | --- | --- |
| 例子 | src/ghost.test.ts | test |
```

| 真实流程 | src/a.test.ts | test |
""")
    p3 = parse_plan(plan3)
    expect(len(p3.matrix_rows) == 1, "围栏里的示例表不算数据")
    expect(not any("ghost" in c for r in p3.matrix_rows for c in r),
           "围栏里的幽灵文件没进矩阵")

    print("\n缺陷 5：钉取最后一列（多一列备注就失效）")
    plan4 = write(tmp / "p4.md", """# p4

## 不变式清单

| # | 不变式 | 钉在哪 | 备注 |
| --- | --- | --- | --- |
| I1 | 有钉 | src/a.ts:1 | 这条是备注 |
""")
    p4 = parse_plan(plan4)
    expect(len(p4.invariants) == 1 and "src/a.ts:1" in p4.invariants[0][2],
           "钉取第三列（不是最后一列）")

    print("\n缺陷 6：编号认不出就静默丢行")
    plan5 = write(tmp / "p5.md", """# p5

## 不变式清单

| I1 | 有钉 | src/a.ts:1 |
| 2 | 纯数字编号 | src/a.ts:1 |
| inv-3 | 小写编号 | src/a.ts:1 |
""")
    p5 = parse_plan(plan5)
    expect(len(p5.invariants) == 3, "纯数字/小写编号的行不被丢")
    expect(any(i == "2" for i, _, _ in p5.invariants), "纯数字编号被认出来")
    expect(any(i.lower() == "inv-3" for i, _, _ in p5.invariants),
           "小写编号被认出来")

    print("\n缺陷 7：车道关键词漏 unit / spec / jest / mocha")
    for name in ("unit:test", "unit", "spec", "jest", "mocha", "pytest",
                 "vitest:unit"):
        expect(_is_lane(name), f"「{name}」算测试车道")

    print("\n缺陷 8：复用扫描漏 Python class / Rust const+type / TS 解构导出")
    write(tmp / "src" / "mod.py", "def helper_fn():\n    pass\n\n\nclass HelperClass:\n    pass\n")
    write(tmp / "src" / "mod.rs", "pub fn rust_fn() {}\npub struct S;\npub type T = u8;\npub const C: u8 = 1;\n")
    write(tmp / "src" / "d.ts", "export const { alpha, beta } = obj;\n")
    h = module_helpers(tmp, ["src"])
    names = " ".join(h.get("src", []))
    expect("helper_fn" in names, "Python def 被认出来")
    expect("HelperClass" in names, "Python class 被认出来")
    expect("rust_fn" in names, "Rust pub fn 被认出来")
    expect("S" in names, "Rust pub struct 被认出来")
    expect("T" in names, "Rust pub type 被认出来")
    expect("C" in names, "Rust pub const 被认出来")
    expect("alpha" in names, "TS 解构导出被认出来")

    print("\n缺陷 9：矩阵引用不存在的测试文件没有检查")
    plan6 = write(tmp / "p6.md", """# p6

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/a.ts:1 |

## 覆盖矩阵

| 流程 | 测试落点 | 车道 |
| --- | --- | --- |
| 缺测试 | src/missing.test.ts | test |
""")
    fs6 = check_plan(parse_plan(plan6), type("R", (), {"root": tmp, "packages": [], "scripts": {}})())
    expect(any("missing.test.ts" in f.detail for f in fs6),
           "矩阵引用不存在的测试文件 ⇒ 报红")

    print("\n缺陷 10：复用表声称复用不存在的文件没有检查")
    plan7 = write(tmp / "p7.md", """# p7

## 复用

| 能力 | 位置 |
| --- | --- |
| 复用指纹 | src/missing-helper.ts |

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/a.ts:1 |
""")
    fs7 = check_plan(parse_plan(plan7), type("R", (), {"root": tmp, "packages": [], "scripts": {}})())
    expect(any("missing-helper" in f.detail for f in fs7),
           "复用表引用不存在的文件 ⇒ 报红")

    print("\n缺陷 11：new 默默覆盖已填好的计划")
    script = Path(__file__).parent / "req_plan.py"
    out = tmp / "docs" / "feat-test-plan.md"   # 默认文件名是 <feature>-test-plan.md
    write(out, "# 我填好的计划\n")
    r = subprocess.run(
        [sys.executable, str(script), "new", "feat", "--repo", str(tmp)],
        capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"},
    )
    expect(r.returncode != 0, "已有计划时 new 拒绝执行（退出码非 0）")
    expect("我填好的计划" in out.read_text(encoding="utf-8"),
           "原计划内容没被覆盖")
    r2 = subprocess.run(
        [sys.executable, str(script), "new", "feat", "--repo", str(tmp),
         "--force"],
        capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"},
    )
    expect(r2.returncode == 0 and "我填好的计划" not in out.read_text(encoding="utf-8"),
           "--force 才覆盖")

    print("\n缺陷 12：check 的计划路径相对 --repo 解析")
    r3 = subprocess.run(
        [sys.executable, str(script), "check", "docs/feat-test-plan.md", "--repo", str(tmp)],
        capture_output=True, text=True, encoding="utf-8", cwd="/", env={**os.environ, "PYTHONUTF8": "1"},
    )
    expect(r3.returncode != 0, "计划缺内容时报错（不是路径找不到）")
    expect("读不了计划文件" not in r3.stdout + r3.stderr,
           "相对 --repo 的计划路径能找到（不是路径错）")

    print("\n缺陷 13：new 打印的命令不存在")
    r4 = subprocess.run(
        [sys.executable, str(script), "new", "feat2", "--repo", str(tmp)],
        capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"},
    )
    expect("req-plan check" not in r4.stdout, "不再打印不存在的 req-plan 命令")
    expect("req_plan.py check" in r4.stdout, "打印真实可跑的命令")

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
