#!/usr/bin/env python3
"""把一次真实运行渲染成会动的首屏图（SVG，无第三方依赖）。

    python3 tools/render_hero_animated.py    # → skills/attest/assets/hero-animated.svg

**为什么不是"逐行打出来"**：README 里的读者可能只看一眼就滚过去，而逐行显现
意味着他在头两秒看到的是一张空卡。所以这里让**每一行始终都在**，动的是别的：
一条亮带沿着报告往下扫（像工具在读自己的报告）、末尾的光标在闪、结论行缓慢
呼吸。扫到哪行，哪行被点亮。

**动效尊重系统设置**：动画全部包在 `@media (prefers-reduced-motion: no-preference)`
里 —— 关了动效的人看到的是一张完整静态图。

**内容是真的**：跑一次 readiness 取原样输出，跟 tools/render_hero.py 一样
（没有输出就不做图）。
"""
from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from render_hero import (  # noqa: E402
    BG, COLS, FG, FS, LH, MUTED, PAD, colorize, report_text, width, wrap,
)

OUT = ROOT / "skills" / "attest" / "assets" / "hero-animated.svg"

# 卡片外观
CARD = "#0d1117"
EDGE = "#30363d"
BAND = "#39c5cf"
DIM = "#8b949e"


def esc(s: str) -> str:
    """SVG 是 XML：文本里的 & < > 要转义。"""
    return html.escape(s, quote=False)


def render(lines: list[str]) -> str:
    wrapped: list[str] = []
    for ln in lines:
        wrapped.extend(wrap(ln, COLS))
    longest = max((width(x) for x in wrapped), default=1)
    w = PAD * 2 + longest * (FS * 0.6)
    title_h = 30
    h = PAD * 2 + title_h + len(wrapped) * LH + 22

    body = []
    for i, ln in enumerate(wrapped):
        y = PAD + title_h + LH * (i + 1) - 4
        body.append(f'<text x="{PAD}" y="{y:.0f}" class="att-line" '
                    f'style="--d:{i}">{colorize(esc(ln))}</text>')

    verdict_i = next((i for i, ln in enumerate(wrapped)
                      if ln.startswith("结论")), None)
    verdict = ""
    if verdict_i is not None:
        y = PAD + title_h + LH * (verdict_i + 1) - 4
        verdict = (f'<rect x="{PAD - 6}" y="{y - 14}" width="{longest * (FS * 0.6) + 12}" '
                   f'height="{LH}" rx="6" class="att-verdict-box"/>')

    css = f"""
    .att-mono {{ font-family: ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
    @keyframes att-scan {{
      0%   {{ transform: translateY({PAD + title_h}px); opacity: 0; }}
      8%   {{ opacity: .5; }}
      92%  {{ opacity: .5; }}
      100% {{ transform: translateY({h - PAD}px); opacity: 0; }}
    }}
    @keyframes att-cursor {{
      0%, 49%   {{ opacity: 1; }}
      50%, 100% {{ opacity: 0; }}
    }}
    @keyframes att-breathe {{
      0%, 100% {{ opacity: .35; }}
      50%      {{ opacity: .85; }}
    }}
    @keyframes att-reveal {{
      from {{ opacity: 0; }}
      to   {{ opacity: 1; }}
    }}
    @media (prefers-reduced-motion: no-preference) {{
      .att-line {{
        animation: att-reveal .5s ease-out both;
        animation-delay: calc(var(--d) * 45ms + .4s);
      }}
      .att-scan {{
        animation: att-scan 9s cubic-bezier(.4,0,.2,1) infinite;
        animation-delay: 2.6s;
      }}
      .att-cursor {{
        animation: att-cursor 1.05s steps(1) infinite;
      }}
      .att-verdict-box {{
        animation: att-breathe 5s ease-in-out infinite;
      }}
    }}
    """

    cursor = (f'<rect x="{PAD}" y="{h - PAD - 14}" width="8" height="14" '
              f'fill="{BAND}" class="att-cursor"/>')
    scan = (f'<rect x="0" y="0" width="{w:.0f}" height="26" rx="6" fill="url(#att-band)" '
            f'class="att-scan"/>')
    dots = "".join(
        f'<circle cx="{18 + i * 15}" cy="16" r="4" fill="{c}" opacity=".55"/>'
        for i, c in enumerate(("#f85149", "#d29922", "#3fb950")))
    title = (f'<text x="{PAD + 58}" y="20" class="att-mono" font-size="11" '
             f'fill="{DIM}">attest · readiness.py · fixtures/ts-colocated</text>')

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h}" '
        f'viewBox="0 0 {w:.0f} {h}" role="img" '
        f'aria-label="attest report">\n'
        f'<defs>\n'
        f'<linearGradient id="att-band" x1="0" y1="0" x2="0" y2="1">\n'
        f'<stop offset="0" stop-color="{BAND}" stop-opacity="0"/>\n'
        f'<stop offset=".5" stop-color="{BAND}" stop-opacity=".16"/>\n'
        f'<stop offset="1" stop-color="{BAND}" stop-opacity="0"/>\n'
        f'</linearGradient>\n'
        f'<filter id="att-shadow" x="-10%" y="-10%" width="120%" height="130%">\n'
        f'<feDropShadow dx="0" dy="8" stdDeviation="12" flood-color="#000" '
        f'flood-opacity=".45"/>\n'
        f'</filter>\n'
        f'</defs>\n'
        f'<style>{css}</style>\n'
        f'<g filter="url(#att-shadow)">\n'
        f'<rect width="{w:.0f}" height="{h}" rx="12" fill="{CARD}" stroke="{EDGE}"/>\n'
        f'</g>\n'
        f'<line x1="0" y1="30" x2="{w:.0f}" y2="30" stroke="{EDGE}" opacity=".7"/>\n'
        f'{dots}\n{title}\n'
        f'<g class="att-mono" font-size="{FS}" fill="{FG}">\n'
        + "\n".join(body)
        + f'\n</g>\n{verdict}\n{cursor}\n{scan}\n</svg>\n')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixture", default="fixtures/ts-colocated")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    svg = render(report_text(args.fixture))
    Path(args.out).write_text(svg, encoding="utf-8")
    print(f"已生成 {args.out}（{len(svg) / 1024:.1f} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
