"""価格取得アダプタ。カテゴリ（物/サービス）や取得元ごとに差し替え可能なプラグイン構成。

新しい取得元を追加するには:
  1. PriceAdapter を継承したクラスを作り、search() で Observation を返す
  2. REGISTRY に登録し、config/sources.json に許可ドメインを追加する
将来の「サービス価格」（例: 引越し・クリーニング）も category="service" のアダプタとして追加できる。
"""
from .base import PriceAdapter, REGISTRY, register  # noqa: F401
from . import rakuten, yahoo, keepa, demo, page  # noqa: F401  (登録のため import)
