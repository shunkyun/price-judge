"""専門店の追加（対応サイト一覧・楽天市場店/Yahoo!店に絞った検索・robots.txt の確認）のテスト。データはすべて架空。"""
import asyncio
import json
import os
import re
import subprocess
import sys

import httpx
import pytest

os.environ["DEMO_MODE"] = "1"
from fastapi.testclient import TestClient  # noqa: E402

from app import main, robots  # noqa: E402
from app.adapters import base as abase  # noqa: E402
from app.adapters import page  # noqa: E402
from app.adapters.rakuten import RakutenAdapter, parse_items  # noqa: E402
from app.adapters.yahoo import YahooAdapter, parse_hits  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import DB  # noqa: E402
from app.judge import judge  # noqa: E402
from app.models import Observation  # noqa: E402
from tests.helpers import NOW, ROOT, allow  # noqa: E402

A = allow()
RAW = json.load(open(os.path.join(ROOT, "config", "sources.json"), encoding="utf-8"))
NEW_STORES = ["edion", "ksdenki", "joshin", "nojima", "sofmap", "alpen", "xebio", "lbreath", "atmos", "montbell",
              "goldwin", "beams", "unitedarrows", "ships", "urbanresearch", "hands", "loft", "nitori", "matsukiyo", "tower"]
c = TestClient(main.app)


def setup_function():
    main.limiter.reset()
    abase._throttle.clear()


# ---------- 設定ファイル（各店の能力を正直に記録） ----------
def test_twenty_new_stores_plus_yodobashi_zozo():
    ids = [s["id"] for s in RAW["sources"]]
    assert len(set(ids)) == len(ids)
    for sid in NEW_STORES + ["yodobashi", "zozotown"]:
        assert sid in ids, sid
    assert len(NEW_STORES) == 20


def test_every_source_records_capabilities():
    for s in RAW["sources"]:
        if s.get("demo_only"):
            continue
        assert s.get("categories"), s["id"]
        assert isinstance(s.get("official_api"), dict) and "available" in s["official_api"], s["id"]
        assert "rakuten_shop" in s and "yahoo_store" in s, s["id"]
        assert s["url_read"]["status"] in ("ok", "unverified", "blocked", "unchecked", "api"), s["id"]
        assert s["url_read"]["note"], s["id"]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", s["verified_at"]), s["id"]
        if s.get("rakuten_shop"):
            assert re.fullmatch(r"[a-z0-9-]+", s["rakuten_shop"]["code"]) and s["rakuten_shop"]["name"]
        if s.get("yahoo_store"):
            assert re.fullmatch(r"[a-z0-9-]+", s["yahoo_store"]["id"]) and s["yahoo_store"]["name"]


def test_no_store_codes_are_shared_or_guessed():
    r = [s["rakuten_shop"]["code"] for s in RAW["sources"] if s.get("rakuten_shop")]
    y = [s["yahoo_store"]["id"] for s in RAW["sources"] if s.get("yahoo_store")]
    assert len(r) == len(set(r)) and len(y) == len(set(y))
    by = {s["id"]: s for s in RAW["sources"]}
    # 確認済みの店舗コード（確認日 2026-11-19）。名前が似た別の店を使わない
    assert by["hands"]["yahoo_store"]["id"] == "hands-net"  # Yahoo!「hands」は別会社の健康食品店
    assert by["joshin"]["rakuten_shop"]["code"] == "jism"
    assert by["sofmap"]["rakuten_shop"]["code"] == "akibamac"
    assert by["urbanresearch"]["rakuten_shop"]["code"] == "ur-rba"
    # 公式店が確認できなかった店は自動検索しない（推測で埋めない）
    for sid in ("yodobashi", "atmos", "montbell", "goldwin", "loft"):
        assert by[sid]["rakuten_shop"] is None and by[sid]["yahoo_store"] is None, sid
    assert by["nojima"]["yahoo_store"] is None and "休止" in by["nojima"]["note"]
    # 専門店の自社サイトは価格APIを公開していない
    for sid in NEW_STORES:
        assert by[sid]["official_api"]["available"] is False


def test_amazon_note_pa_api_requirements():
    am = [s for s in RAW["sources"] if s["id"] == "amazon_jp"][0]
    note = am["official_api"]["note"]
    assert "アソシエイト" in note and "10件" in note and "Keepa" in note and am["official_api"]["available"] is False
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    assert "Creators API" in readme and "適格売上" in readme


def test_allowlist_new_domains_and_path_prefixes():
    assert A.match("https://www.sofmap.com/product_detail.aspx?sku=1").id == "sofmap"
    assert A.match("https://www.supersports.com/ja-jp/xebio/products/1/").id == "xebio"
    assert A.match("https://www.supersports.com/ja-jp/lbreath/products/1/").id == "lbreath"
    assert A.match("https://www.supersports.com/ja-jp/golf/products/1/") is None  # 登録していない店
    assert A.match("https://www.goldwin.co.jp/store/g/1/").id == "goldwin"
    assert A.match("https://www.goldwin.co.jp/ir/") is None
    assert A.match("https://www.loft.co.jp/store/g/g1/").id == "loft"
    assert A.match("https://evil-sofmap.com/x") is None
    assert A.match("http://www.ships ltd.co.jp/") is None


# ---------- 種類の推定と店舗の選び方 ----------
@pytest.mark.parametrize("q,cat", [("iPhone 16 128GB", "electronics"), ("ナイキ エアマックス 90", "shoes"),
                                   ("ノースフェイス ヌプシ", "outdoor"), ("ヒートテック", "clothes"),
                                   ("ニトリ 収納ケース", "goods"), ("化粧水", "drug"), ("ドライヤー", "electronics")])
def test_guess_categories(q, cat):
    assert cat in A.guess_categories(q)


def test_guess_categories_word_boundary_for_ascii():
    assert A.guess_categories("ロンT") == []
    assert "music" not in A.guess_categories("abcdx")  # 「cd」が単語の一部なら一致しない
    assert "electronics" not in A.guess_categories("abctvx stvx")


def test_shops_for_query_limit_and_unknown():
    s = A.shops_for_query("rakuten", "ソニー WH-1000XM5 ヘッドホン", 2)
    assert len(s) == 2 and all(lbl.endswith("（楽天市場店）") for _, _, lbl in s)
    assert A.shops_for_query("rakuten", "ソニー WH-1000XM5 ヘッドホン", 0) == []
    assert A.shops_for_query("yahoo", "よく分からないもの", 3) == []
    y = A.shops_for_query("yahoo", "ナイキ スニーカー", 3)
    assert y[0][0] == "zozo" and y[0][2] == "ZOZOTOWN（Yahoo!ショッピング店）"


# ---------- 表示名（○○（楽天市場店））と取得元の数え方 ----------
def test_rakuten_items_labeled_by_shop():
    data = {"items": [
        {"itemName": "ソニー WH-1000XM5", "itemPrice": 50000, "itemUrl": "https://item.rakuten.co.jp/ksdenki/1/?rafcid=x",
         "itemCode": "ksdenki:1", "shopName": "ケーズデンキ　楽天市場店", "postageFlag": 0, "taxFlag": 0},
        {"itemName": "ソニー WH-1000XM5", "itemPrice": 50100, "itemUrl": "https://item.rakuten.co.jp/someshop/2/",
         "itemCode": "someshop:2", "shopName": "どこかの店", "postageFlag": 0, "taxFlag": 0}]}
    a, b = parse_items(data, NOW, A)
    assert (a.source_id, a.source_name, a.store_id) == ("rakuten", "ケーズデンキ（楽天市場店）", "ksdenki")
    assert (b.source_id, b.source_name, b.store_id) == ("rakuten", "楽天市場", "")
    assert "rafcid" not in a.url
    assert parse_items(data, NOW)[0].source_name == "楽天市場"  # allow 無しは従来どおり


def test_yahoo_hits_labeled_by_seller_id():
    data = {"hits": [
        {"name": "ナイキ エアマックス 90", "price": 18000, "url": "https://store.shopping.yahoo.co.jp/zozo/1.html",
         "shipping": {"code": 2}, "seller": {"sellerId": "zozo", "name": "ZOZOTOWN Yahoo!店"}, "code": "zozo_1"},
        {"name": "ナイキ エアマックス 90", "price": 18100, "url": "https://store.shopping.yahoo.co.jp/hands/1.html",
         "shipping": {"code": 2}, "seller": {"sellerId": "hands", "name": "別の店"}, "code": "hands_1"}]}
    a, b = parse_hits(data, NOW, A)
    assert (a.source_id, a.source_name, a.store_id) == ("yahoo", "ZOZOTOWN（Yahoo!ショッピング店）", "zozotown")
    assert (b.source_name, b.store_id) == ("Yahoo!ショッピング", "")  # 「hands」はハンズではない


def test_labeled_shops_do_not_inflate_source_count():
    obs = []
    for i in range(12):
        o = Observation("rakuten", "ケーズデンキ（楽天市場店）" if i % 2 else "楽天市場",
                        f"https://item.rakuten.co.jp/shop{i}/x{i}/", "ソニー WH-1000XM5", 40000 + i * 100, NOW,
                        seller=f"shop{i}", shipping=0, fees=0, store_id="ksdenki" if i % 2 else "")
        obs.append(o)
    r = judge(obs, A, "ソニー WH-1000XM5", 41000, "new", 10, 2, 3, 1.5, 0.6, None)
    assert r["sources_used"] == ["rakuten"]
    assert r["verdict"] == "insufficient"  # 楽天市場だけ＝1サイト。専門店名で水増ししない


def test_labeled_rakuten_rows_are_still_not_saved(tmp_path):
    db = DB(str(tmp_path / "p.db"), 400)
    o = Observation("rakuten", "ケーズデンキ（楽天市場店）", "https://item.rakuten.co.jp/ksdenki/1/", "t", 100, NOW,
                    shipping=0, store_id="ksdenki")
    y = Observation("yahoo", "ZOZOTOWN（Yahoo!ショッピング店）", "https://store.shopping.yahoo.co.jp/zozo/1.html", "t", 100,
                    NOW, shipping=0, store_id="zozotown")
    assert db.save("k", [o, y]) == 1
    assert [x.source_id for x in db.load("k")] == ["yahoo"]


# ---------- 店舗に絞った検索（公式APIのパラメータ） ----------
def _settings(**kw):
    s = Settings()
    s.rakuten_app_id, s.rakuten_access_key, s.yahoo_app_id = "app", "key", "yid"
    s.upstream_interval = 0.0
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_rakuten_search_adds_shop_code_requests_and_dedupes():
    seen = []

    def handler(req):
        p = dict(req.url.params)
        seen.append(p)
        code = p.get("shopCode")
        item = {"itemName": "ソニー WH-1000XM5 ヘッドホン", "itemPrice": 50000, "postageFlag": 0, "taxFlag": 0,
                "itemUrl": f"https://item.rakuten.co.jp/{code or 'ksdenki'}/1/", "itemCode": f"{code or 'ksdenki'}:1",
                "shopName": "x"}
        return httpx.Response(200, json={"items": [item]})

    a = RakutenAdapter(_settings(shop_search_max=2))
    a.allow = A

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as cl:
            return await a.search(cl, "ソニー WH-1000XM5 ヘッドホン", "new")

    out = asyncio.run(run())
    codes = [p.get("shopCode") for p in seen]
    assert codes[0] is None and len(codes) == 3  # 全体1ページ（続き無し）＋2店
    expected = [c for c, _, _ in A.shops_for_query("rakuten", "ソニー WH-1000XM5 ヘッドホン", 2)]
    assert codes[1:] == expected
    assert all(p["hits"] == "10" and p["postageFlag"] == "1" for p in seen[1:])
    assert len({o.url for o in out}) == len(out)  # 重複URLは1件に
    assert any(o.source_name.endswith("（楽天市場店）") for o in out)


def test_rakuten_search_no_shop_requests_when_disabled_or_unknown():
    seen = []

    def handler(req):
        seen.append(dict(req.url.params))
        return httpx.Response(200, json={"items": []})

    for q, mx in (("ソニー ヘッドホン", 0), ("よく分からないもの", 3)):
        seen.clear()
        a = RakutenAdapter(_settings(shop_search_max=mx))
        a.allow = A

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as cl:
                return await a.search(cl, q, "new")
        asyncio.run(run())
        assert [p.get("shopCode") for p in seen] == [None]


def test_yahoo_search_adds_seller_id_requests():
    seen = []

    def handler(req):
        seen.append(dict(req.url.params))
        return httpx.Response(200, json={"hits": []})

    a = YahooAdapter(_settings(shop_search_max=1))
    a.allow = A

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as cl:
            return await a.search(cl, "ナイキ スニーカー", "new")
    asyncio.run(run())
    assert [p.get("seller_id") for p in seen] == [None, "zozo"]
    assert seen[1]["results"] == "10" and seen[1]["shipping"] == "free"


def test_shop_search_max_env_is_clamped(monkeypatch):
    monkeypatch.setenv("SHOP_SEARCH_MAX", "99")
    assert Settings().shop_search_max == 5
    monkeypatch.setenv("SHOP_SEARCH_MAX", "-3")
    assert Settings().shop_search_max == 0
    monkeypatch.delenv("SHOP_SEARCH_MAX")
    assert Settings().shop_search_max == 2


def test_service_gives_adapters_the_allowlist():
    from app.service import PriceService
    svc = PriceService(Settings(), DB(":memory:", 400), A)
    assert all(a.allow is A for a in svc.adapters)


# ---------- robots.txt ----------
ROB = """
User-agent: *
Disallow: /cart/
Disallow: /*?*keyword=*
Allow: /products/?keyword=*
Disallow: /*.pdf$
Disallow:

User-agent: GPTBot
User-agent: PriceJudgeBot
Disallow: /secret/
"""


def test_robots_parse_specific_group_wins():
    rules = robots.parse(ROB)
    assert rules == [(False, "/secret/")]
    assert not robots.allowed(rules, "https://x.jp/secret/a")
    assert robots.allowed(rules, "https://x.jp/cart/")  # 自分向けのグループだけを使う


def test_robots_wildcards_longest_match_and_end_anchor():
    rules = robots.parse(ROB.split("User-agent: GPTBot")[0])
    al = lambda u: robots.allowed(rules, "https://x.jp" + u)  # noqa: E731
    assert not al("/cart/1")
    assert not al("/list?a=1&keyword=b")
    assert al("/products/?keyword=b")  # より長い Allow が優先
    assert not al("/a/b.pdf") and al("/a/b.pdf?x=1")
    assert al("/g/123/")
    assert robots.allowed([], "https://x.jp/anything")


def _client(routes):
    def handler(req):
        r = routes.get(req.url.path)
        if r is None:
            return httpx.Response(404)
        return r() if callable(r) else r
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.real_robots
def test_robots_check_outcomes():
    async def run(routes, url="https://www.loft.co.jp/store/g/g1/"):
        robots.clear_cache()
        async with _client(routes) as cl:
            try:
                await robots.check(cl, url, page.UA)
                return "ok"
            except robots.RobotsBlocked as e:
                return e.message
    assert asyncio.run(run({})) == "ok"  # 404 → 制限なし（規格どおり）
    assert "HTTP 403" in asyncio.run(run({"/robots.txt": httpx.Response(403)}))
    assert "HTTP 503" in asyncio.run(run({"/robots.txt": httpx.Response(503)}))
    assert "ボット対策" in asyncio.run(run({"/robots.txt": httpx.Response(200, text="<html><body>challenge</body></html>")}))
    assert "許可されていない" in asyncio.run(run({"/robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /store/\n")}))
    assert asyncio.run(run({"/robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /cart/\n")})) == "ok"


@pytest.mark.real_robots
def test_robots_is_cached_per_host():
    n = {"robots": 0}

    def rob():
        n["robots"] += 1
        return httpx.Response(200, text="User-agent: *\nDisallow: /cart/\n")

    async def run():
        robots.clear_cache()
        async with _client({"/robots.txt": rob}) as cl:
            for _ in range(3):
                await robots.check(cl, "https://www.loft.co.jp/store/g/g1/", page.UA, now=100.0)
            await robots.check(cl, "https://www.loft.co.jp/store/g/g1/", page.UA, now=100.0 + robots.OK_TTL + 1)
    asyncio.run(run())
    assert n["robots"] == 2


@pytest.mark.real_robots
def test_fetch_page_refuses_when_robots_disallows():
    page_hits = {"n": 0}

    def prod():
        page_hits["n"] += 1
        return httpx.Response(200, text="<html></html>")

    async def run():
        robots.clear_cache()
        async with _client({"/robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /store/g/\n"),
                            "/store/g/g1/": prod}) as cl:
            return await page.fetch_page(cl, "https://www.loft.co.jp/store/g/g1/", A)
    with pytest.raises(robots.RobotsBlocked):
        asyncio.run(run())
    assert page_hits["n"] == 0  # 商品ページには一度もアクセスしない


@pytest.mark.real_robots
def test_fetch_page_checks_robots_on_redirect_target():
    async def run():
        robots.clear_cache()

        def handler(req):
            if req.url.host == "www.loft.co.jp":
                if req.url.path == "/robots.txt":
                    return httpx.Response(404)
                return httpx.Response(302, headers={"location": "https://www.shipsltd.co.jp/g/1/"})
            if req.url.path == "/robots.txt":
                return httpx.Response(403)
            return httpx.Response(200, text="<html></html>")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as cl:
            return await page.fetch_page(cl, "https://www.loft.co.jp/store/g/g1/", A)
    with pytest.raises(robots.RobotsBlocked):
        asyncio.run(run())


@pytest.mark.real_robots
def test_fetch_page_reads_when_allowed():
    html = ('<script type="application/ld+json">{"@type":"Product","name":"テスト","offers":{"@type":"Offer",'
            '"price":"1980","priceCurrency":"JPY"}}</script>')

    async def run():
        robots.clear_cache()
        async with _client({"/robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /store/cart/\n"),
                            "/store/g/g1/": httpx.Response(200, text=html)}) as cl:
            return await page.observe_url(cl, "https://www.loft.co.jp/store/g/g1/", A)
    obs, name, msg = asyncio.run(run())
    assert msg == "ok" and obs.price == 1980 and obs.source_id == "loft"


# ---------- 画面（対応サイト一覧） ----------
def test_api_sources_public_and_without_secrets(monkeypatch):
    monkeypatch.setattr(main.settings, "access_code", "secret-code-123")
    r = c.get("/api/sources")
    assert r.status_code == 200
    d = r.json()
    ids = [s["id"] for s in d["sources"]]
    assert "demo" not in ids and "ksdenki" in ids and "yodobashi" in ids
    txt = r.text
    assert "secret-code-123" not in txt and "applicationId" not in txt
    k = [s for s in d["sources"] if s["id"] == "ksdenki"][0]
    assert k["rakuten_shop"]["code"] == "ksdenki" and k["url_read"]["status"]
    assert c.get("/api/suggest?q=a").status_code == 401  # 他のAPIは従来どおりコードが必要


def test_sites_page_served_with_external_script_only():
    r = c.get("/sites")
    assert r.status_code == 200 and "対応サイト一覧" in r.text
    assert "<script>" not in r.text and 'src="/static/sites.js' in r.text
    assert c.get("/static/sites.js").status_code == 200
    assert 'href="/sites"' in c.get("/").text


def test_sites_js_syntax():
    node = subprocess.run(["which", "node"], capture_output=True, text=True).stdout.strip()
    if not node:
        pytest.skip("node なし")
    for f in ("static/sites.js", "static/app.js"):
        assert subprocess.run([node, "--check", os.path.join(ROOT, f)]).returncode == 0


def test_readme_and_proposal_tables_in_sync():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "sources_table.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    for f in ("README.md", os.path.join("docs", "提案書.md")):
        t = open(os.path.join(ROOT, f), encoding="utf-8").read()
        assert "| ケーズデンキ |" in t and "`akibamac`" in t and "ヨドバシ" in t


# ---------- Git・Render 無料プラン ----------
def test_gitignore_excludes_secrets_db_dist():
    gi = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read().split()
    for p in (".env", "data/", "dist/", "*.db", ".env.*", "!.env.example", "reviews/", "__pycache__/"):
        assert p in gi, p


def test_render_free_yaml():
    t = open(os.path.join(ROOT, "render.free.yaml"), encoding="utf-8").read()
    assert re.search(r"^\s+plan: free$", t, re.M) and "runtime: docker" in t and "healthCheckPath: /healthz" in t
    assert "disk:" not in t
    for k in ("RAKUTEN_APP_ID", "RAKUTEN_ACCESS_KEY", "YAHOO_APP_ID", "ACCESS_CODE"):
        assert re.search(rf"key: {k}\n\s+sync: false", t), k
    assert "TRUSTED_PROXY_HEADER" in t and "CF-Connecting-IP" in t
    # 無料プランはディスクが無いので DB はコンテナ内（再起動で消える）。DB_PATH はアプリのフォルダ外
    m = re.search(r"key: DB_PATH\n\s+value: (\S+)", t)
    assert m and m.group(1).startswith("/") and not m.group(1).startswith("/app")
    try:
        import yaml  # noqa: F401
    except ImportError:
        return
    import yaml
    d = yaml.safe_load(t)
    svc = d["services"][0]
    assert svc["type"] == "web" and svc["plan"] == "free" and "disk" not in svc
