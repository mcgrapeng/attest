#!/usr/bin/env bash
# 公开自测：对一批公开仓库跑 attest 的交付就绪度判据，把**原样输出**留成证据。
#
#   bash tools/run_self_test.sh <输出目录>
#
# 每个仓库：浅克隆 → 记录 commit SHA → 装依赖 → 跑 readiness.py（模块 = 整仓）。
# 依赖装不上/测试跑不动也照样跑 —— 工具会报「未实测」，那本身就是结论。
set -uo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../skills/attest" && pwd)"
OUT_DIR="${1:-/tmp/attest-self-test}"
WORK="$(mktemp -d /tmp/attest-self-test-work-XXXXXX)"
mkdir -p "$OUT_DIR"

# 仓库|语言|模块路径|说明
# 模块路径是判据的输入（SKILL.md 的契约：readiness.py <模块路径> --repo <根>），
# 给错的话测试发现会跳过仓库的 test 目录 —— 负对照 chalk-wrong-path 就是这个用例。
REPOS=(
  "chalk/chalk|JS|source|最流行的终端着色库之一"
  "expressjs/express|JS|lib|Node 的事实标准 HTTP 框架"
  "sindresorhus/type-fest|TS|source|纯类型库，运行时零依赖"
  "pallets/click|Python|src/click|CLI 框架"
  "pallets/itsdangerous|Python|src/itsdangerous|签名与 token 库"
  "chalk/chalk|JS|.|负对照：模块路径给成整仓"
)

for entry in "${REPOS[@]}"; do
  slug="${entry%%|*}"; rest="${entry#*|}"
  lang="${rest%%|*}"; rest="${rest#*|}"
  module="${rest%%|*}"; note="${rest#*|}"
  name="${slug##*/}"
  [ "$module" = "." ] && name="$name-wrong-path"
  dir="$WORK/$name"

  echo "=== $slug ($lang) ==="
  if ! git clone --depth 1 "https://github.com/$slug.git" "$dir" >/dev/null 2>&1; then
    echo "  克隆失败，跳过"; continue
  fi
  sha="$(git -C "$dir" rev-parse HEAD)"
  stars="$(curl -s -H "Authorization: token ${GITHUB_TOKEN:-}" \
    "https://api.github.com/repos/$slug" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("stargazers_count",0))' 2>/dev/null || echo 0)"
  echo "  commit $sha · ★$stars"

  # 装依赖（有 package.json 走 npm，有 pyproject/setup.py 走 venv）
  if [ -f "$dir/package.json" ]; then
    (cd "$dir" && npm install --silent --no-audit --no-fund >/dev/null 2>&1) || echo "  npm install 失败（测试会报未实测）"
  fi
  if [ -f "$dir/pyproject.toml" ] || [ -f "$dir/setup.py" ]; then
    python3 -m venv "$dir/.venv" >/dev/null 2>&1
    "$dir/.venv/bin/pip" install -q -e "$dir" pytest pytest-cov >/dev/null 2>&1 || echo "  pip install 失败（测试会报未实测）"
  fi

  out="$OUT_DIR/$name.txt"
  if [ -f "$dir/.venv/bin/python" ]; then
    PATH="$dir/.venv/bin:$PATH" python3 "$SKILL_DIR/scripts/readiness.py" "$module" --repo "$dir" > "$out" 2>&1
  else
    python3 "$SKILL_DIR/scripts/readiness.py" "$module" --repo "$dir" > "$out" 2>&1
  fi
  echo "  退出码 $? · 报告 $(wc -l < "$out") 行 → $(basename "$out")"
  {
    echo "slug=$slug"
    echo "lang=$lang"
    echo "module=$module"
    echo "note=$note"
    echo "sha=$sha"
    echo "stars=$stars"
  } > "$OUT_DIR/$name.meta"

  # 仓库本身不动（只读运行），删掉临时克隆
  rm -rf "$dir"
done

echo "=== 全部完成：$OUT_DIR ==="
rm -rf "$WORK"
