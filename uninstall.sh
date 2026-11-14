#!/usr/bin/env bash
# 卸载 req-test-plan skill —— 只删软链，绝不删真实目录。
#
#   bash uninstall.sh
#
# 与 install.sh 一一对应：
#   ~/.claude/skills/req-test-plan
#   ~/.codex/skills/req-test-plan
#   ~/.config/opencode/skills/req-test-plan
#   ~/.agents/skills/req-test-plan          ← 稳定入口（指向本仓库）
set -euo pipefail

PRIMARY="$HOME/.agents/skills/req-test-plan"
LINK_TARGETS=(
  "$HOME/.claude/skills/req-test-plan"
  "$HOME/.codex/skills/req-test-plan"
  "$HOME/.config/opencode/skills/req-test-plan"
)

removed=0
skipped=0

unlink_one() {
  local path="$1"
  if [ -L "$path" ]; then
    rm -f "$path"
    echo "  已删除软链：$path"
    removed=$((removed + 1))
  elif [ -e "$path" ]; then
    echo "  !! $path 不是软链 —— 跳过（不删真实目录）" >&2
    skipped=$((skipped + 1))
  else
    echo "  没有这一条：$path"
  fi
}

echo "卸载 req-test-plan"
for t in "${LINK_TARGETS[@]}"; do
  unlink_one "$t"
done
unlink_one "$PRIMARY"

echo "共删除 $removed 条软链，跳过 $skipped 条（非软链的真实目录，保持原样）"
echo "仓库本身没有被改动 —— 重新装回来：bash install.sh"
