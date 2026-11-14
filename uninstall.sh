#!/usr/bin/env bash
# 卸载 req-test-plan skill —— 只删软链，绝不删真实目录。
#
#   bash uninstall.sh
#
# 这是 install.py 的一层薄壳（跨平台版本：python install.py --uninstall）。
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "找不到 python3 —— 这个脚本只是 install.py 的壳，直接跑：" >&2
  echo "  $PY install.py --uninstall" >&2
  exit 2
fi
exec "$PY" "$SRC/install.py" --uninstall "$@"
