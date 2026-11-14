#!/usr/bin/env bash
# 把 req-test-plan skill 装到各 agent 的 skills 目录（软链）。
#
#   bash install.sh
#
# 这是 install.py 的一层薄壳 —— 真正的实现是 install.py（跨平台，纯标准库）。
# 想要 Windows 上也能装，直接用 `python install.py`。
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "找不到 python3 —— 这个脚本只是 install.py 的壳，直接跑：" >&2
  echo "  $PY install.py" >&2
  exit 2
fi
exec "$PY" "$SRC/install.py" "$@"
