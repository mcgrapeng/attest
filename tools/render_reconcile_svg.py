#!/usr/bin/env python3
"""渲染「双向对账」图（SVG，无第三方依赖）。

    python3 tools/render_reconcile_svg.py              # → skills/attest/assets/reconcile-2way.svg
    python3 tools/render_reconcile_svg.py --lang en    # → skills/attest/assets/reconcile-2way.en.svg

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
OUTS = {
    "zh": ROOT / "skills" / "attest" / "assets" / "reconcile-2way.svg",
    "en": ROOT / "skills" / "attest" / "assets" / "reconcile-2way.en.svg",
}

BG = "#0d1117"
EDGE = "#30363d"
FG = "#e6edf3"
MUTED = "#8b949e"
DIM = "#6e7681"
CYAN = "#39c5cf"
GREEN = "#3fb950"
AMBER = "#d29922"

HUMAN = "human"
MACHINE = "machine"

MONO = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, 'Noto Sans Mono CJK SC', 'PingFang SC', 'Microsoft YaHei', monospace"
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Noto Sans CJK SC', 'Noto Sans', Roboto, 'Helvetica Neue', Arial, sans-serif"

ROW_COLORS = [CYAN, GREEN, AMBER]

CONTENT = {
    "zh": {
        "svg_title": "attest 的双向对账",
        "aria": "attest 的双向对账：计划与代码互相对、判定点与不变式互相对认领，每次对账的一头是人写的、一头是机器推的",
        "title": "双向对账：两头都查",
        "subtitle": "每次对账，一头是人写的，一头是机器推的；漏了哪一头，报告会指名道姓地报出来",
        "origin": {HUMAN: "人写", MACHINE: "机器推"},
        "rows": [
            {
                "left": ("计划里的判定点列", HUMAN),
                "right": ("代码里推导出的判定点", MACHINE),
                "mid": "计划与机器推导对齐",
                "note": "计划写了代码里没有的 / 代码里有计划没写的",
            },
            {
                "left": ("高危判定点", MACHINE),
                "right": ("认领它的不变式", HUMAN),
                "mid": "应有判定点覆盖到位",
                "note": "四类高危（丢弃/错误路径/资源/状态）认不到不变式 ⇒ 报红",
            },
            {
                "left": ("不变式", HUMAN),
                "right": ("认领它的判定点", MACHINE),
                "mid": "不变式有判定点",
                "note": "一个判定点都不认领 ⇒ 只靠一条用例钉着，路径没被测全",
            },
        ],
        "footer_left": "对账不通过 ⇒ 报红，不放过",
        "legend": [HUMAN, MACHINE],
    },
    "en": {
        "svg_title": "attest two-way reconciliation",
        "aria": "attest two-way reconciliation: the plan and the code check each other, decision points and invariants claim each other; every reconciliation has one human-written side and one machine-derived side",
        "title": "Two-way reconciliation: both sides get checked",
        "subtitle": "Each reconciliation has one human-written side and one machine-derived side; a gap on either side is named in the report",
        "origin": {HUMAN: "human-written", MACHINE: "machine-derived"},
        "rows": [
            {
                "left": ("The plan's decision-point list", HUMAN),
                "right": ("Decision points derived from the code", MACHINE),
                "mid": "The plan matches the machine-derived points",
                "note": "Points the plan lists that the code does not have, and the other way round",
            },
            {
                "left": ("High-risk decision points", MACHINE),
                "right": ("The invariant that claims them", HUMAN),
                "mid": "The decision points are covered",
                "note": "The four high-risk kinds (drop / error path / resource / state) claimed by no invariant ⇒ red",
            },
            {
                "left": ("Invariants", HUMAN),
                "right": ("The decision points that claim them", MACHINE),
                "mid": "Every invariant reaches a decision point",
                "note": "An invariant no decision point claims ⇒ one test case holds it and the paths are not covered",
            },
        ],
        "footer_left": "A reconciliation that fails ⇒ red, never passed over",
        "legend": [HUMAN, MACHINE],
    },
}

LAYOUT = {
    "zh": {
        "w": 920,
        "h": 452,
        "left_x": 40,
        "card_w": 244,
        "card_h": 80,
        "gap": 26,
        "top": 96,
        "title_y": 42,
        "title_size": 22,
        "sub_y": 66,
        "sub_size": 13,
        "sub_wrap": 9999,
        "sub_line_h": 18,
        "label_size": 17,
        "label_wrap": 9999,
        "label_dy": 36,
        "label_line_h": 22,
        "chip_size": 12,
        "chip_circle_dy": 60,
        "chip_text_dy": 64,
        "mid_size": 15.5,
        "mid_wrap": 9999,
        "mid_dy": 36,
        "note_size": 11.5,
        "note_wrap": 9999,
        "note_dy": 58,
        "note_line_h": 16,
        "foot_size": 12,
        # 图例右对齐（贴右边缘）：原先写 40 会把右对齐的图例推到左边缘，
        # 压在左边那句说明上。
        "foot_right_x": 880,
    },
    "en": {
        "w": 1240,
        "h": 528,
        "left_x": 40,
        "card_w": 372,
        "card_h": 104,
        "gap": 30,
        "top": 112,
        "title_y": 42,
        "title_size": 22,
        "sub_y": 66,
        "sub_size": 13,
        "sub_wrap": 1120,
        "sub_line_h": 18,
        "label_size": 16.5,
        "label_wrap": 280,
        "label_dy": 34,
        "label_line_h": 22,
        "chip_size": 12,
        "chip_circle_dy": 78,
        "chip_text_dy": 82,
        "mid_size": 15.5,
        "mid_wrap": 400,
        "mid_dy": 40,
        "note_size": 11.5,
        "note_wrap": 360,
        "note_dy": 58,
        "note_line_h": 16,
        "foot_size": 12,
        # 英文图例靠右排，别撞上左边的说明
        "foot_right_x": 1200,
    },
}


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def text_width(s: str, size: float, mono: bool) -> float:
    """粗略估算一段文字的像素宽度（CJK 按全角算）。"""
    w = 0.0
    for ch in s:
        if ord(ch) >= 0x2E80:
            w += size
        elif mono:
            w += size * 0.6
        else:
            w += size * 0.55
    return w


def wrap(s: str, max_px: float, size: float, mono: bool = False) -> list[str]:
    """按像素宽度折行：西文按空格断，没有空格的长串（CJK）按字断。"""
    if max_px <= 0 or text_width(s, size, mono) <= max_px:
        return [s]
    lines: list[str] = []
    cur = ""
    for chunk in s.split(" "):
        if text_width(chunk, size, mono) > max_px:
            if cur:
                lines.append(cur)
                cur = ""
            piece = ""
            for ch in chunk:
                if piece and text_width(piece + ch, size, mono) > max_px:
                    lines.append(piece)
                    piece = ch
                else:
                    piece += ch
            cur = piece
            continue
        cand = f"{cur} {chunk}".strip()
        if cur and text_width(cand, size, mono) > max_px:
            lines.append(cur)
            cur = chunk
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


def spans(x: int, lines: list[str], line_h: float) -> str:
    """多行文本的 <tspan> 序列（首行 dy=0 贴住 <text> 的 y）。"""
    out = []
    for i, line in enumerate(lines):
        dy = 0 if i == 0 else line_h
        out.append(f'<tspan x="{x}" dy="{dy}">{esc(line)}</tspan>')
    return "".join(out)


def card(x: int, y: int, label: str, origin: str, origin_text: str, color: str,
         anchor: str, L: dict) -> str:
    """一侧的标签块：左边左对齐，右边右对齐；下面挂一个来源标签。"""
    card_w = L["card_w"]
    tx = x + 18 if anchor == "start" else x + card_w - 18
    chip_x = tx
    chip_anchor = "start" if anchor == "start" else "end"
    chip_fill = CYAN if origin == HUMAN else AMBER
    label_lines = wrap(label, L["label_wrap"], L["label_size"])
    if len(label_lines) == 1:
        label_markup = (
            f'<text x="{tx}" y="{y + L["label_dy"]}" text-anchor="{anchor}" fill="{FG}"\n'
            f'          font-family="{SANS}" font-size="{L["label_size"]}" '
            f'font-weight="600">{esc(label)}</text>')
    else:
        label_markup = (
            f'<text x="{tx}" y="{y + L["label_dy"]}" text-anchor="{anchor}" fill="{FG}"\n'
            f'          font-family="{SANS}" font-size="{L["label_size"]}" '
            f'font-weight="600">{spans(tx, label_lines, L["label_line_h"])}</text>')
    return f"""
  <g>
    <rect x="{x}" y="{y}" width="{card_w}" height="{L["card_h"]}" rx="10"
          fill="#161b22" stroke="{EDGE}"/>
    <rect x="{x if anchor == 'start' else x + card_w - 3}" y="{y}" width="3"
          height="{L["card_h"]}" rx="1.5" fill="{color}"/>
    {label_markup}
    <circle cx="{chip_x + (5 if anchor == 'start' else -5)}" cy="{y + L["chip_circle_dy"]}" r="3.5"
            fill="{chip_fill}"/>
    <text x="{chip_x + (16 if anchor == 'start' else -16)}" y="{y + L["chip_text_dy"]}"
          text-anchor="{chip_anchor}" fill="{DIM}" font-family="{MONO}"
          font-size="{L["chip_size"]}">{esc(origin_text)}</text>
  </g>"""


def arrows(y: int, color: str, idx: int, L: dict) -> str:
    """中间的两条对账线：上面往右、下面往左，**不穿过中间的说明文字**。

    亮带用 CSS 动画（不是 SMIL）：SMIL 没有「系统要减弱动效时关掉」的办法，
    CSS 有 —— 动画写在 `@media (prefers-reduced-motion: no-preference)` 里，
    系统开了减弱动效就只剩静止的对账线。
    """
    left_x = L["left_x"]
    right_x = L["w"] - L["left_x"]
    lx = left_x + L["card_w"] + 14
    rx = right_x - L["card_w"] - 14
    top = y + 16
    bottom = y + L["card_h"] - 16
    delay = 1.2 + idx * 1.6
    return f"""
  <g>
    <line x1="{lx}" y1="{top}" x2="{rx}" y2="{top}" stroke="{color}"
          stroke-width="1.6" stroke-linecap="round" opacity="0.5"
          class="att-recon-line" style="--d:{delay}"
          marker-end="url(#arrow-{idx})"/>
    <line x1="{rx}" y1="{bottom}" x2="{lx}" y2="{bottom}" stroke="{color}"
          stroke-width="1.6" stroke-linecap="round" opacity="0.5"
          class="att-recon-line" style="--d:{delay + 1.6}"
          marker-end="url(#arrow-rev-{idx})"/>
  </g>"""


def build(lang: str) -> str:
    c = CONTENT[lang]
    L = LAYOUT[lang]
    W = L["w"]
    H = L["h"]
    LEFT_X = L["left_x"]
    RIGHT_X = W - L["left_x"]

    sub_lines = wrap(c["subtitle"], L["sub_wrap"], L["sub_size"], mono=True)
    if len(sub_lines) == 1:
        sub_markup = (f'  <text x="{LEFT_X}" y="{L["sub_y"]}" fill="{MUTED}" '
                      f'font-family="{MONO}" font-size="{L["sub_size"]}"\n'
                      f'        >{esc(c["subtitle"])}</text>')
    else:
        sub_markup = (f'  <text x="{LEFT_X}" y="{L["sub_y"]}" fill="{MUTED}" '
                      f'font-family="{MONO}" font-size="{L["sub_size"]}">'
                      f'{spans(LEFT_X, sub_lines, L["sub_line_h"])}</text>')

    legend = "\n      ".join(
        f'<tspan fill="{CYAN if tag == HUMAN else AMBER}">●</tspan> {c["origin"][tag]}'
        for tag in c["legend"])

    parts = [f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}"
     viewBox="0 0 {W} {H}" role="img"
     aria-label="{c["aria"]}">
  <title>{c["svg_title"]}</title>
  <defs>"""]
    for idx, color in enumerate(ROW_COLORS):
        parts.append(f"""
    <marker id="arrow-{idx}" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="{color}"/>
    </marker>
    <marker id="arrow-rev-{idx}" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="{color}"/>
    </marker>""")
    parts.append(f"""
  </defs>
  <style>
    @keyframes att-recon-pulse {{
      0%, 100% {{ opacity: .2; }}
      50%      {{ opacity: .8; }}
    }}
    @media (prefers-reduced-motion: no-preference) {{
      .att-recon-line {{ animation: att-recon-pulse 3.2s ease-in-out infinite;
                         animation-delay: calc(var(--d) * 1s); }}
    }}
  </style>
  <rect width="{W}" height="{H}" fill="{BG}"/>
  <text x="{LEFT_X}" y="{L["title_y"]}" fill="{FG}" font-family="{SANS}" font-size="{L["title_size"]}"
        font-weight="700">{esc(c["title"])}</text>
{sub_markup}""")

    for idx, row in enumerate(c["rows"]):
        y = L["top"] + idx * (L["card_h"] + L["gap"])
        color = ROW_COLORS[idx]
        parts.append(card(LEFT_X, y, row["left"][0], row["left"][1],
                          c["origin"][row["left"][1]], color, "start", L))
        parts.append(card(RIGHT_X - L["card_w"], y, row["right"][0], row["right"][1],
                          c["origin"][row["right"][1]], color, "end", L))
        parts.append(arrows(y, color, idx, L))
        mid_x = W / 2
        mid_lines = wrap(row["mid"], L["mid_wrap"], L["mid_size"])
        note_lines = wrap(row["note"], L["note_wrap"], L["note_size"], mono=True)
        if len(mid_lines) == 1:
            mid_markup = (f'<text x="{mid_x}" y="{y + L["mid_dy"]}" text-anchor="middle" '
                          f'fill="{color}"\n'
                          f'          font-family="{SANS}" font-size="{L["mid_size"]}" '
                          f'font-weight="600">{esc(row["mid"])}</text>')
        else:
            mid_markup = (f'<text x="{mid_x}" y="{y + L["mid_dy"]}" text-anchor="middle" '
                          f'fill="{color}"\n'
                          f'          font-family="{SANS}" font-size="{L["mid_size"]}" '
                          f'font-weight="600">{spans(mid_x, mid_lines, L["label_line_h"])}</text>')
        if len(note_lines) == 1:
            note_markup = (f'<text x="{mid_x}" y="{y + L["note_dy"]}" text-anchor="middle" '
                           f'fill="{DIM}"\n'
                           f'          font-family="{MONO}" font-size="{L["note_size"]}">'
                           f'{esc(row["note"])}</text>')
        else:
            note_markup = (f'<text x="{mid_x}" y="{y + L["note_dy"]}" text-anchor="middle" '
                           f'fill="{DIM}"\n'
                           f'          font-family="{MONO}" font-size="{L["note_size"]}">'
                           f'{spans(mid_x, note_lines, L["note_line_h"])}</text>')
        parts.append(f"""
  <g>
    {mid_markup}
    {note_markup}
  </g>""")

    parts.append(f"""
  <g>
    <text x="{LEFT_X}" y="{H - 22}" fill="{DIM}" font-family="{MONO}" font-size="{L["foot_size"]}">
      {esc(c["footer_left"])}</text>
    <text x="{L["foot_right_x"]}" y="{H - 22}" text-anchor="end" fill="{DIM}"
          font-family="{MONO}" font-size="{L["foot_size"]}">
      {legend}</text>
  </g>
</svg>""")
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=sorted(OUTS), default="zh")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out = Path(args.out) if args.out else OUTS[args.lang]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(args.lang), encoding="utf-8")
    print(f"已生成 {out}（{out.stat().st_size} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
