"""SQLite に取得した価格を蓄積し、価格推移（グラフ）だけに使う。判定には使わない（C-1）。
- デモデータは保存しない
- 利用者の検索語そのものは保存しない。product_key は「秘密の salt＋正規化後の検索語」の HMAC-SHA256（H-4, N-22）
  salt は DB 内に1度だけ生成して保持（環境変数 HASH_SALT で指定も可）
- スキーマは PRAGMA user_version で管理し、古い DB は起動時に移行（N-8）
- DB を開けない（権限・ディスク無し等）場合は推移の保存だけを止めて起動を続ける（N-2）
- 保存期間 DB_RETENTION_DAYS を過ぎたデータは削除"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from .models import Observation

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_key TEXT NOT NULL,
  source_id TEXT NOT NULL, source_name TEXT NOT NULL,
  url TEXT NOT NULL, title TEXT NOT NULL,
  price INTEGER NOT NULL, shipping INTEGER, fees INTEGER,
  point_amount INTEGER, point_note TEXT, point_computed INTEGER DEFAULT 0,
  condition TEXT NOT NULL, seller TEXT,
  is_history INTEGER NOT NULL DEFAULT 0,
  observed_at TEXT, fetched_at TEXT NOT NULL, raw_ref TEXT,
  UNIQUE(product_key, url, fetched_at, is_history, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_obs_key ON observations(product_key, fetched_at);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
DROP TABLE IF EXISTS products;
"""
SCHEMA_VERSION = 3
# R3-1: 楽天ウェブサービスは価格情報のキャッシュを24時間までとしているため、楽天由来の行は保存しない
NO_STORE_SOURCES = frozenset({"rakuten"})
COLUMNS = {"point_amount": "INTEGER", "point_note": "TEXT", "point_computed": "INTEGER DEFAULT 0",
           "seller": "TEXT", "raw_ref": "TEXT", "observed_at": "TEXT", "fees": "INTEGER", "shipping": "INTEGER"}
_salt = {"v": os.getenv("HASH_SALT", "")}


def hash_key(normalized_query: str) -> str:
    salt = _salt["v"] or "price-judge"
    return hmac.new(salt.encode(), normalized_query.encode("utf-8"), hashlib.sha256).hexdigest()


class DB:
    def __init__(self, path: str, retention_days: int = 400):
        self.path = path
        self.retention_days = retention_days
        self.persistent = True
        try:
            if path != ":memory:":
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            self.conn = sqlite3.connect(path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self._init()
        except (sqlite3.Error, OSError) as e:
            logging.getLogger("price_judge").warning("DB を開けません（%s: %s）。推移の保存を停止して起動を続けます", path, e)
            self.persistent = path == ":memory:"
            self.conn = sqlite3.connect(":memory:", check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self._init()

    def _init(self):
        with _lock:
            ver = self.conn.execute("PRAGMA user_version").fetchone()[0]
            self.conn.executescript(SCHEMA)
            cols = {r[1] for r in self.conn.execute("PRAGMA table_info(observations)")}
            for c, typ in COLUMNS.items():  # N-8: 古い DB に無い列を追加
                if c not in cols:
                    self.conn.execute(f"ALTER TABLE observations ADD COLUMN {c} {typ}")
            if ver < SCHEMA_VERSION:
                # v1 の product_key は検索語の平文だった → 64桁の16進数（ハッシュ）以外は削除（H-4）
                self.conn.execute("DELETE FROM observations WHERE length(product_key) != 64 OR product_key GLOB '*[^0-9a-f]*'")
                # salt 導入前のハッシュは新しいキーと一致しないため、その行は推移に使われず保存期間で自動削除される
            if ver < 3:
                # R3-1: 以前の版で保存した楽天の行を削除
                self.conn.execute("DELETE FROM observations WHERE source_id IN (%s)" % ",".join("?" * len(NO_STORE_SOURCES)),
                                  tuple(NO_STORE_SOURCES))
            if ver < SCHEMA_VERSION:
                self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            row = self.conn.execute("SELECT v FROM meta WHERE k='salt'").fetchone()
            if not _salt["v"]:
                if row is None:
                    salt = secrets.token_hex(16)
                    self.conn.execute("INSERT INTO meta(k, v) VALUES ('salt', ?)", (salt,))
                    _salt["v"] = salt
                else:
                    _salt["v"] = row[0]
            self.conn.commit()

    def save(self, key: str, obs: list) -> int:
        rows = [(key, o.source_id, o.source_name, o.url, o.title, o.price, o.shipping, o.fees,
                 o.point_amount, o.point_note, int(o.point_computed), o.condition, o.seller, int(o.is_history),
                 o.observed_at or "", o.fetched_at, o.raw_ref) for o in obs
                if not o.is_demo and o.source_id not in NO_STORE_SOURCES]
        if not rows or not self.persistent:
            return 0
        with _lock:
            cur = self.conn.executemany(
                "INSERT OR IGNORE INTO observations (product_key, source_id, source_name, url, title, price, shipping, fees,"
                " point_amount, point_note, point_computed, condition, seller, is_history, observed_at, fetched_at, raw_ref)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            cut = (datetime.now(timezone.utc) - timedelta(days=self.retention_days)).isoformat()
            self.conn.execute("DELETE FROM observations WHERE fetched_at < ?", (cut,))
            self.conn.commit()
            return cur.rowcount

    def load(self, key: str, days: int = 365) -> list:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with _lock:
            rows = self.conn.execute(
                "SELECT * FROM observations WHERE product_key=? AND (fetched_at>=? OR (is_history=1 AND observed_at>=?))",
                (key, since, since)).fetchall()
        return [Observation(
            source_id=r["source_id"], source_name=r["source_name"], url=r["url"], title=r["title"],
            price=r["price"], fetched_at=r["fetched_at"], condition=r["condition"], seller=r["seller"] or "",
            is_history=bool(r["is_history"]), observed_at=r["observed_at"] or None, raw_ref=r["raw_ref"] or "",
            shipping=r["shipping"], fees=r["fees"], point_amount=r["point_amount"], point_note=r["point_note"] or "",
            point_computed=bool(r["point_computed"]))
            for r in rows]
