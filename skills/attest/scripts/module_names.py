#!/usr/bin/env python3
"""模块在测试文件里可能出现的名字 + Python 包目录识别。

`readiness` 与 `selfcheck` 各写了一份 `_module_tokens` 和「这个测试文件提到这个
模块吗」（前者还把 src 布局的包目录识别重写了一遍，两边要人工保持对齐）⇒ 抽到
这里，两个模块共用同一实现。
"""
from __future__ import annotations

import re
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


def mentions_module(p: Path, tokens: list[str]) -> bool:
    """这个测试文件提到这个模块吗（路径片段或文件名）。

    `readiness` 的测试归属判定与 `selfcheck` 的副本筛选用的是同一把尺子 ⇒ 只有
    一份实现：改一边不改另一边，就会出现「自检查不出自己注入的缺陷」。
    """
    for tag in tokens:
        if tag and tag in p.name:
            return True
    if not tokens:
        return False
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for tag in tokens:
        pat = r"(?<![A-Za-z0-9_.-])" + re.escape(tag) + r"(?![A-Za-z0-9_-])"
        if re.search(pat, text):
            return True
    return False
