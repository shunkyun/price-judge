"""判定ロジックの性質テスト（レビュー2のファズを取り込み）: 架空データでの不変条件・単調性・順序/ポイント非依存"""
import copy
import re

from hypothesis import given, settings, strategies as st, HealthCheck, assume
from app.models import Observation
from app.judge import judge
from app import stats
from tests.helpers import allow, NOW
from datetime import datetime

AL = allow()
NOWDT = datetime.fromisoformat(NOW)
TITLE = "ナイキ エアフォース1 '07"
ORDER = {"too_cheap":0,"cheap":1,"fair":2,"expensive":3,"too_expensive":4}

obs_st = st.builds(lambda i, src, shop, price, ship, pts: Observation(
        source_id=src, source_name=src,
        url=(f"https://item.rakuten.co.jp/shop{shop}/it{i}" if src=="rakuten" else f"https://store.shopping.yahoo.co.jp/s{shop}/i{i}.html"),
        title=TITLE, price=price, fetched_at=NOW, seller=f"{src}{shop}", shipping=ship, fees=0, point_amount=pts),
    st.integers(0, 10**6), st.sampled_from(["rakuten","yahoo"]), st.integers(0, 12),
    st.integers(1, 200000), st.one_of(st.none(), st.just(0), st.integers(0, 2000)), st.one_of(st.none(), st.integers(0, 5000)))

def run(obs, target=None, **kw):
    return judge(obs, AL, TITLE, target, now=NOWDT, **kw)

@settings(max_examples=250, suppress_health_check=list(HealthCheck), deadline=None)
@given(st.lists(obs_st, min_size=0, max_size=80), st.one_of(st.none(), st.integers(1, 300000)),
       st.integers(4, 15), st.integers(1, 2), st.integers(1, 5))
def test_invariants(obs, target, ms, msrc, msel):
    r = run(obs, target, min_samples=ms, min_sources=msrc, min_sellers=msel)
    inputs = {(o.url, o.price, o.shipping) for o in obs}
    for o in r["observations"]:
        assert (o["url"], o["price"], o["shipping"]) in inputs           # no invented data
        assert o["shipping"] is not None and o["fees"] is not None
        assert o["total_payable"] == o["price"] + o["shipping"] + o["fees"]
    if r["verdict"] == "insufficient":
        assert r["stats"] is None and r["effective_stats"] is None and r["percentile_rank"] is None
        assert not re.search(r"\d[\d,]*円", " ".join(r["reasons"]))       # no price in reasons
    else:
        assert r["sample_count"] >= ms and r["source_count"] >= msrc and r["seller_count"] >= msel
        s = r["stats"]; vals = sorted(o["total_payable"] for o in r["observations"])
        assert s["min"] == vals[0] and s["max"] == vals[-1] and s["n"] == len(vals)
        assert s["min"] <= s["q1"] <= s["median"] <= s["q3"] <= s["max"]
        assert s["fence_low"] <= s["q1"] and s["q3"] <= s["fence_high"]
        if target is not None:
            assert 0 <= r["percentile_rank"] <= 100
        # every yen number in reasons must be target, a stat, a fence or history
        allowed = {round(x) for x in [s["median"], s["q1"], s["q3"], s["fence_low"], s["fence_high"]] if x is not None}
        allowed |= {int(x + 0.5) for x in [s["median"], s["q1"], s["q3"], s["fence_low"], s["fence_high"]]}
        if target: allowed.add(target)
        for m in re.findall(r"([\d,]+)円", " ".join(r["reasons"])):
            assert int(m.replace(",", "")) in allowed, (m, allowed)

@settings(max_examples=150, suppress_health_check=list(HealthCheck), deadline=None)
@given(st.lists(obs_st, min_size=20, max_size=80), st.integers(1, 300000), st.integers(1, 300000))
def test_monotonic(obs, a, b):
    lo, hi = sorted((a, b))
    r1, r2 = run(obs, lo, min_samples=4, min_sellers=1), run(obs, hi, min_samples=4, min_sellers=1)
    if r1["verdict"] in ORDER and r2["verdict"] in ORDER:
        assert ORDER[r1["verdict"]] <= ORDER[r2["verdict"]]

@settings(max_examples=150, suppress_health_check=list(HealthCheck), deadline=None)
@given(st.lists(obs_st, min_size=0, max_size=60), st.integers(1, 300000), st.randoms())
def test_order_and_points_invariance(obs, target, rnd):
    # N-18: 同じURLの重複を含んだままでも、入力順で結果が変わらないこと
    r1 = run(obs, target, min_samples=4, min_sellers=1)
    sh = list(obs); rnd.shuffle(sh)
    r2 = run(sh, target, min_samples=4, min_sellers=1)
    assert (r1["verdict"], r1["sample_count"], r1["stats"] and r1["stats"]["median"]) == (r2["verdict"], r2["sample_count"], r2["stats"] and r2["stats"]["median"])
    pts = [copy.copy(o) for o in obs]
    for o in pts: o.point_amount = 99999
    r3 = run(pts, target, min_samples=4, min_sellers=1)
    assert r3["verdict"] == r1["verdict"]

@settings(max_examples=200, deadline=None)
@given(st.lists(st.integers(1, 10**7), min_size=1, max_size=200), st.floats(0, 100))
def test_stats(vals, p):
    s = sorted(vals)
    v = stats.percentile(s, p)
    assert s[0] <= v <= s[-1]
    r = stats.percentile_rank(s, s[0]); assert 0 <= r <= 100
    sm = stats.summarize(vals); assert sm["min"] <= sm["q1"] <= sm["median"] <= sm["q3"] <= sm["max"]
    h = stats.histogram(vals); assert sum(b["count"] for b in h) == len(vals)
