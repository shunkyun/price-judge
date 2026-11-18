"""信頼サイト許可リスト。URLが許可リストに一致しない価格は採用しない。"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    domains: tuple
    kind: str
    adapter: Optional[str]
    conditions: tuple = ("new",)
    path_prefixes: tuple = ()
    demo_only: bool = False
    note: str = ""
    categories: tuple = ()
    official_api: dict = field(default_factory=dict, hash=False, compare=False)
    rakuten_shop: Optional[dict] = field(default=None, hash=False, compare=False)  # {"code", "name"}
    yahoo_store: Optional[dict] = field(default=None, hash=False, compare=False)  # {"id", "name"}
    url_read: dict = field(default_factory=dict, hash=False, compare=False)  # {"status", "note"}
    verified_at: str = ""
    short: str = ""

    def mall_label(self, platform: str) -> str:
        n = self.short or self.name
        return f"{n}（楽天市場店）" if platform == "rakuten" else f"{n}（Yahoo!ショッピング店）"

    def public(self) -> dict:
        """対応サイト一覧（/api/sources・/sites）に出す情報"""
        return {"id": self.id, "name": self.name, "domains": list(self.domains), "path_prefixes": list(self.path_prefixes),
                "kind": self.kind, "categories": list(self.categories), "auto_search": self.adapter is not None,
                "official_api": self.official_api, "rakuten_shop": self.rakuten_shop, "yahoo_store": self.yahoo_store,
                "url_read": self.url_read, "note": self.note, "verified_at": self.verified_at}


@dataclass
class Allowlist:
    sources: list
    exclude_title_words: list = field(default_factory=list)
    used_title_words: list = field(default_factory=list)
    allow_demo: bool = False
    category_keywords: dict = field(default_factory=dict)

    def mall_shops(self, platform: str) -> list:
        """楽天/Yahoo!に公式店がある店舗 [(店舗コード, Source)]（設定ファイルの順）"""
        key, sub = ("rakuten_shop", "code") if platform == "rakuten" else ("yahoo_store", "id")
        out = []
        for s in self.sources:
            m = getattr(s, key)
            if m and m.get(sub) and not s.demo_only:
                out.append((m[sub], s))
        return out

    def mall_label(self, platform: str, code: str) -> Optional[tuple]:
        """楽天のショップコード / Yahoo!のストアID → (店舗ID, 表示名「○○（楽天市場店）」)。登録外は None"""
        for c, s in self.mall_shops(platform):
            if c == code:
                return s.id, s.mall_label(platform)
        return None

    def guess_categories(self, query: str) -> list:
        """検索語から商品の種類を推定（決まった単語表との一致のみ。AIは使わない）"""
        q = unicodedata.normalize("NFKC", query or "").lower()
        out = []
        for cat, words in self.category_keywords.items():
            for w in words:
                w = unicodedata.normalize("NFKC", w).lower()
                if re.fullmatch(r"[a-z0-9 .\-]+", w):
                    hit = re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", q)
                else:
                    hit = w in q
                if hit:
                    out.append(cat)
                    break
        return out

    def shops_for_query(self, platform: str, query: str, limit: int) -> list:
        """検索語の種類に合う公式店を最大 limit 件 [(店舗コード, 店舗ID, 表示名)]。種類が分からなければ空"""
        if limit <= 0:
            return []
        cats = self.guess_categories(query)
        out, seen = [], set()
        for cat in cats:
            for code, s in self.mall_shops(platform):
                if cat in s.categories and code not in seen:
                    seen.add(code)
                    out.append((code, s.id, s.mall_label(platform)))
        return out[:limit]

    def match(self, url: str) -> Optional[Source]:
        """URLが許可リストのどのソースに該当するか。https 以外、既定以外のポート、認証情報付き、
        未登録ドメインは None（L-1）。"""
        try:
            p = urlparse(url or "")
            port = p.port
        except ValueError:
            return None
        if p.scheme != "https" or not p.hostname or port not in (None, 443):
            return None
        host = p.hostname.lower().rstrip(".")
        if p.username or p.password:
            return None
        path = p.path or "/"
        for s in self.sources:
            if s.demo_only and not self.allow_demo:
                continue
            for d in s.domains:
                if host == d or host.endswith("." + d):
                    if s.path_prefixes and not any(path.startswith(pp) for pp in s.path_prefixes):
                        continue
                    return s
        return None

    def is_trusted(self, url: str) -> bool:
        return self.match(url) is not None

    def get(self, source_id: str) -> Optional[Source]:
        for s in self.sources:
            if s.id == source_id:
                return s
        return None


def parse_allowlist(data: dict, allow_demo: bool = False) -> Allowlist:
    srcs = []
    for s in data.get("sources", []):
        srcs.append(Source(
            id=s["id"], name=s["name"], domains=tuple(d.lower() for d in s["domains"]),
            kind=s.get("kind", "retailer"), adapter=s.get("adapter"),
            conditions=tuple(s.get("conditions", ["new"])),
            path_prefixes=tuple(s.get("path_prefixes", [])),
            demo_only=bool(s.get("demo_only", False)), note=s.get("note", ""),
            categories=tuple(s.get("categories", [])), official_api=s.get("official_api") or {},
            rakuten_shop=s.get("rakuten_shop"), yahoo_store=s.get("yahoo_store"),
            url_read=s.get("url_read") or {}, verified_at=s.get("verified_at", ""), short=s.get("short", ""),
        ))
    return Allowlist(srcs, data.get("exclude_title_words", []), data.get("used_title_words", []), allow_demo,
                     data.get("category_keywords", {}))


@lru_cache(maxsize=4)
def load_allowlist(path: str, allow_demo: bool = False) -> Allowlist:
    with open(path, encoding="utf-8") as f:
        return parse_allowlist(json.load(f), allow_demo)
