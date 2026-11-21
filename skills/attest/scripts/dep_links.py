"""把依赖目录**软链**进副本（不复制体积）。

不带依赖 ⇒ 副本里跑不动测试 ⇒ 那一条判据就"没法测"（反向验证只能证明静态
判据抓得住东西）。软链（不是复制）既能跑真测试，又不花时间与磁盘。

两处用得上：`mutation_check.py`（还要软链工作区根的依赖）与 `selfcheck.py`
（多一个 Rust 的 `target/`）。抽到这里是为了**只有一份实现** —— 各写一份时
Tier 2 的去重扫描判过是重复（连 docstring 都近乎逐字相同）。
"""
from __future__ import annotations

from pathlib import Path

#: 默认要软链的依赖目录（Node / Python）。
DEFAULT_DEP_DIRS = ("node_modules", ".venv", "venv")


def link_deps(repo: Path, dst: Path, *, extra_roots: tuple[str, ...] = (),
              names: tuple[str, ...] = DEFAULT_DEP_DIRS) -> list[str]:
    """把依赖目录软链进副本，返回相对 dst 的路径列表。

    extra_roots：除仓库根之外还要软链的子目录（工作区根），相对 `repo`。
    names：要软链的依赖目录名。
    """
    roots: list[tuple[Path, Path]] = [(repo, dst)]
    for rel in extra_roots:
        roots.append((repo / rel, dst / rel))
    linked: list[str] = []
    for src_root, dst_root in roots:
        for name in names:
            s = src_root / name
            d = dst_root / name
            if not s.is_dir() or d.exists() or d.is_symlink():
                continue
            try:
                d.symlink_to(s, target_is_directory=True)
                linked.append(str(d.relative_to(dst)))
            except OSError:
                pass
    return linked
