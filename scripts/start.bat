@echo off
rem ワンコマンド起動（Windows）: scripts\start.bat をダブルクリック
chcp 65001 >nul
cd /d "%~dp0.."
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist .venv (
  %PY% -m venv .venv
  if errorlevel 1 goto :nopython
)
call .venv\Scripts\activate.bat
rem 依存のインストールは初回（または requirements.txt が変わったとき）だけ（N-15）
fc /b requirements.txt .venv\installed-requirements.txt >nul 2>nul
if errorlevel 1 (
  echo 必要な部品をインストールしています（初回のみ・数分かかります）…
  python -m pip install -q -r requirements.txt
  if errorlevel 1 goto :pipfail
  copy /y requirements.txt .venv\installed-requirements.txt >nul
)
rem .env の読み込み・ブラウザを開く処理は Python 側で行う（BOM・CRLF・記号を含む値にも対応。R3-10）
python -m app.launcher
if errorlevel 1 (
  echo.
  echo 起動に失敗しました。上に表示されたエラーを確認してください。
  echo （よくある原因：ポート 8000 を他のアプリが使用中 → .env に PORT=8001 などを追加）
)
pause
exit /b

:nopython
echo Python が見つかりません。https://www.python.org/ からインストールし、「Add python.exe to PATH」にチェックしてください。
pause
exit /b 1

:pipfail
echo 部品のインストールに失敗しました。インターネット接続（庁内プロキシ等）を確認するか、情報システム担当にご相談ください。
pause
exit /b 1
