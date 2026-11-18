"""入力の予測変換。候補は (1) 過去に検索された商品名 (2) 定番商品の辞書 (3) デモ商品名。
候補は商品名のみで、価格は含まない。"""
from __future__ import annotations

import json

from .textnorm import normalize


def load_seed(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            return [s for s in json.load(f) if isinstance(s, str)]
    except (OSError, ValueError):
        return []


def suggest(q: str, candidates: list, limit: int = 8) -> list:
    """candidates: [(name, weight)]。前方一致 > トークン前方一致 > 部分一致、同順位は重み順。"""
    nq = normalize(q)
    if not nq:
        return []
    nq_c = nq.replace(" ", "")
    scored = {}
    for name, w in candidates:
        nn = normalize(name)
        nc = nn.replace(" ", "")
        if nn.startswith(nq) or nc.startswith(nq_c):
            rank = 0
        elif any(t.startswith(nq) for t in nn.split(" ")):
            rank = 1
        elif nq_c in nc or all(t in nc for t in nq.split(" ")):
            rank = 2
        else:
            continue
        key = nc
        if key not in scored or (rank, -w) < scored[key][0]:
            scored[key] = ((rank, -w), name)
    return [v[1] for v in sorted(scored.values(), key=lambda x: x[0])][:limit]
