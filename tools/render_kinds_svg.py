#!/usr/bin/env python3
"""渲染"证据四档"动画条（SVG，无第三方依赖）。

    python3 tools/render_kinds_svg.py    # → skills/attest/assets/evidence-kinds.svg

四档是 attest 报告的骨架：已实测 / 静态信号 / 未实测 / 本工具不判定。
这张条把它变成四个卡片，一条亮带按顺序扫过去 —— 扫到哪张，哪张亮一下。
读者一眼就知道「结论分四类」，而不必先读完正文。

**动效尊重系统设置**：动画包在 `@media (prefers-reduced-motion: no-preference)` 里。
"""
from __future__ import annotations

import argparse
import html
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "skills" / "attest" / "assets" / "evidence-kinds.svg"

W = 794
CARD_W = 182
GAP = 14
CARD_H = 132
TOP = 30
BG = "#0d1117"
EDGE = "#30363d"
FG = "#e6edf3"
MUTED = "#8b949e"

# 中文名、英文说法、一句话、色
KINDS = [
    ("已实测", "Measured", "跑过了：命令、退出码、输出都在报告里", "#3fb950"),
    ("静态信号", "Static signal", "读源码读出来的，永远不能单独撑起一个「通过」", "#39c5cf"),
    ("未实测", "Not measured", "测不了：报告里写清缺了哪个前置条件", "#d29922"),
    ("本工具不判定", "Not judged", "超出范围：需求理解、产品行为、性能、安全…", "#6e7681"),
]


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def render() -> str:
    cards = []
    for i, (zh, en, note, color) in enumerate(KINDS):
        x = 12 + i * (CARD_W + GAP)
        y = TOP
        # 图标：一个几何记号（不引字体、不引图片）
        cx = x + 22
        cy = y + 26
        if i == 0:
            icon = (f'<path d="M{cx - 7} {cy} l5 5 l9 -11" fill="none" '
                    f'stroke="{color}" stroke-width="2.4" stroke-linecap="round" '
                    f'stroke-linejoin="round"/>')
        elif i == 1:
            icon = (f'<circle cx="{cx}" cy="{cy}" r="9" fill="none" stroke="{color}" '
                    f'stroke-width="2"/>'
                    f'<circle cx="{cx}" cy="{cy}" r="3" fill="{color}"/>')
        elif i == 2:
            icon = (f'<path d="M{cx - 9} {cy} h18" stroke="{color}" stroke-width="2.4" '
                    f'stroke-linecap="round"/>'
                    f'<path d="M{cx - 6} {cy + 7} h12" stroke="{color}" stroke-width="1.6" '
                    f'stroke-linecap="round" opacity=".55"/>')
        else:
            icon = (f'<circle cx="{cx}" cy="{cy}" r="9" fill="none" stroke="{color}" '
                    f'stroke-width="2" stroke-dasharray="3.4 3"/>'
                    f'<path d="M{cx - 6} {cy + 6} L{cx + 6} {cy - 6}" stroke="{color}" '
                    f'stroke-width="2" stroke-linecap="round"/>')
        cards.append(
            f'<g class="att-card" style="--i:{i}">\n'
            f'  <rect x="{x}" y="{y}" width="{CARD_W}" height="{CARD_H}" rx="12" '
            f'class="att-card-bg"/>\n'
            f'  <rect x="{x}" y="{y}" width="3.5" height="{CARD_H}" rx="2" fill="{color}"/>\n'
            f'  {icon}\n'
            f'  <text x="{x + 44}" y="{y + 31}" font-size="16" font-weight="650" '
            f'fill="{FG}">{esc(zh)}</text>\n'
            f'  <text x="{x + 44}" y="{y + 48}" font-size="10.5" fill="{color}" '
            f'letter-spacing=".4">{esc(en)}</text>\n'
            f'  <text x="{x + 20}" y="{y + 78}" font-size="11" fill="{MUTED}">\n'
            + "".join(
                f'    <tspan x="{x + 20}" dy="{0 if j == 0 else 15}">{esc(line)}</tspan>\n'
                for j, line in enumerate(_wrap(note, 22))
            )
            + f'  </text>\n'
            f'</g>')

    css = f"""
    text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
            "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif; }}
    .att-card-bg {{ fill: #0f1419; stroke: {EDGE}; }}
    @keyframes att-kcard {{
      0%, 100% {{ opacity: .55; }}
      50%      {{ opacity: 1; }}
    }}
    @keyframes att-kband {{
      0%   {{ transform: translateX(-260px); opacity: 0; }}
      10%  {{ opacity: .9; }}
      90%  {{ opacity: .9; }}
      100% {{ transform: translateX({W}px); opacity: 0; }}
    }}
    @media (prefers-reduced-motion: no-preference) {{
      .att-card {{ animation: att-kcard 7s ease-in-out infinite;
                   animation-delay: calc(var(--i) * .55s); }}
      .att-kband {{ animation: att-kband 7s cubic-bezier(.45,0,.25,1) infinite; }}
    }}
    """
    band = (f'<rect x="0" y="{TOP - 8}" width="120" height="{CARD_H + 16}" rx="10" '
            f'fill="url(#att-kgrad)" class="att-kband"/>')
    h = TOP + CARD_H + 16
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" '
        f'viewBox="0 0 {W} {h}" role="img" '
        f'aria-label="attest reports four kinds of evidence">\n'
        f'<defs>\n'
        f'<linearGradient id="att-kgrad" x1="0" y1="0" x2="1" y2="0">\n'
        f'<stop offset="0" stop-color="#39c5cf" stop-opacity="0"/>\n'
        f'<stop offset=".5" stop-color="#39c5cf" stop-opacity=".22"/>\n'
        f'<stop offset="1" stop-color="#39c5cf" stop-opacity="0"/>\n'
        f'</linearGradient>\n</defs>\n'
        f'<style>{css}</style>\n'
        f'<rect width="{W}" height="{h}" rx="14" fill="{BG}"/>\n'
        + "\n".join(cards)
        + f'\n{band}\n</svg>\n')


def _wrap(s: str, cols: int) -> list[str]:
    """按显示宽度折行（CJK 算两列）。"""
    out, cur, w = [], "", 0
    for ch in s:
        cw = 2 if ord(ch) > 0x2E80 else 1
        if w + cw > cols:
            out.append(cur)
            cur, w = ch, cw
        else:
            cur += ch
            w += cw
    if cur:
        out.append(cur)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    svg = render()
    Path(args.out).write_text(svg, encoding="utf-8")
    print(f"已生成 {args.out}（{len(svg) / 1024:.1f} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
