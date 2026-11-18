"""ローカル起動用（scripts/start.sh・start.bat から呼ぶ）: python -m app.launcher
.env を Python で安全に読み込み（R3-10）、サーバーの準備ができてからブラウザを開く（N-15）。"""
from __future__ import annotations

import os
import sys
import threading
import time
import urllib.request
import webbrowser

from . import envfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _open_when_ready(port: str) -> None:
    url = f"http://localhost:{port}/"
    for _ in range(120):
        try:
            urllib.request.urlopen(url + "healthz", timeout=2)
            webbrowser.open(url)
            return
        except Exception:  # noqa: BLE001
            time.sleep(1)


def main() -> int:
    if os.path.isfile(os.path.join(ROOT, ".env.txt")) and not os.path.isfile(os.path.join(ROOT, ".env")):
        print("⚠ .env.txt があります。名前を .env に変えないとキーは読み込まれません。", file=sys.stderr)
    envfile.load(os.path.join(ROOT, ".env"))
    host = os.environ.get("HOST", "127.0.0.1")
    port = os.environ.get("PORT", "8000")
    if not port.isdigit():
        print(f"PORT の値が正しくありません: {port!r}", file=sys.stderr)
        return 1
    print(f"起動します → http://localhost:{port}（終了は Ctrl+C / この画面を閉じる）")
    if os.environ.get("OPEN_BROWSER", "1") == "1":
        threading.Thread(target=_open_when_ready, args=(port,), daemon=True).start()
    import uvicorn
    try:
        uvicorn.run("app.main:app", host=host, port=int(port))
    except Exception as e:  # noqa: BLE001
        print(f"起動に失敗しました: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
