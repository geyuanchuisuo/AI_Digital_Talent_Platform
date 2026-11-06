#!/usr/bin/env bash
# 一键交付引导（Linux / macOS）。所有中文输出由 bootstrap.py 打印。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "Python 3.11+ 未安装，请先安装后重跑；详见 README.md「一键交付」章节" >&2
  exit 1
fi

exec "$PY" scripts/A/bootstrap.py --start "$@"
