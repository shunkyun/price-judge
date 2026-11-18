from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import httpx

from ..config import Settings
from ..models import FetchReport, Observation

REGISTRY: dict = {}
_throttle: dict = {}


class UpstreamBusy(Exception):
    """取得元の待ち行列が長すぎる（N-11）"""


def _slot_state(key: str) -> list:
    if key not in _throttle:
        _throttle[key] = [0.0]  # 次に使える時刻（monotonic）
    return _throttle[key]


def estimated_wait(key: str) -> float:
    import time
    return max(0.0, _slot_state(key)[0] - time.monotonic())


def reserve_slots(key: str, interval: float, n: int, max_wait: float = 0.0) -> list:
    """取得元ごとに約 interval 秒間隔の呼び出し枠を n 個まとめて予約する（N-11）。
    1回の検索のページ取得が他の利用者の呼び出しに割り込まれず連続するため、待ち時間が短くなる。
    max_wait > 0 で、最初の枠までの待ちがそれを超える場合は UpstreamBusy。"""
    import time
    st = _slot_state(key)
    now = time.monotonic()
    start = max(now, st[0])
    if max_wait > 0 and start - now > max_wait:
        raise UpstreamBusy()
    slots = [start + i * interval for i in range(n)]
    st[0] = slots[-1] + interval
    return slots


async def wait_until(t: float) -> None:
    import asyncio
    import time
    d = t - time.monotonic()
    if d > 0:
        await asyncio.sleep(d)


async def wait_turn(key: str, interval: float = 1.0) -> None:
    """取得元ごとに最低 interval 秒の間隔を空ける（H-6: 約1リクエスト/秒）。"""
    await wait_until(reserve_slots(key, interval, 1)[0])


def register(cls):
    REGISTRY[cls.id] = cls
    return cls


def dedupe_obs(obs: list) -> list:
    """同じ商品URLの重複（全体検索と店舗絞り込み検索の両方に出た等）を1件に"""
    seen, out = set(), []
    for o in obs:
        k = o.url
        if k in seen:
            continue
        seen.add(k)
        out.append(o)
    return out


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def to_int_price(v) -> Optional[int]:
    """APIの価格値を整数円に。数値でなければ None（推測しない）。"""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v if v > 0 else None
    if isinstance(v, float):
        return int(v) if v.is_integer() and v > 0 else None  # 円の小数は丸めず不採用（N-12）
    if isinstance(v, str):
        s = v.replace(",", "").replace("円", "").replace("¥", "").replace("￥", "").strip()
        if s.isdigit():
            i = int(s)
            return i if i > 0 else None
    return None


class PriceAdapter:
    id = "base"
    name = "base"
    category = "goods"  # goods / service
    provides_history = False
    used_site_count = 0  # 中古品を自動検索できる取得元サイト数（N-5）
    allow = None  # 許可リスト（専門店の楽天市場店・Yahoo!店の表示名と店舗絞り込み検索に使う。PriceService が設定）

    def __init__(self, settings: Settings):
        self.settings = settings

    async def throttle(self) -> None:
        await wait_turn(self.id, self.settings.upstream_interval)

    def reserve(self, n: int) -> list:
        return reserve_slots(self.id, self.settings.upstream_interval, n, self.settings.max_queue_wait)

    def enabled(self) -> bool:
        return False

    def disabled_reason(self) -> str:
        return "未設定"

    async def search(self, client: httpx.AsyncClient, query: str, condition: str) -> list:
        """現在の価格（Observation のリスト）を返す。"""
        return []

    async def history(self, client: httpx.AsyncClient, query: str, url: Optional[str]) -> list:
        """価格履歴（is_history=True の Observation）を返す。"""
        return []

    def report(self, status: str, count: int = 0, message: str = "") -> FetchReport:
        return FetchReport(self.id, self.name, status, count, message)
