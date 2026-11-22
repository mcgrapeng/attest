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
READYNESS="$REPO_ROOT/skills/attest/scripts/readiness.py"

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

# 判定点推导要不要 TypeScript 后端。
#
# 缺陷集 fixture 的结论**依赖 L3**：L2（词法回退）下三条新判据一律不硬判
# （缺能力绝不报绿）⇒ 推导等级一掉到 L2，这五条 fixture 的期望退出码会从
# 1 变成 0，期望表就全飘了。
#
# 探测口径 = `decision_points.detect_backend` 的同一套能力探测（B1 仓库内 +
# B2 全局 npm），只在**全局 + fixture 目录**找（fixture 目录自己没装 TS，
# 探测的就是全局那一份）。探测一次就缓存 —— 真跑一遍 tsc 合成代码要几秒。
TS_BACKEND_STATE=""
have_ts_backend() {
  if [ -z "$TS_BACKEND_STATE" ]; then
    TS_BACKEND_STATE="$(python3 - "$REPO_ROOT" "$SCRIPT_DIR/defect-prototype-class" <<'PY'
import sys
from pathlib import Path
repo = Path(sys.argv[1])
sys.path.insert(0, str(repo / "skills" / "attest" / "scripts"))
import decision_points as dp
print("yes" if dp.detect_backend(Path(sys.argv[2]), "auto") else "no")
PY
)" || TS_BACKEND_STATE="no"
    [ -n "$TS_BACKEND_STATE" ] || TS_BACKEND_STATE="no"
  fi
  [ "$TS_BACKEND_STATE" = "yes" ]
}

# check <名字> <模块路径> <期望退出码|any> <前置条件> [额外参数...]
# 期望从 stdin 读，每行一条：
#   +文本          必须出现
#   -文本          必须不出现
#   ?A;;B;;C       至少出现 A、B、C 中的一个（候选之间用 ;; 分隔）
# 额外参数原样追加到 readiness 命令尾部（既有调用不传就是空）。
check() {
  local name="$1" module="$2" want_exit="$3" pre="$4"
  shift 4
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
  out="$(cd "$dest" && python3 "$READYNESS" "$module" --repo "$dest" "$@" 2>&1 </dev/null)"
  rc=$?

  local problems=() line pat hits a
  local -a alts
  if [ "$want_exit" != "any" ] && [ "$rc" != "$want_exit" ]; then
    problems+=("退出码：期望 ${want_exit}，实际 $rc")
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
+测试租户
EOF

check no-tests src any true <<'EOF'
+测试全绿
-结论：达到交付条件
-✓ 测试全绿
-没有失败用例
?证据不足;;不能判;;未实测;;无信号
EOF

# --- 缺陷集 fixture（判定点引擎） ---
#
# 五条各钉一类缺陷，覆盖三条新判据 + DPSP 四档：
#   silent-drop     → plan-alignment 红（机器推导出、计划里没有）
#   shared-client   → decision-coverage 红（高危判定点没人管）
#   stale-cache     → requirement 覆盖红 + DPSP「untested」
#   prototype-class → DPSP「fail」（变异存活）
#   mixed-subject   → plan-alignment 零覆盖 + DPSP「not-applicable」

check defect-silent-drop src 1 have_ts_backend --plan plan.md <<'EOF'
+src/a.ts#insertRow#drop#1
+机器推导出、计划里没有：1 条
+高危四类缺口：drop 1
+计划里没有这条判定点
+计划与机器推导对齐
+2 条不变式的钉有区分力
-error-path 1
-resource 1
-state 1
-发现不了这类缺陷
-判不了（环境没就绪
EOF

check defect-shared-client src 1 have_ts_backend --plan plan.md <<'EOF'
+src/a.ts#closeClient#resource
+高危四类缺口：drop 0 ｜ error-path 0 ｜ resource 1 ｜ state 0
+既没有归属不变式，也没有豁免理由
+高危判定点（resource）既没有归属不变式，也没有豁免理由
+判定点没覆盖到位
+1 条不变式的钉有区分力
-drop 1
-error-path 1
-state 1
-机器推导出、计划里没有
EOF

check defect-stale-cache src 1 have_ts_backend --plan plan.md <<'EOF'
+src/a.ts#closeClient#resource
+高危四类缺口：drop 0 ｜ error-path 0 ｜ resource 1 ｜ state 0
+既没有归属不变式，也没有豁免理由
+需求覆盖到位
+1/1 条不变式没落到真实用例
+1 条判不了（环境没就绪 / 输出认不出）
+钉的文件不存在：src/cache.test.ts
+杀得死 0 条
-drop 1
-error-path 1
-state 1
-发现不了这类缺陷
EOF

# 这一条用 --json：变异算子的名字（in-to-hasown）只在机器可读输出里，
# 文本报告只说「1 个变异全部存活」，说不出是哪一类变异。
check defect-prototype-class src 1 have_ts_backend --plan plan.md --json <<'EOF'
+"op": "in-to-hasown"
+1 个变异全部存活
+1 条不变式的钉发现不了这类缺陷
+钉有区分力
-"op": "cmp-negate"
-"op": "drop-call"
-"op": "predicate-negate"
EOF

check defect-mixed-subject src 1 have_ts_backend --plan plan.md <<'EOF'
+src/a.ts#exemptFirstPerson#branch
+1 条钉不直接引用目标模块
+不直接引用目标模块的任何代码
+没有可判的变异点
+高危四类缺口：drop 0 ｜ error-path 0 ｜ resource 0 ｜ state 0
-drop 1
-error-path 1
-resource 1
-发现不了这类缺陷
-机器推导出、计划里没有
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
