"""robots.txt の確認（URL指定で商品ページを1件読む前に必ず実行）。

RFC 9309 準拠の簡易実装:
 - 自分の名前（PriceJudgeBot）を含む User-agent グループ、無ければ「*」グループの規則を使う
 - Allow / Disallow は「*」（任意の文字列）と末尾「$」に対応。最も長く一致した規則を採用し、同じ長さなら Allow
 - robots.txt が 404/410 → 制限なし（規格どおり）
 - それ以外の取得失敗（403・5xx・タイムアウト・ボット対策画面など）→ 安全側に倒して「読まない」
"""
from __future__ import annotations

import re
import time
from typing import Optional
from urllib.parse import urlparse

import httpx

BOT_TOKEN = "pricejudgebot"
MAX_ROBOTS_BYTES = 500_000
OK_TTL = 3600.0      # 取得できた robots.txt は1時間使い回す
FAIL_TTL = 300.0     # 取得できなかった場合の再確認間隔（5分）
_cache: dict = {}    # host -> (expires_at, rules or None, reason)


class RobotsBlocked(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def parse(text: str, token: str = BOT_TOKEN) -> list:
    """robots.txt から、自分に適用される (allow: bool, pattern) の一覧を返す。"""
    groups: list = []  # [(agents, rules)]
    agents: list = []
    rules: list = []
    last_was_agent = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip().lower(), v.strip()
        if k == "user-agent":
            if not last_was_agent and (agents or rules):
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(v.lower())
            last_was_agent = True
        elif k in ("allow", "disallow"):
            last_was_agent = False
            if agents:
                rules.append((k == "allow", v))
        else:
            last_was_agent = False
    if agents or rules:
        groups.append((agents, rules))
    mine = [r for a, r in groups if any(x and x != "*" and x in token for x in a)]
    if mine:
        return [x for r in mine for x in r]
    return [x for a, r in groups if "*" in a for x in r]


def _to_regex(pattern: str) -> re.Pattern:
    end = pattern.endswith("$")
    body = pattern[:-1] if end else pattern
    rx = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
    return re.compile(rx + ("$" if end else ""))


def allowed(rules: list, url: str) -> bool:
    p = urlparse(url)
    path = (p.path or "/") + (("?" + p.query) if p.query else "")
    best_len, best_allow = -1, True
    for allow, pat in rules:
        if pat == "":
            continue  # 空の Disallow は「制限なし」
        if _to_regex(pat).match(path):
            n = len(pat)
            if n > best_len or (n == best_len and allow):
                best_len, best_allow = n, allow
    return best_allow


async def _load(client: httpx.AsyncClient, scheme_host: str, ua: str) -> tuple:
    try:
        async with client.stream("GET", scheme_host + "/robots.txt", headers={"User-Agent": ua},
                                 follow_redirects=True, timeout=10) as r:
            if r.status_code in (404, 410):
                return [], ""
            if r.status_code != 200:
                return None, f"robots.txt を確認できませんでした（HTTP {r.status_code}。アクセス制限の可能性）"
            buf = bytearray()
            async for chunk in r.aiter_bytes():
                buf += chunk
                if len(buf) >= MAX_ROBOTS_BYTES:
                    break
            text = bytes(buf).decode("utf-8", errors="replace")
            if "<html" in text[:2000].lower():
                return None, "robots.txt の代わりに別のページ（ボット対策画面など）が返されました"
            return parse(text), ""
    except httpx.HTTPError as e:
        return None, f"robots.txt を確認できませんでした（{type(e).__name__}）"


async def check(client: httpx.AsyncClient, url: str, ua: str, now: Optional[float] = None) -> None:
    """読んでよければ何もしない。だめなら RobotsBlocked。"""
    p = urlparse(url)
    key = f"{p.scheme}://{p.netloc.lower()}"
    t = time.monotonic() if now is None else now
    hit = _cache.get(key)
    if hit and hit[0] > t:
        rules, reason = hit[1], hit[2]
    else:
        rules, reason = await _load(client, key, ua)
        _cache[key] = (t + (OK_TTL if rules is not None else FAIL_TTL), rules, reason)
        if len(_cache) > 500:
            _cache.pop(next(iter(_cache)))
    if rules is None:
        raise RobotsBlocked(reason + "。安全のため、このページは読み取りません。商品名で検索してください。")
    if not allowed(rules, url):
        raise RobotsBlocked("このサイトの robots.txt で自動読み取りが許可されていないページのため、読み取りません。商品名で検索してください。")


def clear_cache() -> None:
    _cache.clear()
