#!/usr/bin/env python3
"""渲染"证据四档"动画条（SVG，无第三方依赖）。

    python3 tools/render_kinds_svg.py             # → skills/attest/assets/evidence-kinds.svg
    python3 tools/render_kinds_svg.py --lang en   # → skills/attest/assets/evidence-kinds.en.svg

四档是 attest 报告的骨架：已实测 / 静态信号 / 未实测 / 本工具不判定。
这张条把它变成四个卡片，一条亮带按顺序扫过去 —— 扫到哪张，哪张亮一下。
读者一眼就知道「结论分四类」，而不必先读完正文。

英文版（--lang en）给英文 README 用：只换文字，不换视觉系统；
英文句子比中文长，所以折行宽度和卡片高度另算。

**动效尊重系统设置**：动画包在 `@media (prefers-reduced-motion: no-preference)` 里。
"""
from __future__ import annotations

import argparse
import html
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTS = {
    "zh": ROOT / "skills" / "attest" / "assets" / "evidence-kinds.svg",
    "en": ROOT / "skills" / "attest" / "assets" / "evidence-kinds.en.svg",
}

W = 794
CARD_W = 182
GAP = 14
TOP = 30
BG = "#0d1117"
EDGE = "#30363d"
FG = "#e6edf3"
MUTED = "#8b949e"

# 标题、副标题（一句话）、色
KINDS = {
    "zh": [
        ("已实测", "Measured", "跑过了：命令、退出码、输出都在报告里", "#3fb950"),
        ("静态信号", "Static signal", "读源码读出来的，永远不能单独撑起一个「通过」", "#39c5cf"),
        ("未实测", "Not measured", "测不了：报告里写清缺了哪个前置条件", "#d29922"),
        ("本工具不判定", "Not judged", "超出范围：需求理解、产品行为、性能、安全…", "#6e7681"),
    ],
    "en": [
        ("Measured", "RUN",
         "Something ran: the command, its exit code and its output are in the report",
         "#3fb950"),
        ("Static signal", "READ",
         "Read from the source, not run. Never supports a pass on its own",
         "#39c5cf"),
        ("Not measured", "BLOCKED",
         "Could not be measured; the report names the missing precondition",
         "#d29922"),
        ("Not judged", "OUT OF SCOPE",
         "Out of scope by design: requirements, product behaviour, performance, security",
         "#6e7681"),
    ],
}

# 排版：中文按显示列（CJK 占两列）折行；英文按词折行，字号不变、行距不变，
# 卡片高一点才放得下最长那张的四行说明。
LAYOUT = {
    "zh": {"card_h": 132, "note_cols": 22, "note_size": 11, "note_dy": 15, "wrap": "cjk"},
    "en": {"card_h": 140, "note_cols": 24, "note_size": 11, "note_dy": 15, "wrap": "words"},
}


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def render(lang: str) -> str:
    cfg = LAYOUT[lang]
    card_h = cfg["card_h"]
    cards = []
    for i, (title, sub, note, color) in enumerate(KINDS[lang]):
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
        lines = (_wrap(note, cfg["note_cols"]) if cfg["wrap"] == "cjk"
                 else _wrap_words(note, cfg["note_cols"]))
        cards.append(
            f'<g class="att-card" style="--i:{i}">\n'
            f'  <rect x="{x}" y="{y}" width="{CARD_W}" height="{card_h}" rx="12" '
            f'class="att-card-bg"/>\n'
            f'  <rect x="{x}" y="{y}" width="3.5" height="{card_h}" rx="2" fill="{color}"/>\n'
            f'  {icon}\n'
            f'  <text x="{x + 44}" y="{y + 31}" font-size="16" font-weight="650" '
            f'fill="{FG}">{esc(title)}</text>\n'
            f'  <text x="{x + 44}" y="{y + 48}" font-size="10.5" fill="{color}" '
            f'letter-spacing=".4">{esc(sub)}</text>\n'
            f'  <text x="{x + 20}" y="{y + 78}" font-size="{cfg["note_size"]}" '
            f'fill="{MUTED}">\n'
            + "".join(
                f'    <tspan x="{x + 20}" dy="{0 if j == 0 else cfg["note_dy"]}">'
                f'{esc(line)}</tspan>\n'
                for j, line in enumerate(lines)
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
    band = (f'<rect x="0" y="{TOP - 8}" width="120" height="{card_h + 16}" rx="10" '
            f'fill="url(#att-kgrad)" class="att-kband"/>')
    h = TOP + card_h + 16
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


def _wrap_words(s: str, cols: int) -> list[str]:
    """按词折行（英文用）：不劈单词，行数最少的前提下各行长度尽量接近。

    纯贪心会留下「security」这种孤字行，所以这里先最少行数、再最小化行长差。
    """
    words = s.split(" ")
    n = len(words)
    # best[i] = (行数, 代价, 切点)；代价是各行「离满行还差多少」的平方和
    best: list[tuple[int, int, list[int]] | None] = [None] * (n + 1)
    best[n] = (0, 0, [])
    for i in range(n - 1, -1, -1):
        cand = None
        for j in range(i + 1, n + 1):
            length = len(" ".join(words[i:j]))
            if length > cols:
                break
            rest = best[j]
            if rest is None:
                continue
            key = (rest[0] + 1, rest[1] + (cols - length) ** 2)
            if cand is None or key < (cand[0], cand[1]):
                cand = (key[0], key[1], [i, j] + rest[2])
        best[i] = cand
    cuts = best[0][2]
    return [" ".join(words[a:b]) for a, b in zip(cuts[::2], cuts[1::2])]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lang", choices=("zh", "en"), default="zh")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out = Path(args.out) if args.out else OUTS[args.lang]
    svg = render(args.lang)
    out.write_text(svg, encoding="utf-8")
    print(f"已生成 {out}（{len(svg) / 1024:.1f} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
