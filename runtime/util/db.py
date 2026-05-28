"""SQLite connection helper. Applies migrations idempotently on first open."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from . import config

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema" / "db" / "sql"

_SHARED: Optional[sqlite3.Connection] = None
_SHARED_PATH: Optional[Path] = None


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    p = db_path or config.DB_PATH
    config.ensure_cache_dir()
    fresh = not p.exists()
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    if fresh:
        apply_migrations(conn)
    return conn


def connect_shared(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Process-lifetime shared connection for the `atelier serve` daemon.

    All transports under one asyncio loop reuse the same connection so
    single-writer-per-subtree can be enforced with in-memory locks rather
    than file-level locks. Reset with `close_shared()`.
    """
    global _SHARED, _SHARED_PATH
    p = db_path or config.DB_PATH
    if _SHARED is None or _SHARED_PATH != p:
        if _SHARED is not None:
            _SHARED.close()
        # check_same_thread=False so a thread executor can run heavy reads;
        # writes are still serialized by claims.SpaceLockRegistry on the loop.
        config.ensure_cache_dir()
        fresh = not p.exists()
        _SHARED = sqlite3.connect(p, check_same_thread=False)
        _SHARED.row_factory = sqlite3.Row
        _SHARED.execute("PRAGMA foreign_keys = ON;")
        _SHARED.execute("PRAGMA journal_mode = WAL;")
        if fresh:
            apply_migrations(_SHARED)
        _SHARED_PATH = p
    return _SHARED


def close_shared() -> None:
    global _SHARED, _SHARED_PATH
    if _SHARED is not None:
        _SHARED.close()
        _SHARED = None
        _SHARED_PATH = None


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply all .sql files in schema/db/sql/ in lexicographic order."""
    for sql_file in sorted(SCHEMA_DIR.glob("*.sql")):
        conn.executescript(sql_file.read_text())
    conn.commit()


def fetchall(conn: sqlite3.Connection, sql: str, *params) -> list[sqlite3.Row]:
    return list(conn.execute(sql, params))


def fetchone(conn: sqlite3.Connection, sql: str, *params) -> Optional[sqlite3.Row]:
    return conn.execute(sql, params).fetchone()


def get_meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = fetchone(conn, "SELECT value FROM meta WHERE key=?", key)
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
