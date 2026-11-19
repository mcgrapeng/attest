#!/usr/bin/env python3
"""模块在测试文件里可能出现的名字 + Python 包目录识别。

`readiness` 与 `selfcheck` 各写了一份 `_module_tokens`（后者还把 src 布局的包目录
识别重写了一遍，两边要人工保持对齐）⇒ 抽到这里，两个模块共用同一实现。

`module_tokens` 给的是「该模块在测试文件里可能被怎么称呼」：相对路径、目录名、
Python 包名 —— 覆盖率车道用它去匹配测试文件里出现的名字。
"""
from __future__ import annotations

from pathlib import Path


def python_package_dir(base: Path) -> Path | None:
    """src 布局里的包目录（src/<pkg>/__init__.py），或本身就是包的目录。

    `base` 是模块所在目录（`src` / `lib` / 包目录本身）。
    """
    if base.name in ("src", "lib"):
        try:
            children = sorted(base.iterdir()) if base.is_dir() else []
        except OSError:  # 目录正被删/读不动 ⇒ 当作「没认出包」
            return None
        for child in children:
            if child.is_dir() and (child / "__init__.py").exists():
                return child
        return None
    if (base / "__init__.py").exists():
        return base
    return None


def module_tokens(module_rel: str, pkg_dir: Path | None) -> list[str]:
    """模块在测试文件里可能出现的名字（相对路径 / 目录名 / Python 包名，去重）。"""
    toks: list[str] = []
    if module_rel and module_rel != ".":
        toks.append(module_rel)
        toks.append(Path(module_rel).name)
    if pkg_dir is not None:
        toks.append(pkg_dir.name)
    out: list[str] = []
    for t in toks:
        if t and t not in out:
            out.append(t)
    return out
