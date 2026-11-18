"""価格推移（日別中央値）の計算。決定的。
系列には kind を付ける: "total"=総支払額（判定と同じ定義） / "item_price"=商品価格のみ（送料を含まない参考）。
過去比較（past_median）は kind="total" の系列だけを使う（H-7）。"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import stats


JST = timezone(timedelta(hours=9))


def _date(o) -> str:
    """日本時間の日付（N-21: UTC のままだと 0〜9時の取得が前日扱いになる）"""
    s = o.observed_at or o.fetched_at
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s[:10]
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(JST).date().isoformat()


def daily_series(obs: list, name: str, kind: str, min_n: int = 1) -> dict:
    by_day = defaultdict(list)
    for o in obs:
        v = o.total_payable if kind == "total" else o.price
        if v is None:
            continue
        by_day[_date(o)].append(v)
    # N-9: 件数の少ない日の中央値は推移の点にしない
    pts = [{"date": d, "median": stats.median(v), "n": len(v)} for d, v in sorted(by_day.items()) if len(v) >= min_n]
    return {"name": name, "kind": kind, "points": pts}


def build_trend(collected: list, history: list, min_point_n: int = 3) -> list:
    """collected: 判定と同じ選別を通過し総支払額が確定した自前蓄積データ、history: 履歴API由来"""
    series = []
    if collected:
        s = daily_series(collected, f"収集データ（総支払額の日別中央値・{min_point_n}件以上の日のみ）", "total", min_point_n)
        if s["points"]:
            series.append(s)
    by_src = defaultdict(list)
    for o in history:
        by_src[o.source_name].append(o)
    for src, items in by_src.items():
        kind = "total" if all(o.total_payable is not None for o in items) else "item_price"
        label = f"{src}（総支払額）" if kind == "total" else f"{src}（商品価格のみ・送料を含まない参考値）"
        s = daily_series(items, label, kind)
        if s["points"]:
            series.append(s)
    return series


def past_median(series: list, exclude_recent_days: int = 7, now: Optional[datetime] = None) -> Optional[float]:
    """直近 N 日を除いた、総支払額系列の日別中央値の中央値（比較用）"""
    now = now or datetime.now(timezone.utc)
    cut = (now - timedelta(days=exclude_recent_days)).astimezone(JST).date().isoformat()
    vals = [p["median"] for s in series if s.get("kind") == "total" for p in s["points"] if p["date"] < cut]
    return stats.median(vals) if len(vals) >= 3 else None
