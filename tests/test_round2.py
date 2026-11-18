"""レビュー2（N-1〜N-28）の修正に対するテスト。データはすべて架空。"""
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import zipfile

import pytest

os.environ["DEMO_MODE"] = "1"
from fastapi.testclient import TestClient  # noqa: E402

from app import main, trend  # noqa: E402
from app.adapters import base as abase  # noqa: E402
from app.adapters.base import PriceAdapter, UpstreamBusy, now_iso, reserve_slots  # noqa: E402
from app.adapters.page import extract_product  # noqa: E402
from app.config import ConfigError, Settings, validate  # noqa: E402
from app.db import DB, hash_key  # noqa: E402
from app.judge import clean_observations, judge  # noqa: E402
from app.llm import deletion_only  # noqa: E402
from app.models import Observation  # noqa: E402
from app.ratelimit import RateLimiter, client_ip  # noqa: E402
from app.service import PriceService, ServiceBusy  # noqa: E402
from app.textnorm import accessory_reason, mismatch_reason  # noqa: E402
from tests.helpers import NOW, ROOT, allow, ob, sample  # noqa: E402

c = TestClient(main.app)
A = allow()
FIX = json.load(open(os.path.join(ROOT, "tests", "fixtures", "realistic_titles.json"), encoding="utf-8"))


def setup_function():
    main.limiter.reset()


def _passes(q, t):
    o = Observation("yahoo", "y", "https://store.shopping.yahoo.co.jp/s/1.html", t, 10000, NOW, shipping=0)
    kept, _ = clean_observations([o], A, q, "new", 0.6, None)
    return bool(kept)


# ---------- N-1 照合 ----------
def test_n1_realistic_titles_regression():
    g = [_passes(q, t) for q, t in FIX["genuine"]]
    acc = [_passes(q, t) for q, t in FIX["accessory"]]
    other = [_passes(q, t) for q, t in FIX["different_model"]]
    assert len(g) >= 30 and len(acc) >= 15
    assert sum(g) / len(g) >= 0.8, f"本物の通過率 {sum(g)}/{len(g)}"
    assert sum(acc) / len(acc) <= 0.1, f"付属品の通過率 {sum(acc)}/{len(acc)}"
    assert sum(other) / len(other) <= 0.1


@pytest.mark.parametrize("q,t", [
    ("ニューバランス 996", "New Balance CM996 BG"),
    ("アシックス ゲルカヤノ 14", "asics GEL-KAYANO14 1201A019"),
    ("ナイキ エアマックス 90", "NIKE AIR MAX 90 CN8490-100"),
    ("エアポッズ プロ 第2世代", "Apple AirPods Pro（第2世代）"),
])
def test_n1_aliases_and_model_normalization(q, t):
    assert mismatch_reason(q, t) == "" and _passes(q, t)


def test_n1_variant_words_in_query_are_not_mismatch():
    assert mismatch_reason("AirPods Max", "Apple AirPods Max スペースグレイ") == ""
    assert mismatch_reason("AirPods", "Apple AirPods Max") != ""  # クエリに無い Max は別商品


def test_n1_attached_case_is_not_accessory_but_for_use_is():
    q = "AirPods Pro 第2世代"
    assert accessory_reason(q, "Apple AirPods Pro 第2世代 MagSafe充電ケース付き") == ""
    for t in ("AirPods Pro 第2世代 用 イヤーピース", "AirPods Pro 対応 ケース", "AirPods Pro専用 収納ポーチ"):
        assert accessory_reason(q, t), t
    assert accessory_reason("ナイキ エアフォース1", "ナイキ エアフォース1 シューレース 替え紐")


def test_n1_demo_contains_messy_titles_and_filters_them():
    r = c.post("/api/analyze", json={"query": "ソニー WH-1000XM5"}).json()
    reasons = " ".join(e["excluded_reason"] for e in r["excluded"])
    assert "付属品" in reasons and r["verdict"] != "insufficient"


# ---------- N-2 コンテナ起動・DB ----------
def test_n2_entrypoint_fixes_ownership_then_drops_privileges():
    ep = open(os.path.join(ROOT, "scripts", "docker-entrypoint.sh"), encoding="utf-8").read()
    df = open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8").read()
    assert "chown" in ep and "setpriv" in ep and "os.setuid" in ep
    assert "ENTRYPOINT" in df and "\nUSER appuser" not in df
    subprocess.run(["sh", "-n", os.path.join(ROOT, "scripts", "docker-entrypoint.sh")], check=True)
    if os.getuid() != 0:  # 非 root ではそのままコマンドを実行する
        out = subprocess.run(["sh", os.path.join(ROOT, "scripts", "docker-entrypoint.sh"), "echo", "ok"],
                             capture_output=True, text=True, check=True).stdout
        assert out.strip() == "ok"


def test_n2_db_unwritable_falls_back(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    db = DB(str(blocker / "sub" / "prices.db"))  # 親がファイル → 開けない
    assert db.persistent is False
    assert db.save("k" * 64, [ob(1000)]) == 0


# ---------- N-3 利用者IP・制限 ----------
class _Req:
    def __init__(self, headers, host="10.0.0.5"):
        from starlette.datastructures import Headers
        self.headers = Headers(headers)
        self.client = type("C", (), {"host": host})()


def test_n3_spoofed_forwarded_for_ignored():
    spoof = {"X-Forwarded-For": "1.2.3.4, 203.0.113.9"}
    assert client_ip(_Req(spoof), 1) == "203.0.113.9"  # 右端（信頼プロキシが追記）
    assert client_ip(_Req(spoof), 0) == "10.0.0.5"
    assert client_ip(_Req({**spoof, "CF-Connecting-IP": "198.51.100.7"}), 0, "CF-Connecting-IP") == "198.51.100.7"
    assert client_ip(_Req({"CF-Connecting-IP": "not-an-ip"}), 0, "CF-Connecting-IP") == "10.0.0.5"


def test_n3_render_config_uses_cloudflare_header():
    y = open(os.path.join(ROOT, "render.yaml"), encoding="utf-8").read()
    assert "CF-Connecting-IP" in y


def test_n3_limiter_lru_eviction_keeps_active_keys():
    rl = RateLimiter(max_keys=3)
    for i in range(3):
        assert rl.allow(f"k{i}", 1)
    assert not rl.allow("k0", 1)  # k0 を最近使用に
    rl.allow("k3", 1)  # 最も古い k1 が追い出される
    assert len(rl.hits) <= 3 and not rl.allow("k0", 1)


# ---------- N-4 / N-23 配布 ----------
def test_n4_package_warns_and_excludes(tmp_path):
    import shutil
    work = tmp_path / "pj"
    shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns("dist", ".venv", "__pycache__", ".pytest_cache", "data"))
    (work / ".env").write_text("RAKUTEN_APP_ID=secret")
    (work / ".env.txt").write_text("RAKUTEN_APP_ID=secret")
    (work / ".hypothesis").mkdir(exist_ok=True)
    (work / "reviews").mkdir(exist_ok=True)
    (work / "reviews" / "x.md").write_text("x")
    p = subprocess.run([sys.executable, str(work / "scripts" / "package.py")], capture_output=True, text=True, check=True)
    assert ".env" in p.stderr and "アップロードしないでください" in p.stderr
    names = zipfile.ZipFile(work / "dist" / "price-judge.zip").namelist()
    assert not any(n.endswith((".env", ".env.txt")) or "/reviews/" in n or ".hypothesis" in n for n in names)
    assert "price-judge/.env.example" in names and "price-judge/scripts/package.bat" in names


def test_n4_guide_never_puts_env_in_upload():
    g = open(os.path.join(ROOT, "docs", "使い方ガイド.md"), encoding="utf-8").read()
    for m in ("Render の画面", "Private", ".env.txt", "再発行", "有料"):
        assert m in g, m


def test_n15_start_scripts():
    sh = open(os.path.join(ROOT, "scripts", "start.sh"), encoding="utf-8").read()
    bat = open(os.path.join(ROOT, "scripts", "start.bat"), encoding="utf-8").read()
    launcher = open(os.path.join(ROOT, "app", "launcher.py"), encoding="utf-8").read()
    assert ".env.txt" in launcher and "healthz" in launcher  # 準備完了後にブラウザを開く
    for s in (sh, bat):
        assert "app.launcher" in s and "installed-requirements" in s
    assert "\r\n" in open(os.path.join(ROOT, "scripts", "start.bat"), "rb").read().decode()


# ---------- N-5 中古は1サイトのみ → 参考値 ----------
def test_n5_used_single_site_reference_only():
    r = c.post("/api/analyze", json={"query": "ソニー WH-1000XM5", "condition": "used", "target_price": 30000}).json()
    assert r["verdict"] == "reference" and r["used_single_site"] is True
    assert r["stats"] is None and r["percentile_rank"] is None
    assert r["reference_stats"]["n"] == r["sample_count"] >= r["min_samples"]
    assert any("1サイト" in x for x in r["reasons"])
    assert r["trend"] == [] and r["trend_hidden"]


def test_n5_judge_reference_flag():
    obs = [ob(10000 + i * 100, i, "yahoo", cond="used") for i in range(12)]
    r = judge(obs, A, "ナイキ エアフォース1 '07", 9000, "used", single_source_reference=True)
    assert r["verdict"] == "reference" and r["reference_stats"]["median"]
    r2 = judge(obs, A, "ナイキ エアフォース1 '07", 9000, "used")
    assert r2["verdict"] == "insufficient" and r2["reference_stats"] is None


# ---------- N-6 / N-7 / N-13 / N-14 アクセスコード ----------
def test_n6_bruteforce_lockout(monkeypatch):
    monkeypatch.setattr(main.settings, "access_code", "correct-code-123")
    for _ in range(main.settings.access_code_max_failures):
        assert c.post("/api/auth", json={"code": "bad"}).status_code == 401
    assert c.post("/api/auth", json={"code": "correct-code-123"}).status_code == 429
    assert c.get("/api/suggest", params={"q": "a"}, headers={"X-Access-Code": "correct-code-123"}).status_code == 429
    main.limiter.reset()
    assert c.post("/api/auth", json={"code": "correct-code-123"}).json()["ok"] is True


def test_n7_frontend_asks_once_in_page():
    js = open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8").read()
    html = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
    assert "prompt(" not in js and "/api/auth" in js and "X-Session-Id" in js and 'id="gateForm"' in html


def test_n13_shared_office_ip_with_code(monkeypatch):
    monkeypatch.setattr(main.settings, "access_code", "correct-code-123")
    lim = main.settings.rate_analyze_per_min
    ok = 0
    for i in range(lim * 3):  # 同じIPから3台の端末
        h = {"X-Access-Code": "correct-code-123", "X-Session-Id": f"session-{i % 3:04d}"}
        ok += c.get("/api/suggest", params={"q": "ナイキ"}, headers=h).status_code == 200
    assert ok == lim * 3 if main.settings.rate_suggest_per_min >= lim * 3 else ok > 0
    # 未認証（コード無し運用）では IP 単位
    monkeypatch.setattr(main.settings, "access_code", "")
    main.limiter.reset()
    codes = [c.post("/api/analyze", json={"query": "ナイキ エアフォース1"}).status_code for _ in range(lim + 1)]
    assert codes[-1] == 429


def test_n13_authed_sessions_get_separate_analyze_budget(monkeypatch):
    monkeypatch.setattr(main.settings, "access_code", "correct-code-123")
    lim = main.settings.rate_analyze_per_min
    codes = []
    for s in ("aaaaaaaa", "bbbbbbbb"):
        tok = c.post("/api/auth", json={"code": "correct-code-123"}, headers={"X-Session-Id": s}).json()["token"]
        h = {"X-Access-Token": tok}
        codes += [c.post("/api/analyze", json={"query": "ナイキ エアフォース1"}, headers=h).status_code for _ in range(lim)]
    assert codes.count(200) == 2 * lim  # 共有IPでも端末ごとに上限


def test_n14_non_ascii_code_rejected_and_n17_min_samples_clamped():
    s = Settings()
    s.access_code = "あいうえお"
    with pytest.raises(ConfigError):
        validate(s)
    s = Settings()
    s.min_samples = 2
    w = validate(s)
    assert s.min_samples == 4 and w
    r = judge(sample([10000, 11000, 12000]), A, "ナイキ エアフォース1 '07", 11000, min_samples=1, min_sellers=1)
    assert r["verdict"] == "insufficient"  # 3件では落ちずに判定不可


# ---------- N-8 DB 移行 ----------
def test_n8_migrates_v1_database(tmp_path):
    p = str(tmp_path / "old.db")
    con = sqlite3.connect(p)
    con.executescript("""CREATE TABLE observations (id INTEGER PRIMARY KEY AUTOINCREMENT, product_key TEXT NOT NULL,
      source_id TEXT NOT NULL, source_name TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL, price INTEGER NOT NULL,
      condition TEXT NOT NULL, is_history INTEGER NOT NULL DEFAULT 0, fetched_at TEXT NOT NULL);
      CREATE TABLE products (k TEXT);""")
    con.execute("INSERT INTO observations (product_key, source_id, source_name, url, title, price, condition, fetched_at)"
                " VALUES ('ナイキ エアフォース1', 'r', 'r', 'https://x', 't', 1, 'new', ?)", (NOW,))
    con.commit()
    con.close()
    db = DB(p)
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(observations)")}
    assert {"shipping", "fees", "seller", "observed_at", "point_amount"} <= cols
    assert db.conn.execute("SELECT count(*) FROM observations").fetchone()[0] == 0  # 平文キーは削除
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] >= 2
    assert db.persistent


# ---------- N-9 推移の価格水準を隠す ----------
def test_n9_trend_hidden_when_insufficient():
    r = c.post("/api/analyze", json={"query": "無印良品 スニーカー"}).json()
    assert r["verdict"] == "insufficient" and r["trend"] == [] and r["trend_hidden"] and r["history_median"] is None


def test_n9_trend_point_needs_min_n():
    obs = [ob(1000, 0), ob(1100, 1), ob(1200, 2, fetched="2026-11-10T03:00:00+00:00")]
    s = trend.daily_series(obs, "x", "total", min_n=2)
    assert [p["n"] for p in s["points"]] == [2]


# ---------- N-11 混雑・待ち ----------
def test_n11_reserve_slots_contiguous_and_busy():
    key = "test-slots"
    abase._throttle.pop(key, None)
    s1 = reserve_slots(key, 1.0, 2)
    s2 = reserve_slots(key, 1.0, 2)
    assert s1[1] - s1[0] == pytest.approx(1.0) and s2[0] - s1[1] == pytest.approx(1.0)
    with pytest.raises(UpstreamBusy):
        reserve_slots(key, 1.0, 1, max_wait=0.5)
    abase._throttle.pop(key, None)


class _Slow(PriceAdapter):
    id = "slow"
    name = "slow"
    calls = 0

    def enabled(self):
        return True

    async def search(self, client, query, condition):
        type(self).calls += 1
        await asyncio.sleep(0.05)
        return [Observation("yahoo", "y", f"https://store.shopping.yahoo.co.jp/s{i}/i{i}.html", "ナイキ エアフォース1 '07",
                            10000 + i * 50, now_iso(), seller=f"s{i}", shipping=0, fees=0) for i in range(12)]


def _svc(tmp_path):
    s = Settings()
    s.demo_mode_raw = "0"
    svc = PriceService(s, db=DB(str(tmp_path / "t.db")), allow=allow(False))
    svc.adapters = [_Slow(s)]
    return svc


def test_n11_inflight_dedupe(tmp_path):
    svc = _svc(tmp_path)
    _Slow.calls = 0

    async def go():
        return await asyncio.gather(svc.analyze("ナイキ エアフォース1 '07"), svc.analyze("ナイキ エアフォース1 '07"))

    a, b = asyncio.run(go())
    assert _Slow.calls == 1 and a["sample_count"] == b["sample_count"]


def test_n11_busy_returns_503(monkeypatch):
    def busy(*a, **k):
        raise ServiceBusy("ただいま混雑しています")
    monkeypatch.setattr(main.service, "analyze", busy)
    r = c.post("/api/analyze", json={"query": "ナイキ エアフォース1"})
    assert r.status_code == 503 and "混雑" in r.json()["error"]


def test_n11_frontend_shows_progress():
    js = open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8").read()
    assert "秒経過" in js and "queue_wait_sec" in js


# ---------- N-12 ページ解析 ----------
def _ld(offers):
    return f'<script type="application/ld+json">{json.dumps({"@type": "Product", "name": "x", "offers": offers})}</script>'


def test_n12_ambiguous_page_prices():
    assert extract_product(_ld({"@type": "Offer", "price": "12000", "priceCurrency": "JPY"}))["price"] == 12000
    assert extract_product(_ld({"@type": "Offer", "price": 0.6, "priceCurrency": "JPY"}))["price"] is None
    assert extract_product(_ld({"@type": "Offer", "price": "12000.5", "priceCurrency": "JPY"}))["price"] is None
    two = {"@type": "Offer", "priceCurrency": "JPY", "priceSpecification": [
        {"price": 10000, "valueAddedTaxIncluded": False}, {"price": 11000, "valueAddedTaxIncluded": True}]}
    assert extract_product(_ld(two))["price"] is None
    ship = {"@type": "Offer", "price": 12000, "priceCurrency": "JPY", "shippingDetails": {
        "doesNotShip": True, "shippingDestination": {"addressCountry": "JP"}, "shippingRate": {"value": 0, "currency": "JPY"}}}
    assert extract_product(_ld(ship))["shipping"] is None


# ---------- N-16 正規化の削除専用チェック ----------
def test_n16_deletion_only_token_subsequence():
    assert deletion_only("【送料無料】ソニー WH-1000XM5 ブラック", "ソニー WH-1000XM5")
    assert not deletion_only("ソニー WH-1000XM5", "WH-1000X")       # 語の途中切り
    assert not deletion_only("iPhone 15 Pro", "iPhone 15")         # 区別語 Pro は削除不可
    assert not deletion_only("ナイキ エアフォース1 '07", "ナイキ")    # 数字を含む語は削除不可
    assert not deletion_only("ナイキ エアフォース1", "Nike Air Force 1")


# ---------- N-18 重複URLの順序依存 ----------
def test_n18_duplicate_url_order_independent():
    base = sample([10000 + i * 100 for i in range(12)])
    d1 = ob(50000, 0, "yahoo")
    r1 = judge(base + [d1], A, "ナイキ エアフォース1 '07", 11000, min_sellers=1)
    r2 = judge([d1] + base, A, "ナイキ エアフォース1 '07", 11000, min_sellers=1)
    assert (r1["verdict"], r1["stats"]["median"]) == (r2["verdict"], r2["stats"]["median"])


# ---------- N-19 / N-20 ----------
def test_n19_target_url_not_in_population(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    target = "https://store.shopping.yahoo.co.jp/s3/i3.html"

    async def fake_resolve(client, url):
        o = Observation("yahoo", "y", target, "ナイキ エアフォース1 '07", 10150, now_iso(), seller="s3", shipping=0, fees=0)
        return {"source": "yahoo", "message": "", "obs": o, "name": "ナイキ エアフォース1 '07"}
    monkeypatch.setattr(svc, "resolve_url", fake_resolve)
    svc.s.min_sources = 1
    r = asyncio.run(svc.analyze(None, url=target))
    assert all(o["url"] != target for o in r["observations"])


def test_n20_below_min_note():
    r = judge(sample([10000 + i * 100 for i in range(12)]), A, "ナイキ エアフォース1 '07", 9950, min_sellers=1)
    assert r["verdict"] in ("cheap", "fair")
    assert any("すべての出品より安い" in x for x in r["reasons"] + r.get("notes", []))


# ---------- N-21 / N-22 ----------
def test_n21_trend_dates_in_jst():
    o = ob(1000, fetched="2026-11-15T16:30:00+00:00")
    assert trend._date(o) == "2026-11-16"


def test_n22_salted_hash():
    import hashlib
    DB(":memory:")
    assert hash_key("ナイキ") != hashlib.sha256("ナイキ".encode()).hexdigest() and len(hash_key("ナイキ")) == 64


# ---------- N-24〜N-27 ----------
def test_n24_rakuten_official_credit():
    html = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
    assert '<a href="https://developers.rakuten.com/" target="_blank">Supported by Rakuten Developers</a>' in html


def test_n25_publish_warnings_in_status():
    j = c.get("/api/status").json()
    assert isinstance(j["warnings"], list)
    assert any("利用規約" in w for w in j["warnings"])  # 同梱の規約は［ ］が未記入


def test_n26_hints_when_nothing_found():
    r = c.post("/api/analyze", json={"query": "存在しない商品 ZZZ-999"}).json()
    assert r["sample_count"] == 0 and r["hints"]
    js = open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8").read()
    assert "${ok ? `<label>検討中の総支払額" in js  # 判定しないときは価格入力欄を出さない


def test_n27_favicon():
    r = c.get("/static/favicon.svg")
    assert r.status_code == 200 and "svg" in r.headers["content-type"]
    assert c.get("/favicon.ico").status_code == 200
    assert 'rel="icon"' in open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()


def test_n10_demo_suggest_only_demo_products():
    from app.adapters.demo import DEMO_PRODUCTS
    names = {p["name"] for p in DEMO_PRODUCTS}
    sug = c.get("/api/suggest", params={"q": "そに"}).json()["suggestions"]
    assert sug and all(s in names for s in sug)
