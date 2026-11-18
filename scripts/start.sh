#!/usr/bin/env sh
# ワンコマンド起動（Mac / Linux）: ./scripts/start.sh
# 既定は自分のPCからのみアクセス可（127.0.0.1）。LAN に公開する場合は HOST=0.0.0.0 を指定。
set -e
cd "$(dirname "$0")/.."
PY="$(command -v python3 || command -v python)"
[ -d .venv ] || "$PY" -m venv .venv
. .venv/bin/activate
# 依存のインストールは初回（または requirements.txt が変わったとき）だけ（N-15）
if ! cmp -s requirements.txt .venv/.installed-requirements 2>/dev/null; then
  echo "必要な部品をインストールしています（初回のみ・数分かかります）…"
  python3 -m pip install -q -r requirements.txt || { echo "インストールに失敗しました。インターネット接続（プロキシ）を確認してください。"; exit 1; }
  cp requirements.txt .venv/.installed-requirements
fi
# .env の読み込み・ブラウザを開く処理は Python 側で行う（値に $ や ; を含んでも安全。R3-10）
exec python3 -m app.launcher
