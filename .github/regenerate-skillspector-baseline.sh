#!/usr/bin/env bash
# 重新生成 SkillSpector 基线。
#
#   bash .github/regenerate-skillspector-baseline.sh
#
# 基线里只收 **HIGH / CRITICAL** —— 那些是评审（docs/oss-release-review.md 第六节）
# 逐条复核过的项。MEDIUM 项不进基线：每次扫描都照样报出来，不被藏起来。
#
# 基线指纹含**位置** ⇒ 改了 scripts/ 里的代码就要重新跑一次这个脚本，
# 否则 CI 会把"老位置"的 HIGH 报成新的。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$REPO_ROOT/.github/skillspector-baseline.yaml"

if ! command -v skillspector >/dev/null 2>&1; then
  echo "装不上 skillspector：pip install skillspector" >&2
  exit 2
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# 扫一份干净的副本：仓库里的字节码不能进基线，也不该被扫。
(cd "$REPO_ROOT" && tar --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='.git' --exclude='.omx' -cf - .) | (cd "$WORK" && tar -xf -)

cd "$WORK"
# 产物写到**扫描目录之外** —— 写在里面会被下一次扫描扫到（自己扫自己）。
OUT_DIR="$(mktemp -d)"
# 扫到 HIGH 时 scanner 的退出码是非 0 —— 那正是我们要收进基线的东西。
skillspector scan . --no-llm --format json --output "$OUT_DIR/scan.json" >/dev/null || true
skillspector baseline . --no-llm --output "$OUT_DIR/base.yaml" \
  --reason "reviewed" >/dev/null || true

if [ ! -f "$OUT_DIR/base.yaml" ] || [ ! -f "$OUT_DIR/scan.json" ]; then
  echo "扫描没产出结果（skillspector 装坏了？）" >&2
  exit 1
fi

python3 - "$OUT" "$OUT_DIR/scan.json" "$OUT_DIR/base.yaml" <<'PY'
import json
import re
import sys

out, scan_path, base_path = sys.argv[1], sys.argv[2], sys.argv[3]

REASON = {
    "E2": "子进程只透传白名单环境变量（child_env）—— 逐处复核过，"
          "见 docs/oss-release-review.md 第六节",
    "AE1": "文档（SKILL.md）引用 scripts/readiness.py 时只引了片段 —— "
           "是文档里的引用，不是缺失的产物",
    "PE3": "注释里点名 .npmrc，是为了说明**不**复制它（凭据不该进临时副本）",
    "SC9": ".github/ 下随仓库发布的维护脚本 —— 是仓库的一部分，"
           "不是藏起来的可执行产物",
    "AST9": "反射调用误报（getattr(args, 'exec') 只是读 argparse 的属性，"
            "不是执行 sink）—— 已改写为直接属性访问",
}

scan = json.load(open(scan_path, encoding="utf-8"))
high_rules = {i.get("id") for i in (scan.get("issues") or [])
              if i.get("severity") in ("HIGH", "CRITICAL")}

text = open(base_path, encoding="utf-8").read()
version = re.search(r"^version: (.+)$", text, re.M)
scanner_version = re.search(r"^scanner_version: (.+)$", text, re.M)
entries = re.findall(
    r"- hash: sha256:([0-9a-f]+)\n  rule_id: (\w+)\n  file: (.*)\n  reason: (.*)",
    text)

kept = [(h, r, f) for h, r, f, _ in entries if r in high_rules]
if not kept:
    print("没有 HIGH/CRITICAL —— 基线是空的（干净）", file=sys.stderr)

with open(out, "w", encoding="utf-8") as fh:
    fh.write("# SkillSpector baseline —— 这里列出的 finding 在后续扫描里被抑制。\n")
    fh.write("# 只收 HIGH / CRITICAL：它们逐条复核过（docs/oss-release-review.md 第六节）。\n")
    fh.write("# MEDIUM 项**不进**基线 —— 每次扫描照样报出来，不藏。\n")
    fh.write("# 重新生成：bash .github/regenerate-skillspector-baseline.sh\n")
    fh.write(f"version: {version.group(1) if version else 2}\n")
    fh.write(f"scanner_version: "
             f"{scanner_version.group(1).strip() if scanner_version else ''}\n")
    fh.write("rules: []\n")
    fh.write("fingerprints:\n")
    for h, rule, f in kept:
        fh.write(f"- hash: sha256:{h}\n")
        fh.write(f"  rule_id: {rule}\n")
        fh.write(f"  file: {f}\n")
        fh.write(f"  reason: {REASON.get(rule, 'reviewed')}\n")

print(f"写入 {out}：{len(kept)} 条 HIGH/CRITICAL "
      f"（共扫描到 {len(entries)} 条，全部 HIGH 规则：{sorted(high_rules)}）")
PY
