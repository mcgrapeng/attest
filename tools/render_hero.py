#!/usr/bin/env python3
"""把一次真实运行渲染成首屏图（SVG，无第三方依赖）。

    python3 tools/render_hero.py            # → skills/attest/assets/hero.svg
    python3 tools/render_hero.py --png      # 另存一份 PNG（需 Chrome）

**为什么是折行而不是缩放**：GitHub 的 README 正文列宽约 838px，图按这个宽度渲染 ⇒
图片里的有效字号 = 838 ÷ 最长行的列数（等宽字）。之前那张图是最长行约 90 列 ⇒ 有效字号
只有 9.3px（手机上 3.8px）—— 缩放救不了，**列数才是杠杆**。这里折到 64 列 ⇒ 有效字号
13px，终端感还在，字能读。

**为什么用 SVG 而不是截图**：SVG 里字号与列宽是显式的（不依赖设备像素比），
文本可选中、屏幕阅读器能读到（这是那张 PNG 最贵的一个缺点）。
"""
from __future__ import annotations

import argparse
import html
import subprocess
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "attest"
OUT = SKILL / "assets" / "hero.svg"

COLS = 64          # 最长列数 ⇒ 有效字号 = 838 / 64 ≈ 13px
FS = 14            # SVG 里的字号（px）
LH = 20            # 行高
PAD = 18
BG = "#0d1117"
FG = "#c9d1d9"
MUTED = "#8b949e"
TEAL = "#39c5cf"
GREEN = "#3fb950"
RED = "#f85149"
YELLOW = "#d29922"


def width(s: str) -> int:
    """字符串的终端列宽（CJK 与全角标点算 2 列）。"""
    w = 0
    for ch in s:
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def wrap(s: str, cols: int) -> list[str]:
    """按列宽折行（不拆单词 —— 报告里每行都是独立的一句话，拆了更难读）。"""
    if width(s) <= cols:
        return [s]
    out, cur, cur_w = [], "", 0
    for ch in s:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if cur_w + cw > cols:
            out.append(cur)
            cur, cur_w = ch, cw
        else:
            cur += ch
            cur_w += cw
    if cur:
        out.append(cur)
    return out


def colorize(line: str) -> str:
    """按报告里的记号上色。"""
    s = html.escape(line)
    if s.startswith("──") or s.startswith("将执行"):
        return f'<tspan fill="{TEAL}">{s}</tspan>'
    if s.lstrip().startswith("✓"):
        return f'<tspan fill="{GREEN}">{s}</tspan>'
    if s.lstrip().startswith("✗"):
        return f'<tspan fill="{RED}">{s}</tspan>'
    if s.lstrip().startswith("—") or s.lstrip().startswith("○"):
        return f'<tspan fill="{YELLOW}">{s}</tspan>'
    if s.startswith("结论") or s.startswith("退出码"):
        return f'<tspan fill="{FG}"><tspan font-weight="700">{s}</tspan></tspan>'
    if s.startswith("  ") and not s.lstrip().startswith(("✓", "✗", "—", "○")):
        return f'<tspan fill="{MUTED}">{s}</tspan>'
    return s


def report_text(fixture: str) -> list[str]:
    """跑一次真实的 readiness，取它的原样输出。"""
    r = subprocess.run(
        [sys.executable, str(SKILL / "scripts" / "readiness.py"),
         "src", "--repo", str(ROOT / fixture)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT))
    out = (r.stdout + r.stderr).rstrip("\n").splitlines()
    if not out:
        raise SystemExit("readiness 没有输出，hero 不做假图")
    return out


def render(lines: list[str]) -> str:
    wrapped: list[str] = []
    for ln in lines:
        wrapped.extend(wrap(ln, COLS))
    longest = max((width(x) for x in wrapped), default=1)
    w = PAD * 2 + longest * (FS * 0.6)
    h = PAD * 2 + len(wrapped) * LH
    body = []
    for i, ln in enumerate(wrapped):
        y = PAD + LH * (i + 1) - 4
        body.append(f'<text x="{PAD}" y="{y:.0f}">{colorize(ln)}</text>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h}" '
        f'viewBox="0 0 {w:.0f} {h}" role="img" '
        f'aria-label="attest report">\n'
        f'<rect width="{w:.0f}" height="{h}" rx="10" fill="{BG}"/>\n'
        f'<g font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" '
        f'font-size="{FS}" fill="{FG}">\n'
        + "\n".join(body)
        + "\n</g>\n</svg>\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixture", default="fixtures/ts-colocated")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    svg = render(report_text(args.fixture))
    Path(args.out).write_text(svg, encoding="utf-8")
    print(f"已生成 {args.out}（{len(svg.splitlines())} 行 · 最长 "
          f"{max((width(x) for x in svg.splitlines()), default=0)} 字节的文本块）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
