#!/bin/sh
# コンテナの起動処理（N-2, R3-8）
# Render などの永続ディスクは起動時に root 所有でマウントされるため、root のうちに保存先の所有者を
# appuser に変えてから、権限を落として（非 root で）アプリを起動する。
# 所有者を変えるのは「保存先フォルダそのもの」と「DB ファイル（-wal/-shm/-journal を含む）」だけ（再帰しない）。
# 保存先がシステムのフォルダやアプリのフォルダの場合は何も変更しない。
set -e
DB="${DB_PATH:-/data/prices.db}"
case "$DB" in
  /*) ;;
  *) echo "警告: DB_PATH は絶対パスで指定してください（例 /data/prices.db）。所有者を変更しません: $DB" >&2; DB="" ;;
esac
if [ -n "$DB" ]; then
  DATA_DIR="$(dirname "$DB")"
  case "$DATA_DIR" in
    /|/app|/app/*|/usr|/usr/*|/etc|/etc/*|/bin|/sbin|/lib|/lib/*|/lib64|/boot|/dev|/dev/*|/proc|/proc/*|/sys|/sys/*|/root|/home|/var|/opt|/srv|/run|/tmp)
      echo "警告: DB_PATH の保存先 $DATA_DIR はシステム／アプリのフォルダのため、所有者を変更しません（/data などを指定してください）" >&2
      DATA_DIR="" ;;
  esac
fi
if [ "$(id -u)" = "0" ]; then
  if [ -n "$DB" ] && [ -n "$DATA_DIR" ]; then
    mkdir -p "$DATA_DIR" 2>/dev/null || true
    chown appuser:appuser "$DATA_DIR" 2>/dev/null || echo "警告: $DATA_DIR の所有者を変更できませんでした（推移の保存が止まる場合があります）" >&2
    for f in "$DB" "$DB-wal" "$DB-shm" "$DB-journal"; do
      if [ -e "$f" ]; then chown appuser:appuser "$f" 2>/dev/null || true; fi
    done
  fi
  if command -v setpriv >/dev/null 2>&1; then
    exec setpriv --reuid=appuser --regid=appuser --init-groups -- "$@"
  fi
  exec python3 -c 'import os,pwd,sys
p=pwd.getpwnam("appuser"); os.setgroups([]); os.setgid(p.pw_gid); os.setuid(p.pw_uid)
os.environ["HOME"]=p.pw_dir; os.execvp(sys.argv[1], sys.argv[1:])' "$@"
fi
exec "$@"
