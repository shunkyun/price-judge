"""検索〜判定のオーケストレーション。

C-1: 判定（件数・分布・取得元数）には「今回の取得分」だけを使う。DB の蓄積は推移グラフ専用。
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from collections import OrderedDict
from typing import Optional

import httpx

from . import adapters, trend
from .adapters.base import UpstreamBusy, estimated_wait
from .adapters.page import observe_url
from .adapters.rakuten import rakuten_item_code
from .adapters.demo import DEMO_PRODUCTS
from .config import BASE_DIR, Settings
from .db import DB, NO_STORE_SOURCES, hash_key
from .judge import canonical_url, clean_observations, judge, robust_filter
from .llm import LLM
from .robots import RobotsBlocked
from .sources import Allowlist, load_allowlist
from .textnorm import normalize


class UserError(Exception):
    pass


class ServiceBusy(Exception):
    """取得元の待ち行列が長い（N-11）"""

    def __init__(self, msg: str = "", retry_after: int = 30):
        super().__init__(msg)
        self.retry_after = max(1, int(retry_after))


class TTLCache:
    """TTL 付き LRU（M-2）"""

    def __init__(self, max_items: int = 200, ttl: float = 600):
        self.max, self.ttl, self.d = max_items, ttl, OrderedDict()

    def get(self, k):
        v = self.d.get(k)
        if v is None:
            return None
        if time.time() - v[0] > (v[2] if len(v) > 2 else self.ttl):
            del self.d[k]
            return None
        self.d.move_to_end(k)
        return v[1]

    def set(self, k, val, ttl: Optional[float] = None):
        self.d[k] = (time.time(), val, self.ttl if ttl is None else ttl)
        self.d.move_to_end(k)
        while len(self.d) > self.max:
            self.d.popitem(last=False)

    def __len__(self):
        return len(self.d)


class PriceService:
    def __init__(self, settings: Settings, db: Optional[DB] = None, allow: Optional[Allowlist] = None):
        self.s = settings
        self.allow = allow or load_allowlist(settings.sources_path, settings.demo_mode)
        self.db = db or DB(settings.db_path, settings.db_retention_days)
        self.llm = LLM(settings)
        self.adapters = [cls(settings) for cls in adapters.REGISTRY.values()]
        for a in self.adapters:
            a.allow = self.allow
        self._cache = TTLCache()
        self._inflight: dict = {}  # 同じ検索の同時実行を1回にまとめる（N-11）

    def sources_overview(self) -> dict:
        act = {a.id for a in self.active_adapters()}
        return {
            "demo_mode": self.s.demo_mode,
            "rakuten_enabled": "rakuten" in act, "yahoo_enabled": "yahoo" in act,
            "shop_search_max": self.s.shop_search_max,
            "sources": [x.public() for x in self.allow.sources if not x.demo_only],
        }

    def publish_warnings(self) -> list:
        """公開前に直すべき設定（N-25）。起動ログと /api/status に出す"""
        out = []
        try:
            with open(os.path.join(BASE_DIR, "static", "terms.html"), encoding="utf-8") as f:
                if "［" in f.read():
                    out.append("利用規約（static/terms.html）の［ ］部分（運営者・連絡先・制定日）が未記入です")
        except OSError:
            pass
        if not self.db.persistent:
            out.append("価格推移の保存先（DB_PATH）を開けないため、推移の蓄積を停止しています")
        return out

    def used_site_count(self) -> int:
        return sum(a.used_site_count for a in self.active_adapters())

    def queue_wait(self) -> float:
        return max([estimated_wait(a.id) for a in self.active_adapters()] + [0.0])

    def active_adapters(self) -> list:
        if self.s.demo_mode:  # デモでは実データと混ぜない
            return [a for a in self.adapters if a.id == "demo"]
        return [a for a in self.adapters if a.id != "demo" and a.enabled()]

    def status(self) -> dict:
        act = self.active_adapters()
        return {
            "demo_mode": self.s.demo_mode,
            "access_code_required": bool(self.s.access_code),
            "llm_enabled": self.llm.enabled,
            "llm_provider_host": self.llm.provider_host if self.llm.enabled else "",
            "min_samples": self.s.min_samples,
            "min_sources": self.s.min_sources,
            "min_sellers": self.s.min_sellers,
            "used_site_count": self.used_site_count(),
            "queue_wait_sec": round(self.queue_wait(), 1),
            "warnings": self.publish_warnings(),
            "demo_products": [p["name"] for p in DEMO_PRODUCTS] if self.s.demo_mode else [],
            "credits": [a.id for a in act if a.id in ("rakuten", "yahoo")],
            "adapters": [{"id": a.id, "name": a.name, "enabled": a in act,
                          "reason": "" if a in act else (
                              "デモモード中" if self.s.demo_mode and a.id != "demo" else a.disabled_reason())}
                         for a in self.adapters],
            "trusted_sources": [{"id": x.id, "name": x.name, "domains": list(x.domains), "kind": x.kind,
                                 "auto_search": x.adapter is not None,
                                 "mall_search": bool(x.rakuten_shop or x.yahoo_store)}
                                for x in self.allow.sources if not x.demo_only or self.s.demo_mode],
        }

    async def _fetch(self, client, query: str, condition: str, url: Optional[str]):
        ck = (normalize(query), condition, url or "")
        hit = self._cache.get(ck)
        if hit:
            return hit
        if ck in self._inflight:  # N-11: 同じ検索が実行中なら結果を共有
            return await asyncio.shield(self._inflight[ck])
        if self.queue_wait() > self.s.max_queue_wait:
            w = int(self.queue_wait()) + 1  # L3-1: 本文の秒数と Retry-After を一致させる
            raise ServiceBusy(f"ただいま混雑しています（待ち時間の目安 約{w}秒）。少し時間をおいてお試しください。", w)

        async def run():
            # 最初に検索した人が接続を切っても、相乗りしている人の取得が止まらないよう、独立したタスク・接続で取得する
            async with httpx.AsyncClient(timeout=self.s.http_timeout) as own:
                return await self._fetch_now(own, query, condition, url)

        task = asyncio.ensure_future(run())
        self._inflight[ck] = task
        task.add_done_callback(lambda t: (self._inflight.pop(ck, None), t.cancelled() or t.exception()))
        return await asyncio.shield(task)

    async def _fetch_now(self, client, query: str, condition: str, url: Optional[str]):
        ck = (normalize(query), condition, url or "")
        acts = self.active_adapters()

        async def run_search(a):
            try:
                res = await a.search(client, query, condition)
                return res, a.report("ok", len(res))
            except UpstreamBusy:
                return [], a.report("error", 0, "混雑のため今回は取得を見送りました")
            except Exception as e:  # noqa: BLE001
                return [], a.report("error", 0, f"取得失敗: {type(e).__name__}")

        async def run_hist(a):
            try:
                return await a.history(client, query, url)
            except Exception:  # noqa: BLE001
                return []

        res = await asyncio.gather(*[run_search(a) for a in acts])
        hist = await asyncio.gather(*[run_hist(a) for a in acts if a.provides_history])
        out = ([o for r, _ in res for o in r], [o for h in hist for o in h], [rep.to_dict() for _, rep in res])
        # R3-6: 取得元のどれかが失敗・混雑した結果はキャッシュしない（同時の検索は上の相乗りで1回にまとまる）
        if all(rep.status == "ok" for _, rep in res):
            self._cache.set(ck, out)
        return out

    async def resolve_url(self, client, url: str) -> dict:
        src = self.allow.match(url)
        if src is None:
            raise UserError("このURLは信頼サイトの許可リストに含まれていないため対象外です（https の商品ページのみ対応）。商品名で検索してください。")
        if self.s.demo_mode:
            raise UserError("デモモードではURL指定は使えません。サンプル商品名で検索してください。")
        obs, name, msg = None, None, ""
        try:
            ra = next((a for a in self.adapters if a.id == "rakuten"), None)
            if rakuten_item_code(url) and ra and ra.enabled():
                obs = await ra.lookup(client, url)
                name = obs.title if obs else None
                msg = "ok" if obs else "楽天APIで商品が見つかりませんでした"
            else:
                obs, name, msg = await observe_url(client, url, self.allow)
        except RobotsBlocked as e:
            obs, name, msg = None, None, e.message
        except Exception as e:  # noqa: BLE001  M-3: どんな異常でもユーザー向けメッセージに
            obs, name, msg = None, None, f"ページを取得・解析できませんでした（{type(e).__name__}）"
        return {"source": src.name, "obs": obs, "name": name, "message": msg}

    async def analyze(self, query: Optional[str] = None, url: Optional[str] = None,
                      target_price: Optional[int] = None, condition: str = "new") -> dict:
        query = (query or "").strip()
        url = (url or "").strip() or None
        if query and re.match(r"https?://", query):
            url, query = query, ""
        if condition not in ("new", "used"):
            condition = "new"
        notes = []
        url_info = None
        async with httpx.AsyncClient(timeout=self.s.http_timeout) as client:
            if url:
                url_info = await self.resolve_url(client, url)
                if not query:
                    query = re.sub(r"\s+", " ", url_info["name"] or "")[:80]
                if not query:
                    raise UserError(f"URLから商品名を読み取れませんでした（{url_info['message']}）。商品名を入力してください。")
            if not query:
                raise UserError("商品名またはURLを入力してください。")
            search_query = query
            if self.llm.enabled and self.s.llm_normalize:
                try:
                    nq = await self.llm.normalize_query(client, query)
                    if nq and nq != query:
                        search_query = nq
                        notes.append(f"AIが検索語から不要な語を除き「{nq}」で検索しました（語の追加・価格の生成はしていません）。")
                except Exception:  # noqa: BLE001
                    pass
            current, history, reports = await self._fetch(client, search_query, condition, url)

        # N-19: URLで指定した商品自身は母集団に入れない（自分と比べない）
        if url_info and url_info["obs"] is not None:
            target_canon = canonical_url(url_info["obs"].url)
            current = [o for o in current if canonical_url(o.url) != target_canon]

        # 判定は今回の取得分のみ（C-1）。過去比較の基準は「過去に判定と同じ選別を通過したデータ」だけ（R3-4）
        hist_ok = [o for o in history if self.allow.match(o.url)]
        key = hash_key(normalize(search_query) + "|" + condition)
        if not self.s.demo_mode:
            past = self.db.load(key, days=365)
            past_cur = _clean_by_day([o for o in past if not o.is_history], self.allow, search_query, condition,
                                     self.s.relevance_min, self.s.iqr_k)
            past_hist = [o for o in past if o.is_history]
        else:
            past_cur, past_hist = [], []
        hist_base = trend.build_trend(_dedupe(_trend_sources(past_cur)), _dedupe(past_hist + hist_ok), self.s.trend_min_point_n)
        hist_med = trend.past_median(hist_base)

        target_note = None
        if target_price is None and url_info and url_info["obs"] is not None:
            o = url_info["obs"]
            if o.total_payable is not None:
                target_price = o.total_payable
                target_note = "指定URLの総支払額を判定対象にしました。"
            else:
                target_note = (f"指定URLの商品価格は {o.price:,}円（税込）ですが、送料・手数料が確定できないため自動判定していません。"
                               "送料込みの総支払額を入力してください。")
        single_site_used = condition == "used" and self.used_site_count() < self.s.min_sources
        result = judge(current, self.allow, search_query, target_price, condition,
                       self.s.min_samples, self.s.min_sources, self.s.min_sellers, self.s.iqr_k,
                       self.s.relevance_min, self.s.recent_days, hist_med,
                       single_source_reference=single_site_used)
        final_now = result.pop("_final", [])
        if result.get("history_suppressed"):
            hist_med = None
        # 推移：判定で最終的に残った出品だけを保存・表示する（R3-4）。楽天の価格は保存しない（R3-1）
        if not self.s.demo_mode:
            self.db.save(key, _trend_sources(final_now) + hist_ok)
            series = trend.build_trend(_dedupe(_trend_sources(past_cur + final_now)), _dedupe(past_hist + hist_ok),
                                       self.s.trend_min_point_n)
        else:
            series = trend.build_trend(final_now, history, self.s.trend_min_point_n)
        trend_hidden = None
        if result["verdict"] in ("insufficient", "reference", "choose_variant"):
            # N-9: 判定しないときは推移グラフにも価格水準を出さない
            series, hist_med = [], None
            trend_hidden = "判定に必要な件数がそろっていないため、価格推移は表示しません。"
        if not result["sample_count"] and result["verdict"] == "insufficient" and not result.get("price_split"):
            result["hints"] = (["デモで調べられる商品：" + "、".join(p["name"] for p in DEMO_PRODUCTS)] if self.s.demo_mode else
                               ["ブランド名＋型番（例：ソニー WH-1000XM5）で検索してみてください",
                                "色・サイズ・「送料無料」などの語は外すと見つかりやすくなります",
                                "商品ページのURL（楽天・Yahoo!ショッピング等）を貼り付けることもできます"])
        if target_note:
            notes.append(target_note)
        result.update({
            "demo_mode": self.s.demo_mode,
            "display_name": search_query,
            "input_url": url,
            "url_info": None if not url_info else {
                "source": url_info["source"], "message": url_info["message"],
                "obs": url_info["obs"].to_dict() if url_info["obs"] else None},
            "notes": notes,
            "fetch_reports": reports,
            "trend": series,
            "trend_hidden": trend_hidden,
            "trend_note": None if self.s.demo_mode else TREND_NOTE,
            "history_median": hist_med,
            "used_single_site": single_site_used,
        })
        return result


def _dedupe(obs: list) -> list:
    seen, out = set(), []
    for o in obs:
        k = (o.url, o.fetched_at, o.observed_at, o.price)
        if k not in seen:
            seen.add(k)
            out.append(o)
    return out


TREND_NOTE = ("価格の推移は楽天市場以外の取得元のみで作成しています（楽天ウェブサービスの規定により、"
              "楽天の価格情報は保存しないため）。")


def _trend_sources(obs: list) -> list:
    """推移・保存に使える出品（R3-1: 楽天由来は保存も推移表示もしない）"""
    return [o for o in obs if o.source_id not in NO_STORE_SOURCES]


def _clean_by_day(obs, allow, query, condition, rel_min, iqr_k=1.5):
    """推移・過去比較用：日ごとに判定と同じ選別（照合・送料確定・価格帯の分断・中央値30%・IQR）を行う（R3-4）。"""
    by_day = {}
    for o in obs:
        by_day.setdefault(o.fetched_at[:10], []).append(o)
    kept = []
    for items in by_day.values():
        k, _ = clean_observations(items, allow, query, condition, rel_min, None)
        k = [o for o in k if o.total_payable is not None]
        final, _, split, _ = robust_filter(k, 0.3, iqr_k)
        if not split:
            kept += final
    return kept
