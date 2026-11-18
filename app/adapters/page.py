"""ユーザーが指定した商品URL（許可リスト内のみ）から、ページに埋め込まれた構造化データ
（JSON-LD schema.org/Product, OGP/itemprop）を読み取る。HTML本文の推測パースはしない。

あいまいな場合は採用しない（C-3）:
 - 価格: offers が複数で価格が異なる / AggregateOffer / 通貨が JPY と明示されていない / 税抜（valueAddedTaxIncluded=false）
 - 送料: shippingDetails が複数 / 配送先が日本全国と確認できない / 通貨が JPY でない → 送料不明
"""
from __future__ import annotations

import json
import re
from html import unescape
from typing import Optional
from urllib.parse import urljoin

import httpx

from .base import now_iso, to_int_price
from .. import robots
from ..models import Observation
from ..sources import Allowlist

UA = "Mozilla/5.0 (compatible; PriceJudgeBot/1.1)"
MAX_BYTES = 3_000_000


def _iter_jsonld(html: str):
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(m.group(1).strip())  # M-3: unescape しない（正しいJSONを壊すため）
        except (ValueError, TypeError):
            continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
            elif isinstance(d, dict):
                yield d
                if "@graph" in d:
                    stack.append(d["@graph"])


def _meta(html: str, key: str) -> Optional[str]:
    k = re.escape(key)
    m = re.search(r'<meta[^>]+(?:property|name|itemprop)=["\']%s["\'][^>]+content=["\']([^"\']+)["\']' % k, html, re.I) or \
        re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name|itemprop)=["\']%s["\']' % k, html, re.I)
    return unescape(m.group(1)) if m else None


def _str(v) -> Optional[str]:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _offer_price(offer: dict) -> Optional[int]:
    if not isinstance(offer, dict) or offer.get("@type") == "AggregateOffer":
        return None
    cur = offer.get("priceCurrency")
    price = offer.get("price")
    spec = offer.get("priceSpecification")
    if isinstance(spec, list):
        if len(spec) != 1:
            return None  # N-12: 価格指定が複数（税抜・税込の併記等）はあいまいなので採用しない
        spec = spec[0]
    if isinstance(spec, dict):
        if _falsy(spec.get("valueAddedTaxIncluded")):
            return None
        cur = cur or spec.get("priceCurrency")
        price = price if price is not None else spec.get("price")
    if cur != "JPY":
        return None
    return _yen_int(price)


def _falsy(v) -> bool:
    return v is False or str(v).strip().lower() in ("false", "0", "no")


def _yen_int(v) -> Optional[int]:
    """円の価格として整数のみ採用（N-12: 0.6 → 1 のような丸めはしない）"""
    if isinstance(v, float):
        return int(v) if v.is_integer() and v > 0 else None
    if isinstance(v, str) and re.fullmatch(r"\s*[\d,]+\.\d*\s*", v):
        f = float(v.replace(",", ""))
        return int(f) if f.is_integer() and f > 0 else None
    return to_int_price(v)


def _shipping(offers: list) -> Optional[int]:
    vals = set()
    for o in offers:
        sd = o.get("shippingDetails") if isinstance(o, dict) else None
        if isinstance(sd, list):
            if len(sd) != 1:
                return None
            sd = sd[0]
        if not isinstance(sd, dict):
            return None
        if not _falsy(sd.get("doesNotShip", False)):
            return None  # N-12: 配送不可の指定がある
        dest = sd.get("shippingDestination")
        if isinstance(dest, list):
            if len(dest) != 1:
                return None
            dest = dest[0]
        if not isinstance(dest, dict) or dest.get("addressCountry") not in ("JP", "jp") \
                or any(dest.get(k) for k in ("addressRegion", "postalCode", "postalCodeRange")):
            return None
        rate = sd.get("shippingRate")
        if not isinstance(rate, dict) or rate.get("currency") != "JPY":
            return None
        v = rate.get("value")
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)) and v >= 0 and float(v).is_integer():
            vals.add(int(v))
        elif isinstance(v, str) and v.strip().isdigit():
            vals.add(int(v.strip()))
        else:
            return None
    return vals.pop() if len(vals) == 1 else None


def extract_product(html: str) -> dict:
    """{name, price, currency, shipping}。あいまいな値は None。"""
    for d in _iter_jsonld(html):
        t = d.get("@type")
        types = t if isinstance(t, list) else [t]
        if "Product" not in types:
            continue
        offers = d.get("offers")
        offers = offers if isinstance(offers, list) else ([offers] if isinstance(offers, dict) else [])
        prices = [_offer_price(o) for o in offers]
        price = prices[0] if prices and None not in prices and len(set(prices)) == 1 else None
        shipping = _shipping(offers) if price is not None else None
        return {"name": _str(d.get("name")), "price": price, "currency": "JPY" if price else None, "shipping": shipping}
    name = _str(_meta(html, "og:title"))
    cur = _meta(html, "product:price:currency") or _meta(html, "og:price:currency")
    raw = _meta(html, "product:price:amount") or _meta(html, "og:price:amount")
    price = to_int_price(raw) if cur == "JPY" else None
    return {"name": name, "price": price, "currency": cur if price else None, "shipping": None}


async def fetch_page(client: httpx.AsyncClient, url: str, allow: Allowlist, max_redirects: int = 3) -> tuple:
    """許可リスト内URLのみ取得。robots.txt で許可されていないページ・robots.txt を確認できないサイトは読まない。リダイレクト先も毎回検査（SSRF対策）。上限バイトで打ち切る（L-2）。"""
    cur = url
    for _ in range(max_redirects + 1):
        if allow.match(cur) is None:
            raise ValueError("許可リスト外のURLです")
        await robots.check(client, cur, UA)  # 店舗追加: robots.txt で許可されたページだけ読む（リダイレクト先も毎回）
        async with client.stream("GET", cur, headers={"User-Agent": UA, "Accept-Language": "ja"},
                                 follow_redirects=False) as r:
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                cur = urljoin(cur, r.headers["location"])
                continue
            r.raise_for_status()
            buf = bytearray()
            async for chunk in r.aiter_bytes():
                buf += chunk
                if len(buf) >= MAX_BYTES:
                    break
            return cur, bytes(buf).decode(r.encoding or "utf-8", errors="replace")
    raise ValueError("リダイレクトが多すぎます")


async def observe_url(client: httpx.AsyncClient, url: str, allow: Allowlist) -> tuple:
    """(Observation or None, product_name or None, message)"""
    final_url, html = await fetch_page(client, url, allow)
    src = allow.match(final_url)
    info = extract_product(html)
    name = info.get("name")
    if info.get("price") is None:
        return None, name, "ページから確定できる税込価格（円）を読み取れませんでした"
    cond = "used" if src.conditions == ("used",) else "new"
    obs = Observation(source_id=src.id, source_name=src.name, url=final_url, title=name or final_url,
                      price=info["price"], fetched_at=now_iso(), condition=cond,
                      seller=src.name, shipping=info.get("shipping"),
                      fees=0 if src.kind in ("official", "retailer") else None)
    return obs, name, "ok"
