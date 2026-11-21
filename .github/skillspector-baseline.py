#!/usr/bin/env python3
"""把仓库的规则级 allowlist 翻译成 SkillSpector 认的 baseline 文件。

    python3 .github/skillspector-baseline.py --out /tmp/ss-baseline.yaml
    skillspector scan skills/attest --baseline /tmp/ss-baseline.yaml   # 退出码 0
    skillevaluator security-scan skills/attest \
        --skillspector-baseline /tmp/ss-baseline.yaml                   # 证据完整

为什么需要它：SkillSpector 扫到 HIGH 时退出码是非 0（这是它的约定，见
.github/check-skillspector.sh 的注释），而 SkillEvaluator 的 Security Scan
步骤把「非 0」读成「扫描器坏了」⇒ 那一步永远 incomplete，拿不到安全证据。
把**已经人工复核过、明确接受**的规则 id 写成 baseline，扫描器就以 0 退出，
评估器能给出完整的 Security Scan 结果。

单一事实源仍然是 .github/skillspector-allowlist.yaml —— 这个脚本只做翻译，
不新增第二份要人工维护的清单，也不放宽任何一条没复核过的规则。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = ROOT / ".github" / "skillspector-allowlist.yaml"

# 与 check-skillspector.sh 用的是同一条正则：只认「规则 id」，不认位置指纹。
ID_RE = re.compile(r"^\s*-\s+id:\s*(\w+)", re.M)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="写出的 baseline YAML 路径")
    args = ap.parse_args()

    if not ALLOWLIST.is_file():
        print(f"找不到 {ALLOWLIST}", file=sys.stderr)
        return 1

    ids = sorted(set(ID_RE.findall(ALLOWLIST.read_text(encoding="utf-8"))))
    if not ids:
        print(f"{ALLOWLIST} 里没解析出任何规则 id", file=sys.stderr)
        return 1

    lines = [
        "# SkillSpector baseline —— 由 .github/skillspector-baseline.py 生成，别手改。",
        "# 规则 id 来自 .github/skillspector-allowlist.yaml（人工逐条复核过）。",
        "# 每条的复核理由在 allowlist 文件里，这里只放指针。",
        "version: 2",
        "rules:",
    ]
    for rid in ids:
        lines.append(f'  - id: "{rid}"')
        lines.append(
            f'    reason: >-'
            f"\n      已复核接受 —— 理由见 .github/skillspector-allowlist.yaml 的 {rid} 条。"
        )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"写出 {out}（{len(ids)} 条规则：{', '.join(ids)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
