import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", ":memory:")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _robots_allow_all(request, monkeypatch):
    """既存のページ取得テストはモック通信なので robots.txt の確認を素通しにする。
    robots.txt 自体のテストは @pytest.mark.real_robots を付ける。"""
    from app import robots
    robots.clear_cache()
    if request.node.get_closest_marker("real_robots"):
        yield
        return

    async def _ok(client, url, ua, now=None):
        return None
    monkeypatch.setattr(robots, "check", _ok)
    yield
