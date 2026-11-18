import asyncio
from datetime import datetime, timezone

from app.adapters.demo import DEMO_PRODUCTS, demo_observations, find_demo
from app.adapters.keepa import asin_from_url, keepa_time_to_iso, parse_history
from app.adapters.page import extract_product
from app.adapters.rakuten import parse_items, rakuten_item_code
from app.adapters.yahoo import parse_hits
from app.llm import DailyCounter, LLMQuotaExceeded, deletion_only, strip_prices
from app.suggest import suggest
from app.textnorm import normalize, relevance


def test_normalize_kana_width():
    assert normalize("ｴｱﾌｫｰｽ") == normalize("えあふぉーす") == "エアフォース"


def test_h3_model_and_variant_mismatch():
    from app.textnorm import mismatch_reason as m
    assert m("AirPods", "Apple AirPods Max スペースグレイ")
    assert m("iPhone 15", "iPhone 15 Pro Max 1TB")
    assert m("iPhone 15", "iPhone 16 128GB")
    assert m("iPhone 15", "iPhone 150")
    assert m("Apple AirPods Pro 第2世代", "Apple AirPods Pro 第1世代")
    assert m("iPhone 15 mini", "iPhone 15 mini") == ""
    assert m("ソニー WH-1000XM5", "SONY ソニー WH-1000XM5 ブラック") == ""
    assert m("ユニクロ ウルトラライトダウンジャケット", "ユニクロ ウルトラライトダウンジャケット メンズ") == ""
    assert m("プロテイン", "ザバス プロテイン") == ""  # 「プロ」は単語境界で判定


def test_relevance():
    assert relevance("ソニー WH-1000XM5", "【新品】SONY ソニー WH-1000XM5 ブラック") == 1.0
    assert relevance("ソニー WH-1000XM5", "パナソニック ドライヤー") < 0.6


def test_suggest_prefix_and_kana():
    c = [("ナイキ エアフォース1 '07", 1), ("ナイキ ダンク ロー", 1), ("ニューバランス 996", 1)]
    assert suggest("ないき", c)[0].startswith("ナイキ")
    assert suggest("ダンク", c) == ["ナイキ ダンク ロー"]
    assert suggest("", c) == []


def test_strip_prices():
    assert strip_prices("ナイキ 16,500円 エアフォース ¥9800") == "ナイキ  エアフォース"


def test_c2_no_llm_explanation_code():
    import app.llm as m
    assert not hasattr(m.LLM, "explain")  # C-2: AI説明文は廃止


def test_m6_normalize_deletion_only():
    assert deletion_only("【送料無料】ナイキ エアフォース1 '07 新品", "ナイキ エアフォース1 '07")
    assert not deletion_only("ナイキ エアフォース1", "ナイキ エアジョーダン1")
    assert not deletion_only("ナイキ エアフォース1", "Nike Air Force 1")
    assert not deletion_only("abc", "")


def test_h6_llm_daily_cap():
    c = DailyCounter(2)
    c.take(); c.take()
    import pytest
    with pytest.raises(LLMQuotaExceeded):
        c.take()


def test_rakuten_parse():
    data = {"items": [
        {"itemName": "A", "itemPrice": 10000, "itemUrl": "https://item.rakuten.co.jp/s/a/", "postageFlag": 0, "pointRate": 2, "taxFlag": 0},
        {"itemName": "B", "itemPrice": 9000, "itemUrl": "https://item.rakuten.co.jp/s/b/", "postageFlag": 1, "pointRate": 1, "taxFlag": 0},
        {"itemName": "C", "itemPrice": "x", "itemUrl": "https://item.rakuten.co.jp/s/c/"}]}
    out = parse_items(data, "2026-11-16T00:00:00+00:00")
    assert len(out) == 2
    assert out[0].shipping == 0 and out[0].point_amount == 9090 * 2 // 100
    assert out[1].shipping is None  # 送料別→不明
    assert out[0].point_computed is True  # M-7: 倍率からの計算値と明示
    assert rakuten_item_code("https://item.rakuten.co.jp/shop1/item-9/") == "shop1:item-9"


def test_yahoo_parse():
    data = {"hits": [
        {"name": "A", "price": 5000, "url": "https://store.shopping.yahoo.co.jp/a/1.html", "shipping": {"code": 2}, "point": {"amount": 50}, "condition": "new"},
        {"name": "B", "price": 5000, "url": "https://store.shopping.yahoo.co.jp/a/2.html", "shipping": {"code": 3}, "condition": "used"}]}
    out = parse_hits(data, "2026-11-16T00:00:00+00:00")
    assert out[0].shipping == 0 and out[0].point_amount == 50
    assert out[1].shipping is None and out[1].condition == "used" and out[1].point_amount is None


def test_keepa_parse():
    assert asin_from_url("https://www.amazon.co.jp/dp/B0ABCDEFGH?ref=x") == "B0ABCDEFGH"
    assert keepa_time_to_iso(0).startswith("2011-01-01")
    out = parse_history({"title": "T", "csv": [[100, 5000, 200, -1, 300, 5200]]}, "B0ABCDEFGH", "now")
    assert [o.price for o in out] == [5000, 5200] and all(o.is_history for o in out)


def test_rakuten_url_strips_query_with_app_id():
    data = {"items": [{"itemName": "A", "itemPrice": 1000, "postageFlag": 0, "taxFlag": 0,
                       "itemUrl": "https://item.rakuten.co.jp/s/a/?rafcid=SECRET_APP_ID"}]}
    assert "rafcid" not in parse_items(data, "t")[0].url


def _ld(obj):
    import json
    return '<script type="application/ld+json">' + json.dumps(obj) + '</script>'


def test_c3_offers_variants_ambiguous():
    h = _ld({"@type": "Product", "name": "X", "offers": [
        {"@type": "Offer", "price": 500, "priceCurrency": "JPY"}, {"@type": "Offer", "price": 16500, "priceCurrency": "JPY"}]})
    assert extract_product(h)["price"] is None
    h2 = _ld({"@type": "Product", "name": "X", "offers": [
        {"@type": "Offer", "price": 16500, "priceCurrency": "JPY"}, {"@type": "Offer", "price": "16500", "priceCurrency": "JPY"}]})
    assert extract_product(h2)["price"] == 16500


def test_c3_tax_excluded_rejected():
    h = _ld({"@type": "Product", "name": "X", "offers": {"@type": "Offer", "price": 15000, "priceCurrency": "JPY",
             "priceSpecification": {"valueAddedTaxIncluded": False}}})
    assert extract_product(h)["price"] is None


def test_c3_currency_must_be_jpy():
    assert extract_product(_ld({"@type": "Product", "offers": {"price": 20, "priceCurrency": "USD"}}))["price"] is None
    assert extract_product(_ld({"@type": "Product", "offers": {"price": 2000}}))["price"] is None  # 通貨不明
    assert extract_product('<meta property="product:price:amount" content="2000">')["price"] is None


def test_c3_shipping_ambiguity():
    base = {"@type": "Offer", "price": 15000, "priceCurrency": "JPY"}
    jp = {"@type": "DefinedRegion", "addressCountry": "JP"}
    def ship(sd):
        return extract_product(_ld({"@type": "Product", "name": "X", "offers": dict(base, shippingDetails=sd)}))["shipping"]
    assert ship({"shippingRate": {"value": 0, "currency": "JPY"}, "shippingDestination": jp}) == 0
    assert ship([{"shippingRate": {"value": 0, "currency": "JPY"}, "shippingDestination": dict(jp, addressRegion="東京都")},
                 {"shippingRate": {"value": 2000, "currency": "JPY"}, "shippingDestination": jp}]) is None
    assert ship({"shippingRate": {"value": "5.99", "currency": "USD"}, "shippingDestination": jp}) is None
    assert ship({"shippingRate": {"value": 500, "currency": "JPY"}}) is None  # 配送先不明


def test_m3_bad_name_and_entities():
    h = _ld({"@type": "Product", "name": ["a", "b"], "offers": {"price": 100, "priceCurrency": "JPY"}})
    assert extract_product(h)["name"] is None
    h2 = '<script type="application/ld+json">{"@type":"Product","name":"A &quot;B&quot;","offers":{"price":100,"priceCurrency":"JPY"}}</script>'
    r = extract_product(h2)
    assert r["price"] == 100 and r["name"] == "A &quot;B&quot;"


def test_page_jsonld():
    html = '<script type="application/ld+json">{"@type":"Product","name":"X","offers":{"@type":"Offer","price":"12,800","priceCurrency":"JPY"}}</script>'
    assert extract_product(html) == {"name": "X", "price": 12800, "currency": "JPY", "shipping": None}
    html2 = '<script type="application/ld+json">{"@type":"Product","name":"X","offers":{"price":"20","priceCurrency":"USD"}}</script>'
    assert extract_product(html2)["price"] is None


def test_demo_is_labeled_and_deterministic():
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    p = find_demo("エアフォース1")
    a, b = demo_observations(p, "new", now), demo_observations(p, "new", now)
    assert [o.price for o in a] == [o.price for o in b]
    assert all(o.is_demo and ".demo.invalid/" in o.url for o in a)
    assert find_demo("存在しない商品xyz") is None


def test_h6_upstream_throttle_serializes_calls():
    import time
    from app.adapters.base import wait_turn

    async def go():
        t0 = time.monotonic()
        await asyncio.gather(*[wait_turn("t-upstream", 0.2) for _ in range(3)])
        return time.monotonic() - t0
    assert asyncio.run(go()) >= 0.39
