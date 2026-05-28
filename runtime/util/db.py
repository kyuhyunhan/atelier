"""SQLite connection helper. Applies migrations idempotently on first open."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from . import config

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema" / "db" / "sql"


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
