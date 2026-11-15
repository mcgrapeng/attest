#!/usr/bin/env python3
"""round-3 回归测试：把评审 round 2 的缺陷钉住。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from req_plan import check_plan, module_helpers, parse_plan  # noqa: E402

FAILURES: list[str] = []
TMPDIRS: list[Path] = []


def _load_testkit_audit():
    """导入 ai-testing-system 的 testkit_audit。

    路径**从本文件推导**（先找仓库/工作区自带的，再退到机器级安装位置
    ~/.agents/skills/ai-testing-system）—— 不写死某台机器的家目录。
    找不到就返回 (None, None, 原因)，由调用方跳过相关断言并打印提示
    （静默跳过最糟：会让人以为那些断言通过）。
    """
    here = Path(__file__).resolve()
    candidates = [p / ".agents" / "skills" / "ai-testing-system" / "scripts"
                  for p in here.parents]
    candidates.append(
        Path.home() / ".agents" / "skills" / "ai-testing-system" / "scripts")
    for d in candidates:
        script = d / "testkit_audit.py"
        if not script.is_file():
            continue
        sys.path.insert(0, str(d))
        try:
            import testkit_audit  # noqa: E402
        except Exception as e:  # noqa: BLE001 —— 缺依赖也算"没有"
            return None, script, f"testkit_audit 导入失败（{e}）"
        return testkit_audit, script, ""
    return None, None, ("找不到 ai-testing-system 的 testkit_audit.py"
                        "（也没在 ~/.agents/skills 下找到）")


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
    repo = FakeRepo(tmp)

    print("缺陷 A：表尾框线（| --- | --- |）把最后一条数据行当表头丢掉")
    plan = write(tmp / "a.md", """# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/a.ts:1 |

## 覆盖矩阵

| 流程 | 测试落点 |
| --- | --- |
| 主干 | src/a.test.ts |
| 分支 | src/missing-branch.test.ts |
| --- | --- |
""")
    p = parse_plan(plan)
    fs = check_plan(p, repo)
    expect(len(p.matrix_rows) == 2, "表尾框线前的两条数据行都在")
    expect(any("missing-branch" in f.detail for f in fs),
           "最后一条数据行里的坏钉照样报红（没被静默丢掉）")

    print("\n缺陷 B：两列的不变式行被静默丢弃 ⇒ 假全绿")
    plan2 = write(tmp / "b.md", """# p

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/a.ts:1 |
| I2 | 少了一列没钉 |
""")
    p2 = parse_plan(plan2)
    expect(len(p2.invariants) == 2, "两列的行也被算成不变式（没被丢掉）")
    fs2 = check_plan(p2, repo)
    expect(any(f.item.startswith("不变式") and "没有钉" in f.detail for f in fs2),
           "两列的不变式行 ⇒ 报「没有钉」（而不是静默跳过）")

    print("\n缺陷 C：畸形 manifest 让 new 直接崩")
    for bad, label in (
        ("[]", "根是数组"),
        ('{"scripts": ["a"]}', "scripts 是数组"),
        ('"hello"', "根是字符串"),
    ):
        d = mkdtemp()
        write(d / "package.json", bad)
        write(d / "src" / "a.ts", "export const a = 1\n")
        r = subprocess.run(
            [sys.executable, str(HERE / "req_plan.py"), "new", "f",
             "--repo", str(d), "--module", "src"],
            capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        expect(r.returncode == 0 and "Traceback" not in r.stderr,
               f"畸形 manifest（{label}）不崩")

    print("\n缺陷 D：解构导出带默认值时抓到的是默认值")
    d2 = mkdtemp()
    write(d2 / "src" / "x.ts", "export const { alpha = 1, beta } = obj;\n")
    names = " ".join(module_helpers(d2, ["src"]).get("src", []))
    expect("alpha" in names, "alpha 被认出来")
    expect("beta" in names, "beta 被认出来")
    expect(" 1  (" not in names, "默认值 1 没被当成符号名")

    print("\n缺陷 E：Python 库 + 同仓 Node 服务 ⇒ 纯 Python 库被要求写 e2e")
    audit, audit_script, why = _load_testkit_audit()
    if audit is None:
        print(f"  skip 缺陷 E/F/G：{why}")
    else:
        d3 = mkdtemp()
        write(d3 / "package.json",
              '{"scripts":{"test":"vitest"},"dependencies":{"fastify":"^5"}}')
        write(d3 / "src" / "a.ts", "export const a = 1;\n")
        write(d3 / "pyproject.toml", "[project]\nname='x'\n")
        write(d3 / "pkg" / "__init__.py", "")
        expect(audit.has_http_service(d3, "ts"), "TS 侧看成服务型（fastify）")
        expect(not audit.has_http_service(d3, "py"),
               "Python 侧不因同仓的 Node 服务而变成服务型")

        print("\n缺陷 F：子项目式 Rust monorepo 的清单只读根 ⇒ 假全红")
        d4 = mkdtemp()
        write(d4 / "crate" / "Cargo.toml", '[package]\nname="c"\n')
        write(d4 / "crate" / "mutants.toml", "{}")
        write(d4 / "crate" / "src" / "lib.rs", "#[test]\nfn t(){}\n")
        r = subprocess.run(
            [sys.executable, str(audit_script), str(d4)],
            capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        out = r.stdout + r.stderr
        expect("── Rust" in out, "Rust 段跑出来了")
        expect("变异审计 (rust)" not in out,
               "crate/mutants.toml 被认出来（不再假全红）")

        print("\n缺陷 G：子目录同时有 package.json 和 Cargo.toml ⇒ Rust 被漏")
        d5 = mkdtemp()
        write(d5 / "pkg" / "package.json", '{"scripts":{"test":"vitest"}}')
        write(d5 / "pkg" / "Cargo.toml", '[package]\nname="c"\n')
        write(d5 / "pkg" / "src" / "lib.rs", "#[test]\nfn t(){}\n")
        langs = audit.detect_langs(d5)
        expect("rust" in langs and "ts" in langs,
               "同一子目录的 TS 与 Rust 都被认出来")

    print("\n缺陷 H：畸形 manifest 的嵌套类型写错会崩")
    if audit is None:
        print(f"  skip 缺陷 H：{why}")
    else:
        for bad, label in (
            ('{"dependencies": ["react"]}', "dependencies 是数组"),
            ('{"dependencies": "react"}', "dependencies 是字符串"),
            ('{"scripts": ["test"]}', "scripts 是数组"),
        ):
            d6 = mkdtemp()
            write(d6 / "package.json", bad)
            write(d6 / "src" / "a.ts", "export const a = 1;\n")
            r = subprocess.run(
                [sys.executable, str(audit_script), str(d6)],
                capture_output=True, text=True, encoding="utf-8",
                env={**os.environ, "PYTHONUTF8": "1"},
            )
            # 审计器有**关键缺口**时退出码是 1 —— 这里只要求"不崩"
            expect("Traceback" not in r.stderr, f"嵌套畸形（{label}）不崩")

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
