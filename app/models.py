from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class Observation:
    """実際に取得した価格1件。数値は必ず取得元レスポンスから読んだ値（LLM生成・推定は禁止）。"""
    source_id: str
    source_name: str
    url: str
    title: str
    price: int  # 商品価格（円・税込、取得元表記どおり）
    fetched_at: str  # 取得日時 ISO8601 (UTC)
    condition: str = "new"  # new / used
    seller: str = ""
    is_history: bool = False  # 価格履歴API由来の過去値
    observed_at: Optional[str] = None  # 履歴値の時点（無ければ fetched_at）
    is_demo: bool = False
    raw_ref: str = ""  # 取得元での識別子（itemCode, ASIN 等）
    store_id: str = ""  # 楽天市場店・Yahoo!店として登録した専門店のID（config/sources.json）。無ければ空
    shipping: Optional[int] = None  # 送料（円）。0=送料無料/送料込み、None=取得元で不明
    fees: Optional[int] = 0  # 必須手数料（円）。None=不明。購入者手数料が無い取得元は 0
    point_amount: Optional[int] = None  # 付与ポイント（円相当）。取得元が明示した場合のみ
    point_note: str = ""  # ポイント値の根拠
    point_computed: bool = False  # True=取得元の倍率から計算した値（取得元が金額を明示したものではない）

    @property
    def payable_known(self) -> bool:
        return self.shipping is not None and self.fees is not None

    @property
    def total_payable(self) -> Optional[int]:
        """総支払額 = 税込価格 + 送料 + 必須手数料。どれか不明なら None（推測しない）"""
        if not self.payable_known:
            return None
        return self.price + self.shipping + self.fees

    @property
    def effective_price(self) -> Optional[int]:
        """実質価格 = 総支払額 − ポイント。ポイントが明示されていない場合は None"""
        tp = self.total_payable
        if tp is None or self.point_amount is None:
            return None
        return tp - self.point_amount

    def value(self, basis: str = "total") -> Optional[int]:
        return self.effective_price if basis == "effective" else self.total_payable

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total_payable"] = self.total_payable
        d["effective_price"] = self.effective_price
        return d


@dataclass
class Excluded:
    obs: Observation
    reason: str

    def to_dict(self) -> dict:
        d = self.obs.to_dict()
        d["excluded_reason"] = self.reason
        return d


@dataclass
class FetchReport:
    source_id: str
    source_name: str
    status: str  # ok / skipped / error
    count: int = 0
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
