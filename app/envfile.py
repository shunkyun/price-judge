""".env の安全な読み込み（R3-10）。シェルで実行しないため、$ ; ` 空白などがそのまま値になる。
- UTF-8（BOM 付きも可）、改行は LF / CRLF のどちらでも可
- KEY=VALUE、先頭の export は無視、# で始まる行はコメント
- 値を "..." または '...' で囲んだ場合は外側の引用符だけを外す（中身は展開しない）
- 引用符で囲まない値の後ろの「 #」以降はコメント
- すでに環境変数が設定されている場合は上書きしない（Render 等の画面の設定を優先）"""
from __future__ import annotations

import os
import re

_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def parse(text: str) -> dict:
    out = {}
    if text.startswith("\ufeff"):
        text = text[1:]
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, val = line.split("=", 1)
        key = key.strip()
        if not _KEY.fullmatch(key):
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        else:
            val = re.split(r"\s+#", val, maxsplit=1)[0].strip()
        out[key] = val
    return out


def load(path: str, override: bool = False) -> dict:
    """path を読み込んで os.environ に設定する。設定した項目を返す。ファイルが無ければ何もしない。"""
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8", errors="replace") as f:
        data = parse(f.read())
    applied = {}
    for k, v in data.items():
        if override or k not in os.environ:
            os.environ[k] = v
            applied[k] = v
    return applied
