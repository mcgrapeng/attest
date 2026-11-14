#!/usr/bin/env python3
"""回归测试：install.py / install.sh —— 装与卸。

install.py 是**跨平台**的安装实现（install.sh 只是它的一层薄壳），
所以这两条路径都得守住。

    python3 scripts/test_install.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
INSTALL = ROOT / "install.py"
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


def run(home: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run([sys.executable, str(INSTALL), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          env=env)


def links(home: Path) -> list[Path]:
    return [home / ".agents" / "skills" / "attest",
            home / ".claude" / "skills" / "attest",
            home / ".codex" / "skills" / "attest",
            home / ".config" / "opencode" / "skills" / "attest"]


def test_install_and_uninstall() -> None:
    print("\n安装：四条软链 + 自检")
    home = mkdtemp()
    r = run(home)
    expect(r.returncode == 0, f"退出码 0（实际 {r.returncode}）")
    expect("自检通过" in r.stdout, "装完做了自检")
    for p in links(home):
        expect(p.is_symlink(), f"{p.relative_to(home)} 是软链")
    primary = home / ".agents" / "skills" / "attest"
    expect(primary.resolve() == ROOT.resolve(),
           "稳定入口指向本仓库")
    for p in links(home)[1:]:
        expect(p.resolve() == primary.resolve(),
               f"{p.relative_to(home)} 指向稳定入口")

    print("\n重复安装：幂等")
    r2 = run(home)
    expect(r2.returncode == 0, "第二次装也成功")
    for p in links(home):
        expect(p.is_symlink(), f"{p.relative_to(home)} 仍是软链")

    print("\n真实目录不被删")
    home2 = mkdtemp()
    real = home2 / ".claude" / "skills" / "attest"
    real.mkdir(parents=True)
    (real / "keep.txt").write_text("用户数据\n", encoding="utf-8")
    r3 = run(home2)
    expect(r3.returncode != 0, "有真实目录时退出码非 0（报告了问题）")
    expect("不是软链" in r3.stderr or "不是软链" in r3.stdout,
           "说明了为什么不删")
    expect((real / "keep.txt").is_file(), "用户的真实目录内容还在")

    print("\n卸载：只删软链")
    r4 = run(home, "--uninstall")
    expect(r4.returncode == 0, "卸载退出码 0")
    for p in links(home):
        expect(not p.exists() and not p.is_symlink(),
               f"{p.relative_to(home)} 已删")
    expect(ROOT.is_dir(), "仓库本身没被动过")


def test_shell_wrapper_is_thin() -> None:
    print("\ninstall.sh / uninstall.sh 只是 install.py 的薄壳")
    for name in ("install.sh", "uninstall.sh"):
        p = ROOT / name
        expect(p.is_file(), f"{name} 存在")
        text = p.read_text(encoding="utf-8")
        expect("install.py" in text, f"{name} 指向 install.py")
        expect("ln -s" not in text, f"{name} 里没有自己那套软链逻辑")
    # 薄壳要真的能跑
    home = mkdtemp()
    proc = subprocess.run(["bash", str(ROOT / "install.sh")],
                          capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "HOME": str(home)})
    expect(proc.returncode == 0, "bash install.sh 能跑通")
    expect((home / ".agents" / "skills" / "attest").is_symlink(),
           "薄壳装出了软链")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    test_install_and_uninstall()
    test_shell_wrapper_is_thin()
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
