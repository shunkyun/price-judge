"""config/sources.json から「対応サイト一覧」の表（Markdown）を作り、README.md と docs/提案書.md の
<!-- SOURCES-TABLE:START --> 〜 <!-- SOURCES-TABLE:END --> の間を書き換える。

  python scripts/sources_table.py          # 書き換え
  python scripts/sources_table.py --check  # 表が古ければ終了コード1（テスト用）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
START, END = "<!-- SOURCES-TABLE:START -->", "<!-- SOURCES-TABLE:END -->"
CAT = {"all": "総合", "electronics": "家電", "clothes": "服", "shoes": "靴", "sports": "スポーツ", "outdoor": "アウトドア",
       "goods": "生活雑貨", "drug": "ドラッグ", "music": "音楽"}
READ = {"ok": "○", "unverified": "△", "blocked": "×", "unchecked": "－", "api": "API"}


def auto(s: dict) -> str:
    out = []
    if s["id"] == "rakuten":
        out.append("楽天API（全体）")
    if s["id"] == "yahoo":
        out.append("Yahoo! API（全体）")
    if s["id"] == "amazon_jp":
        out.append("なし（Keepa で ASIN 指定時のみ履歴）")
    if s.get("rakuten_shop"):
        out.append(f"楽天市場店 `{s['rakuten_shop']['code']}`")
    if s.get("yahoo_store"):
        out.append(f"Yahoo!店 `{s['yahoo_store']['id']}`")
    return "<br>".join(out) or "なし"


def table() -> str:
    d = json.loads((BASE / "config" / "sources.json").read_text(encoding="utf-8"))
    rows = ["| サイト | 分野 | 自動検索（公式API経由） | 店舗の公式API | URL読み取り |", "|---|---|---|---|---|"]
    for s in d["sources"]:
        if s.get("demo_only"):
            continue
        cats = "・".join(CAT.get(c, c) for c in s.get("categories", []))
        api = "あり" if (s.get("official_api") or {}).get("available") else "なし"
        rd = READ.get((s.get("url_read") or {}).get("status"), "？")
        rows.append(f"| {s['name']} | {cats} | {auto(s)} | {api} | {rd} |")
    dates = sorted({s.get("verified_at") for s in d["sources"] if s.get("verified_at")})
    rows.append("")
    rows.append(f"URL読み取り：○＝robots.txt で許可・構造化データ（JSON-LD）を確認済み／△＝robots.txt は許可・構造化データ未確認／"
                f"×＝このサーバー環境からはアクセス拒否（403・ボット対策）や無応答で読めない／－＝未確認／API＝APIで読む。どの場合も読み取りの直前に robots.txt を確認し、許可が確認できなければ読まない。"
                f"確認日：{'、'.join(dates)}。詳しい理由は画面の「対応サイト一覧」（/sites）と `config/sources.json` に記録。")
    return "\n".join(rows)


def render(text: str) -> str:
    if START not in text or END not in text:
        return text
    a, rest = text.split(START, 1)
    _, b = rest.split(END, 1)
    return f"{a}{START}\n{table()}\n{END}{b}"


def main() -> int:
    check = "--check" in sys.argv
    stale = []
    for p in (BASE / "README.md", BASE / "docs" / "提案書.md"):
        old = p.read_text(encoding="utf-8")
        new = render(old)
        if new != old:
            stale.append(p.name)
            if not check:
                p.write_text(new, encoding="utf-8")
    if check and stale:
        print("表が古いファイル:", ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
