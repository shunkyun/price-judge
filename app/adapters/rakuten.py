"""楽天市場 商品検索API（公式）。要 RAKUTEN_APP_ID + RAKUTEN_ACCESS_KEY。
https://webservice.rakuten.co.jp/documentation/ichiba-item-search
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse, urlunparse

from .base import PriceAdapter, dedupe_obs, now_iso, register, to_int_price, wait_until
from ..models import Observation


def rakuten_item_code(url: str) -> Optional[str]:
    m = re.match(r"https?://item\.rakuten\.co\.jp/([^/]+)/([^/?#]+)", url or "")
    return f"{m.group(1)}:{m.group(2)}" if m else None


def clean_url(url: str) -> str:
    """クエリ文字列を除去（楽天の rafcid 等にアプリIDが含まれるため、公開・保存しない）"""
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


def shop_code_of(it: dict, url: str) -> str:
    code = str(it.get("shopCode") or "")
    if not code:
        ic = str(it.get("itemCode") or "")
        code = ic.split(":", 1)[0] if ":" in ic else ""
    if not code:
        m = re.match(r"https://item\.rakuten\.co\.jp/([^/]+)/", url or "")
        code = m.group(1) if m else ""
    return code


def parse_items(data: dict, fetched_at: str, allow=None) -> list:
    """allow を渡すと、登録した専門店の楽天市場店の商品は「○○（楽天市場店）」と表示（取得元は楽天市場のまま）"""
    out = []
    for it in data.get("items", []) or []:
        if "item" in it and isinstance(it["item"], dict):  # formatVersion=1
            it = it["item"]
        price = to_int_price(it.get("itemPrice"))
        url = clean_url(it.get("itemUrl") or "")
        if price is None or not url:
            continue
        # postageFlag: 0=送料込み（送料無料）, 1=送料別（金額はAPIに無い→不明）
        pf = it.get("postageFlag")
        shipping = 0 if pf == 0 else None
        # taxFlag: 0=税込, 1=税別 → 税別は総支払額が確定できないので送料と同様に不明扱い
        if it.get("taxFlag") == 1:
            continue
        rate = it.get("pointRate")
        pts, note = None, ""
        if isinstance(rate, int) and rate >= 1:
            # 楽天の通常ポイントは税抜価格×倍率%。税率10%前提で計算（APIの倍率を使用、推定ではなく計算）
            pts = (price * 10 // 11) * rate // 100
            note = f"楽天API pointRate={rate}倍（税抜価格×{rate}%、税率10%前提）"
        label = allow.mall_label("rakuten", shop_code_of(it, url)) if allow is not None else None
        out.append(Observation(
            source_id="rakuten", source_name=label[1] if label else "楽天市場", store_id=label[0] if label else "",
            url=url, title=it.get("itemName", ""),
            price=price, fetched_at=fetched_at, condition="new", seller=it.get("shopName", ""),
            raw_ref=it.get("itemCode", ""), shipping=shipping, fees=0,
            point_amount=pts, point_note=note, point_computed=pts is not None))
    return out


@register
class RakutenAdapter(PriceAdapter):
    id = "rakuten"
    name = "楽天市場"

    def enabled(self) -> bool:
        return bool(self.settings.rakuten_app_id and self.settings.rakuten_access_key)

    def disabled_reason(self) -> str:
        return "RAKUTEN_APP_ID / RAKUTEN_ACCESS_KEY 未設定"

    def _params(self, **kw) -> dict:
        p = {"applicationId": self.settings.rakuten_app_id, "accessKey": self.settings.rakuten_access_key,
             "format": "json", "formatVersion": 2, "hits": 30, "availability": 1}
        p.update(kw)
        return p

    def _headers(self) -> dict:
        return {"Origin": self.settings.rakuten_origin} if self.settings.rakuten_origin else {}

    async def search(self, client, query, condition):
        if condition == "used":
            return []  # 楽天市場の検索は新品中心。中古は扱わない
        out = []
        # 店舗追加: 検索語の種類に合う専門店の楽天市場店に絞った検索（1店1回・最大 SHOP_SEARCH_MAX 店）
        shops = self.allow.shops_for_query("rakuten", query, self.settings.shop_search_max) if self.allow else []
        # 送料込み(postageFlag=1)の商品を優先取得して総支払額が確定するサンプルを確保
        slots = self.reserve(2 + len(shops))  # 2ページ分＋店舗分の枠を連続で確保（N-11）
        for slot, page in zip(slots[:2], (1, 2)):
            await wait_until(slot)
            r = await client.get(self.settings.rakuten_endpoint, headers=self._headers(),
                                 params=self._params(keyword=query[:128], page=page, postageFlag=1, sort="standard"))
            r.raise_for_status()
            data = r.json()
            out += parse_items(data, now_iso(), self.allow)
            if len(data.get("items") or []) < 30:
                break  # 続きが無ければ2ページ目は取得しない
        for slot, (code, _sid, _label) in zip(slots[2:], shops):
            await wait_until(slot)
            r = await client.get(self.settings.rakuten_endpoint, headers=self._headers(),
                                 params=self._params(keyword=query[:128], shopCode=code, hits=10, page=1,
                                                     postageFlag=1, sort="standard"))
            r.raise_for_status()
            out += parse_items(r.json(), now_iso(), self.allow)
        return dedupe_obs(out)

    async def lookup(self, client, url) -> Optional[Observation]:
        code = rakuten_item_code(url)
        if not code:
            return None
        await self.throttle()
        r = await client.get(self.settings.rakuten_endpoint, headers=self._headers(),
                             params=self._params(itemCode=code, availability=0))
        r.raise_for_status()
        items = parse_items(r.json(), now_iso(), self.allow)
        return items[0] if items else None
