"""簡易レート制限（スライディングウィンドウ・メモリ内）。H-6 / N-3 / N-6 / N-13
複数インスタンスで動かす場合は各インスタンスごとの制限になる点に注意。"""
from __future__ import annotations

import ipaddress
import threading
import time
from collections import OrderedDict, deque


class RateLimiter:
    """キーごとの呼び出し時刻を保持。キー数が上限を超えたら「最も長く使われていないキー」から削除する
    （N-3: 以前は全消去しており、偽装キーを大量に送ると全員の制限がリセットできた）。"""

    def __init__(self, max_keys: int = 20000):
        self.hits: OrderedDict = OrderedDict()
        self.lock = threading.Lock()
        self.max_keys = max_keys

    def _q(self, key: str, now: float, window: float) -> deque:
        q = self.hits.get(key)
        if q is None:
            q = deque()
            self.hits[key] = q
            while len(self.hits) > self.max_keys:
                self.hits.popitem(last=False)
        else:
            self.hits.move_to_end(key)
        while q and now - q[0] > window:
            q.popleft()
        return q

    def allow(self, key: str, limit: int, window: float = 60.0) -> bool:
        """上限内なら記録して True"""
        if limit <= 0:
            return True
        now = time.monotonic()
        with self.lock:
            q = self._q(key, now, window)
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def exceeded(self, key: str, limit: int, window: float) -> bool:
        """記録せずに、直近 window 秒の件数が上限以上かを返す"""
        now = time.monotonic()
        with self.lock:
            return len(self._q(key, now, window)) >= limit

    def record(self, key: str, window: float) -> None:
        now = time.monotonic()
        with self.lock:
            self._q(key, now, window).append(now)

    def reset(self):
        with self.lock:
            self.hits.clear()


def _valid_ip(s: str) -> str:
    s = (s or "").strip()
    try:
        return str(ipaddress.ip_address(s))
    except ValueError:
        return ""


def _peer_trusted(request, cidrs: str) -> bool:
    if not cidrs:
        return True
    peer = _valid_ip(request.client.host if request.client else "")
    if not peer:
        return False
    for c in cidrs.split(","):
        try:
            if ipaddress.ip_address(peer) in ipaddress.ip_network(c.strip(), strict=False):
                return True
        except ValueError:
            continue
    return False


def client_ip(request, trust_proxy_hops: int = 0, client_ip_header: str = "", trusted_cidrs: str = "") -> str:
    """利用者のIPを決める（N-3, R3-5）。
    - client_ip_header（TRUSTED_PROXY_HEADER。例 CF-Connecting-IP）: 明示的に設定したときだけ使う。既定では一切信用しない
    - trust_proxy_hops=N: X-Forwarded-For の「右から N 番目」（信頼するプロキシが追記した値）。左端は偽装できるため使わない
    - trusted_cidrs を設定した場合、接続元がその範囲のときだけ上の2つを使う
    - どれも当てはまらなければ接続元アドレス"""
    peer = request.client.host if request.client else "unknown"
    if (client_ip_header or trust_proxy_hops > 0) and not _peer_trusted(request, trusted_cidrs):
        return peer
    if client_ip_header:
        ip = _valid_ip(request.headers.get(client_ip_header, ""))
        if ip:
            return ip
    if trust_proxy_hops > 0:
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(parts) >= trust_proxy_hops:
            ip = _valid_ip(parts[-trust_proxy_hops])
            if ip:
                return ip
    return peer
