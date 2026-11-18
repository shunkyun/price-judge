from tests.helpers import allow


def test_trusted_domains():
    a = allow()
    assert a.match("https://item.rakuten.co.jp/shop/abc/").id == "rakuten"
    assert a.match("https://store.shopping.yahoo.co.jp/s/x.html").id == "yahoo"
    assert a.match("https://www.amazon.co.jp/dp/B000000000").id == "amazon_jp"
    assert a.match("https://zozo.jp/shop/x/goods/1/").id == "zozotown"
    assert a.match("https://jp.mercari.com/item/m123").id == "mercari"


def test_untrusted_and_lookalikes():
    a = allow()
    for u in ["https://evil.com/item.rakuten.co.jp", "https://item.rakuten.co.jp.evil.com/x",
              "https://fakeamazon.co.jp/dp/X", "ftp://amazon.co.jp/x", "javascript:alert(1)",
              "https://user:pw@amazon.co.jp/x", "http://127.0.0.1/", "not a url", ""]:
        assert a.match(u) is None, u


def test_l1_https_and_default_port_only():
    a = allow()
    assert a.match("http://item.rakuten.co.jp/shop/x") is None
    assert a.match("https://item.rakuten.co.jp:8443/shop/x") is None
    assert a.match("https://item.rakuten.co.jp:443/shop/x").id == "rakuten"


def test_path_prefix_official_store():
    a = allow()
    assert a.match("https://www.uniqlo.com/jp/ja/products/E1").id == "uniqlo"
    assert a.match("https://www.uniqlo.com/us/en/products/E1") is None  # 日本以外は対象外
    assert a.match("https://www.nike.com/jp/t/air-force-1") .id == "nike"


def test_demo_domain_only_in_demo_mode():
    assert allow(False).match("https://demo-a.demo.invalid/item/1") is None
    assert allow(True).match("https://demo-a.demo.invalid/item/1").id == "demo"
