"""C-1 / H-4 / H-7 / L-3 / M-2 のテスト（フェイクアダプタで非デモモードを再現）"""
import asyncio
import sqlite3

from app.adapters.base import PriceAdapter
from app.config import Settings
from app.db import DB
from app.models import Observation
from app.service import PriceService, TTLCache
from app import trend
from tests.helpers import allow

Q = "ナイキ エアフォース1 '07"


class Fake(PriceAdapter):
    id = "fake"
    name = "fake"

    def enabled(self):
        return True

    async def search(self, client, query, condition):
        from app.adapters.base import now_iso
        out = []
        for i, p in enumerate([15000, 15500, 16000, 16200, 16500, 16600, 16800, 17000, 17200, 17500, 18000, 16400]):
            src, host = ("rakuten", "item.rakuten.co.jp") if i % 2 else ("yahoo", "store.shopping.yahoo.co.jp")
            out.append(Observation(src, src, f"https://{host}/shop{i}/x{i}", Q, p, now_iso(), seller=f"s{i}", shipping=0, fees=0))
        out.append(Observation("x", "x", "https://evil.example/x", Q, 1, now_iso(), shipping=0))
        return out


def make(tmp_path):
    s = Settings()
    s.demo_mode_raw = "0"
    s.db_path = str(tmp_path / "t.db")
    svc = PriceService(s, db=DB(s.db_path), allow=allow(False))
    svc.adapters = [Fake(s)]
    return svc


def test_c1_repeated_queries_do_not_inflate_samples(tmp_path):
    svc = make(tmp_path)
    counts = []
    for q in [Q, Q + " ", "ﾅｲｷ エアフォース1 '07", "ナイキ　エアフォース1 '07"]:
        svc._cache = TTLCache()  # キャッシュ切れを再現
        r = asyncio.run(svc.analyze(q))
        counts.append(r["sample_count"])
        assert not any("重複" in e["excluded_reason"] for e in r["excluded"])
    assert counts == [12, 12, 12, 12]


def test_h4_raw_query_not_persisted_and_untrusted_not_saved(tmp_path):
    svc = make(tmp_path)
    asyncio.run(svc.analyze("山田太郎 090-1234-5678 " + Q))
    con = sqlite3.connect(svc.s.db_path)
    dump = "\n".join(con.iterdump())
    assert "山田太郎" not in dump and "090-1234-5678" not in dump
    assert "evil.example" not in dump  # L-3
    assert "products" not in [r[0] for r in con.execute("select name from sqlite_master")]


def test_h7_past_median_only_total_series():
    s_total = {"kind": "total", "points": [{"date": f"2026-0{m}-01", "median": 100, "n": 1} for m in range(1, 5)]}
    s_item = {"kind": "item_price", "points": [{"date": f"2026-0{m}-02", "median": 999, "n": 1} for m in range(1, 5)]}
    assert trend.past_median([s_total, s_item]) == 100
    assert trend.past_median([s_item]) is None


def test_m2_cache_bounded():
    c = TTLCache(max_items=3)
    for i in range(10):
        c.set(i, i)
    assert len(c) == 3 and c.get(9) == 9 and c.get(0) is None
