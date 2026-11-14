#!/usr/bin/env bash
# 把 req-test-plan skill 装到各 agent 的 skills 目录（软链）。
#
#   bash install.sh
#
# 布局：
#   ~/.agents/skills/req-test-plan          ← 稳定入口，指向本仓库
#   ~/.claude/skills/req-test-plan          → 指向上面那个
#   ~/.codex/skills/req-test-plan           → 指向上面那个
#   ~/.config/opencode/skills/req-test-plan → 指向上面那个
#
# 只动**软链**。目标位置已经是一个真实目录时 —— 拒绝并告警，不删用户数据。
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRIMARY="$HOME/.agents/skills/req-test-plan"
LINK_TARGETS=(
  "$HOME/.claude/skills/req-test-plan"
  "$HOME/.codex/skills/req-test-plan"
  "$HOME/.config/opencode/skills/req-test-plan"
)

link_to() {
  # link_to <路径> <指向>
  local path="$1" target="$2"
  mkdir -p "$(dirname "$path")"
  if [ -L "$path" ]; then
    rm -f "$path"
    echo "  覆盖已有软链：$path"
  elif [ -e "$path" ]; then
    echo "  !! $path 已存在且不是软链 —— 跳过（不删真实目录）" >&2
    return 1
  fi
  ln -s "$target" "$path"
  echo "  已链接：$path -> $target"
}

echo "安装 req-test-plan"
echo "  来源：$SRC"

echo "稳定入口："
link_to "$PRIMARY" "$SRC" || true

echo "各 agent 的 skills 目录："
for t in "${LINK_TARGETS[@]}"; do
  link_to "$t" "$PRIMARY" || true
done

# 自检：装完要能跑。
if [ -f "$PRIMARY/scripts/readiness.py" ]; then
  if python3 "$PRIMARY/scripts/readiness.py" --help >/dev/null 2>&1; then
    echo "自检通过：$PRIMARY/scripts/readiness.py --help"
  else
    echo "!! 自检失败：readiness.py 跑不起来" >&2
    exit 1
  fi
else
  echo "!! 装完找不到 $PRIMARY/scripts/readiness.py" >&2
  exit 1
fi

echo "完成。用法见 $PRIMARY/README.md"
