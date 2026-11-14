#!/usr/bin/env python3
"""反向验证（self-check）的回归测试。

守的是 selfcheck.py 的**契约**，不是它的实现细节：

    - drop-test / inject-sleep 两种故障各自能让对应判据从绿翻红
    - 判不出时如实报「没法证明」，不包装成「验证通过」
    - 绝不改用户仓库的任何文件
    - 判据 id 取自 readiness.py --json 的真实字段

    python3 scripts/test_selfcheck.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SELFCHECK = HERE / "selfcheck.py"
READINESS = HERE / "readiness.py"
FAILURES: list[str] = []
TMPDIRS: list[Path] = []

sys.path.insert(0, str(HERE))
import selfcheck  # noqa: E402


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


def snapshot(root: Path) -> dict[str, tuple]:
    """目录里每个文件的 (mtime_ns, size, sha256) —— 用来证明"没被改过"。"""
    out: dict[str, tuple] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "node_modules"]
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            try:
                data = p.read_bytes()
            except OSError:
                continue
            out[str(p.relative_to(root))] = (
                p.stat().st_mtime_ns, len(data),
                hashlib.sha256(data).hexdigest())
    return out


# ---------------------------------------------------------------- 合成仓库


def make_ts_repo(root: Path) -> None:
    """TS 仓：测试 colocated，模块有导出且测试引用了它。"""
    write(root / "package.json", json.dumps(
        {"name": "sc-ts", "version": "0.0.0", "private": True,
         "type": "module", "scripts": {"test": "node --test"}}, indent=2))
    write(root / "src" / "calc.ts",
          "export function add(a: number, b: number): number {\n"
          "  return a + b;\n}\n\n"
          "export function subtract(a: number, b: number): number {\n"
          "  return a - b;\n}\n")
    write(root / "src" / "calc.test.ts",
          'import assert from "node:assert";\n'
          'import test from "node:test";\n\n'
          'import { add, subtract } from "./calc.ts";\n\n'
          'test("add returns the sum", () => {\n'
          "  assert.strictEqual(add(2, 3), 5);\n"
          "});\n\n"
          'test("subtract returns the difference", () => {\n'
          "  assert.strictEqual(subtract(5, 3), 2);\n"
          "});\n")


def make_python_repo(root: Path) -> None:
    """Python 仓：pytest 系，测试在 tests/ 下。"""
    write(root / "pyproject.toml",
          '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    write(root / "src" / "pkg" / "__init__.py", "")
    write(root / "src" / "pkg" / "core.py",
          "def add(a, b):\n    return a + b\n\n\n"
          "def multiply(a, b):\n    return a * b\n")
    write(root / "tests" / "test_core.py",
          "from pkg.core import add, multiply\n\n\n"
          "def test_add_returns_the_sum():\n"
          "    assert add(2, 3) == 5\n\n\n"
          "def test_multiply_returns_the_product():\n"
          "    assert multiply(2, 3) == 6\n")


def make_no_test_repo(root: Path) -> None:
    """没有测试文件的仓 —— 没有可注入的故障目标。"""
    write(root / "package.json", json.dumps(
        {"name": "sc-no-test", "version": "0.0.0", "private": True,
         "type": "module", "scripts": {"test": "node --test"}}, indent=2))
    write(root / "src" / "thing.ts",
          "export function thing(): string {\n  return 'thing';\n}\n")


def make_test_dir_repo(root: Path) -> None:
    """仓库级 test/ 布局：模块里没有 colocated 测试，测试在 test/ 下。"""
    write(root / "package.json", json.dumps(
        {"name": "sc-test-dir", "version": "0.0.0", "private": True,
         "type": "module", "scripts": {"test": "node --test"}}, indent=2))
    write(root / "src" / "calc.ts",
          "export function add(a: number, b: number): number {\n"
          "  return a + b;\n}\n")
    write(root / "test" / "calc.test.ts",
          'import assert from "node:assert";\n'
          'import test from "node:test";\n\n'
          'import { add } from "../src/calc.ts";\n\n'
          'test("add returns the sum", () => {\n'
          "  assert.strictEqual(add(2, 3), 5);\n"
          "});\n")


# ---------------------------------------------------------------- 用例


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    # ---------------------------------------------------------- 黑盒
    print("黑盒：selfcheck 不 import readiness 的内部函数")
    src = SELFCHECK.read_text(encoding="utf-8")
    expect("import readiness" not in src and "from readiness" not in src,
           "selfcheck.py 没有 import readiness（判据全靠子进程拿）")
    expect(not hasattr(selfcheck, "readiness"),
           "selfcheck 模块里没有 readiness 符号")

    # ---------------------------------------------------------- 判据 id
    print("\n判据 id：取自 readiness.py --json 的真实字段")
    ids_repo = mkdtemp()
    make_ts_repo(ids_repo)
    r = subprocess.run(
        [sys.executable, str(READINESS), "src", "--repo", str(ids_repo),
         "--json"], capture_output=True, text=True, encoding="utf-8",
        check=False)
    data = json.loads(r.stdout)
    real_ids = [(c.get("id") or c.get("key")) for c in data["checks"]]
    expect("uncovered-exports" in real_ids,
           "判据 id「uncovered-exports」真实存在")
    expect("hygiene" in real_ids, "判据 id「hygiene」真实存在")
    expect(all(c.get("key") for c in data["checks"]),
           "每条判据都有 key 字段（selfcheck 兼容 key / id 两种命名）")

    # ---------------------------------------------------------- drop-test
    print("\nTS 仓：drop-test 让「导出的逻辑都有测试」翻红")
    ts_repo = mkdtemp()
    make_ts_repo(ts_repo)
    before_snapshot = snapshot(ts_repo)
    res = selfcheck.run_selfcheck("src", ts_repo, faults=("drop-test",))
    f0 = res["faults"][0]
    expect(f0["kind"] == "drop-test", "产出 drop-test 这一条故障记录")
    expect(f0["target"] is not None, "找到了可清空的测试文件")
    expect(f0["before"] and f0["before"]["status"] == "pass",
           "注入前 uncovered-exports 是 pass")
    expect(f0["after"] and f0["after"]["status"] in selfcheck.RED_STATUS,
           "注入后 uncovered-exports 不再是绿的")
    expect(f0["after"] and f0["after"]["id"] == "uncovered-exports",
           "翻红的是 uncovered-exports 这条判据")
    expect(f0["flipped"] is True, "flipped 为真")
    expect(res["verdict"] == "反向验证通过",
           f"结论是「反向验证通过」（实际：{res['verdict']}）")
    expect(res["exit_code"] == 0, "退出码 0")

    # ---------------------------------------------------------- inject-sleep
    print("\nTS 仓：inject-sleep 让「测试卫生」翻红")
    res2 = selfcheck.run_selfcheck("src", ts_repo, faults=("inject-sleep",))
    f1 = res2["faults"][0]
    expect(f1["before"] and f1["before"]["status"] == "pass",
           "注入前 hygiene 是 pass")
    expect(f1["after"] and f1["after"]["status"] in selfcheck.RED_STATUS,
           "注入后 hygiene 不再是绿的")
    expect(f1["after"] and f1["after"]["id"] == "hygiene",
           "翻红的是 hygiene 这条判据")
    expect(f1["flipped"] is True, "flipped 为真")
    expect(res2["verdict"] == "反向验证通过", "结论是「反向验证通过」")

    # ---------------------------------------------------------- 不改用户仓库
    print("\n绝不改用户仓库")
    after_snapshot = snapshot(ts_repo)
    expect(before_snapshot == after_snapshot,
           "跑完 self-check，用户仓库里每个文件的内容与 mtime 都没变")
    expect(not list(ts_repo.rglob("*.orig")) and
           not list(ts_repo.rglob("*selfcheck*")),
           "用户仓库里没落下任何注入产物")

    # ---------------------------------------------------------- 没有目标
    print("\n没有测试文件的仓：如实报「没法证明」")
    nt_repo = mkdtemp()
    make_no_test_repo(nt_repo)
    res3 = selfcheck.run_selfcheck("src", nt_repo, faults=("drop-test",
                                                            "inject-sleep"))
    expect(res3["verdict"] == "没有可注入的故障目标",
           f"结论是「没有可注入的故障目标」（实际：{res3['verdict']}）")
    expect(res3["exit_code"] != 0, "退出码非 0")
    expect(all(not f["flipped"] for f in res3["faults"]),
           "没有把「没翻红」包装成「验证通过」")
    expect(all(f["note"] for f in res3["faults"]),
           "每条故障都说了为什么没法注入")

    # ---------------------------------------------------------- 注入前就不绿
    print("\n注入前判据就不是绿的 ⇒ 报「反向验证未通过」")
    red_repo = mkdtemp()
    make_ts_repo(red_repo)
    # 加一个没人测的导出 ⇒ uncovered-exports 注入前就是 fail
    write(red_repo / "src" / "extra.ts",
          "export function neverTested(): number {\n  return 1;\n}\n")
    res4 = selfcheck.run_selfcheck("src", red_repo, faults=("drop-test",))
    f3 = res4["faults"][0]
    expect(f3["before"] and f3["before"]["status"] != "pass",
           "注入前 uncovered-exports 就不是 pass")
    expect(f3["flipped"] is False, "这种情况不算翻红")
    expect(res4["verdict"] == "反向验证未通过",
           f"结论是「反向验证未通过」（实际：{res4['verdict']}）")
    expect(res4["exit_code"] != 0, "退出码非 0")

    # ---------------------------------------------------------- 模块不存在
    print("\n模块路径不存在")
    res5 = selfcheck.run_selfcheck("src/nope", ts_repo)
    expect(res5["verdict"] == "没有可注入的故障目标", "报「没有可注入的故障目标」")
    expect(res5["exit_code"] != 0, "退出码非 0")

    # ---------------------------------------------------------- Python 仓
    print("\nPython 仓：两种故障都能翻红")
    py_repo = mkdtemp()
    make_python_repo(py_repo)
    py_before = snapshot(py_repo)
    res6 = selfcheck.run_selfcheck("src", py_repo)
    kinds = {f["kind"]: f for f in res6["faults"]}
    expect(kinds["drop-test"]["flipped"] is True,
           "Python 仓：drop-test 翻红")
    expect(kinds["inject-sleep"]["flipped"] is True,
           "Python 仓：inject-sleep 翻红")
    expect(kinds["inject-sleep"]["after"]["id"] == "hygiene",
           "Python 仓：翻红的是 hygiene")
    expect(snapshot(py_repo) == py_before,
           "Python 仓也没被改过")

    # ---------------------------------------------------------- 仓库级 test/ 布局
    print("\n仓库级 test/ 布局：模块里没有 colocated 测试")
    td_repo = mkdtemp()
    make_test_dir_repo(td_repo)
    td_before = snapshot(td_repo)
    res7 = selfcheck.run_selfcheck("src", td_repo)
    tk = {f["kind"]: f for f in res7["faults"]}
    expect(tk["drop-test"]["flipped"] is True,
           "test/ 布局：drop-test 翻红")
    expect(tk["drop-test"]["target"] == "test/calc.test.ts",
           "test/ 布局：目标选中了仓库级测试文件")
    expect(tk["inject-sleep"]["flipped"] is True,
           "test/ 布局：inject-sleep 翻红")
    expect(snapshot(td_repo) == td_before, "test/ 布局的仓库也没被改过")

    # ---------------------------------------------------------- 副本跑不动
    print("\n副本里跑不动测试 ⇒ 如实说，不因此改判结论")
    nr_repo = mkdtemp()
    make_ts_repo(nr_repo)
    write(nr_repo / "package.json", json.dumps(
        {"name": "sc-no-runner", "version": "0.0.0", "private": True,
         "type": "module",
         "scripts": {"test": "definitely-not-a-real-runner run"}}, indent=2))
    res8 = selfcheck.run_selfcheck("src", nr_repo, faults=("inject-sleep",))
    expect(res8["copy_runnable"] is False,
           "报告标明副本里跑不动测试")
    expect(any("跑不动" in n for n in res8["notes"]),
           "报告里明说副本里跑不动测试")
    expect(res8["faults"][0]["flipped"] is True,
           "静态判据仍然翻红（不依赖真跑测试）")

    # ---------------------------------------------------------- 副本策略
    print("\n副本策略：node_modules 不复制，配置文件复制")
    copy_repo = mkdtemp()
    make_ts_repo(copy_repo)
    write(copy_repo / "node_modules" / "left-pad" / "index.js",
          "module.exports = 1;\n")
    dst = mkdtemp() / "copy"
    selfcheck._copy_subset(copy_repo.resolve(), dst, "src")
    expect(not (dst / "node_modules").exists(), "node_modules 没有被复制")
    expect((dst / "package.json").is_file(), "仓库的 package.json 被复制了")
    expect((dst / "src" / "calc.test.ts").is_file(),
           "模块的测试文件被复制了")
    expect((dst / "src" / "calc.ts").is_file(), "模块的源码被复制了")

    # ---------------------------------------------------------- CLI
    print("\nCLI")
    out = subprocess.run(
        [sys.executable, str(SELFCHECK), "src", "--repo", str(ts_repo),
         "--fault", "all", "--json"],
        capture_output=True, text=True, encoding="utf-8",
        check=False)
    expect(out.returncode == 0, "CLI 退出码 0")
    try:
        j = json.loads(out.stdout)
    except json.JSONDecodeError:
        j = {}
    expect(isinstance(j, dict) and j.get("verdict") == "反向验证通过",
           "CLI --json 输出可解析且结论正确")
    expect(isinstance(j.get("faults"), list) and len(j["faults"]) == 2,
           "CLI --json 输出里有两条故障记录")
    f = (j.get("faults") or [{}])[0]
    for key in ("kind", "target", "flipped", "before", "after"):
        expect(key in f, f"--json 的 fault 条目里有 {key} 字段")
    for key in ("id", "title", "status"):
        expect(key in (f.get("before") or {}),
               f"--json 的 before 里有 {key} 字段")

    human = subprocess.run(
        [sys.executable, str(SELFCHECK), "src", "--repo", str(ts_repo)],
        capture_output=True, text=True, encoding="utf-8",
        check=False)
    expect("反向验证通过" in human.stdout, "CLI 人读输出打印结论")
    expect("pass →" in human.stdout, "CLI 人读输出打印翻红前后的对照")

    bad = subprocess.run(
        [sys.executable, str(SELFCHECK), "src", "--repo", str(nt_repo),
         "--json"], capture_output=True, text=True, encoding="utf-8",
        check=False)
    expect(bad.returncode != 0, "没有目标时 CLI 退出码非 0")
    try:
        jb = json.loads(bad.stdout)
    except json.JSONDecodeError:
        jb = {}
    expect(jb.get("verdict") == "没有可注入的故障目标",
           "没有目标时 CLI --json 的结论如实")

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
