#!/usr/bin/env python3
"""把 Archify 渲染出的工作流图抽成 README 能直接嵌的动画 SVG。

    # 1. 先用 Archify skill 从 candidate.json 渲染出 HTML（它的 finalize 会跑完
    #    validate / deliver / check / browser-check 四道闸）
    #    候选文件在 .archify/workflow-attest-flow-20261116-231500/candidate.json
    # 2. 再跑这个脚本：
    python3 tools/render_flow_svg.py <archify.html> \
      --out skills/attest/assets/attest-flow.svg

**为什么不能直接嵌 HTML**：GitHub 的 README 不跑 HTML 里的 JS，而 Archify 的
「trace 动画」是 viewer 在运行时驱动的 ⇒ 抽出来的静态 SVG 不会动。所以这里
**自己加一层动画**：节点逐个升起、连线有光点流过、泳道与阶段标签依次点亮，
全部用 SVG 内的 CSS keyframes 实现（浏览器用 <img> 加载 SVG 时 CSS 动画照跑）。

**三件必要的手术**：
1. 页面级选择器改成 SVG 根元素能命中的形式（html svg → svg；html → svg）。
2. 裁掉 viewer 的界面样式（工具栏、抽屉、引导层）—— 只留「选择器里出现过的
   类/属性在 SVG 标记里也存在」的规则，CSS 从 200KB 降到几十 KB。
3. 内联 CSS 里的 '<' / '&' 转义 —— SVG 以 XML 解析，裸 '<' 会把解析器带沟里
   （CSS 注释里的 "<html>" 会被当成子元素）。

**尊重减弱动效**：动画规则全部包在 `@media (prefers-reduced-motion: no-preference)`
里，系统关了动效的人看到的是一张完整静态图。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "skills" / "attest" / "assets" / "attest-flow.svg"

# 自加的那层动画：时长、每步的错峰、每条边的光点间距
CYCLE = "12s"
STAGGER = "110ms"

ANIM_CSS = f"""
/* ---- attest 自己加的动画层（Archify 的 trace 由 viewer 的 JS 驱动，嵌不进 README） ----
   原则：图在任何时刻都完整可读 —— 动的是光与呼吸，不是「逐行画出来」。
   README 里的读者可能只看一眼就滚过去，不能让他撞见半张图。 */
@keyframes att-glow {{
  0%, 100% {{ stroke-opacity: .58; }}
  50%      {{ stroke-opacity: 1; }}
}}
@keyframes att-float {{
  0%, 100% {{ transform: translateY(0); }}
  50%      {{ transform: translateY(-1.5px); }}
}}
@keyframes att-comet {{
  0%   {{ stroke-dashoffset: 80; opacity: 0; }}
  14%  {{ opacity: .9; }}
  72%  {{ opacity: .9; }}
  100% {{ stroke-dashoffset: -80; opacity: 0; }}
}}
@keyframes att-chip {{
  0%, 100% {{ fill-opacity: .55; }}
  50%      {{ fill-opacity: 1; }}
}}
@media (prefers-reduced-motion: no-preference) {{
  g[data-node-id] {{
    animation: att-float 9s ease-in-out infinite;
    animation-delay: calc(var(--d, 0) * 260ms);
  }}
  g[data-node-id] rect {{
    animation: att-glow 6s ease-in-out infinite;
    animation-delay: calc(var(--d, 0) * 400ms);
  }}
  path.att-comet {{
    stroke-dasharray: 2 26;
    animation: att-comet 5.5s linear infinite;
    animation-delay: calc(var(--d, 0) * 620ms);
  }}
  .phase-chip {{
    animation: att-chip 7s ease-in-out infinite;
  }}
}}
"""


def rewrite(css: str) -> str:
    """把页面级选择器改成 SVG 根元素能命中的形式。"""
    css = re.sub(r"\bhtml\s+svg\b", "svg", css)
    css = re.sub(r"\bhtml\s*\[", "[", css)
    css = re.sub(r"(^|[{};,])\s*html\b", r"\1svg", css)
    css = re.sub(r"(^|[{};,])\s*:root\b", r"\1svg", css)
    css = re.sub(r"(^|[{};,])\s*body\b", r"\1svg", css)
    return css


def split_rules(css: str) -> list[str]:
    """按顶层规则切分（@media / @keyframes 的花括号要配平）。"""
    out, buf, depth = [], [], 0
    for ch in css:
        buf.append(ch)
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                out.append("".join(buf))
                buf = []
    if buf:
        out.append("".join(buf))
    return out


def selector_tokens(selector: str) -> set[str]:
    t = set(re.findall(r"\.([A-Za-z][\w-]*)", selector))
    t |= set(re.findall(r"#([A-Za-z][\w-]*)", selector))
    t |= set(re.findall(r"\[([\w-]+)", selector))
    return t


def prune(css: str, markup: str) -> str:
    """只留选择器可能命中 SVG 标记里某个元素的规则（viewer 的界面样式全裁掉）。"""
    present = {c for g in re.findall(r'class="([^"]*)"', markup) for c in g.split()}
    present |= set(re.findall(r'id="([^"]*)"', markup))
    present |= set(re.findall(r"data-([\w-]+)", markup))
    kept = []
    for rule in split_rules(css):
        head = rule.split("{", 1)[0].strip()
        if head.startswith("@keyframes") or head.startswith("@font-face"):
            kept.append(rule)
            continue
        if head.startswith("@media") or head.startswith("@supports"):
            inner = rule[rule.index("{") + 1:rule.rindex("}")]
            sub = prune(inner, markup)
            if sub.strip():
                kept.append(f"{head}{{{sub}}}")
            continue
        sel = head.split(",")[0]
        t = selector_tokens(sel)
        if not t or any(x in present for x in t):
            kept.append(rule)
    return "\n".join(kept)


def add_comets(svg: str) -> str:
    """给每条边加一条同色的「光点」副本，沿线流动。"""
    out, last = [], 0
    pattern = re.compile(
        r"<path\b[^>]*?data-edge-id=\"[^\"]*\"[^>]*?(?:/>|>.*?</path>)", re.S)
    for m in pattern.finditer(svg):
        out.append(svg[last:m.start()])
        tag = m.group(0)
        cls = re.search(r'class="([^"]*)"', tag)
        if cls:
            comet = tag.replace('class="', 'class="att-comet ', 1)
            comet = re.sub(r'\s*data-edge-id="[^"]*"', "", comet, count=1)
            comet = re.sub(r'style="([^"]*)"', r'style="\1" data-comet="1"', comet, count=1)
            out.append(comet)
        out.append(tag)
        last = m.end()
    out.append(svg[last:])
    return "".join(out)


def stagger(svg: str) -> str:
    """给节点 / 边按文档顺序标上 --d（错峰用）。"""
    counter = {"n": 0, "e": 0}

    def node(m):
        counter["n"] += 1
        return m.group(0)[:-1] + f' style="--d:{counter["n"]}">'

    def edge(m):
        counter["e"] += 1
        tag = m.group(0)
        if "style=" in tag:
            return re.sub(r'style="([^"]*)"',
                          lambda x: f'style="{x.group(1)};--d:{counter["e"]}"', tag)
        return tag[:-1] + f' style="--d:{counter["e"]}">'

    svg = re.sub(r"<g\b[^>]*?data-node-id=\"[^\"]*\"[^>]*?>", node, svg)
    svg = re.sub(r"<path\b[^>]*?data-edge-id=\"[^\"]*\"[^>]*?>", edge, svg)
    return svg


def build(html_path: Path, theme: str) -> str:
    html = html_path.read_text(encoding="utf-8")
    m = re.search(r"<svg\b.*?</svg>", html, re.S)
    if not m:
        raise SystemExit(f"{html_path} 里没找到 <svg>")
    svg = m.group(0)
    styles = re.findall(r"<style[^>]*>(.*?)</style>", html, re.S)
    theme_css = styles[1] if len(styles) > 1 else ""
    svg_css = styles[2] if len(styles) > 2 else ""

    css = prune(rewrite(svg_css), svg) + "\n" + prune(rewrite(theme_css), svg)
    css = css.replace("&", "&amp;").replace("<", "&lt;")

    svg = add_comets(svg)
    svg = stagger(svg)
    svg = svg.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
    svg = re.sub(r"^<svg\b", f'<svg data-theme="{theme}"', svg, count=1)
    svg = svg.replace(">", f"><style>{css}\n{ANIM_CSS}</style>", 1)
    vb = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    if vb and "width=" not in svg.split(">", 1)[0]:
        svg = re.sub(r"^<svg\b", f'<svg width="{vb.group(1)}" height="{vb.group(2)}"', svg, count=1)
    return svg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("html", help="Archify finalize 出来的 HTML")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--theme", default="dark", choices=["dark", "light"])
    args = ap.parse_args()
    svg = build(Path(args.html), args.theme)
    Path(args.out).write_text(svg, encoding="utf-8")
    print(f"已生成 {args.out}（{len(svg) / 1024:.0f} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
