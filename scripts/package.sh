#!/usr/bin/env sh
# 配布・アップロード用 zip を作成: ./scripts/package.sh → dist/price-judge.zip
set -e
cd "$(dirname "$0")/.."
PY="$(command -v python3 || command -v python)"
exec "$PY" scripts/package.py
