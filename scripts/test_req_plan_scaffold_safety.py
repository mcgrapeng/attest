#!/usr/bin/env python3
"""scaffold 的写入安全与语言感知回归测试。

钉住 5 件事（每件一组断言）：
  1. 路径穿越：越界的钉被拒绝并报红，一个文件都不写。
  2. 代码注入：用例名/分组名插进字符串时被转义，生成物里没有可执行副作用。
  3. 语言感知：落点的扩展名决定生成风格；认不出来就拒绝生成。
  4. r3/r4 不再写死家目录；缺依赖时跳过并说清楚，不崩溃也不静默。
  5. 模块说明与"只有 scaffold 写文件"的事实一致。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "req_plan.py"
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


def plan_text(pin: str, title: str = "测试计划：feat",
              desc: str = "阈值恒为 1") -> str:
    """一份只含一个落点的计划。"""
    return f"""# {title}

- 目标模块：`src`

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | {desc} | {pin} |

## 覆盖矩阵

| 流程 | 测试落点 | 车道 |
| --- | --- | --- |
| 主干 | {pin} | test |
"""


def ts_repo() -> Path:
    """一个像样的 TS 仓库（有 JS/TS 文件 ⇒ 认得出约定）。"""
    repo = mkdtemp() / "repo"
    write(repo / "package.json", '{"scripts":{"test":"vitest"}}')
    write(repo / "src" / "a.ts", "export const a = 1;\n")
    write(repo / "src" / "a.test.ts",
          "import { describe, it } from 'node:test';\n"
          "describe('a', () => { it('old', () => {}); });\n")
    return repo


def js_unescape(text: str) -> str:
    """把 JS 单引号字符串里的转义还原（用来证明载荷没跑出字面量）。"""
    out = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append({"n": "\n", "r": "\r", "\\": "\\", "'": "'"}.get(nxt, nxt))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def js_string_arg(line: str, marker: str) -> tuple[str | None, str]:
    """取出 `marker('...')` 里那个字符串字面量。

    按转义规则扫描 —— 返回 (字面量原文, 字面量后面的剩余文本)。
    剩余文本里若还有代码，就说明载荷跑出了字符串。
    """
    i = line.find(marker + "('")
    if i < 0:
        return None, ""
    j = i + len(marker) + 2
    buf = []
    while j < len(line):
        c = line[j]
        if c == "\\":
            buf.append(c)
            j += 1
            if j < len(line):
                buf.append(line[j])
                j += 1
            continue
        if c == "'":
            return "".join(buf), line[j + 1:]
        buf.append(c)
        j += 1
    return None, ""


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    print("1. 路径穿越：越界的钉被拒绝并报红，一个文件都不写")
    repo = ts_repo()
    outside = mkdtemp() / "outside"
    rel = os.path.relpath(outside, repo)
    pin = f"{rel}/x.test.ts::用例甲"
    write(repo / "docs" / "plan.md", plan_text(pin))
    r = run("scaffold", str(repo / "docs" / "plan.md"), "--repo", str(repo))
    expect(r.returncode != 0, "越界的钉 ⇒ 退出码非 0")
    expect("写入安全" in r.stdout, "越界的钉 ⇒ 报红并说明是写入安全问题")
    expect(not (outside / "x.test.ts").exists(),
           "越界的钉 ⇒ 仓库外一个文件都没写")
    expect("已生成" not in r.stdout, "越界的钉 ⇒ 不报「已生成」")

    print("\n1b. 绝对路径的钉同样被拒绝")
    abs_target = mkdtemp() / "abs" / "x.test.ts"
    pin2 = f"{abs_target}::用例甲"
    write(repo / "docs" / "plan2.md", plan_text(pin2))
    r2 = run("scaffold", str(repo / "docs" / "plan2.md"), "--repo", str(repo))
    expect(r2.returncode != 0 and "写入安全" in r2.stdout,
           "绝对路径的钉 ⇒ 拒绝并报红")
    expect(not abs_target.exists(), "绝对路径的钉 ⇒ 没写盘")

    print("\n1c. 合法落点照常生成（闸不能把功能写死）")
    repo3 = ts_repo()
    write(repo3 / "docs" / "plan.md",
          plan_text("src/feature/new.test.ts::keeps threshold at one"))
    r3 = run("scaffold", str(repo3 / "docs" / "plan.md"), "--repo", str(repo3))
    expect(r3.returncode == 0, "合法落点 ⇒ 退出码 0")
    expect((repo3 / "src" / "feature" / "new.test.ts").exists(),
           "合法落点 ⇒ 文件生成了")

    print("\n2. 代码注入：用例名里的引号被转义，载荷留在字符串里")
    payload = ("x', () => {}); process.stdout.write('PWNED_MARKER\\n'); it('y")
    repo4 = ts_repo()
    write(repo4 / "docs" / "plan.md",
          plan_text(f"src/inject.test.ts::{payload}"))
    r4 = run("scaffold", str(repo4 / "docs" / "plan.md"), "--repo", str(repo4))
    gen = repo4 / "src" / "inject.test.ts"
    expect(r4.returncode == 0 and gen.exists(), "注入用例名 ⇒ 文件照常生成")
    text = gen.read_text(encoding="utf-8") if gen.exists() else ""
    it_lines = [ln for ln in text.splitlines() if ln.strip().startswith("it(")]
    expect(len(it_lines) == 1, "注入没有多造出一个 it(...)")
    if it_lines:
        lit, rest = js_string_arg(it_lines[0], "it")
        expect(lit is not None, "能按转义规则扫出 it(...) 的字符串参数")
        if lit is not None:
            expect(js_unescape(lit) == payload,
                   "转义还原后与原用例名一致（载荷整个在字符串里）")
            expect(rest.strip() == ", () => {",
                   "字符串后面只剩回调（载荷没跑到字符串外面）")
    expect(text.count("it('") == 1, "只有一个 it(...) 调用")

    print("\n2b. 分组名（describe）同样被转义")
    repo5 = ts_repo()
    write(repo5 / "docs" / "plan.md", plan_text(
        "src/inject2.test.ts::普通用例", title="标题', () => {}); x('"))
    r5 = run("scaffold", str(repo5 / "docs" / "plan.md"), "--repo", str(repo5))
    expect(r5.returncode == 0, "注入分组名 ⇒ 文件照常生成")
    text5 = (repo5 / "src" / "inject2.test.ts").read_text(encoding="utf-8")
    d_lines = [ln for ln in text5.splitlines() if ln.strip().startswith("describe(")]
    expect(len(d_lines) == 1, "注入没有多造出一个 describe(...)")
    if d_lines:
        lit5, rest5 = js_string_arg(d_lines[0], "describe")
        expect(lit5 is not None, "能按转义规则扫出 describe(...) 的字符串参数")
        if lit5 is not None:
            expect(js_unescape(lit5) == "标题', () => {}); x('",
                   "分组名转义还原后与原标题一致（载荷整个在字符串里）")
            expect(rest5.strip() == ", () => {",
                   "分组名后面只剩回调（载荷没跑到字符串外面）")

    print("\n2c. 不变量描述里的 `*/` 不能把块注释撑破")
    repo6 = ts_repo()
    write(repo6 / "docs" / "plan.md", plan_text(
        "src/inject3.test.ts::普通用例",
        desc="*/ process.stdout.write('PWNED2'); /*"))
    r6 = run("scaffold", str(repo6 / "docs" / "plan.md"), "--repo", str(repo6))
    expect(r6.returncode == 0, "注入注释头 ⇒ 文件照常生成")
    text6 = (repo6 / "src" / "inject3.test.ts").read_text(encoding="utf-8")
    expect(text6.count("*/") == 1,
           "块注释只有一处结束（描述里的 `*/` 没撑破注释）")

    print("\n3. 语言感知：落点的扩展名决定生成风格")
    py = mkdtemp() / "pyrepo"
    write(py / "src" / "pkg" / "mod.py", "def add(a, b):\n    return a + b\n")
    write(py / "docs" / "plan.md",
          plan_text("src/pkg/test_add.py::test_add"))
    rp = run("scaffold", str(py / "docs" / "plan.md"), "--repo", str(py))
    pytext = (py / "src" / "pkg" / "test_add.py").read_text(encoding="utf-8")
    expect(rp.returncode == 0, "Python 仓库 ⇒ scaffold 成功")
    expect("def test_add" in pytext, "Python 落点生成 pytest 风格函数")
    expect("assert " in pytext, "Python 落点里有 assert")
    expect("node:test" not in pytext and "describe(" not in pytext,
           "Python 落点里没有 TypeScript")

    rs = mkdtemp() / "rsrepo"
    write(rs / "Cargo.toml", '[package]\nname="c"\n')
    write(rs / "src" / "lib.rs", "pub fn add(a: i32, b: i32) -> i32 { a + b }\n")
    write(rs / "docs" / "plan.md", plan_text("tests/add_test.rs::test_add"))
    rr = run("scaffold", str(rs / "docs" / "plan.md"), "--repo", str(rs))
    rstext = (rs / "tests" / "add_test.rs").read_text(encoding="utf-8")
    expect(rr.returncode == 0, "Rust 仓库 ⇒ scaffold 成功")
    expect("#[test]" in rstext and "assert_eq!" in rstext,
           "Rust 落点生成 cargo test 风格")

    go = mkdtemp() / "gorepo"
    write(go / "go.mod", "module example.com/app\n\ngo 1.21\n")
    write(go / "app" / "main.go", "package main\n\nfunc main() {}\n")
    write(go / "docs" / "plan.md", plan_text("app/add_test.go::TestAdd"))
    rg = run("scaffold", str(go / "docs" / "plan.md"), "--repo", str(go))
    gotext = (go / "app" / "add_test.go").read_text(encoding="utf-8")
    expect(rg.returncode == 0, "Go 仓库 ⇒ scaffold 成功")
    expect("func TestAdd(t *testing.T)" in gotext and "t.Fatalf" in gotext,
           "Go 落点生成 go test 风格")

    print("\n3b. 认不出语言 ⇒ 拒绝生成并报错（不把 TS 写进 .py 路径）")
    empty = mkdtemp() / "emptyrepo"
    write(empty / "src" / "pkg" / "mod.py", "x = 1\n")
    write(empty / "docs" / "plan.md", plan_text("src/pkg/x.test.ts::用例甲"))
    re_ = run("scaffold", str(empty / "docs" / "plan.md"), "--repo", str(empty))
    expect(re_.returncode != 0, "没有 JS/TS 代码却要写 .ts ⇒ 退出码非 0")
    expect("语言不确定" in re_.stdout, "没有 JS/TS 代码却要写 .ts ⇒ 报红说明原因")
    expect(not (empty / "src" / "pkg" / "x.test.ts").exists(),
           "没有 JS/TS 代码却要写 .ts ⇒ 一个文件都没写")

    print("\n3c. 不支持的语言也拒绝生成")
    write(empty / "docs" / "plan2.md", plan_text("src/pkg/x.test.rb::用例甲"))
    reb = run("scaffold", str(empty / "docs" / "plan2.md"), "--repo", str(empty))
    expect(reb.returncode != 0 and "语言不支持" in reb.stdout,
           ".rb 落点 ⇒ 拒绝并报红")

    print("\n3d. detect_conventions 认得出 Python 仓库")
    sys.path.insert(0, str(HERE))
    from req_plan import detect_conventions  # noqa: E402
    conv = detect_conventions(py)
    expect(conv.language == "python", "Python 仓库 ⇒ 主语言认成 python")
    expect((conv.framework, conv.assertion) == ("pytest", "assert"),
           "Python 仓库 ⇒ 约定认成 pytest / assert")

    print("\n3e. 仓库还没有测试文件 ⇒ 从 package.json 推断 JS 框架")
    # vitest 仓库：依赖里有 vitest，但一个测试文件都还没有
    vit = mkdtemp() / "vitrepo"
    write(vit / "package.json",
          '{"devDependencies":{"vitest":"^2.0.0"},'
          '"scripts":{"test":"vitest run"}}')
    write(vit / "src" / "a.ts", "export const a = 1;\n")
    write(vit / "docs" / "plan.md", plan_text("src/a.test.ts::用例甲"))
    rv = run("scaffold", str(vit / "docs" / "plan.md"), "--repo", str(vit))
    vtext = (vit / "src" / "a.test.ts").read_text(encoding="utf-8")
    expect(rv.returncode == 0, "vitest 仓库（还没有测试文件）⇒ scaffold 成功")
    expect("from 'vitest'" in vtext, "vitest 依赖 ⇒ 骨架是 vitest 风格")
    expect("node:test" not in vtext,
           "vitest 仓库里不出现 node:test（那个 runner 根本不收）")

    jest = mkdtemp() / "jestrepo"
    write(jest / "package.json", '{"devDependencies":{"jest":"^29.0.0"}}')
    write(jest / "src" / "a.ts", "export const a = 1;\n")
    write(jest / "docs" / "plan.md", plan_text("src/a.test.ts::用例甲"))
    rj = run("scaffold", str(jest / "docs" / "plan.md"), "--repo", str(jest))
    jtext = (jest / "src" / "a.test.ts").read_text(encoding="utf-8")
    expect(rj.returncode == 0, "jest 仓库（还没有测试文件）⇒ scaffold 成功")
    expect("from '@jest/globals'" in jtext, "jest 依赖 ⇒ 骨架是 jest 风格")
    expect("node:test" not in jtext,
           "jest 仓库里不出现 node:test（那个 runner 根本不收）")

    nodet = mkdtemp() / "nodetrepo"
    write(nodet / "package.json", '{"scripts":{"test":"node --test"}}')
    write(nodet / "src" / "a.ts", "export const a = 1;\n")
    write(nodet / "docs" / "plan.md", plan_text("src/a.test.ts::用例甲"))
    rn = run("scaffold", str(nodet / "docs" / "plan.md"), "--repo", str(nodet))
    ntext = (nodet / "src" / "a.test.ts").read_text(encoding="utf-8")
    expect(rn.returncode == 0, "node --test 仓库 ⇒ 不该走进死胡同")
    expect("from 'node:test'" in ntext, "node --test 脚本 ⇒ 骨架是 node:test 风格")

    both = mkdtemp() / "bothrepo"
    write(both / "package.json",
          '{"devDependencies":{"vitest":"^2.0.0"},"scripts":{"test":"jest"}}')
    write(both / "src" / "a.ts", "export const a = 1;\n")
    write(both / "docs" / "plan.md", plan_text("src/a.test.ts::用例甲"))
    rb = run("scaffold", str(both / "docs" / "plan.md"), "--repo", str(both))
    btext = (both / "src" / "a.test.ts").read_text(encoding="utf-8")
    expect(rb.returncode == 0, "依赖与脚本同时给出框架 ⇒ scaffold 成功")
    expect("from 'vitest'" in btext,
           "依赖里的框架优先于 scripts 命令里的（vitest 胜 jest）")

    print("\n3f. 推断不出框架 ⇒ 拒绝生成并报红（不静默默认 node:test）")
    nofw = mkdtemp() / "nofwrepo"
    write(nofw / "package.json", '{"scripts":{"test":"npm run custom"}}')
    write(nofw / "src" / "a.ts", "export const a = 1;\n")
    write(nofw / "docs" / "plan.md", plan_text("src/a.test.ts::用例甲"))
    rnf = run("scaffold", str(nofw / "docs" / "plan.md"), "--repo", str(nofw))
    expect(rnf.returncode != 0, "推断不出框架 ⇒ 退出码非 0")
    expect("框架推断不出" in rnf.stdout, "推断不出框架 ⇒ 报红并说明原因")
    expect(not (nofw / "src" / "a.test.ts").exists(),
           "推断不出框架 ⇒ 一个文件都没写")
    expect("已生成" not in rnf.stdout, "推断不出框架 ⇒ 不报「已生成」")

    print("\n3g. 显式指定框架 ⇒ 认不出也能生成（拒绝不是死胡同）")
    rnf2 = run("scaffold", str(nofw / "docs" / "plan.md"), "--repo", str(nofw),
               "--framework", "vitest")
    ftext = (nofw / "src" / "a.test.ts").read_text(encoding="utf-8")
    expect(rnf2.returncode == 0, "--framework vitest ⇒ scaffold 成功")
    expect("from 'vitest'" in ftext, "--framework vitest ⇒ 骨架是 vitest 风格")

    print("\n4. r3/r4 不再写死家目录；缺依赖时跳过而不是崩溃")
    r3src = (HERE / "test_req_plan_r3.py").read_text(encoding="utf-8")
    r4src = (HERE / "test_req_plan_r4.py").read_text(encoding="utf-8")
    expect("/Users/" not in r3src, "r3 里没有硬编码的家目录")
    expect("/Users/" not in r4src, "r4 里没有硬编码的家目录")
    # 模拟"别人的机器"：把脚本复制到没有 .agents/skills 的临时目录，HOME 指向空目录
    sim = mkdtemp() / "sim"
    sim.mkdir(parents=True, exist_ok=True)
    simhome = mkdtemp() / "home"
    simhome.mkdir(parents=True, exist_ok=True)
    for name in ("test_req_plan_r3.py", "test_req_plan_r4.py", "req_plan.py"):
        shutil.copy2(HERE / name, sim / name)
    for name in ("test_req_plan_r3.py", "test_req_plan_r4.py"):
        rr2 = subprocess.run([sys.executable, str(sim / name)],
                             capture_output=True, text=True, encoding="utf-8",
                             env={**os.environ, "HOME": str(simhome),
                                  "PYTHONUTF8": "1"})
        expect(rr2.returncode == 0,
               f"{name}：没有 ai-testing-system 时退出码仍是 0")
        expect("skip" in rr2.stdout,
               f"{name}：没有 ai-testing-system 时打印了 skip 提示")
        expect("Traceback" not in rr2.stderr,
               f"{name}：没有 ai-testing-system 时不崩")

    print("\n5. 模块说明与「只有 scaffold 写文件」一致")
    src = SCRIPT.read_text(encoding="utf-8")
    doc = src.split('"""', 2)[1]
    expect("只读代码，不改任何东西" not in doc,
           "模块说明不再声称「只读代码，不改任何东西」")
    expect("scaffold" in doc and "写文件" in doc,
           "模块说明写清了：只有 scaffold 写文件")

    print("\n6. check 的读取面：越界的钉报红，而且不去读仓库外的文件")
    outside = mkdtemp() / "outside"
    write(outside / "x.test.ts", "test('别的用例', () => {});\n")
    repo = ts_repo()
    rel = os.path.relpath(outside, repo)
    write(repo / "docs" / "plan.md", plan_text(f"{rel}/x.test.ts::用例甲"))
    rc = run("check", str(repo / "docs" / "plan.md"), "--repo", str(repo))
    expect(rc.returncode != 0, "check 遇到越界的钉 ⇒ 退出码非 0")
    expect("钉越界" in rc.stdout, "check 遇到越界的钉 ⇒ 报红")
    expect(f"不存在: {rel}/x.test.ts" not in rc.stdout,
           "越界的钉没被当成「文件不存在」再报一次（说明没去读它）")
    expect("里不存在: '用例甲'" not in rc.stdout,
           "越界的钉没被读进用例名（说明没去读它）")
    expect("已生成" not in rc.stdout, "check 不写文件")

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
