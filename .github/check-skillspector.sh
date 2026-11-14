#!/usr/bin/env bash
# 查 SkillSpector 扫描结果里有没有**未放行**的 HIGH / CRITICAL。
#
#   bash .github/check-skillspector.sh
#
# 退出码：
#   0  HIGH/CRITICAL 全部落在 .github/skillspector-allowlist.yaml 里
#   1  有未放行的 HIGH/CRITICAL（改代码，别加 allowlist 条目）
#   2  skillspector 没装 / 扫描失败
#
# 扫的是**去掉字节码的干净副本** —— 仓库里的 .pyc 既不该进包，也不该被扫
# （扫出来是"分发卫生"问题，不是行为风险；那是 CI 的 hygiene job 管的）。
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ALLOWLIST="$REPO_ROOT/.github/skillspector-allowlist.yaml"

if ! command -v skillspector >/dev/null 2>&1; then
  echo "装不上 skillspector：pip install skillspector" >&2
  exit 2
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# 副本放在扫描目录之外 —— 写在里面会被下一次扫描扫到（自己扫自己）。
(cd "$REPO_ROOT" && tar --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='.git' --exclude='.omx' -cf - .) | (cd "$WORK" && tar -xf -)

OUT_DIR="$(mktemp -d)"
# 扫到 HIGH 时 scanner 的退出码是非 0 —— 那正是我们要看的东西。
(cd "$WORK" && skillspector scan . --no-llm --format json \
    --output "$OUT_DIR/scan.json") >/dev/null || true

if [ ! -f "$OUT_DIR/scan.json" ]; then
  echo "扫描没产出结果（skillspector 装坏了？）" >&2
  exit 2
fi

python3 - "$OUT_DIR/scan.json" "$ALLOWLIST" <<'PY'
import json
import re
import sys

scan_path, allow_path = sys.argv[1], sys.argv[2]

allow_text = open(allow_path, encoding="utf-8").read()
allowed = set(re.findall(r"^\s*-\s+id:\s*(\w+)", allow_text, re.M))

report = json.load(open(scan_path, encoding="utf-8"))
issues = report.get("issues") or []
high = [i for i in issues if i.get("severity") in ("HIGH", "CRITICAL")]
bad = [i for i in high if i.get("id") not in allowed]

for i in high:
    loc = i.get("location") or {}
    tag = "已放行" if i.get("id") in allowed else "未放行"
    print(f"{tag} {i.get('id')} {i.get('severity')} "
          f"{loc.get('file')}:{loc.get('start_line')} {i.get('finding')}")

ra = report.get("risk_assessment") or {}
print(f"\n扫描评分：{ra.get('score')} / {ra.get('severity')} "
      f"（扫描器的原始意见：{ra.get('recommendation')}）")
print("  ↑ 这是扫描器对**全部** finding 的加权评分，包含下面这些已复核放行的项 —— "
      "它不知道我们的 allowlist。判定看的是「未放行条数」，不是这个分。")
print(f"HIGH/CRITICAL 共 {len(high)} 条，其中未放行 {len(bad)} 条；"
      f"MEDIUM/LOW {len(issues) - len(high)} 条（不拦，照报）")

if bad:
    print("\n未放行的 HIGH/CRITICAL：")
    for i in bad:
        loc = i.get("location") or {}
        print(f"  {i.get('id')} {i.get('severity')} "
              f"{loc.get('file')}:{loc.get('start_line')} {i.get('finding')}")
    print("\n若确有新风险 ⇒ 改代码，别加 allowlist 条目。")
    sys.exit(1)
print("\nOK：HIGH/CRITICAL 全部落在 allowlist 内。")
PY
