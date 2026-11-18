@echo off
rem 配布・アップロード用 zip を作成（Windows）: scripts\package.bat をダブルクリック → dist\price-judge.zip
chcp 65001 >nul
cd /d "%~dp0.."
where py >nul 2>nul && (py -3 scripts\package.py) || (python scripts\package.py)
if errorlevel 1 echo zip の作成に失敗しました。上のメッセージを確認してください。
pause
