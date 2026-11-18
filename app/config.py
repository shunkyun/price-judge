"""環境変数による設定。ローカルパスはハードコードしない（全て env で上書き可能）。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    rakuten_app_id: str = field(default_factory=lambda: os.getenv("RAKUTEN_APP_ID", ""))
    rakuten_access_key: str = field(default_factory=lambda: os.getenv("RAKUTEN_ACCESS_KEY", ""))
    rakuten_origin: str = field(default_factory=lambda: os.getenv("RAKUTEN_ORIGIN", ""))
    rakuten_endpoint: str = field(default_factory=lambda: os.getenv(
        "RAKUTEN_ENDPOINT", "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20220601"))
    yahoo_app_id: str = field(default_factory=lambda: os.getenv("YAHOO_APP_ID", ""))
    keepa_api_key: str = field(default_factory=lambda: os.getenv("KEEPA_API_KEY", ""))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gpt-4o-mini"))
    llm_vision_model: str = field(default_factory=lambda: os.getenv("LLM_VISION_MODEL", "") or os.getenv("LLM_MODEL", "gpt-4o-mini"))
    llm_normalize: bool = field(default_factory=lambda: os.getenv("LLM_NORMALIZE", "0") == "1")
    # DEMO_MODE: auto（APIキーが無ければデモ） / 1（常にデモ） / 0（デモ無効）
    demo_mode_raw: str = field(default_factory=lambda: os.getenv("DEMO_MODE", "auto").lower())
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", str(BASE_DIR / "data" / "prices.db")))
    sources_path: str = field(default_factory=lambda: os.getenv("SOURCES_PATH", str(BASE_DIR / "config" / "sources.json")))
    suggest_seed_path: str = field(default_factory=lambda: os.getenv("SUGGEST_SEED_PATH", str(BASE_DIR / "config" / "suggest_seed.json")))
    min_samples: int = field(default_factory=lambda: _int("MIN_SAMPLES", 10))
    min_sellers: int = field(default_factory=lambda: _int("MIN_SELLERS", 3))
    max_queue_wait: float = field(default_factory=lambda: _float("MAX_QUEUE_WAIT_SEC", 20.0))
    trend_min_point_n: int = field(default_factory=lambda: _int("TREND_MIN_POINT_N", 3))
    upstream_interval: float = field(default_factory=lambda: _float("UPSTREAM_INTERVAL_SEC", 1.0))
    rate_analyze_per_min: int = field(default_factory=lambda: _int("RATE_ANALYZE_PER_MIN", 10))
    rate_identify_per_min: int = field(default_factory=lambda: _int("RATE_IDENTIFY_PER_MIN", 3))
    rate_suggest_per_min: int = field(default_factory=lambda: _int("RATE_SUGGEST_PER_MIN", 120))
    llm_daily_limit: int = field(default_factory=lambda: _int("LLM_DAILY_LIMIT", 100))
    # 公開時の簡易パスコード（共有キー）。設定すると /api/status 以外の API にコードが必要（H-6）
    access_code: str = field(default_factory=lambda: os.getenv("ACCESS_CODE", ""))
    # N-3: X-Forwarded-For の右から何番目を利用者IPとみなすか（0=使わない）。旧設定 TRUST_PROXY=1 は 1 段として扱う
    trust_proxy: int = field(default_factory=lambda: max(0, _int("TRUST_PROXY", 0)))
    # R3-5: 手前の信頼できるプロキシが必ず上書きする利用者IPヘッダ（既定なし＝ヘッダは一切信用しない）。
    # Render は Cloudflare 経由のため render.yaml で CF-Connecting-IP を設定。旧名 CLIENT_IP_HEADER も読む（警告つき）
    client_ip_header: str = field(default_factory=lambda: (os.getenv("TRUSTED_PROXY_HEADER", "") or os.getenv("CLIENT_IP_HEADER", "")).strip())
    # 指定した場合、接続元がこの範囲（カンマ区切りの CIDR）のときだけヘッダ・X-Forwarded-For を信用する
    trusted_proxy_cidrs: str = field(default_factory=lambda: os.getenv("TRUSTED_PROXY_CIDRS", "").strip())
    # R3-5: 誤りの上限。端末（セッション）ごと access_code_max_failures、回線（IP）ごと access_code_ip_max_failures（10分あたり）
    access_code_ip_max_failures: int = field(default_factory=lambda: _int("ACCESS_CODE_IP_MAX_FAILURES", 50))
    # R3-7: 1つの回線（IP）から1時間に開始できる利用（セッション）の上限
    max_sessions_per_ip: int = field(default_factory=lambda: _int("MAX_SESSIONS_PER_IP", 30))
    session_hours: float = field(default_factory=lambda: _float("SESSION_HOURS", 12.0))
    # R3-11: 画面に管理者向けの注意を出すのは自分のPC（127.0.0.1）か、?admin=<ADMIN_TOKEN> を付けたときだけ
    admin_token: str = field(default_factory=lambda: os.getenv("ADMIN_TOKEN", "").strip())
    # N-13: アクセスコード認証済みのとき、IP単位の上限（共有回線向けに緩める）。利用者ごと（セッション）は rate_analyze_per_min
    rate_ip_authed_per_min: int = field(default_factory=lambda: _int("RATE_IP_AUTHED_PER_MIN", 60))
    # N-6: アクセスコードの失敗許容回数（10分あたり）
    access_code_max_failures: int = field(default_factory=lambda: _int("ACCESS_CODE_MAX_FAILURES", 10))
    db_retention_days: int = field(default_factory=lambda: _int("DB_RETENTION_DAYS", 400))
    min_sources: int = field(default_factory=lambda: _int("MIN_SOURCES", 2))
    iqr_k: float = field(default_factory=lambda: _float("OUTLIER_IQR_K", 1.5))
    relevance_min: float = field(default_factory=lambda: _float("RELEVANCE_MIN", 0.6))
    recent_days: int = field(default_factory=lambda: _int("RECENT_DAYS", 30))
    http_timeout: float = field(default_factory=lambda: _float("HTTP_TIMEOUT", 10.0))
    # 店舗追加: 検索語の種類に合う専門店の公式店（楽天市場店・Yahoo!店）に絞った検索を、取得元ごとに最大何店ぶん追加するか（0=しない）
    shop_search_max: int = field(default_factory=lambda: max(0, min(5, _int("SHOP_SEARCH_MAX", 2))))
    max_image_bytes: int = field(default_factory=lambda: _int("MAX_IMAGE_BYTES", 5 * 1024 * 1024))

    @property
    def has_live_sources(self) -> bool:
        return bool((self.rakuten_app_id and self.rakuten_access_key) or self.yahoo_app_id)

    @property
    def demo_mode(self) -> bool:
        if self.demo_mode_raw in ("1", "true", "on"):
            return True
        if self.demo_mode_raw in ("0", "false", "off"):
            return False
        return not self.has_live_sources

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)


class ConfigError(ValueError):
    pass


def validate(s: Settings) -> list:
    """設定値の検査（N-14, N-17）。致命的な誤りは ConfigError、それ以外は警告文のリストを返す。"""
    warns = []
    if s.access_code:
        if not s.access_code.isascii() or not s.access_code.isprintable():
            raise ConfigError("ACCESS_CODE には半角英数字・記号のみ使えます（日本語などはブラウザから送信できません）")
        if len(s.access_code) < 12:
            warns.append("ACCESS_CODE は12文字以上のランダムな文字列を推奨します")
    if os.getenv("CLIENT_IP_HEADER") and not os.getenv("TRUSTED_PROXY_HEADER"):
        warns.append("CLIENT_IP_HEADER は旧名です。TRUSTED_PROXY_HEADER に変更してください")
    if s.client_ip_header and not s.trusted_proxy_cidrs and not os.getenv("RENDER"):
        warns.append(f"TRUSTED_PROXY_HEADER={s.client_ip_header} が設定されていますが、Render 以外では利用者が偽装できます。"
                     "そのヘッダを必ず上書きするプロキシの内側でだけ使うか、TRUSTED_PROXY_CIDRS を設定してください")
    if s.min_samples < 4:
        warns.append(f"MIN_SAMPLES={s.min_samples} は小さすぎるため 4 として扱います（外れ値判定に最低4件必要）")
        s.min_samples = 4
    if s.min_sources < 1:
        warns.append("MIN_SOURCES は 1 以上にしてください（1 として扱います）")
        s.min_sources = 1
    if s.min_sellers < 1:
        warns.append("MIN_SELLERS は 1 以上にしてください（1 として扱います）")
        s.min_sellers = 1
    if not 0 < s.relevance_min <= 1:
        warns.append("RELEVANCE_MIN は 0〜1 の範囲で指定してください（0.6 として扱います）")
        s.relevance_min = 0.6
    if s.iqr_k <= 0:
        warns.append("OUTLIER_IQR_K は正の数にしてください（1.5 として扱います）")
        s.iqr_k = 1.5
    check_db_path(s.db_path)
    return warns


_DANGEROUS_DIRS = {"/", "/app", "/usr", "/etc", "/bin", "/sbin", "/lib", "/lib64", "/boot", "/dev", "/proc", "/sys",
                   "/root", "/home", "/var", "/opt", "/srv", "/run", "/usr/local", "/usr/local/bin"}


def check_db_path(path: str) -> None:
    """R3-8: DB_PATH の検査。システムのフォルダ直下やアプリのコードのフォルダを保存先にしない"""
    if path == ":memory:":
        return
    if not path or path.endswith(("/", "\\")) or os.path.isdir(path):
        raise ConfigError(f"DB_PATH はファイル名まで指定してください（例 /data/prices.db）: {path!r}")
    d = os.path.dirname(os.path.abspath(path))
    if d.replace("\\", "/").rstrip("/") in {x.rstrip("/") for x in _DANGEROUS_DIRS} | {""}:
        raise ConfigError(f"DB_PATH の保存先フォルダ {d} はシステムのフォルダです。専用のフォルダ（例 /data）を指定してください")
    if os.path.abspath(d) in (os.path.abspath(str(BASE_DIR)), os.path.abspath(str(BASE_DIR / "app"))):
        raise ConfigError(f"DB_PATH をアプリのフォルダ（{d}）に置かないでください（例 ./data/prices.db）")


def get_settings() -> Settings:
    return Settings()
