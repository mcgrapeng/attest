#!/usr/bin/env python3
"""把 skill 打包成可分发的 `.skill` 文件（zip 格式）。

    python3 package.py                 # → dist/attest.skill
    python3 package.py --out /tmp/x    # 指定输出目录
    python3 package.py --check         # 只检查会打进包的内容，不写文件

`.skill` 是社区通行的分发格式：一个 zip，第一层是 skill 目录本身。
装的时候解开到某个 agent 的 skills 目录即可。

**自包含**：不依赖 skill-creator 之类的外部工具（那是另一个 skill 的脚本，
OSS 用户不一定装了）。格式与它产出的兼容。

排除：`.git` / `.github`（CI 配置不是 skill 的一部分）、`dist`、`fixtures`
（黄金样本是开发用的，体积大）、`evals`、`.omx`（本地运行态）、
`__pycache__`、`.pyc`、`.DS_Store`。
"""
from __future__ import annotations

import argparse
import fnmatch
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL_NAME = "attest"

EXCLUDE_DIRS = {"__pycache__", "node_modules", ".git", ".github", ".omx",
                "dist", "fixtures", "evals", ".venv", "venv", ".pytest_cache",
                ".ruff_cache", ".mypy_cache", ".omx-state-locks", ".claude",
                ".idea"}
EXCLUDE_GLOBS = ("*.pyc", "*.pyo", ".DS_Store", "*.identity.json")
# 回归套件是**仓库**的开发资产（21 个文件、约 3000 行），不是 skill 的运行时 ——
# 不打进包；仓库里照旧有。
EXCLUDE_PREFIXES = ("scripts/test_",)
# 打包前必须存在的文件（少了就不该发）
REQUIRED = ("SKILL.md", "LICENSE", "README.md")


def excluded(rel: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in rel.parts):
        return True
    if any(rel.as_posix().startswith(pref) for pref in EXCLUDE_PREFIXES):
        return True
    return any(fnmatch.fnmatch(rel.name, pat) for pat in EXCLUDE_GLOBS)


def collect() -> list[Path]:
    files = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        if excluded(rel):
            continue
        files.append(p)
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist"))
    ap.add_argument("--check", action="store_true",
                    help="只列出会打进包的内容，不写文件")
    args = ap.parse_args()

    missing = [r for r in REQUIRED if not (ROOT / r).is_file()]
    if missing:
        print(f"缺少必需文件，打包中止：{', '.join(missing)}", file=sys.stderr)
        return 1

    files = collect()
    total = sum(p.stat().st_size for p in files)
    print(f"会打进包的文件：{len(files)} 个，{total / 1024:.0f} KB")
    for p in files:
        print(f"  {p.relative_to(ROOT)}")

    # 字节码绝不能进包（既泄漏作者环境，又让用户拿到不可读的产物）
    bad = [p for p in files if p.suffix in (".pyc", ".pyo")
           or "__pycache__" in p.parts]
    if bad:
        print("\n有字节码要进包 —— 中止：", file=sys.stderr)
        for p in bad:
            print(f"  {p.relative_to(ROOT)}", file=sys.stderr)
        return 1

    if args.check:
        return 0

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{SKILL_NAME}.skill"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            # 第一层是 skill 目录本身 —— 与社区通行的 .skill 格式一致
            zf.write(p, p.relative_to(ROOT.parent))
    print(f"\n已打包：{target}（{target.stat().st_size / 1024:.0f} KB）")
    print(f"装法：解开到 <agent>/skills/ 下，或直接用 install.sh 建软链。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
