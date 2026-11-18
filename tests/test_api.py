import os

os.environ["DEMO_MODE"] = "1"
os.environ["DB_PATH"] = ":memory:"
from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.main import app  # noqa: E402

c = TestClient(app)


def setup_function():
    main.limiter.reset()


def test_status_demo():
    j = c.get("/api/status").json()
    assert j["demo_mode"] is True and "llm_provider_host" in j


def test_suggest_dictionary_only():
    j = c.get("/api/suggest", params={"q": "ないき"}).json()
    assert any("ナイキ" in s for s in j["suggestions"])


def test_h4_queries_never_become_suggestions():
    c.post("/api/analyze", json={"query": "山田太郎 090-1234-5678"})
    assert c.get("/api/suggest", params={"q": "山田"}).json()["suggestions"] == []


def test_analyze_demo_judgment():
    r = c.post("/api/analyze", json={"query": "ソニー WH-1000XM5", "target_price": 44000}).json()
    assert r["demo_mode"] is True
    assert r["verdict"] in ("cheap", "fair", "expensive")
    assert r["sample_count"] >= r["min_samples"]
    assert all(o["is_demo"] for o in r["observations"])
    assert any("外れ値" in e["excluded_reason"] for e in r["excluded"])
    assert any("送料不明" in e["excluded_reason"] for e in r["excluded"])
    assert r["trend"]
    assert "ai_summary" not in r  # C-2


def test_analyze_demo_insufficient_has_no_stats():
    r = c.post("/api/analyze", json={"query": "無印良品 スニーカー"}).json()
    assert r["verdict"] == "insufficient" and r["stats"] is None


def test_h7_demo_history_comparison_same_basis():
    r = c.post("/api/analyze", json={"query": "ソニー WH-1000XM5"}).json()
    assert all("kind" in s for s in r["trend"])
    assert any("同じ総支払額基準" in x for x in r["reasons"])


def test_untrusted_url_rejected():
    r = c.post("/api/analyze", json={"query": "https://scam.example/item/1"})
    assert r.status_code == 400 and "許可リスト" in r.json()["error"]


def test_l8_demo_does_not_fetch_urls():
    r = c.post("/api/analyze", json={"query": "https://item.rakuten.co.jp/shop/item/"})
    assert r.status_code == 400 and "デモモード" in r.json()["error"]


def test_l10_validation_error_format():
    r = c.post("/api/analyze", json={"query": "x", "target_price": -5})
    assert r.status_code == 422 and isinstance(r.json()["error"], str)


def test_h6_rate_limit():
    codes = [c.post("/api/analyze", json={"query": "ソニー WH-1000XM5"}).status_code for _ in range(12)]
    assert codes[:10] == [200] * 10 and codes[-1] == 429


def test_m1_large_body_rejected_before_read():
    r = c.post("/api/identify", content=b"x", headers={"content-length": str(50 * 1024 * 1024), "content-type": "image/png"})
    assert r.status_code == 413


def test_m1_magic_bytes():
    r = c.post("/api/identify", files={"image": ("a.png", b"not an image", "image/png")})
    assert r.status_code == 400
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    r = c.post("/api/identify", files={"image": ("a.png", png, "image/png")})
    assert r.status_code == 200 and r.json()["demo"] is True


def test_l4_security_headers_and_head():
    r = c.get("/")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert c.head("/").status_code == 200


def test_terms_page_and_credits():
    assert "プライバシーポリシー" in c.get("/terms").text
    html = c.get("/").text
    assert "Supported by Rakuten Developers" in html and "Webサービス by Yahoo! JAPAN" in html


def test_h6_access_code_optional(monkeypatch):
    monkeypatch.setattr(main.settings, "access_code", "secret-123")
    try:
        assert c.get("/api/status").status_code == 200  # 状態確認はコード不要
        r = c.get("/api/suggest", params={"q": "ナイキ"})
        assert r.status_code == 401 and r.json()["need_code"] is True
        assert c.get("/api/suggest", params={"q": "ナイキ"}, headers={"X-Access-Code": "wrong"}).status_code == 401
        assert c.get("/api/suggest", params={"q": "ナイキ"}, headers={"X-Access-Code": "secret-123"}).status_code == 200
        assert c.get("/").status_code == 200  # 画面自体は表示できる
    finally:
        monkeypatch.setattr(main.settings, "access_code", "")
