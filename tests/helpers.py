"""テスト専用のフィクスチャ（架空データ）。本番コードからは使用しない。"""
from app.models import Observation
from app.sources import parse_allowlist
import json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = "2026-11-16T03:00:00+00:00"


def allow(demo=False):
    with open(os.path.join(ROOT, "config", "sources.json"), encoding="utf-8") as f:
        return parse_allowlist(json.load(f), demo)


def ob(price, i=0, src="rakuten", title="ナイキ エアフォース1 '07", shipping=0, fees=0, pts=None, cond="new",
       fetched=NOW, host=None):
    host = host or {"rakuten": "item.rakuten.co.jp", "yahoo": "store.shopping.yahoo.co.jp"}[src]
    return Observation(source_id=src, source_name=src, url=f"https://{host}/shop{i}/item{i}", title=title,
                       price=price, fetched_at=fetched, condition=cond, shipping=shipping, fees=fees, point_amount=pts,
                       seller=f"shop{i}")


def sample(prices, title="ナイキ エアフォース1 '07"):
    return [ob(p, i, "rakuten" if i % 2 else "yahoo", title) for i, p in enumerate(prices)]
