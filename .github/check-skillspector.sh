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
# 两个要点：
#
# 1. **扫的是发布物**（干净副本），不是工作树。工作树里有 .git/、.claude/（agent 的
#    机器本地设置）、__pycache__/、.omx/ 这些**不进包**的东西 —— 扫它们得到的是
#    "分发卫生"问题，不是行为风险，而分发卫生由 CI 的 hygiene job 管。
#
# 2. **放行是规则级的**（按 rule id），不是逐条 finding 的位置指纹。位置指纹会让
#    改了代码就失效 —— 对开源贡献者是持续的假红（每次改代码都要先"重新生成基线"，
#    久而久之就没人看红灯了）。规则级不依赖位置：改代码不会误红，而**新规则**仍然
#    会被拦。真正的不变量由另一条精确检查守着：
#    `python3 scripts/check_env_inheritance.py`（生产代码不得整份继承 os.environ）。
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ALLOWLIST="$REPO_ROOT/.github/skillspector-allowlist.yaml"

if ! command -v skillspector >/dev/null 2>&1; then
  echo "装不上 skillspector：pip install skillspector" >&2
  exit 2
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# 干净副本 = 会发布出去的那份（不含 .git / .claude / 字节码 / 本地运行态）
(cd "$REPO_ROOT" && tar --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='.git' --exclude='.omx' --exclude='.claude' \
    --exclude='.github/state' --exclude='dist' -cf - .) \
  | (cd "$WORK" && tar -xf -)

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
