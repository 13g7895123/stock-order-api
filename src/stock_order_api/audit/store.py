"""SQLite 稽核 + 快照 + 快取儲存。

三張表（見 plan-account.md §7）：
  - audit_events:   每筆 LOGIN / QUERY_* / ERROR 事件
  - cache_entries:  持久化 TTL 快取（進程重啟仍可用）
  - snapshots:      每次成功查詢的原始結果 payload（便於跨日比對）
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_DDL = """
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event TEXT NOT NULL,
    request_id TEXT,
    account TEXT,
    ok INTEGER NOT NULL,
    message TEXT,
    payload_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_events_ts ON audit_events(ts);
CREATE INDEX IF NOT EXISTS idx_audit_events_event ON audit_events(event);

CREATE TABLE IF NOT EXISTS cache_entries (
    cache_key TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    ttl_sec INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    account TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_kind_ts ON snapshots(kind, ts);
"""


def _iso_now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="milliseconds")


class AuditStore:
    """稽核/快取/快照共用儲存。"""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(_DDL)

    # ---------- 稽核事件 ----------
    def log_event(
        self,
        event: str,
        ok: bool,
        request_id: str | None = None,
        account: str | None = None,
        message: str | None = None,
        payload: Any = None,
    ) -> int:
        payload_json = json.dumps(payload, ensure_ascii=False, default=str) if payload is not None else None
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO audit_events(ts,event,request_id,account,ok,message,payload_json)"
                " VALUES (?,?,?,?,?,?,?)",
                (_iso_now(), event, request_id, account, int(ok), message, payload_json),
            )
            return int(cur.lastrowid or 0)

    # ---------- 快照 ----------
    def save_snapshot(self, kind: str, account: str, payload: Any) -> int:
        payload_json = json.dumps(payload, ensure_ascii=False, default=str)
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO snapshots(ts,kind,account,payload_json) VALUES (?,?,?,?)",
                (_iso_now(), kind, account, payload_json),
            )
            return int(cur.lastrowid or 0)

    # ---------- 快取 ----------
    def cache_get(self, key: str) -> tuple[str, int, str] | None:
        """回傳 (fetched_at_iso, ttl_sec, payload_json) 或 None。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT fetched_at, ttl_sec, payload_json FROM cache_entries WHERE cache_key=?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return (row["fetched_at"], int(row["ttl_sec"]), row["payload_json"])

    def cache_set(self, key: str, ttl_sec: int, payload: Any) -> None:
        payload_json = json.dumps(payload, ensure_ascii=False, default=str)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO cache_entries(cache_key,fetched_at,ttl_sec,payload_json)"
                " VALUES(?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET"
                " fetched_at=excluded.fetched_at, ttl_sec=excluded.ttl_sec,"
                " payload_json=excluded.payload_json",
                (key, _iso_now(), ttl_sec, payload_json),
            )

    def cache_invalidate(self, prefix: str = "") -> int:
        with self._lock, self._conn:
            if prefix:
                cur = self._conn.execute(
                    "DELETE FROM cache_entries WHERE cache_key LIKE ?",
                    (f"{prefix}%",),
                )
            else:
                cur = self._conn.execute("DELETE FROM cache_entries")
            return int(cur.rowcount or 0)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
