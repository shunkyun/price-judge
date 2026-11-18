"""価格判定ロジック（決定的。LLMは一切関与しない）。

判定は常に「総支払額 = 税込価格 + 送料 + 必須手数料」で行う。
実質価格（総支払額 − 明示ポイント）は参考表示（分布の見え方の切替）にのみ使い、判定には使わない（H-2）。

手順:
 1. 許可リスト外URLを除外
 2. 対象コンディション（新品/中古）以外を除外
 3. 付属品等の除外語 / 型番・世代不一致 / 区別語（Pro, Max 等）/ タイトル関連度不足 を除外
 4. 古い取得、同一URLの重複（最新のみ）を除外
 5. 送料・手数料が不明なものを除外（推測しない。件数にも数えない）
 6. 同一店舗・同一総支払額の出品（色・サイズ違い）を1件に集約
 7. IQR法で外れ値を除外（フェンスは除外前のデータで1回だけ計算）
 8. 有効サンプル数 < MIN_SAMPLES、取得元サイト数 < MIN_SOURCES、店舗数 < MIN_SELLERS なら「判定不可」
    （このとき価格帯・中央値などの統計値は返さない）
 9. 判定対象価格のパーセンタイル順位とフェンスで 安い/適正/高い を決定
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse, urlunparse

from . import stats
from .models import Excluded, Observation
from .sources import Allowlist
from .textnorm import accessory_reason, contains_any, lookalike_reason, mismatch_reason, relevance

VERDICTS = {
    "cheap": "安い",
    "fair": "適正",
    "expensive": "高い",
    "too_cheap": "安すぎ（要注意）",
    "too_expensive": "かなり高い",
    "insufficient": "データ不足のため判定不可",
    "no_target": "相場を表示中（価格未入力）",
    "reference": "参考値のみ（判定なし）",
    "choose_variant": "容量を選んでください（判定なし）",
}
GAP_RATIO = 3.0       # R3-4: 隣り合う価格の比がこれを超えたら「価格帯が分かれている」
GAP_MINORITY = 0.3    # 安い側がこの割合未満なら付属品・類似品として除外、以上なら判定しない
HISTORY_MAX_DIFF = 50  # R3-4: 過去の中央値と ±50% を超えて違う場合は比較文を出さない
_STORAGE_RX = re.compile(r"(?<![\d.])(\d{2,4})\s?(gb|tb)(?![a-z])")


def storages(title: str) -> set:
    """タイトルに書かれた容量（128GB 等）。R3-3"""
    from .textnorm import normalize
    return {f"{int(n)}{u.upper()}" for n, u in _STORAGE_RX.findall(normalize(title))}


def robust_filter(usable: list, accessory_ratio: float = 0.3, iqr_k: float = 1.5) -> tuple:
    """価格の整合性による選別（判定・推移・過去比較で共通。R3-4）。
    返り値 (final, excluded, split, fences)。split=True は価格帯が2つに分かれていて判定できない状態。"""
    excluded = []
    vals = sorted(o.total_payable for o in usable)
    if len(vals) >= 4:
        # 1) 価格帯の分断：隣り合う価格の比が GAP_RATIO 超
        cut = None
        for i in range(1, len(vals)):
            if vals[i - 1] > 0 and vals[i] / vals[i - 1] > GAP_RATIO:
                cut = vals[i]
                low_share = i / len(vals)
                break
        if cut is not None:
            if low_share >= GAP_MINORITY:
                return [], [Excluded(o, "価格帯が2つに分かれているため判定に使わない（付属品・類似品・別モデルの混入の可能性）")
                            for o in usable], True, None
            low = [o for o in usable if o.total_payable < cut]
            excluded += [Excluded(o, "付属品・類似品の疑い（価格帯が大きく離れた少数のグループ）") for o in low]
            usable = [o for o in usable if o.total_payable >= cut]
    # 2) 中央値の accessory_ratio 未満
    if len(usable) >= 4 and accessory_ratio > 0:
        med0 = stats.median([o.total_payable for o in usable])
        low = [o for o in usable if o.total_payable < med0 * accessory_ratio]
        excluded += [Excluded(o, f"中央値の{int(accessory_ratio * 100)}%未満（付属品・部品・類似品、または大幅値引きの可能性）")
                     for o in low]
        usable = [o for o in usable if o not in low]
    # 3) IQR（フェンスは1回だけ計算）
    vals_all = [o.total_payable for o in usable]
    fences = stats.iqr_fences(vals_all, iqr_k) if len(vals_all) >= 4 else None
    final = []
    for o in usable:
        if fences and not (fences[0] <= o.total_payable <= fences[1]):
            excluded.append(Excluded(o, "外れ値（IQR法）"))
        else:
            final.append(o)
    return final, excluded, False, fences
BASIS_LABEL = "総支払額（税込＋送料＋手数料）"


def canonical_url(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme, (p.hostname or "").lower(), p.path.rstrip("/"), "", "", ""))


def parse_ts(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def clean_observations(obs: list, allow: Allowlist, query: str, condition: str = "new",
                       relevance_min: float = 0.6, recent_days: Optional[int] = None,
                       now: Optional[datetime] = None) -> tuple:
    """商品の同一性・信頼性で選別。(kept, excluded)。履歴値は対象外。"""
    now = now or datetime.now(timezone.utc)
    excluded = []
    latest_by_url: dict = {}
    for o in obs:
        if o.is_history:
            continue
        if allow.match(o.url) is None:
            excluded.append(Excluded(o, "信頼サイト許可リスト外"))
            continue
        if not isinstance(o.price, int) or isinstance(o.price, bool) or o.price <= 0:
            excluded.append(Excluded(o, "価格が不正"))
            continue
        cond = o.condition
        if cond == "new" and contains_any(o.title, allow.used_title_words):
            cond = "used"
        if cond != condition:
            excluded.append(Excluded(o, "コンディション違い（新品/中古）"))
            continue
        w = accessory_reason(query, o.title, allow.exclude_title_words)
        if w:
            excluded.append(Excluded(o, f"付属品・別商品の可能性（「{w}」を含む）"))
            continue
        mr = mismatch_reason(query, o.title) or lookalike_reason(query, o.title)
        if mr:
            excluded.append(Excluded(o, mr))
            continue
        if relevance(query, o.title) < relevance_min:
            excluded.append(Excluded(o, "商品名の一致度が低い"))
            continue
        if recent_days is not None and parse_ts(o.fetched_at) < now - timedelta(days=recent_days):
            excluded.append(Excluded(o, f"{recent_days}日より古い取得"))
            continue
        key = canonical_url(o.url)
        prev = latest_by_url.get(key)
        if prev is not None:
            # N-18: 入力順に依存しない。新しい取得 > 総支払額が確定 > 安い方 > タイトル順 で1件を残す
            if _dup_rank(o) > _dup_rank(prev):
                excluded.append(Excluded(prev, "同一URLの重複"))
                latest_by_url[key] = o
            else:
                excluded.append(Excluded(o, "同一URLの重複"))
            continue
        latest_by_url[key] = o
    return list(latest_by_url.values()), excluded


def _storage_order(label: str) -> int:
    n = int(re.match(r"\d+", label).group(0))
    return n * (1024 if label.endswith("TB") else 1)


def _dup_rank(o: Observation) -> tuple:
    tp = o.total_payable
    return (parse_ts(o.fetched_at), tp is not None, -(tp if tp is not None else o.price), o.title, o.price)


def _yen(v: float) -> str:
    # 四捨五入（Python の round は偶数丸めのため使わない。画面側 Math.round と一致させる）
    return f"{math.floor(v + 0.5):,}円"


def seller_key(o: Observation) -> str:
    """店舗の識別子。seller が無ければURLの先頭パス（楽天・Yahooでは店舗コード）を使う。"""
    seg = urlparse(o.url).path.strip("/").split("/")[0]
    return f"{o.source_id}:{o.seller or seg}"


def judge(obs: list, allow: Allowlist, query: str, target_price: Optional[int] = None,
          condition: str = "new", min_samples: int = 10, min_sources: int = 2, min_sellers: int = 3,
          iqr_k: float = 1.5, relevance_min: float = 0.6, recent_days: Optional[int] = 30,
          history_median: Optional[float] = None, now: Optional[datetime] = None,
          single_source_reference: bool = False, accessory_ratio: float = 0.3) -> dict:
    """single_source_reference: 取得元が1サイトしか存在しない条件（実運用の中古）では判定せず、
    件数・店舗数が足りていれば「参考値」として統計だけを返す（N-5）。"""
    min_samples = max(4, int(min_samples))  # N-17: IQR に最低4件必要
    min_sources, min_sellers = max(1, int(min_sources)), max(1, int(min_sellers))
    kept, excluded = clean_observations(obs, allow, query, condition, relevance_min, recent_days, now)

    usable, seen_variant = [], set()
    for o in kept:
        if o.shipping is None:
            excluded.append(Excluded(o, "送料不明（判定・件数に含めない）"))
        elif o.fees is None:
            excluded.append(Excluded(o, "手数料不明（判定・件数に含めない）"))
        else:
            vk = (seller_key(o), o.total_payable)
            if vk in seen_variant:
                excluded.append(Excluded(o, "同一店舗・同一価格の出品（色・サイズ違い等）を集約"))
                continue
            seen_variant.add(vk)
            usable.append(o)

    # R3-3: 容量が複数書かれた出品（どの容量の価格か不明）は除外。容量を指定しない検索で容量が混在するなら選んでもらう
    q_storage = storages(query)
    groups: Counter = Counter()
    keep = []
    for o in usable:
        st_ = storages(o.title)
        if len(st_) > 1:
            excluded.append(Excluded(o, "容量が複数記載（どの容量の価格か不明）"))
            continue
        if st_:
            groups[next(iter(st_))] += 1
        keep.append(o)
    usable = keep
    variant_groups = None
    if not q_storage and len(groups) >= 2:
        variant_groups = [{"label": k, "count": v} for k, v in sorted(groups.items(), key=lambda kv: _storage_order(kv[0]))]

    if variant_groups is None:
        final_obs, ex2, split, fences = robust_filter(usable, accessory_ratio, iqr_k)
    else:
        final_obs, ex2, split, fences = list(usable), [], False, None
    excluded += ex2
    final_obs.sort(key=lambda o: o.total_payable)

    sources = sorted({o.source_id for o in final_obs})
    sellers = Counter(seller_key(o) for o in final_obs)
    n = len(final_obs)
    result = {
        "query": query,
        "condition": condition,
        "basis_label": BASIS_LABEL,
        "target_price": target_price,
        "sample_count": n,
        "source_count": len(sources),
        "seller_count": len(sellers),
        "sources_used": sources,
        "min_samples": min_samples,
        "min_sources": min_sources,
        "min_sellers": min_sellers,
        "excluded_count": len(excluded),
        "observations": [o.to_dict() for o in final_obs],
        "excluded": [e.to_dict() for e in excluded],
        "points_known_count": sum(1 for o in final_obs if o.point_amount is not None),
        "stats": None,
        "effective_stats": None,
        "reference_stats": None,
        "verdict": None,
        "verdict_label": None,
        "percentile_rank": None,
        "confidence": None,
        "reasons": [],
        "variant_groups": variant_groups,
        "price_split": split,
        "history_suppressed": False,
        "_final": final_obs,
    }
    if variant_groups is not None:
        result["verdict"] = "choose_variant"
        result["verdict_label"] = VERDICTS["choose_variant"]
        result["sample_count"] = 0
        result["reasons"].append("容量によって価格が大きく異なるため、容量を指定しない検索では判定しません。"
                                 "下の容量を選ぶと、その容量だけで判定します（" +
                                 "、".join(f"{g['label']} {g['count']}件" for g in variant_groups) + "）。")
        return result
    if split:
        result["verdict"] = "insufficient"
        result["verdict_label"] = VERDICTS["insufficient"]
        result["sample_count"] = 0
        result["reasons"].append("取得した価格が大きく離れた2つの価格帯に分かれているため判定しません"
                                 "（付属品・類似品・別モデルが多く混ざっている可能性）。型番まで含めて検索してください。")
        return result
    lack = []
    if n < min_samples:
        lack.append(f"有効サンプル {n}件（必要 {min_samples}件以上）")
    if len(sources) < min_sources:
        lack.append(f"取得元サイト {len(sources)}種類（必要 {min_sources}種類以上）")
    if len(sellers) < min_sellers:
        lack.append(f"店舗数 {len(sellers)}店（必要 {min_sellers}店以上）")
    if lack and single_source_reference and n >= min_samples and len(sellers) >= min_sellers:
        # N-5: 取得元が1サイトしか無いので判定はしない。統計は「参考値」として返す
        st = stats.summarize([o.total_payable for o in final_obs])
        result["reference_stats"] = st
        result["verdict"] = "reference"
        result["verdict_label"] = VERDICTS["reference"]
        result["reasons"].append(
            f"中古品を自動検索できる取得元が現在1サイトのみのため、判定（安い/適正/高い）は行いません。"
            f"以下は{len(sellers)}店舗・{n}件の参考値です：中央値 {_yen(st['median'])}、中央50% {_yen(st['q1'])}〜{_yen(st['q3'])}。")
        return result
    if lack:
        # H-1: 判定不可のときは価格帯・中央値を一切返さない
        result["verdict"] = "insufficient"
        result["verdict_label"] = VERDICTS["insufficient"]
        result["reasons"].append("信頼性を確保できる件数に達していないため判定しません：" + "、".join(lack) + "。")
        return result

    vals = [o.total_payable for o in final_obs]
    st = stats.summarize(vals)
    lo_f, hi_f = fences if fences else (st["min"], st["max"])
    st["fence_low"], st["fence_high"] = lo_f, hi_f
    result["stats"] = st
    eff = [o.effective_price for o in final_obs if o.effective_price is not None]
    if len(eff) >= min_samples:
        result["effective_stats"] = stats.summarize(eff)  # 参考表示用（判定には使わない）

    disp = st["dispersion"] or 0
    top_share = max(sellers.values()) / n
    if n >= max(30, min_samples * 3) and disp <= 0.25 and top_share <= 0.3:
        conf = "高"
    elif n >= min_samples * 2 and disp <= 0.5 and top_share <= 0.3:
        conf = "中"
    else:
        conf = "低"
    result["confidence"] = conf
    result["reasons"].append(
        f"信頼サイト{len(sources)}種類・{len(sellers)}店舗から有効サンプル{n}件（除外{len(excluded)}件）。"
        f"{BASIS_LABEL}の中央値 {_yen(st['median'])}、中央50%の価格帯 {_yen(st['q1'])}〜{_yen(st['q3'])}。")
    result["reasons"].append("比較対象は送料が確定できる出品（送料無料・送料込み）に限っています。")

    if target_price is None:
        result["verdict"] = "no_target"
        result["verdict_label"] = VERDICTS["no_target"]
        result["reasons"].append(f"適正価格帯の目安は {_yen(st['q1'])}〜{_yen(st['q3'])} です。検討中の総支払額を入力すると判定します。")
    else:
        pr = stats.percentile_rank(sorted(vals), target_price)
        result["percentile_rank"] = round(pr, 1)
        if target_price < lo_f:
            v = "too_cheap"
            msg = f"{_yen(target_price)} は相場の下限目安 {_yen(lo_f)} を下回ります。偽物・詐欺・別商品の可能性に注意してください。"
        elif target_price < st["q1"]:
            v = "cheap"
            msg = f"{_yen(target_price)} は安い方から{pr:.0f}%の位置（下位25%未満）です。"
        elif target_price <= st["q3"]:
            v = "fair"
            msg = f"{_yen(target_price)} は中央50%の価格帯内（{pr:.0f}%点）です。"
        elif target_price <= hi_f:
            v = "expensive"
            msg = f"{_yen(target_price)} は高い方の25%に入ります（{pr:.0f}%点）。"
        else:
            v = "too_expensive"
            msg = f"{_yen(target_price)} は相場の上限目安 {_yen(hi_f)} を超えています。"
        if target_price < st["min"] and v in ("cheap", "fair"):
            msg += " 取得したすべての出品より安い価格です。商品の状態・販売元をよく確認してください。"
        result["verdict"], result["verdict_label"] = v, VERDICTS[v]
        result["reasons"].append(msg)

    if history_median is not None and history_median > 0:
        diff = (st["median"] - history_median) / history_median * 100
        if abs(diff) > HISTORY_MAX_DIFF:
            # R3-4: 極端な差は別商品の混入などによる可能性が高いので、比較文（％）を出さない
            result["history_suppressed"] = True
            result["reasons"].append(f"過去の推移と大きく異なる（±{HISTORY_MAX_DIFF}%超）ため、過去との比較は表示しません。")
        elif abs(diff) < 3:
            result["reasons"].append(f"現在の中央値は過去の中央値 {_yen(history_median)}（同じ総支払額基準）とほぼ同水準です。")
        else:
            word = "高く" if diff > 0 else "安く"
            result["reasons"].append(f"現在の中央値は過去の中央値 {_yen(history_median)}（同じ総支払額基準）より {abs(diff):.0f}% {word}なっています。")
    if conf == "低":
        result["reasons"].append("ばらつきが大きい、サンプルが少なめ、または特定の店舗に偏っているため、判定の信頼度は「低」です。")
    return result
