"""Keepa API（Amazon.co.jp の価格履歴）。要 KEEPA_API_KEY（有料）。Amazon の商品URL（ASIN）指定時のみ。
https://keepa.com/#!discuss/t/product-object/116
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from .base import PriceAdapter, now_iso, register
from ..models import Observation

KEEPA_EPOCH_MIN = 21564000  # keepa time minutes → unix minutes の差分
DOMAIN_JP = 5


def asin_from_url(url: str) -> Optional[str]:
    m = re.search(r"amazon\.co\.jp/(?:.*/)?(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})", url or "")
    return m.group(1) if m else None


def keepa_time_to_iso(km: int) -> str:
    return datetime.fromtimestamp((km + KEEPA_EPOCH_MIN) * 60, tz=timezone.utc).replace(microsecond=0).isoformat()


def parse_history(product: dict, asin: str, fetched_at: str) -> list:
    """csv[0] = Amazon本体の価格履歴 [time, price, time, price, ...]。-1 は在庫なし（除外）。"""
    out = []
    csv = product.get("csv") or []
    series = csv[0] if csv and csv[0] else []
    title = product.get("title") or asin
    url = f"https://www.amazon.co.jp/dp/{asin}"
    for i in range(0, len(series) - 1, 2):
        t, p = series[i], series[i + 1]
        if not isinstance(p, int) or p <= 0:
            continue
        out.append(Observation(
            source_id="amazon_jp", source_name="Amazon.co.jp（Keepa履歴）", url=url, title=title,
            price=p, fetched_at=fetched_at, is_history=True, observed_at=keepa_time_to_iso(t),
            raw_ref=asin, shipping=None, fees=0))
    return out


@register
class KeepaAdapter(PriceAdapter):
    id = "keepa"
    name = "Keepa（Amazon価格履歴）"
    provides_history = True

    def enabled(self) -> bool:
        return bool(self.settings.keepa_api_key)

    def disabled_reason(self) -> str:
        return "KEEPA_API_KEY 未設定"

    async def history(self, client, query, url):
        asin = asin_from_url(url or "")
        if not asin:
            return []
        await self.throttle()
        r = await client.get("https://api.keepa.com/product", params={
            "key": self.settings.keepa_api_key, "domain": DOMAIN_JP, "asin": asin, "history": 1})
        r.raise_for_status()
        prods = r.json().get("products") or []
        return parse_history(prods[0], asin, now_iso()) if prods else []
