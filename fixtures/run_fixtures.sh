#!/usr/bin/env bash
# 黄金 fixture 回归 —— 逐个跑，核对钉死的期望判定。
#
#   bash fixtures/run_fixtures.sh
#
# 退出码：
#   0  全部 fixture 命中
#   1  有 fixture 与期望不符
#   3  没有不符，但有 fixture 因环境缺失未判定（例如没装 pytest-cov）
#
# 每个 fixture 都先**复制到临时目录**再跑 —— 不污染仓库
# （pytest 会写 .coverage 与 __pycache__，仓库里有这些会被 CI 的
# "存在 .pyc 即失败"闸门抓住）。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
READYNESS="$REPO_ROOT/scripts/readiness.py"

if [ ! -f "$READYNESS" ]; then
  echo "找不到 readiness.py：$READYNESS" >&2
  exit 2
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/req-plan-fixtures.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

# fixture 必须是干净的：别连本机的 PG，也别在 fixture 里留下字节码。
export POSTGRES_URL="" DATABASE_URL=""
export PYTHONDONTWRITEBYTECODE=1

TOTAL=0; HIT=0; MISS=0; SKIPPED=0
declare -a SUMMARY=()

have_node() { command -v node >/dev/null 2>&1; }

have_pytest_cov() {
  command -v pytest >/dev/null 2>&1 || return 1
  # 不要写成 `pytest --help | grep -q`：grep -q 找到就提前退出 ⇒ pytest 收到
  # SIGPIPE ⇒ 管道整体非 0（脚本开了 pipefail）⇒ 前置条件永远为假。
  local help
  help="$(pytest --help 2>&1)" || return 1
  case "$help" in *--cov*) return 0;; esac
  return 1
}

# check <名字> <模块路径> <期望退出码|any> <前置条件>
# 期望从 stdin 读，每行一条：
#   +文本          必须出现
#   -文本          必须不出现
#   ?A;;B;;C       至少出现 A、B、C 中的一个（候选之间用 ;; 分隔）
check() {
  local name="$1" module="$2" want_exit="$3" pre="$4"
  TOTAL=$((TOTAL + 1))

  if ! "$pre"; then
    SKIPPED=$((SKIPPED + 1))
    SUMMARY+=("跳过  ${name}  （环境不满足：${pre}）")
    printf '\n=== %s ===\n跳过 —— 环境不满足（%s），本条期望未判定\n' "$name" "$pre"
    return 0
  fi

  local dest="$WORK/$name" out rc
  rm -rf "$dest"
  cp -R "$SCRIPT_DIR/$name" "$dest"
  out="$(cd "$dest" && python3 "$READYNESS" "$module" --repo "$dest" 2>&1 </dev/null)"
  rc=$?

  local problems=() line pat hits a
  local -a alts
  if [ "$want_exit" != "any" ] && [ "$rc" != "$want_exit" ]; then
    problems+=("退出码：期望 $want_exit，实际 $rc")
  fi
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    case "$line" in
      +*)
        pat="${line#+}"
        # 同上：不用管道（grep -q 提前退出会让写端吃 SIGPIPE，pipefail 下整条
        # 管道判负）。bash 的 [[ == ]] 没有这个问题，也快。
        if [[ "$out" != *"$pat"* ]]; then
          problems+=("应出现但没有：$pat")
        fi
        ;;
      -*)
        pat="${line#-}"
        if [[ "$out" == *"$pat"* ]]; then
          problems+=("不应出现但出现了：$pat")
        fi
        ;;
      \?*)
        pat="${line#\?}"
        hits=0
        while IFS= read -r a; do
          [ -z "$a" ] && continue
          if [[ "$out" == *"$a"* ]]; then hits=1; break; fi
        done <<< "${pat//;;/$'\n'}"
        if [ "$hits" = 0 ]; then
          problems+=("以下之一应出现（一个都没有）：$pat")
        fi
        ;;
    esac
  done

  if [ ${#problems[@]} -eq 0 ]; then
    HIT=$((HIT + 1))
    SUMMARY+=("命中  $name")
    printf '\n=== %s ===\n命中（退出码 %s）\n' "$name" "$rc"
  else
    MISS=$((MISS + 1))
    SUMMARY+=("不符  $name")
    printf '\n=== %s ===\n不符（退出码 %s，期望 %s）\n' "$name" "$rc" "$want_exit"
    for line in "${problems[@]}"; do printf '  - %s\n' "$line"; done
    printf -- '--- 实际输出（最多 6000 字符）---\n'
    printf '%s\n' "$out" | head -c 6000
    printf '\n'
  fi
}

# ---------------------------------------------------------------- 期望表

check ts-colocated src 0 have_node <<'EOF'
+单测 1 个
+导出的逻辑都有测试
-零引用
+没有失败用例
EOF

check python-repo src 0 have_pytest_cov <<'EOF'
+核心逻辑测试到位
-缺少判据
-没拿到 Python 覆盖率
+个第三方依赖
-模块没有第三方依赖
+都有多个调用点
-0 个导出符号都有多个调用点
-没有可分析的导出符号
+导出的逻辑都有测试
-零引用
EOF

check rust-repo src 2 true <<'EOF'
+TS/JS/Python
-✓
EOF

check english-reconciler src 1 have_node <<'EOF'
+数据流漂移
-生产租户无漂移
EOF

check no-tests src any true <<'EOF'
+测试全绿
-结论：达到交付条件
-✓ 测试全绿
-没有失败用例
?证据不足;;不能判;;未实测;;无信号
EOF

# ---------------------------------------------------------------- 汇总

printf '\n================ 汇总 ================\n'
for line in "${SUMMARY[@]}"; do printf '%s\n' "$line"; done
printf '\n共 %s 个 fixture：命中 %s / 不符 %s / 未判定 %s\n' \
  "$TOTAL" "$HIT" "$MISS" "$SKIPPED"

if [ "$MISS" -gt 0 ]; then
  exit 1
fi
if [ "$SKIPPED" -gt 0 ]; then
  exit 3
fi
exit 0
