#!/usr/bin/env python3
"""渲染「双向对账」图（SVG，无第三方依赖）。

    python3 tools/render_reconcile_svg.py    # → skills/attest/assets/reconcile-2way.svg

attest 的核心机制是**双向对账**：计划里的判定点与代码里推导出的判定点互相对，
不变式与判定点互相对认领。三条判据各是一次对账，方向不同：

    计划里的判定点列 ⇄ 代码里推导出的判定点   → 计划与机器推导对齐
    高危判定点         ⇄ 认领它的不变式          → 应有判定点覆盖到位
    不变式             ⇄ 认领它的判定点          → 不变式有判定点

每次对账的两头**一头是人写的、一头是机器推的**（图里用「人写 / 机器推」标出）——
漏了哪一头，报告会指名道姓地报出来。

**动效尊重系统设置**：对账线按顺序描亮，包在
`@media (prefers-reduced-motion: no-preference)` 里。
"""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "skills" / "attest" / "assets" / "reconcile-2way.svg"

W = 920
H = 452
BG = "#0d1117"
EDGE = "#30363d"
FG = "#e6edf3"
MUTED = "#8b949e"
DIM = "#6e7681"
CYAN = "#39c5cf"
GREEN = "#3fb950"
AMBER = "#d29922"

HUMAN = "人写"
MACHINE = "机器推"

MONO = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, 'Noto Sans Mono CJK SC', 'PingFang SC', 'Microsoft YaHei', monospace"
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Noto Sans CJK SC', 'Noto Sans', Roboto, 'Helvetica Neue', Arial, sans-serif"

ROWS = [
    {
        "left": ("计划里的判定点列", HUMAN),
        "right": ("代码里推导出的判定点", MACHINE),
        "mid": "计划与机器推导对齐",
        "color": CYAN,
        "note": "计划写了代码里没有的 / 代码里有计划没写的",
    },
    {
        "left": ("高危判定点", MACHINE),
        "right": ("认领它的不变式", HUMAN),
        "mid": "应有判定点覆盖到位",
        "color": GREEN,
        "note": "四类高危（丢弃/错误路径/资源/状态）认不到不变式 ⇒ 报红",
    },
    {
        "left": ("不变式", HUMAN),
        "right": ("认领它的判定点", MACHINE),
        "mid": "不变式有判定点",
        "color": AMBER,
        "note": "一个判定点都不认领 ⇒ 只靠一条用例钉着，路径没被测全",
    },
]

LEFT_X = 40
RIGHT_X = W - 40
CARD_W = 244
CARD_H = 80
GAP = 26
TOP = 96


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def card(x: int, y: int, label: str, origin: str, color: str,
         anchor: str) -> str:
    """一侧的标签块：左边左对齐，右边右对齐；下面挂一个来源标签。"""
    tx = x + 18 if anchor == "start" else x + CARD_W - 18
    chip_x = tx if anchor == "start" else tx
    chip_anchor = "start" if anchor == "start" else "end"
    chip_fill = CYAN if origin == HUMAN else AMBER
    return f"""
  <g>
    <rect x="{x}" y="{y}" width="{CARD_W}" height="{CARD_H}" rx="10"
          fill="#161b22" stroke="{EDGE}"/>
    <rect x="{x if anchor == 'start' else x + CARD_W - 3}" y="{y}" width="3"
          height="{CARD_H}" rx="1.5" fill="{color}"/>
    <text x="{tx}" y="{y + 36}" text-anchor="{anchor}" fill="{FG}"
          font-family="{SANS}" font-size="17" font-weight="600">{esc(label)}</text>
    <circle cx="{chip_x + (5 if anchor == 'start' else -5)}" cy="{y + 60}" r="3.5"
            fill="{chip_fill}"/>
    <text x="{chip_x + (16 if anchor == 'start' else -16)}" y="{y + 64}"
          text-anchor="{chip_anchor}" fill="{DIM}" font-family="{MONO}"
          font-size="12">{esc(origin)}</text>
  </g>"""


def arrows(y: int, color: str, idx: int) -> str:
    """中间的两条对账线：上面往右、下面往左，**不穿过中间的说明文字**。"""
    lx = LEFT_X + CARD_W + 14
    rx = RIGHT_X - CARD_W - 14
    top = y + 16
    bottom = y + CARD_H - 16
    delay = 1.2 + idx * 1.6
    return f"""
  <g>
    <line x1="{lx}" y1="{top}" x2="{rx}" y2="{top}" stroke="{color}"
          stroke-width="1.6" stroke-linecap="round" opacity="0.5"
          marker-end="url(#arrow-{idx})">
      <animate attributeName="opacity" values="0.2;0.8;0.2" dur="3.2s"
               begin="{delay}s" repeatCount="indefinite"/>
    </line>
    <line x1="{rx}" y1="{bottom}" x2="{lx}" y2="{bottom}" stroke="{color}"
          stroke-width="1.6" stroke-linecap="round" opacity="0.5"
          marker-end="url(#arrow-rev-{idx})">
      <animate attributeName="opacity" values="0.2;0.8;0.2" dur="3.2s"
               begin="{delay + 1.6}s" repeatCount="indefinite"/>
    </line>
  </g>"""


def build() -> str:
    parts = [f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}"
     viewBox="0 0 {W} {H}" role="img"
     aria-label="attest 的双向对账：计划与代码互相对、判定点与不变式互相对认领，每次对账的一头是人写的、一头是机器推的">
  <title>attest 的双向对账</title>
  <defs>"""]
    for idx, row in enumerate(ROWS):
        parts.append(f"""
    <marker id="arrow-{idx}" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="{row['color']}"/>
    </marker>
    <marker id="arrow-rev-{idx}" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="{row['color']}"/>
    </marker>""")
    parts.append(f"""
  </defs>
  <rect width="{W}" height="{H}" fill="{BG}"/>
  <text x="{LEFT_X}" y="42" fill="{FG}" font-family="{SANS}" font-size="22"
        font-weight="700">双向对账：两头都查</text>
  <text x="{LEFT_X}" y="66" fill="{MUTED}" font-family="{MONO}" font-size="13"
        >每次对账，一头是人写的，一头是机器推的；漏了哪一头，报告会指名道姓地报出来</text>""")

    for idx, row in enumerate(ROWS):
        y = TOP + idx * (CARD_H + GAP)
        parts.append(card(LEFT_X, y, row["left"][0], row["left"][1],
                          row["color"], "start"))
        parts.append(card(RIGHT_X - CARD_W, y, row["right"][0], row["right"][1],
                          row["color"], "end"))
        parts.append(arrows(y, row["color"], idx))
        mid_x = W / 2
        parts.append(f"""
  <g>
    <text x="{mid_x}" y="{y + 36}" text-anchor="middle" fill="{row['color']}"
          font-family="{SANS}" font-size="15.5" font-weight="600">{esc(row['mid'])}</text>
    <text x="{mid_x}" y="{y + 58}" text-anchor="middle" fill="{DIM}"
          font-family="{MONO}" font-size="11.5">{esc(row['note'])}</text>
  </g>""")

    parts.append(f"""
  <g>
    <text x="{LEFT_X}" y="{H - 22}" fill="{DIM}" font-family="{MONO}" font-size="12">
      对账不通过 ⇒ 报红，不放过</text>
    <text x="{W - RIGHT_X}" y="{H - 22}" text-anchor="end" fill="{DIM}"
          font-family="{MONO}" font-size="12">
      <tspan fill="{CYAN}">●</tspan> 人写
      <tspan fill="{AMBER}">●</tspan> 机器推</text>
  </g>
</svg>""")
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(), encoding="utf-8")
    print(f"已生成 {out}（{out.stat().st_size} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
