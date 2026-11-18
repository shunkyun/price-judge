"""Yahoo!ショッピング 商品検索API v3（公式）。要 YAHOO_APP_ID（Client ID）。
https://developer.yahoo.co.jp/webapi/shopping/v3/itemsearch.html
"""
from __future__ import annotations

from .base import PriceAdapter, dedupe_obs, now_iso, register, to_int_price, wait_until
from ..models import Observation

ENDPOINT = "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"


def parse_hits(data: dict, fetched_at: str, allow=None) -> list:
    """allow を渡すと、登録した専門店の Yahoo!店の商品は「○○（Yahoo!ショッピング店）」と表示（取得元は Yahoo!ショッピングのまま）"""
    out = []
    for h in data.get("hits", []) or []:
        price = to_int_price(h.get("price"))
        url = h.get("url") or ""
        if not url.startswith("https://"):
            continue
        if price is None or not url:
            continue
        ship = h.get("shipping") or {}
        # shipping.code: 1=設定なし, 2=送料無料, 3=条件付き送料無料 → 確定できるのは 2 のみ
        shipping = 0 if str(ship.get("code")) == "2" else None
        pt = h.get("point") or {}
        amt = pt.get("amount")
        pts = amt if isinstance(amt, int) and not isinstance(amt, bool) and amt >= 0 else None
        cond = "used" if h.get("condition") == "used" else "new"
        seller = h.get("seller") or {}
        label = allow.mall_label("yahoo", str(seller.get("sellerId") or "")) if allow is not None else None
        out.append(Observation(
            source_id="yahoo", source_name=label[1] if label else "Yahoo!ショッピング", store_id=label[0] if label else "",
            url=url, title=h.get("name", ""),
            price=price, fetched_at=fetched_at, condition=cond,
            seller=seller.get("name", ""), raw_ref=h.get("code", ""),
            shipping=shipping, fees=0, point_amount=pts,
            point_note="Yahoo API point.amount（通常ポイント）" if pts is not None else ""))
    return out


@register
class YahooAdapter(PriceAdapter):
    id = "yahoo"
    name = "Yahoo!ショッピング"
    used_site_count = 1

    def enabled(self) -> bool:
        return bool(self.settings.yahoo_app_id)

    def disabled_reason(self) -> str:
        return "YAHOO_APP_ID 未設定"

    async def search(self, client, query, condition):
        out = []
        # 店舗追加: 検索語の種類に合う専門店の Yahoo!店に絞った検索（1店1回・最大 SHOP_SEARCH_MAX 店）
        shops = self.allow.shops_for_query("yahoo", query, self.settings.shop_search_max) if self.allow else []
        slots = self.reserve(2 + len(shops))  # 2ページ分＋店舗分の枠を連続で確保（N-11）
        base = {"appid": self.settings.yahoo_app_id, "query": query, "condition": condition,
                "in_stock": "true", "shipping": "free"}
        for slot, start in zip(slots[:2], (1, 51)):
            await wait_until(slot)
            r = await client.get(ENDPOINT, params={**base, "results": 50, "start": start})
            r.raise_for_status()
            data = r.json()
            out += parse_hits(data, now_iso(), self.allow)
            if len(data.get("hits") or []) < 50:
                break  # 続きが無ければ2ページ目は取得しない
        for slot, (sid, _store, _label) in zip(slots[2:], shops):
            await wait_until(slot)
            r = await client.get(ENDPOINT, params={**base, "results": 10, "start": 1, "seller_id": sid})
            r.raise_for_status()
            out += parse_hits(r.json(), now_iso(), self.allow)
        return dedupe_obs(out)
