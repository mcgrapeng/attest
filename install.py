#!/usr/bin/env python3
"""把 attest skill 装到各 agent 的 skills 目录（软链）。

    python3 install.py              # 装
    python3 install.py --uninstall  # 卸

**跨平台**：纯标准库，没有 bash 依赖（`install.sh` 现在只是它的一层薄壳）。
Windows 上 `os.symlink` 可能需要管理员/开发者模式 —— 失败时给出等价的手工命令，
不删用户数据。

布局：
    ~/.agents/skills/attest          ← 稳定入口，指向本仓库里的 skill 目录
    ~/.claude/skills/attest          → 指向上面那个
    ~/.codex/skills/attest           → 指向上面那个
    ~/.config/opencode/skills/attest → 指向上面那个

只动**软链**。目标位置已经是真实目录 ⇒ 跳过并告警，绝不删用户数据。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
# skill 本体在 skills/attest/ 下 —— skill 必须住在仓库的**子目录**里，
# 否则 skills.sh / anthropics 官方 marketplace 的索引器扫不到它。
SRC = REPO / "skills" / "attest"
PRIMARY = Path.home() / ".agents" / "skills" / "attest"
LINK_TARGETS = [
    Path.home() / ".claude" / "skills" / "attest",
    Path.home() / ".codex" / "skills" / "attest",
    Path.home() / ".config" / "opencode" / "skills" / "attest",
]


def install() -> int:
    print(f"安装 attest（来源：{SRC}）")
    problems = 0

    def link(path: Path, target: Path) -> None:
        nonlocal problems
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            print(f"  覆盖已有软链：{path}")
            path.unlink()
        elif path.exists():
            print(f"  !! {path} 已存在且不是软链 —— 跳过（不删真实目录）")
            problems += 1
            return
        try:
            path.symlink_to(target, target_is_directory=True)
            print(f"  已链接：{path} -> {target}")
        except OSError as e:
            print(f"  !! 软链失败（{e}）")
            print(f"     等价的手工命令：ln -s \"{target}\" \"{path}\"")
            problems += 1

    print("稳定入口：")
    link(PRIMARY, SRC)
    print("各 agent 的 skills 目录：")
    for t in LINK_TARGETS:
        link(t, PRIMARY)

    script = PRIMARY / "scripts" / "readiness.py"
    if not script.is_file():
        print(f"!! 装完找不到 {script}", file=sys.stderr)
        return 1
    import subprocess
    try:
        ok = subprocess.run([sys.executable, str(script), "--help"],
                            capture_output=True, timeout=60).returncode == 0
    except OSError as e:
        print(f"!! 自检跑不起来：{e}", file=sys.stderr)
        return 1
    if not ok:
        print(f"!! 自检失败：{script} --help", file=sys.stderr)
        return 1
    print(f"自检通过：{script}")
    print(f"完成。用法见 {REPO / 'README.md'}")
    return 1 if problems else 0


def uninstall() -> int:
    print("卸载 attest")
    removed = skipped = 0
    for path in [PRIMARY] + LINK_TARGETS:
        if path.is_symlink():
            path.unlink()
            print(f"  已删除软链：{path}")
            removed += 1
        elif path.exists():
            print(f"  !! {path} 不是软链 —— 跳过（不删真实目录）")
            skipped += 1
        else:
            print(f"  没有这一条：{path}")
    print(f"共删除 {removed} 条软链，跳过 {skipped} 条（非软链的真实目录，保持原样）")
    print("仓库本身没有被改动 —— 重新装回来：python3 install.py")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--uninstall", action="store_true", help="卸载（只删软链）")
    args = ap.parse_args()
    return uninstall() if args.uninstall else install()


if __name__ == "__main__":
    sys.exit(main())
