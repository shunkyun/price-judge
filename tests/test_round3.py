"""第3回レビュー（R3-1〜R3-11, L3-1〜L3-3）の修正に対するテスト。データはすべて架空。
レビュアーの検証スクリプト（/tmp/pjfuzz の match3.py, acc3.py, r3_judge.py, r3_busy.py）の確認内容を取り込んでいる。"""
import asyncio
import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta, timezone

import pytest

os.environ["DEMO_MODE"] = "1"
from fastapi.testclient import TestClient  # noqa: E402

from app import envfile, main  # noqa: E402
from app.adapters import base as abase  # noqa: E402
from app.adapters.base import PriceAdapter, UpstreamBusy  # noqa: E402
from app.config import ConfigError, Settings, check_db_path  # noqa: E402
from app.db import DB, hash_key  # noqa: E402
from app.judge import clean_observations, judge  # noqa: E402
from app.models import Observation  # noqa: E402
from app.ratelimit import client_ip  # noqa: E402
from app.service import PriceService, TREND_NOTE  # noqa: E402
from app.textnorm import canon, normalize  # noqa: E402
from tests.helpers import NOW, ROOT, allow  # noqa: E402

c = TestClient(main.app)
A = allow()
FIX = json.load(open(os.path.join(ROOT, "tests", "fixtures", "realistic_titles.json"), encoding="utf-8"))
Q = "ソニー WH-1000XM5"


def setup_function():
    main.limiter.reset()


def _passes(q, t):
    o = Observation("yahoo", "y", "https://store.shopping.yahoo.co.jp/s/1.html", t, 10000, NOW, shipping=0)
    kept, _ = clean_observations([o], A, q, "new", 0.6, None)
    return bool(kept)


def _now(days=0):
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()


def mk(i, price, title=Q, src=None, ts=None):
    src = src or ("rakuten" if i % 2 else "yahoo")
    url = f"https://item.rakuten.co.jp/shop{i}/x{i}/" if src == "rakuten" else f"https://store.shopping.yahoo.co.jp/shop{i}/x{i}.html"
    return Observation(src, src, url, title, price, ts or _now(), seller=f"shop{i}", shipping=0, fees=0)


# ---------- R3-1 楽天の価格は保存しない ----------
def test_r3_1_rakuten_rows_never_saved(tmp_path):
    db = DB(str(tmp_path / "a.db"))
    n = db.save("a" * 64, [mk(1, 1000, src="rakuten"), mk(2, 1000, src="yahoo")])
    rows = db.conn.execute("SELECT source_id FROM observations").fetchall()
    assert n == 1 and [r[0] for r in rows] == ["yahoo"]


def test_r3_1_migration_deletes_existing_rakuten_rows(tmp_path):
    p = str(tmp_path / "v2.db")
    db = DB(p)
    db.conn.execute("INSERT INTO observations (product_key, source_id, source_name, url, title, price, condition, fetched_at)"
                    " VALUES (?, 'rakuten', 'r', 'https://item.rakuten.co.jp/a/b/', 't', 1, 'new', ?)", ("b" * 64, NOW))
    db.conn.execute("PRAGMA user_version = 2")
    db.conn.commit()
    db.conn.close()
    db2 = DB(p)
    assert db2.conn.execute("SELECT count(*) FROM observations WHERE source_id='rakuten'").fetchone()[0] == 0
    assert db2.conn.execute("PRAGMA user_version").fetchone()[0] == 3


class _Mixed(PriceAdapter):
    id = "mixed"
    name = "mixed"

    def enabled(self):
        return True

    async def search(self, client, query, condition):
        return [mk(i, 40000 + i * 100) for i in range(12)]


def _svc(tmp_path, adapter_cls=_Mixed):
    s = Settings()
    s.demo_mode_raw = "0"
    svc = PriceService(s, db=DB(str(tmp_path / "t.db")), allow=allow(False))
    svc.adapters = [adapter_cls(s)]
    return svc


def test_r3_1_service_trend_uses_non_rakuten_only(tmp_path):
    svc = _svc(tmp_path)
    r = asyncio.run(svc.analyze(Q))
    srcs = {row[0] for row in svc.db.conn.execute("SELECT source_id FROM observations")}
    assert srcs == {"yahoo"} and r["trend_note"] == TREND_NOTE
    assert all(p["n"] == 6 for s in r["trend"] for p in s["points"])  # 楽天の6件は推移に入らない


def test_r3_1_terms_and_readme():
    terms = open(os.path.join(ROOT, "static", "terms.html"), encoding="utf-8").read()
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    assert "保存しません" in terms and "24時間" in terms
    assert "LINEヤフーに問い合わせる" in readme


# ---------- R3-2 別名の境界・文脈 ----------
@pytest.mark.parametrize("title,bad", [
    ("adidas サンバイザー OG ゴルフ", "samba"), ("ダンクシュート 全巻", "dunk"), ("バンズ 6個入り パン", "vans"),
    ("スイッチ プレート 2口", "switch"), ("ソニーエンジェル", "sony"), ("無地 無印 Tシャツ", "muji"),
])
def test_r3_2_alias_not_applied_inside_words_or_without_context(title, bad):
    assert bad not in canon(title).split(" ")


@pytest.mark.parametrize("title,good", [
    ("ナイキエアマックス90", "air max"), ("ニンテンドースイッチ 本体", "switch"), ("アディダス サンバ OG", "samba"),
    ("ナイキ ダンク ロー", "dunk"), ("VANS バンズ オールドスクール スニーカー", "vans"),
])
def test_r3_2_alias_still_applied_in_context(title, good):
    assert f" {good} " in f" {canon(title)} "


def test_r3_2_r3_3_must_not_match_fixture_has_zero_pass():
    passed = [(q, t) for q, t in FIX["must_not_match"] if _passes(q, t)]
    assert passed == []
    acc = [(q, t) for q, t in FIX["accessory_review3"] if _passes(q, t)]
    assert acc == []
    g = [_passes(q, t) for q, t in FIX["genuine"] + FIX["genuine_review3"]]
    assert sum(g) / len(g) >= 0.9


# ---------- R3-3 型違い・類似品・部品・容量 ----------
@pytest.mark.parametrize("q,t", [
    ("Nintendo Switch", "ニンテンドー スイッチ 2"), ("Nintendo Switch", "Nintendo Switch 有機ELモデル"),
    ("ナイキ ダンク ロー", "ナイキ ダンク ハイ"), ("ユニクロ ヒートテック", "ユニクロ 超極暖ヒートテック"),
    ("AirPods Pro 第2世代", "AirPods Pro 第2世代 風 Bluetooth イヤホン"), ("iPhone 15 128GB", "iPhone 15 128GB 整備品"),
    ("AirPods Pro 第2世代", "AirPods Pro 第2世代 右耳のみ 片耳"), ("ナイキ エアマックス 90", "ナイキ エアマックス 270 ナイキ90周年"),
])
def test_r3_3_variants_excluded(q, t):
    assert not _passes(q, t)


def test_r3_3_light_grey_is_color_not_lite():
    assert _passes("ナイキ エアフォース1 '07", "NIKE AIR FORCE 1 '07 ライト グレー")
    assert _passes("ナイキ エアフォース1 '07", "ナイキ エアフォース1 '07 ライトボーン")
    assert _passes("Nintendo Switch 有機ELモデル", "任天堂 スイッチ 有機EL ネオンブルー")


def test_r3_3_storage_must_be_chosen():
    obs = [mk(i, 120000 + i * 100, f"Apple iPhone 15 {'128GB' if i % 2 else '256GB'} ブラック") for i in range(16)]
    r = judge(obs, A, "iPhone 15", 125000)
    assert r["verdict"] == "choose_variant" and r["stats"] is None and r["percentile_rank"] is None
    assert [g["label"] for g in r["variant_groups"]] == ["128GB", "256GB"]
    obs128 = [mk(i, 120000 + i * 100, "Apple iPhone 15 128GB ブラック") for i in range(12)] + \
             [mk(50, 125000, "iPhone 15 128GB/256GB/512GB")]
    r2 = judge(obs128 + obs[:6], A, "iPhone 15 128GB", 121000)
    assert r2["verdict"] in ("cheap", "fair", "expensive") and r2["sample_count"] == 12
    assert any("容量が複数記載" in e["excluded_reason"] for e in r2["excluded"])


# ---------- R3-4 類似品が多数のとき・推移の基準 ----------
def test_r3_4_majority_lookalikes_not_judged():
    obs = [mk(i, 2980 + i * 10) for i in range(7)] + [mk(100 + i, 39800 + i * 500) for i in range(5)]
    r = judge(obs, A, Q, 41000)
    assert r["verdict"] == "insufficient" and r["stats"] is None and r["price_split"] is True
    assert "2つの価格帯" in r["reasons"][0]


def test_r3_4_few_lookalikes_removed():
    obs = [mk(i, 2980 + i * 10) for i in range(2)] + [mk(100 + i, 39800 + i * 300) for i in range(12)]
    r = judge(obs, A, Q, 41000, min_sellers=1)
    assert r["verdict"] in ("fair", "expensive", "cheap") and r["sample_count"] == 12


class _Hist(PriceAdapter):
    id = "hist"
    name = "hist"

    def enabled(self):
        return True

    async def search(self, client, query, condition):
        return [mk(i, 40000 + i * 100, src="yahoo") for i in range(12)]


def test_r3_4_past_median_uses_same_filters(tmp_path):
    svc = _svc(tmp_path, _Hist)
    svc.s.min_sources = 1
    key = hash_key(normalize(Q) + "|new")
    old = []
    for d in range(10, 20):  # 各日：類似品4件＋本物3件（価格帯が分かれた日は推移・過去比較に使わない）
        old += [mk(1000 + d * 10 + i, 2980, ts=_now(d), src="yahoo") for i in range(4)]
        old += [mk(2000 + d * 10 + i, 40000, ts=_now(d), src="yahoo") for i in range(3)]
    svc.db.save(key, old)
    r = asyncio.run(svc.analyze(Q))
    assert r["history_median"] is None
    assert not any("%" in x and "過去" in x for x in r["reasons"])


def test_r3_4_extreme_history_difference_suppressed():
    obs = [mk(i, 40000 + i * 100) for i in range(12)]
    r = judge(obs, A, Q, None, min_sellers=1, history_median=3000)
    assert r["history_suppressed"] and not any("%" in x and "高く" in x for x in r["reasons"])


# ---------- R3-5 / R3-7 アクセスコード ----------
CODE = "correct-code-123"


def _auth(sid, code=CODE):
    return c.post("/api/auth", json={"code": code}, headers={"X-Session-Id": sid})


def test_r3_5_lockout_is_per_device_and_tokens_keep_working(monkeypatch):
    monkeypatch.setattr(main.settings, "access_code", CODE)
    tok = _auth("goodgood").json()["token"]
    for _ in range(main.settings.access_code_max_failures):
        assert _auth("badbadbad", "wrong").status_code == 401
    assert _auth("badbadbad").status_code == 429            # 間違えた端末だけ止まる
    assert _auth("otherdev1").json()["ok"] is True          # 同じ回線の別の端末は入れる
    assert c.get("/api/suggest", params={"q": "そに"}, headers={"X-Access-Token": tok}).status_code == 200


def test_r3_5_ip_backstop_but_existing_tokens_unaffected(monkeypatch):
    monkeypatch.setattr(main.settings, "access_code", CODE)
    monkeypatch.setattr(main.settings, "access_code_ip_max_failures", 15)
    tok = _auth("goodgood").json()["token"]
    for i in range(15):
        _auth(f"rotating{i:04d}", "wrong")
    assert _auth("newcomer1").status_code == 429            # セッションIDを変えても回線全体の上限で止まる
    assert c.get("/api/suggest", params={"q": "そに"}, headers={"X-Access-Token": tok}).status_code == 200


def test_r3_5_forged_token_rejected(monkeypatch):
    monkeypatch.setattr(main.settings, "access_code", CODE)
    tok = _auth("goodgood").json()["token"]
    sid, exp, sig = tok.split(".")
    for bad in (f"{'0' * 32}.{exp}.{sig}", f"{sid}.{int(exp) + 999}.{sig}", f"{sid}.{int(time.time()) - 5}.{sig}", "x"):
        assert c.get("/api/suggest", params={"q": "そに"}, headers={"X-Access-Token": bad}).status_code == 401


def test_r3_7_sessions_per_ip_capped(monkeypatch):
    monkeypatch.setattr(main.settings, "access_code", CODE)
    monkeypatch.setattr(main.settings, "max_sessions_per_ip", 3)
    codes = [_auth(f"dev{i:05d}").status_code for i in range(4)]
    assert codes == [200, 200, 200, 429]


class _Req:
    def __init__(self, headers, host="10.0.0.5"):
        from starlette.datastructures import Headers
        self.headers = Headers(headers)
        self.client = type("C", (), {"host": host})()


def test_r3_5_proxy_header_only_when_configured():
    h = {"CF-Connecting-IP": "198.51.100.7", "X-Forwarded-For": "1.1.1.1, 203.0.113.9"}
    assert client_ip(_Req(h)) == "10.0.0.5"                                            # 既定：信用しない
    assert client_ip(_Req(h), 0, "CF-Connecting-IP") == "198.51.100.7"
    assert client_ip(_Req(h), 0, "CF-Connecting-IP", "10.0.0.0/8") == "198.51.100.7"
    assert client_ip(_Req(h, host="192.0.2.1"), 0, "CF-Connecting-IP", "10.0.0.0/8") == "192.0.2.1"
    assert client_ip(_Req(h, host="192.0.2.1"), 1, "", "10.0.0.0/8") == "192.0.2.1"
    assert Settings().client_ip_header == os.getenv("TRUSTED_PROXY_HEADER", os.getenv("CLIENT_IP_HEADER", ""))
    y = open(os.path.join(ROOT, "render.yaml"), encoding="utf-8").read()
    assert "TRUSTED_PROXY_HEADER" in y and "CF-Connecting-IP" in y


def test_r3_5_frontend_uses_token_not_code():
    js = open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8").read()
    assert "X-Access-Token" in js and '"X-Access-Code"' not in js and "accessCode" not in js


# ---------- R3-6 失敗をキャッシュしない・切断 ----------
def test_r3_6_failures_not_cached_and_leader_cancel(tmp_path):
    calls = {"n": 0}

    class Flaky(PriceAdapter):
        id = "flaky"
        name = "flaky"
        fail = True

        def enabled(self):
            return True

        async def search(self, client, query, condition):
            calls["n"] += 1
            await asyncio.sleep(0.05)
            if type(self).fail:
                raise UpstreamBusy()
            return [mk(i, 40000 + i * 100) for i in range(12)]

    svc = _svc(tmp_path, Flaky)

    async def go():
        r1 = await svc.analyze(Q)
        Flaky.fail = False
        r2 = await svc.analyze(Q)
        svc._cache = type(svc._cache)()
        t1 = asyncio.create_task(svc.analyze(Q))
        await asyncio.sleep(0.01)
        t2 = asyncio.create_task(svc.analyze(Q))
        await asyncio.sleep(0.01)
        t1.cancel()
        return r1, r2, await t2

    r1, r2, r3 = asyncio.run(go())
    assert r1["verdict"] == "insufficient" and r2["sample_count"] > 0 and calls["n"] >= 2
    assert r3["sample_count"] > 0  # 最初の利用者が切断しても相乗りした側は結果を受け取る


def test_l3_1_retry_after_matches_message():
    main.service._cache = type(main.service._cache)()
    abase._throttle["demo"] = [time.monotonic() + 40]
    try:
        r = c.post("/api/analyze", json={"query": Q})
        assert r.status_code == 503
        assert f"約{r.headers['retry-after']}秒" in r.json()["error"]
    finally:
        abase._throttle["demo"] = [0.0]


# ---------- R3-8 DB_PATH ----------
@pytest.mark.parametrize("p", ["/prices.db", "/app/prices.db", "/etc/x.db", "/usr/x.db", os.path.join(ROOT, "x.db")])
def test_r3_8_dangerous_db_path_rejected(p):
    with pytest.raises(ConfigError):
        check_db_path(p)


def test_r3_8_entrypoint_never_chowns_outside_data_dir():
    ep = open(os.path.join(ROOT, "scripts", "docker-entrypoint.sh"), encoding="utf-8").read()
    assert "chown -R" not in ep
    for bad in ("/prices.db", "relative.db", "/app/p.db"):
        r = subprocess.run(["sh", os.path.join(ROOT, "scripts", "docker-entrypoint.sh"), "true"],
                           env={**os.environ, "DB_PATH": bad}, capture_output=True, text=True)
        assert r.returncode == 0 and "所有者を変更しません" in r.stderr
    check_db_path("/data/prices.db")


# ---------- R3-9 無料プラン ----------
def test_r3_9_free_blueprint_and_guide():
    free = open(os.path.join(ROOT, "render.free.yaml"), encoding="utf-8").read()
    assert "plan: free" in free and "disk:" not in free and "TRUSTED_PROXY_HEADER" in free
    g = open(os.path.join(ROOT, "docs", "使い方ガイド.md"), encoding="utf-8").read()
    assert "Blueprint Path" in g and "render.free.yaml" in g


# ---------- R3-10 .env の読み込み ----------
def test_r3_10_env_parsing_is_literal(monkeypatch, tmp_path):
    text = '\ufeffACCESS_CODE=ab$cd;rm -rf `x`\r\nexport RAKUTEN_APP_ID="12 34"\r\n# c\r\nYAHOO_APP_ID=abc # memo\r\nBAD KEY=1\r\nQ=\'$HOME\'\r\n'
    d = envfile.parse(text)
    assert d == {"ACCESS_CODE": "ab$cd;rm -rf `x`", "RAKUTEN_APP_ID": "12 34", "YAHOO_APP_ID": "abc", "Q": "$HOME"}
    p = tmp_path / ".env"
    p.write_bytes(text.encode("utf-8"))
    monkeypatch.setenv("YAHOO_APP_ID", "from-dashboard")
    for k in ("ACCESS_CODE", "RAKUTEN_APP_ID", "Q"):
        monkeypatch.delenv(k, raising=False)
    envfile.load(str(p))
    assert os.environ["YAHOO_APP_ID"] == "from-dashboard" and os.environ["ACCESS_CODE"] == "ab$cd;rm -rf `x`"
    for f in ("start.sh", "start.bat"):
        s = open(os.path.join(ROOT, "scripts", f), encoding="utf-8").read()
        assert ". ./.env" not in s and 'for /f' not in s


# ---------- R3-11 管理者向けの注意 ----------
def test_r3_11_warnings_only_for_admin(monkeypatch):
    assert c.get("/api/status").json()["warnings"]  # テストクライアント＝自分のPC
    pub = c.get("/api/status", headers={"X-Forwarded-For": "203.0.113.5"}).json()
    assert pub["warnings"] == []
    monkeypatch.setattr(main.settings, "admin_token", "adm-secret")
    j = c.get("/api/status", params={"admin": "adm-secret"}, headers={"X-Forwarded-For": "203.0.113.5"}).json()
    assert j["warnings"]


# ---------- L3-2 戻る ----------
def test_l3_2_condition_and_price_are_pushed_to_history():
    js = open(os.path.join(ROOT, "static", "app.js"), encoding="utf-8").read()
    assert "function pushRoute" in js and 'p.get("cond")' in js and 'p.get("tp")' in js
