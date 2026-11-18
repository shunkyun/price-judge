"""配布・アップロード用 zip を作る（Mac/Linux/Windows 共通）: python3 scripts/package.py → dist/price-judge.zip
- .env 系（.env.example 以外）・データベース・検品記録（reviews/）・各種キャッシュは入れない（L-9, N-23）
- 元のフォルダに .env 等があれば「このフォルダをそのままアップロードしない」よう警告（N-4）
- 作成後に zip の中身を検査し、秘密情報らしいファイルが入っていたら失敗にする"""
from __future__ import annotations

import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX_DIRS = {".git", ".venv", "venv", "data", "dist", "__pycache__", ".pytest_cache", ".hypothesis", "reviews",
           ".mypy_cache", ".ruff_cache", "node_modules", ".idea", ".vscode"}
EX_EXT = (".pyc", ".db", ".sqlite", ".sqlite3", ".db-journal", ".log", ".pem", ".key")


def is_secret(name: str) -> bool:
    base = os.path.basename(name)
    if base == ".env.example":
        return False
    return base.startswith(".env") or base.lower().endswith((".env", ".env.txt")) or base.endswith(EX_EXT)


def build(root: str = ROOT, out: str = "") -> tuple:
    out = out or os.path.join(root, "dist", "price-judge.zip")
    warnings = []
    for f in sorted(os.listdir(root)):
        if is_secret(f):
            warnings.append(f"元のフォルダに {f} があります（zip には入れていません）。このフォルダを GitHub 等へそのままアップロードしないでください")
    if os.path.isdir(os.path.join(root, "data")):
        warnings.append("元のフォルダに data/（価格のデータベース）があります（zip には入れていません）")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for cur, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in EX_DIRS)
            for f in sorted(files):
                if is_secret(f):
                    continue
                p = os.path.join(cur, f)
                z.write(p, os.path.join("price-judge", os.path.relpath(p, root)).replace(os.sep, "/"))
    with zipfile.ZipFile(out) as z:
        bad = [n for n in z.namelist() if is_secret(n) or "/reviews/" in n or "/data/" in n]
    if bad:
        os.remove(out)
        raise SystemExit("エラー: 秘密情報らしいファイルが zip に含まれたため中止しました: " + ", ".join(bad))
    return out, warnings


if __name__ == "__main__":
    path, warns = build()
    for w in warns:
        print("⚠ 警告:", w, file=sys.stderr)
    print(f"{os.path.relpath(path, ROOT)} を作成しました（.env・データベース・検品記録は含みません）")
