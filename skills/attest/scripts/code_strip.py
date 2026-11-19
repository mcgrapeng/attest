#!/usr/bin/env python3
"""代码里的字符串与注释换成空格（**保持偏移量与行号不变**）。

以前 `decision_points` 与 `mutation_check` 各有一份 JS stripper：正则逐字相同、
`_blank` 也逐字相同，只有 docstring 措辞不同，两个 docstring 里都写着
「改一处要改两处」⇒ 抽到这里，两个模块共用一份实现。

顺序要紧：**字符串在前** ⇒ 字符串里的 ``//`` 不会被当成注释。
"""
from __future__ import annotations

import re

_STRIP_RE = re.compile(
    r"'(?:\\.|[^'\\\n])*'"
    r'|"(?:\\.|[^"\\\n])*"'
    r"|`(?:\\.|[^`\\])*`"
    r"|//[^\n]*"
    r"|/\*.*?\*/",
    re.S,
)


def _blank(m: re.Match[str]) -> str:
    """把匹配段换成空格，但**保留换行** —— 行号是报告字段（DP 报告 / 变异点报告），塌了就错。"""
    return re.sub(r"[^\n]", " ", m.group(0))


def strip_js_code(src: str) -> str:
    """把 JS/TS 的字符串与注释换成空格（**保持偏移量与行号不变**）。"""
    return _STRIP_RE.sub(_blank, src)


def strip_py_code(src: str) -> str:
    """把 Python 的字符串与注释换成空格（**保持偏移量不变**）。

    tokenize 给的是 (行, 列)，不是绝对偏移 ⇒ 得自己换算，
    否则第 2 行往后的字符串会盖错位置（第一版就踩了这个）。
    """
    import io
    import tokenize
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, ValueError):
        return src
    starts = [0]
    for ln in src.splitlines(keepends=True):
        starts.append(starts[-1] + len(ln))
    out = list(src)
    for tok in toks:
        if tok.type not in (tokenize.STRING, tokenize.COMMENT):
            continue
        s = starts[tok.start[0] - 1] + tok.start[1]
        e = starts[tok.end[0] - 1] + tok.end[1]
        for i in range(max(0, s), min(e, len(out))):
            if out[i] != "\n":
                out[i] = " "
    return "".join(out)
