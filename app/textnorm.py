"""文字列正規化と、検索結果が「同じ商品か」の判定（決定的。LLM不使用）。

N-1 の方針:
 - ブランド・シリーズ名は別名辞書（config/aliases.json）で英字・カタカナの揺れを吸収してから比較
 - 型番は英字と数字を連結した形で比較（GEL-KAYANO 14 = gelkayano14、WH-1000XM5 = wh1000xm5）。
   前後が数字でなければ一致（CM996 ⊇ 996、WH-1000XM5B（色コード）⊇ wh1000xm5、iPhone15 ⊇ 15）
 - 区別語（Pro/Max/mini 等）は、クエリに同じ語（英字・カタカナのどちらか）が無いときだけ「別モデル」扱い
 - 付属品は「名詞（イヤーピース等）」と「<商品名> 用/対応/専用/向け」「for <商品名>」の型で判定。
   「充電ケース付き」「ケーブル付属」のように付属・同梱を示す語が続く場合は付属品とみなさない

R3-2/R3-3 の方針:
 - 別名は語の境界でだけ置き換える（カタカナの途中には一致させない：サンバイザー≠サンバ、ダンクシュート≠ダンク）。
   区切りの無いカタカナ列（ナイキエアマックス）は、既知の語だけで完全に分割できる場合に限って分割する
 - あいまいな別名（スイッチ、バンズ、サンバ、ダンク）は、同じ文字列にブランド等の文脈語があるときだけ適用。
   「無印」単独は「ノーブランド」の意味があるため別名にしない
 - 型違い：ハイ/ロー/ミッド、有機EL、超極暖など。番号付きシリーズ（Switch 2 等）はクエリに無い番号を別モデル扱い
 - 類似品（〜風、ジェネリック等）と部品（片耳、〜のみ）を除外
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from functools import lru_cache

KATA = "ァ-ヺー"
_ALIAS_PATH = os.getenv("ALIASES_PATH", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "aliases.json"))

# 区別語：英字・カタカナのどちらで書かれても同じ語として扱う（H-3, N-1）
VARIANT_FORMS = [
    ("pro", "プロ"), ("max", "マックス"), ("mini", "ミニ"), ("plus", "プラス"), ("ultra", "ウルトラ"),
    ("lite", "ライト"), ("slim", "スリム"), ("kids", "キッズ"), ("junior", "ジュニア", "jr"), ("baby", "ベビー"),
    ("se",), ("fe",),
    # R3-3: 型違い
    ("oled",), ("超極暖",), ("極暖",),
    ("extra warm", "エクストラウォーム"),
]
# R3-3: 互いに排他な型（クエリで1つを指定したときだけ、別の型を含むタイトルを除外。例：ダンク ロー ≠ ダンク ハイ）
EXCLUSIVE_GROUPS = [[("high", "ハイ", "hi"), ("low", "ロー"), ("mid", "ミッド")]]
# R3-3: 類似品・模倣品を示す語（単独の語として現れたとき）
LOOKALIKE_WORDS = ("風", "似", "ジェネリック", "ノーブランド", "類似品", "互換品", "コピー", "レプリカ")
# R3-3: 本体の一部だけ（片耳、ケースのみ 等）
PART_PATTERNS = r"(片耳|右耳|左耳|片側|(?<!本体)単品|(?:ケース|充電ケース|イヤホン|イヤフォン|右|左|箱|外箱|靴箱|元箱|付属品|パーツ)\s*のみ)"
# 区切りの無いカタカナ列を分割するときに使ってよい一般語（別名辞書の語に加える）
COMMON_KATA = ["ホワイト", "ブラック", "グレー", "グレイ", "ネイビー", "レッド", "ブルー", "グリーン", "ベージュ", "ブラウン",
               "ピンク", "イエロー", "オレンジ", "パープル", "シルバー", "ゴールド", "カーキ", "クリーム", "ボーン", "ライト",
               "ダーク", "パンダ", "メンズ", "レディース", "キッズ", "ジュニア", "ユニセックス", "シューズ", "ダウン", "ロー",
               "ハイ", "ミッド", "レトロ", "オリジナルス", "プロ", "マックス", "ミニ", "プラス", "ウルトラ", "スリム", "モデル",
               "セット", "カラー", "サイズ", "ワン", "ノイズキャンセリング", "ディテクト"]
COLOR_AFTER_LIGHT = ("グレー", "グレイ", "ブルー", "グリーン", "ピンク", "ブラウン", "ベージュ", "パープル", "オレンジ",
                     "イエロー", "ボーン", "カーキ", "ネイビー", "クリーム", "シルバー")
_NUM_UNIT_AFTER = r"(?:周年|年|周|個|枚|足|本|組|点|台|色|口|回|cm|mm|ml|%|インチ|gb|tb)"
# 付属品を示す名詞（config の exclude_title_words と併用）
ACCESSORY_NOUNS = ["イヤーピース", "イヤーチップ", "イヤーパッド", "イヤパッド", "ケーブル", "シューレース", "靴紐", "靴ひも",
                   "インソール", "中敷き", "ポーチ", "収納袋", "収納ケース", "キャリングケース", "替えパッド", "充電器", "スタンド",
                   "クリーナー", "防水スプレー"]
ATTACHED_MARKERS = ("付", "同梱", "セット内容", "付属")  # 「〜付き」「〜付属」「〜同梱」
USE_MARKERS = ("専用", "用", "対応", "向け", "互換")


def kata(s: str) -> str:
    """ひらがな→カタカナ"""
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = kata(s)
    s = re.sub(r"[\s\u3000・/／,、。「」【】『』\[\]()（）<>＜＞《》'’\"!！?？★☆◆◇■□●○※＊*♪|｜:：;；~〜]+", " ", s)
    return s.strip()


def _is_kata(ch: str) -> bool:
    return "ァ" <= ch <= "ヺ" or ch == "ー"


def _edge(v: str, left: bool) -> str:
    ch = v[0] if left else v[-1]
    if re.match(r"[a-z]", ch):
        return "(?<![a-z])" if left else "(?![a-z])"
    if _is_kata(ch):
        return "(?<![ァ-ヺー])" if left else "(?![ァ-ヺー])"
    return ""


@lru_cache(maxsize=1)
def _alias_table():
    """(正規表現, {表記: 正規形}, {表記: 文脈語リスト}, カタカナ分割用の語彙)"""
    try:
        with open(_ALIAS_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    pairs, context = [], {}
    for canon_, spec in raw.get("aliases", {}).items():
        variants = spec if isinstance(spec, list) else spec.get("variants", [])
        for v in variants:
            nv = normalize(v)
            if nv:
                pairs.append((nv, canon_))
    for canon_, spec in raw.get("contextual", {}).items():
        req = [normalize(w) for w in spec.get("requires", []) if normalize(w)]
        for v in spec.get("variants", []):
            nv = normalize(v)
            if nv:
                pairs.append((nv, canon_))
                context[nv] = req
    pairs.sort(key=lambda p: -len(p[0]))  # 長い表記から置換
    vocab = {v for v, _ in pairs if all(_is_kata(c) for c in v)} | set(COMMON_KATA)
    if not pairs:
        return [None, {}, {}, frozenset(vocab)]
    lookup = {v: c for v, c in pairs}
    pat = "|".join(_edge(v, True) + re.escape(v) + _edge(v, False) for v, _ in pairs)
    return [re.compile(pat), lookup, context, frozenset(vocab)]


def _segment_kata(run: str, vocab: frozenset) -> str:
    """区切りの無いカタカナ列を既知の語だけで完全に分割できれば空白で区切る（最少分割）。できなければそのまま。"""
    n = len(run)
    best = [None] * (n + 1)
    best[0] = []
    for i in range(n):
        if best[i] is None:
            continue
        for j in range(i + 2, n + 1):
            w = run[i:j]
            if w in vocab and (best[j] is None or len(best[j]) > len(best[i]) + 1):
                best[j] = best[i] + [w]
    return " ".join(best[n]) if best[n] and len(best[n]) > 1 else run


@lru_cache(maxsize=20000)
def canon(s: str) -> str:
    """照合用の正規形。camelCase分割 → normalize → 世代表記 → カタカナ列の分割 → 別名辞書（境界・文脈つき）"""
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)  # iPhone15ProMax → i Phone15 Pro Max
    n = normalize(s)
    n = re.sub(r"第\s*(\d+)\s*世代", r" gen\1 ", n)
    n = re.sub(r"(\d+)\s*(?:st|nd|rd|th)\s*gen(?:eration)?", r" gen\1 ", n)
    n = re.sub(r"\bgen(?:eration)?\s+(\d+)\b", r" gen\1 ", n)
    rx, lookup, context, vocab = _alias_table()
    n = re.sub(r"[ァ-ヺー]{4,}", lambda m: _segment_kata(m.group(0), vocab), n)
    n = " " + re.sub(r"\s+", " ", n) + " "
    if rx is not None:
        def sub(m):
            v = m.group(0)
            req = context.get(v)
            if req is not None and not any(w in n for w in req):
                return v  # 文脈語が無いあいまいな別名は置き換えない（R3-2）
            return " " + lookup[v] + " "
        n = rx.sub(sub, n)
    # 色名の「ライト（グレー等）」は区別語の lite ではない（R3-3）
    n = re.sub(r"ライト ?(?=(?:" + "|".join(COLOR_AFTER_LIGHT) + r")(?![ァ-ヺー]))", "light ", n)
    return re.sub(r"\s+", " ", n).strip()


def _split_scripts(s: str) -> list:
    """英字・数字・カタカナ・その他の境界でトークン化（gen2 / wh1000xm5 のような英数字は1語）"""
    return re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*|[" + KATA + r"]+|[^\sa-z0-9" + KATA + r"]+", s)


def tokens(s: str) -> list:
    return [t for t in canon(s).split(" ") if t]


def compact(s: str) -> str:
    """空白とハイフンを除いた比較用文字列。数字と数字の間の区切りだけは '|' として残す（15 128gb ≠ 15128gb）。"""
    c = canon(s).replace("-", "")
    c = re.sub(r"(?<=\d)\s+(?=\d)", "|", c)
    return c.replace(" ", "")


def _qtokens(query: str) -> list:
    return [t.replace("-", "") for t in tokens(query)]


def _token_in(tok: str, ctitle: str) -> bool:
    """数字を含むトークンは、前後が数字でない位置にあるときだけ一致（15 は 150 や 115 に一致しない）"""
    if not any(ch.isdigit() for ch in tok):
        return tok in ctitle
    for m in re.finditer(re.escape(tok), ctitle):
        before = ctitle[m.start() - 1] if m.start() > 0 else ""
        after = ctitle[m.end()] if m.end() < len(ctitle) else ""
        if (tok[0].isdigit() and before.isdigit()) or (tok[-1].isdigit() and after.isdigit()):
            continue
        if tok.isdigit() and re.match(_NUM_UNIT_AFTER, ctitle[m.end():]):
            continue  # 「90周年」「2個」は型番の 90・2 ではない（R3-3）
        return True
    return False


def _gens(s: str) -> set:
    return set(re.findall(r"gen(\d+)", canon(s)))


def _variant_present(forms: tuple, canon_text: str) -> bool:
    toks = set(_split_scripts(canon_text))
    return any((f in toks) if " " not in f else (f" {f} " in f" {canon_text} ") for f in forms)


@lru_cache(maxsize=1)
def _numbered_series() -> tuple:
    try:
        with open(_ALIAS_PATH, encoding="utf-8") as f:
            return tuple(json.load(f).get("numbered_series", []))
    except (OSError, ValueError):
        return ()


def _series_numbers(canon_text: str, series: str) -> set:
    rx = r"(?<![a-z])" + re.escape(series) + r" ?(\d{1,2})(?![\d.]|" + _NUM_UNIT_AFTER[3:-1] + r")"
    return set(re.findall(rx, canon_text))


def mismatch_reason(query: str, title: str) -> str:
    """同一商品でない明確な理由があれば返す（空文字=問題なし）。"""
    cq, ct = canon(query), canon(title)
    ctitle = compact(title)
    for tok in _qtokens(query):
        if re.fullmatch(r"gen\d+", tok) or not any(ch.isdigit() for ch in tok):
            continue
        if not _token_in(tok, ctitle):
            return f"型番・数字「{tok}」が一致しない"
    gq, gt = _gens(query), _gens(title)
    if gq and gt and not gt <= gq:
        return "世代が異なる"
    for forms in VARIANT_FORMS:
        if _variant_present(forms, ct) and not _variant_present(forms, cq):
            return f"別モデルの可能性（「{forms[0]}」を含む）"
    for group in EXCLUSIVE_GROUPS:
        chosen = [f for f in group if _variant_present(f, cq)]
        if len(chosen) == 1:
            other = [f for f in group if f is not chosen[0] and _variant_present(f, ct)]
            if other and not _variant_present(chosen[0], ct):
                return f"別モデルの可能性（「{other[0][0]}」）"
    for name in _required_series():  # R3-2: シリーズ名（サンバ、ダンク等）はタイトルにも必須
        if _has_phrase(name, cq) and not _has_phrase(name, ct):
            return f"シリーズ名「{name}」が一致しない"
    for series in _numbered_series():  # R3-3: Switch 2 等（クエリに無い番号付きの後継モデル）
        if not re.search(r"(?<![a-z])" + re.escape(series) + r"(?![a-z])", cq):
            continue
        extra = _series_numbers(ct, series) - _series_numbers(cq, series) - gq
        if extra:
            return f"別モデルの可能性（「{series} {sorted(extra)[0]}」）"
    return ""


@lru_cache(maxsize=1)
def _required_series() -> tuple:
    try:
        with open(_ALIAS_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return ()
    skip = set(raw.get("brands", [])) | set(raw.get("generic", []))
    names = set(raw.get("aliases", {})) | set(raw.get("contextual", {}))
    return tuple(sorted((n for n in names if n not in skip), key=len, reverse=True))


def _has_phrase(phrase: str, canon_text: str) -> bool:
    return re.search(r"(?<![a-z" + KATA + r"])" + re.escape(phrase) + r"(?![a-z" + KATA + r"])", canon_text) is not None


def lookalike_reason(query: str, title: str) -> str:
    """類似品・模倣品・部品だけの出品を示す語（R3-3）。空文字=問題なし。"""
    ct, cq = canon(title), canon(query)
    toks = ct.split(" ")
    for w in LOOKALIKE_WORDS:
        if w in cq:
            continue
        if w in toks:
            return f"類似品の可能性（「{w}」）"
        if len(w) > 1 and any(t.endswith(w) and len(t) > len(w) for t in toks):
            return f"類似品の可能性（「{w}」）"
    # 「オールドスクール風」のように語の直後に付く「風」「似」
    for tok in _qtokens(query):
        if len(tok) >= 2 and re.search(re.escape(tok) + r" ?(?:風|似|タイプ)(?![ァ-ヺー一-龥])", ct.replace("-", "")):
            return "類似品の可能性（「風」）"
    m = re.search(PART_PATTERNS, ct)
    if m and m.group(0) not in cq:
        return f"本体の一部のみの可能性（「{m.group(0)}」）"
    return ""


def _bigrams(t: str) -> set:
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else {t}


def relevance(query: str, title: str) -> float:
    """クエリの各トークンがタイトルに含まれる割合（0-1）。数字を含むトークンは完全一致のみ。
    世代トークン（gen2）は、タイトルに世代表記が無い場合は数えない（「AirPods Pro 2」等の省略表記に対応）。"""
    q = _qtokens(query)
    ctitle = compact(title)
    if not _gens(title):
        q = [t for t in q if not re.fullmatch(r"gen\d+", t)]
    if not q:
        return 0.0
    score = 0.0
    for tok in q:
        if _token_in(tok, ctitle):
            score += 1.0
        elif not any(ch.isdigit() for ch in tok):
            bg = _bigrams(tok)
            score += 0.8 * sum(1 for b in bg if b in ctitle) / len(bg) if bg else 0
    return score / len(q)


def contains_any(title: str, words: list, query: str = "") -> str:
    """タイトルに語が含まれれば（クエリ自体に含まれない場合のみ）その語を返す。"""
    nt, nq = canon(title), canon(query)
    for w in words:
        nw = canon(w)
        if nw and nw in nt and nw not in nq:
            return w
    return ""


def _attached_after(text: str, end: int, window: int = 14) -> bool:
    tail = text[end:end + window]
    return any(m in tail for m in ATTACHED_MARKERS)


def accessory_reason(query: str, title: str, extra_words: list = ()) -> str:
    """付属品・関連グッズと判断できる語を返す（空文字=付属品ではない）。"""
    ct, cq = canon(title), canon(query)
    # 1) 付属品の名詞（「付き」「付属」「同梱」が続く場合は本体の付属物なので除外しない）
    for w in list(extra_words) + ACCESSORY_NOUNS:
        nw = canon(w)
        if not nw or nw in cq:
            continue
        for m in re.finditer(re.escape(nw), ct):
            if not _attached_after(ct, m.end()):
                return w
    # 2) 「<商品名・型番> 用/対応/専用/向け/互換」「for <商品名>」
    qt = [t for t in _qtokens(query) if len(t) >= 2 or any(ch.isdigit() for ch in t)]
    flat = ct.replace("-", "")
    for tok in qt:
        for m in re.finditer(re.escape(tok), flat):
            after = flat[m.end():].lstrip(" ")
            after = re.sub(r"^(gen\d+|\d+(?:mm|cm|インチ)?)\s*", "", after)  # 「第2世代 対応」「'07 用」
            for mk in USE_MARKERS:
                if after.startswith(mk):
                    return f"{tok} {mk}"
            before = flat[:m.start()].rstrip(" ")
            if re.search(r"(?<![a-z])for$", before):
                return f"for {tok}"
    if re.search(r"(?:^| )(?:専用|互換)(?: |$)", ct) and "専用" not in cq:
        return "専用"
    return ""
