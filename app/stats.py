"""決定的な統計処理。価格の数値はすべてここで計算する（LLMは関与しない）。"""
from __future__ import annotations

import math
from typing import Iterable, Sequence


def percentile(sorted_vals: Sequence[float], p: float) -> float:
    """線形補間パーセンタイル（Excel PERCENTILE.INC / numpy linear と同じ）。p: 0-100"""
    if not sorted_vals:
        raise ValueError("empty")
    if not 0 <= p <= 100:
        raise ValueError("p out of range")
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    pos = (n - 1) * p / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    frac = pos - lo
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac)


def median(vals: Iterable[float]) -> float:
    return percentile(sorted(vals), 50)


def iqr_fences(vals: Sequence[float], k: float = 1.5) -> tuple:
    s = sorted(vals)
    q1, q3 = percentile(s, 25), percentile(s, 75)
    iqr = q3 - q1
    return q1 - k * iqr, q3 + k * iqr


def remove_outliers(vals: Sequence[float], k: float = 1.5) -> tuple:
    """Tukey の IQR 法で外れ値を除外。(kept, removed) を返す。4件未満では除外しない。"""
    if len(vals) < 4:
        return list(vals), []
    lo, hi = iqr_fences(vals, k)
    kept = [v for v in vals if lo <= v <= hi]
    removed = [v for v in vals if v < lo or v > hi]
    return kept, removed


def percentile_rank(sorted_vals: Sequence[float], x: float) -> float:
    """x がサンプル中で何%点か（同値は半分として数える）。0-100"""
    n = len(sorted_vals)
    if n == 0:
        raise ValueError("empty")
    below = sum(1 for v in sorted_vals if v < x)
    equal = sum(1 for v in sorted_vals if v == x)
    return (below + 0.5 * equal) / n * 100.0


def summarize(vals: Sequence[float]) -> dict:
    if not vals:
        raise ValueError("empty")
    s = sorted(vals)
    n = len(s)
    mean = sum(s) / n
    var = sum((v - mean) ** 2 for v in s) / (n - 1) if n > 1 else 0.0
    q1, med, q3 = percentile(s, 25), percentile(s, 50), percentile(s, 75)
    return {
        "n": n,
        "min": s[0],
        "max": s[-1],
        "mean": round(mean, 1),
        "stdev": round(math.sqrt(var), 1),
        "p10": percentile(s, 10),
        "q1": q1,
        "median": med,
        "q3": q3,
        "p90": percentile(s, 90),
        "iqr": q3 - q1,
        # 相対的なばらつき（IQR/中央値）。大きいほど相場が定まっていない
        "dispersion": round((q3 - q1) / med, 3) if med else None,
    }


def histogram(vals: Sequence[float], bins: int = 10) -> list:
    if not vals:
        return []
    lo, hi = min(vals), max(vals)
    if lo == hi:
        return [{"from": lo, "to": hi, "count": len(vals)}]
    width = (hi - lo) / bins
    out = [{"from": lo + i * width, "to": lo + (i + 1) * width, "count": 0} for i in range(bins)]
    for v in vals:
        idx = min(int((v - lo) / width), bins - 1)
        out[idx]["count"] += 1
    return out
