"""LLM の利用は「画像からの商品名推定」「検索語の整形（削除のみ）」に限定（C-2, M-6）。
- 価格・統計値・判定の説明文を LLM に作らせない（説明文は judge.py の決定的テンプレートのみ）
- 1日の呼び出し回数に上限（LLM_DAILY_LIMIT）を設けて費用を抑える（H-6）"""
from __future__ import annotations

import base64
import json
import re
import threading
from datetime import datetime, timezone
from typing import Optional

import httpx

from .config import Settings
from .textnorm import VARIANT_FORMS, normalize

PRICE_PAT = re.compile(r"[¥￥]\s*[\d,]+|[\d,]+\s*円|\d{1,3}(?:,\d{3})+")


class LLMQuotaExceeded(Exception):
    pass


def strip_prices(s: str) -> str:
    return PRICE_PAT.sub("", s or "").strip()


def deletion_only(original: str, proposed: str) -> bool:
    """整形結果が「元入力のトークン列から語を丸ごと削除しただけ」か（N-16）。
    - トークン単位の部分列であること（語の途中切り「WH-1000X」は不可）
    - 数字を含む語（型番・世代・容量）と区別語（Pro/Max 等）は削除不可"""
    o = [t for t in normalize(original).split(" ") if t]
    p = [t for t in normalize(proposed).split(" ") if t]
    if not proposed.strip() or not p:
        return False
    variant = {f for forms in VARIANT_FORMS for f in forms}
    i = 0
    for t in o:
        if i < len(p) and t == p[i]:
            i += 1
        elif any(ch.isdigit() for ch in t) or t in variant:
            return False
    return i == len(p)


def _extract_json(text: str):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


class DailyCounter:
    def __init__(self, limit: int):
        self.limit = limit
        self.day = ""
        self.count = 0
        self.lock = threading.Lock()

    def take(self) -> None:
        with self.lock:
            today = datetime.now(timezone.utc).date().isoformat()
            if today != self.day:
                self.day, self.count = today, 0
            if self.count >= self.limit:
                raise LLMQuotaExceeded()
            self.count += 1


class LLM:
    def __init__(self, s: Settings):
        self.s = s
        self.quota = DailyCounter(s.llm_daily_limit)

    @property
    def enabled(self) -> bool:
        return self.s.llm_enabled

    @property
    def provider_host(self) -> str:
        m = re.match(r"https?://([^/]+)", self.s.llm_base_url or "")
        return m.group(1) if m else ""

    async def _chat(self, client: httpx.AsyncClient, model: str, messages: list, max_tokens: int = 300) -> str:
        self.quota.take()
        r = await client.post(f"{self.s.llm_base_url.rstrip('/')}/chat/completions",
                              headers={"Authorization": f"Bearer {self.s.llm_api_key}"},
                              json={"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens},
                              timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    async def identify_image(self, client, data: bytes, mime: str) -> list:
        b64 = base64.b64encode(data).decode()
        prompt = ("この画像に写っている商品を特定してください。価格は絶対に書かないでください。"
                  "確信が持てない場合は無理に特定せず、一般名称（例: 黒いレザースニーカー）を返してください。"
                  'JSONのみで回答: {"candidates":[{"name":"検索に使える商品名(ブランド+モデル名)","brand":"","category":"服|靴|家電|その他","confidence":0.0-1.0}]}'
                  " 候補は最大3件。")
        text = await self._chat(client, self.s.llm_vision_model, [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}])
        data_ = _extract_json(text) or {}
        out = []
        for c in (data_.get("candidates") or [])[:3]:
            if not isinstance(c, dict):
                continue
            name = strip_prices(str(c.get("name", "")))[:80]
            if not name:
                continue
            try:
                conf = max(0.0, min(1.0, float(c.get("confidence", 0))))
            except (TypeError, ValueError):
                conf = 0.0
            out.append({"name": name, "brand": strip_prices(str(c.get("brand", "")))[:40],
                        "category": str(c.get("category", ""))[:10], "confidence": conf})
        return out

    async def normalize_query(self, client, query: str) -> Optional[str]:
        text = await self._chat(client, self.s.llm_model, [{"role": "user", "content":
            "次の入力から、通販サイトの商品検索に不要な語（宣伝文句・記号・送料無料等）を削除してください。"
            "語の追加・言い換え・翻訳は禁止です。価格は書かないでください。"
            f'JSONのみ: {{"query":"..."}}\n入力: {query}'}], max_tokens=80)
        q = strip_prices(str((_extract_json(text) or {}).get("query", "")))[:80]
        return q if q and deletion_only(query, q) else None
