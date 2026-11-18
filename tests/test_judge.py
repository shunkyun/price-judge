from datetime import datetime

import pytest

from app.judge import judge
from app.models import Observation
from tests.helpers import allow, ob, sample, NOW

T = datetime.fromisoformat(NOW)
Q = "ナイキ エアフォース1 '07"
BASE = [15000, 15500, 16000, 16200, 16500, 16600, 16800, 17000, 17200, 17500, 18000, 16400]


def J(obs, **kw):
    kw.setdefault("now", T)
    return judge(obs, allow(), Q, **kw)


def test_insufficient_samples():
    r = J(sample(BASE[:9]), target_price=16000)
    assert r["verdict"] == "insufficient"
    assert r["verdict_label"] == "データ不足のため判定不可"
    assert r["percentile_rank"] is None


def test_h1_insufficient_returns_no_price_range():
    r = J(sample(BASE[:6]), target_price=16000)
    assert r["stats"] is None and r["effective_stats"] is None
    assert not any("中央値" in x or "価格帯" in x for x in r["reasons"])


def test_insufficient_sources():
    obs = [ob(p, i, "rakuten") for i, p in enumerate(BASE)]
    assert J(obs, target_price=16000)["verdict"] == "insufficient"


def test_min_sellers():
    obs = sample(BASE)
    for i, o in enumerate(obs):
        o.seller = f"s{i % 2}"
    obs = [o for o in obs]
    r = J(obs, min_sellers=3)
    assert r["verdict"] == "insufficient" and r["seller_count"] <= 2


def test_min_samples_configurable():
    assert J(sample(BASE[:6]), target_price=16000, min_samples=5)["verdict"] == "fair"


def test_verdicts():
    obs = sample(BASE)
    assert J(obs, target_price=16500)["verdict"] == "fair"
    assert J(obs, target_price=15300)["verdict"] == "cheap"
    assert J(obs, target_price=17900)["verdict"] == "expensive"
    assert J(obs, target_price=5000)["verdict"] == "too_cheap"
    assert J(obs, target_price=40000)["verdict"] == "too_expensive"
    assert J(obs)["verdict"] == "no_target"


def test_outliers_excluded():
    r = J(sample(BASE + [3000, 90000]), target_price=16500)
    assert r["sample_count"] == len(BASE)
    rs = [e["excluded_reason"] for e in r["excluded"]]
    # 3,000円は価格帯が大きく離れた少数グループなので「付属品・類似品の疑い」、90,000円は IQR の外れ値
    assert rs.count("外れ値（IQR法）") == 1 and sum("付属品・類似品の疑い" in x for x in rs) == 1


def test_l7_fences_computed_once_before_removal():
    r = J(sample(BASE + [3000, 90000]))
    from app import stats
    lo, hi = stats.iqr_fences(BASE + [90000])  # 3,000円は付属品の疑いとして IQR より前に除外
    assert (r["stats"]["fence_low"], r["stats"]["fence_high"]) == (lo, hi)


def test_untrusted_url_excluded():
    obs = sample(BASE)
    obs.append(Observation("x", "x", "https://scam.example/item", Q, 1000, NOW, shipping=0))
    r = J(obs)
    assert any(e["excluded_reason"] == "信頼サイト許可リスト外" for e in r["excluded"])


def test_unknown_shipping_not_counted():
    obs = sample(BASE)
    for o in obs[:4]:
        o.shipping = None
    r = J(obs, target_price=16500)
    assert r["sample_count"] == len(BASE) - 4 and r["verdict"] == "insufficient"


def test_unknown_fees_not_counted():
    obs = sample(BASE)
    obs[0].fees = None
    assert J(obs)["sample_count"] == len(BASE) - 1


def test_total_payable_includes_shipping_and_fees():
    o = ob(10000, shipping=800, fees=330)
    assert o.total_payable == 11130
    o.point_amount = 100
    assert o.effective_price == 11030


def test_judgment_uses_total_payable():
    obs = sample(BASE)
    r1 = J(obs)
    for o in obs:
        o.shipping = 1000
    assert J(obs)["stats"]["median"] == r1["stats"]["median"] + 1000


def test_h2_judgment_never_uses_effective_price():
    obs = sample(BASE)
    for o in obs:
        o.point_amount = 3000  # 大きなポイントがあっても判定は総支払額
    r = J(obs, target_price=16500)
    assert r["verdict"] == "fair"
    assert r["effective_stats"]["median"] == r["stats"]["median"] - 3000
    import inspect
    assert "basis" not in inspect.signature(judge).parameters


def test_m4_same_shop_variants_collapsed():
    obs = sample(BASE)
    for k in range(5):  # 同じ店の同価格（色違い）
        v = ob(16500, 100 + k, "yahoo")
        v.seller = "shop0"
        v.url = f"https://store.shopping.yahoo.co.jp/shop0/color{k}.html"
        obs.append(v)
    r = J(obs)
    assert sum("集約" in e["excluded_reason"] for e in r["excluded"]) >= 4


def test_h3_variants_excluded_in_judge():
    obs = sample(BASE, title="Apple AirPods Pro 第2世代")
    obs.append(ob(25000, 90, "yahoo", "Apple AirPods Pro 第1世代"))
    obs.append(ob(80000, 91, "yahoo", "Apple AirPods Max"))
    r = judge(obs, allow(), "Apple AirPods Pro 第2世代", now=T)
    assert r["sample_count"] == len(BASE)


def test_condition_and_used_words():
    obs = sample(BASE)
    obs.append(ob(9000, 99, "yahoo", Q + " 中古 美品"))
    assert any(e["excluded_reason"].startswith("コンディション") for e in J(obs)["excluded"])


def test_accessory_and_irrelevant_excluded():
    obs = sample(BASE)
    obs.append(ob(1980, 98, "yahoo", Q + " 専用ケース"))
    obs.append(ob(16000, 97, "yahoo", "アディダス スタンスミス"))
    rs = [e["excluded_reason"] for e in J(obs)["excluded"]]
    assert any("ケース" in x for x in rs) and any("一致" in x for x in rs)


def test_duplicate_url_latest_kept():
    obs = sample(BASE)
    obs.append(ob(99999, 0, "yahoo", fetched="2026-11-10T00:00:00+00:00"))
    r = J(obs)
    assert all(o["price"] != 99999 for o in r["observations"])


def test_old_observations_excluded():
    obs = sample(BASE)
    obs.append(ob(16000, 50, "yahoo", fetched="2026-09-01T00:00:00+00:00"))
    assert any("より古い" in e["excluded_reason"] for e in J(obs)["excluded"])


def test_every_shown_price_has_url_and_timestamp():
    for o in J(sample(BASE))["observations"]:
        assert o["url"].startswith("https://") and o["fetched_at"]


def test_history_median_reason():
    r = J(sample(BASE), history_median=20000)
    assert any("過去の中央値" in x and "安く" in x for x in r["reasons"])


def test_m5_population_note():
    assert any("送料が確定できる出品" in x for x in J(sample(BASE))["reasons"])
