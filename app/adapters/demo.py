"""デモモード用のサンプルデータ（架空）。
!!! 実在の価格ではありません。UIでは必ず「サンプルデータ」と表示されます。テスト・説明用。 !!!
URLは予約ドメイン demo.invalid（実在しない）を使用。乱数は商品ごとに固定シードで決定的。
"""
from __future__ import annotations

import random
import zlib
from datetime import datetime, timedelta, timezone

from .base import PriceAdapter, register
from ..models import Observation
from ..textnorm import normalize, relevance

DEMO_PRODUCTS = [
    {"name": "ナイキ エアフォース1 '07", "base": 16500, "n": 36, "spread": 0.07, "used_base": 9800, "used_n": 18,
     "titles": ["NIKE AIR FORCE 1 '07 ナイキ エアフォース1 CW2288-111", "ナイキ エア フォース 1 '07 ホワイト メンズ",
                "[NIKE] AIR FORCE 1 07 LOW WHITE", "【送料無料】ナイキ エアフォース1 '07 スニーカー 26.5cm"],
     "accessories": ["ナイキ エアフォース1 '07 用 シューレース 靴紐", "エアフォース1 '07 インソール 中敷き"],
     "others": ["ナイキ エアフォース1 '07 キッズ", "ナイキ エアフォース1 '07 LV8"]},
    {"name": "ユニクロ ウルトラライトダウンジャケット", "base": 6990, "n": 22, "spread": 0.06,
     "titles": ["UNIQLO ウルトラライトダウンジャケット メンズ ネイビー", "ユニクロ ウルトラライトダウン ジャケット レディース"],
     "accessories": ["ユニクロ ウルトラライトダウンジャケット 用 収納袋"], "others": []},
    {"name": "ソニー WH-1000XM5", "base": 44000, "n": 48, "spread": 0.06, "used_base": 29000, "used_n": 15,
     "titles": ["SONY ワイヤレスノイズキャンセリングヘッドホン WH-1000XM5 (B) ブラック", "ソニー WH-1000XM5B ブラック Bluetooth対応",
                "SONY WH1000XM5 プラチナシルバー 国内正規品", "【あす楽】SONY ソニー WH-1000XM5 SM"],
     "accessories": ["ソニー WH-1000XM5 用 イヤーパッド 交換", "WH-1000XM5 専用 キャリングポーチ"],
     "others": ["ソニー WH-1000XM4 ブラック"]},
    {"name": "Apple AirPods Pro 第2世代", "base": 37800, "n": 42, "spread": 0.05,
     "titles": ["Apple AirPods Pro（第2世代）MagSafe充電ケース（USB-C）付き MTJV3J/A", "AirPods Pro 2 アップル 正規品 MTJV3J/A",
                "アップル エアポッズプロ 第2世代 ワイヤレスイヤホン"],
     "accessories": ["AirPods Pro 第2世代 対応 イヤーピース 交換用 3サイズ", "for AirPods Pro 第2世代 イヤーチップ"],
     "others": ["Apple AirPods Pro 第1世代 MWP22J/A", "Apple AirPods Max スペースグレイ"]},
    {"name": "アディダス サンバ OG", "base": 15400, "n": 28, "spread": 0.08,
     "titles": ["adidas SAMBA OG アディダス サンバ B75806", "アディダス オリジナルス サンバ OG ブラック"],
     "accessories": [], "others": []},
    {"name": "無印良品 スニーカー", "base": 3990, "n": 6, "spread": 0.05,  # 件数不足の例
     "titles": ["無印良品 撥水 スニーカー", "MUJI 疲れにくい スニーカー"], "accessories": [], "others": []},
]
SITES = [("demo_a", "サンプルサイトA（架空）"), ("demo_b", "サンプルサイトB（架空）")]
USED_SITES = [SITES[1]]  # 実運用に合わせ、中古を自動検索できる取得元は1サイトのみ（N-5, N-10）
SHOPS = [f"サンプル店{c}（架空）" for c in "ABCDEFGH"]


def find_demo(query: str):
    best, score = None, 0.0
    for p in DEMO_PRODUCTS:
        s = relevance(query, p["name"])
        if normalize(query) == normalize(p["name"]):
            s = 2
        if s > score:
            best, score = p, s
    return best if score >= 0.6 else None


def demo_observations(p: dict, condition: str, now: datetime) -> list:
    base = p["base"] if condition == "new" else p.get("used_base")
    n = p["n"] if condition == "new" else p.get("used_n", 0)
    if not base or not n:
        return []
    rng = random.Random(zlib.crc32((p["name"] + condition).encode()))
    out = []
    fetched = now.replace(microsecond=0).isoformat()
    sites = SITES if condition == "new" else USED_SITES
    titles = p.get("titles") or [p["name"]]
    for i in range(n):
        sid, sname = sites[i % len(sites)]
        shop = SHOPS[i % len(SHOPS)]
        price = int(round(base * (1 + rng.gauss(0, p["spread"])) / 10) * 10)
        shipping = 0 if rng.random() < 0.7 else (rng.choice([550, 770, 880]) if rng.random() < 0.6 else None)
        title = titles[i % len(titles)] + (" 中古" if condition == "used" else "")
        out.append(Observation(
            source_id=sid, source_name=sname, url=f"https://{sid.replace('_', '-')}.demo.invalid/item/{i}",
            title=title, price=price, fetched_at=fetched, condition=condition, seller=shop,
            is_demo=True, shipping=shipping, fees=0,
            point_amount=(price // 100) if rng.random() < 0.6 else None,
            point_note="サンプル（架空）" ))
    # 除外処理の例（実際の検索結果に混ざりやすいもの）：外れ値・付属品・別モデル
    sid, sname = sites[0]
    host = sid.replace("_", "-")
    suffix = " 中古" if condition == "used" else ""
    out.append(Observation(sid, sname, f"https://{host}.demo.invalid/item/outlier", titles[0] + suffix,
                           int(base * 0.6), fetched, condition, seller=SHOPS[0], is_demo=True, shipping=0, fees=0))
    for j, t in enumerate(p.get("accessories", []) + [p["name"] + " 専用ケース"]):
        out.append(Observation(sid, sname, f"https://{host}.demo.invalid/item/acc{j}", t + suffix,
                               1980 + 300 * j, fetched, condition, seller=SHOPS[1], is_demo=True, shipping=0, fees=0))
    for j, t in enumerate(p.get("others", [])):
        out.append(Observation(sid, sname, f"https://{host}.demo.invalid/item/other{j}", t + suffix,
                               int(base * 0.9), fetched, condition, seller=SHOPS[2], is_demo=True, shipping=0, fees=0))
    return out


def demo_history(p: dict, now: datetime, days: int = 180) -> list:
    rng = random.Random(zlib.crc32(("hist" + p["name"]).encode()))
    out = []
    for d in range(days, 0, -3):
        t = now - timedelta(days=d)
        trend = 1 + 0.08 * (d / days)  # 過去の方がやや高い
        price = int(round(p["base"] * trend * (1 + rng.gauss(0, 0.02)) / 10) * 10)
        # 履歴も総支払額（送料0・手数料0）として定義をそろえたサンプル
        out.append(Observation("demo_a", "サンプル履歴（架空）", "https://demo-a.demo.invalid/history", p["name"],
                               price, now.replace(microsecond=0).isoformat(), "new", is_history=True,
                               observed_at=t.replace(microsecond=0).isoformat(), is_demo=True, shipping=0, fees=0))
    return out


@register
class DemoAdapter(PriceAdapter):
    id = "demo"
    name = "デモ（サンプルデータ）"
    provides_history = True
    used_site_count = len(USED_SITES)

    def enabled(self) -> bool:
        return self.settings.demo_mode

    def disabled_reason(self) -> str:
        return "DEMO_MODE 無効"

    async def search(self, client, query, condition):
        p = find_demo(query)
        return demo_observations(p, condition, datetime.now(timezone.utc)) if p else []

    async def history(self, client, query, url):
        p = find_demo(query)
        return demo_history(p, datetime.now(timezone.utc)) if p else []
