"""vectors.db — the semantic sidecar store (RFC 0002 P2).

A separate SQLite file beside atelier.db, owned entirely by the semantic mode.
Deliberately NOT a migration on the main DB: vec0 needs the sqlite-vec loadable
extension, and putting it in schema/db/sql/ would make every atelier operation
depend on that extension. As a sidecar, semantic search is a plug-in — absent
extension → `VecStore.open` returns None → callers stay lexical-only.

Two tables, two lifetimes (the durable/projection split):

  embedding_cache(content_hash, signature → vector)   DURABLE
      keyed by the chunk text's own hash + the gateway signature. Survives any
      rebuild; `rm atelier.db && reindex` re-embeds nothing whose text didn't
      change (RFC 0002 §9). The expensive thing is the vector, so the vector
      is what's cached — by content, not by location.

  vec_chunks  (vec0 virtual table: rowid=chunk_id → float[dim])   PROJECTION
      the kNN index, rebuilt from main-DB chunks ⋈ cache on every sync(); same
      disposable status as chunks_fts. rowid == chunks.id is the bridge that
      lets VecSemantic join hits back to pages in the main DB.

Assumption — UNIT-NORMALIZED embeddings. vec0's MATCH ranks by L2 distance,
which is rank-equivalent to cosine ONLY for unit-length vectors. The default
provider (bge-m3) already returns normalized vectors (verified: ‖v‖₂ = 1.0), so
no normalization happens here. A future provider that returns UN-normalized
vectors would make L2 diverge from cosine and silently degrade ranking — add an
L2-normalize step at store+query time (and give the test fakes direction-varying
vectors) when such a provider is introduced. Not built speculatively (YAGNI).
"""
from __future__ import annotations

import hashlib
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from ...util import config as _config
from ...util import logging as log


def _vectors_db_path() -> Path:
    # Resolved per call so test monkeypatching of CACHE_DIR is honored.
    return _config.CACHE_DIR / "vectors.db"


def _load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    """Load the sqlite-vec extension into this connection. False = unavailable
    (package not installed or loading forbidden) — the caller degrades."""
    try:
        import sqlite_vec
    except ImportError:
        return False
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception as e:                      # pragma: no cover - platform-specific
        log.info("vecstore.extension_load_failed", error=str(e))
        return False


def _pack(vec: List[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


@dataclass
class SyncStats:
    total: int = 0       # chunks considered
    embedded: int = 0    # unique vectors computed via the gateway (deduped by text)
    reused: int = 0      # chunks served without a gateway call (cache hit OR dup text)


class VecStore:
    """The sidecar handle. Construct via `VecStore.open` (None when the
    extension is unavailable)."""

    def __init__(self, conn: sqlite3.Connection, signature: str, dim: int) -> None:
        self._conn = conn
        self._sig = signature
        self._dim = dim

    # ── lifecycle ───────────────────────────────────────────────────────────

    @classmethod
    def open(cls, *, gateway_signature: str, dim: int,
             path: Optional[Path] = None) -> Optional["VecStore"]:
        p = path or _vectors_db_path()
        _config.ensure_cache_dir()
        conn = sqlite3.connect(p)
        conn.row_factory = sqlite3.Row
        if not _load_sqlite_vec(conn):
            conn.close()
            return None
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS embedding_cache (
              content_hash TEXT NOT NULL,
              signature    TEXT NOT NULL,
              dim          INTEGER NOT NULL,
              vector       BLOB NOT NULL,
              PRIMARY KEY (content_hash, signature)
            );
            CREATE TABLE IF NOT EXISTS meta (
              key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
        """)
        # The vec0 projection is dim-bound: a dim change (new model family)
        # means a new virtual table. Signature lives in meta for diagnostics.
        row = conn.execute("SELECT value FROM meta WHERE key='dim'").fetchone()
        if row is not None and int(row["value"]) != dim:
            conn.execute("DROP TABLE IF EXISTS vec_chunks")
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0("
            f"embedding float[{dim}])")
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('dim', ?)", (str(dim),))
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('signature', ?)",
                     (gateway_signature,))
        conn.commit()
        return cls(conn, gateway_signature, dim)

    def close(self) -> None:
        self._conn.close()

    # ── write side ──────────────────────────────────────────────────────────

    def sync(self, main_conn: sqlite3.Connection, gateway,
             commit_batch: int = 256) -> SyncStats:
        """Bring the substrate up to date with the main DB's chunks.

        1. hash every chunk's text; look up (hash, signature) in the cache;
        2. embed ONLY the misses, STREAMING: each `commit_batch` is embedded and
           committed before the next starts — so a long bulk pass is durable
           (an interruption keeps completed batches) and observable (progress is
           logged), not an all-or-nothing in-memory accumulation;
        3. rebuild the vec_chunks projection (clear-and-repopulate, idempotent).
        Unchanged text costs zero gateway calls — the determinism guarantee.

        `commit_batch` is the durability granularity (DB commits); the gateway
        independently sub-batches HTTP requests. Keep them distinct.
        """
        stats = SyncStats()
        rows = list(main_conn.execute("SELECT id, text FROM chunks"))
        stats.total = len(rows)
        hashes = {r["id"]: _text_hash(r["text"]) for r in rows}

        cached = {
            r["content_hash"]: r["vector"]
            for r in self._conn.execute(
                "SELECT content_hash, vector FROM embedding_cache WHERE signature=?",
                (self._sig,))
        }
        # Dedup misses by content_hash: identical chunk text (common — headers,
        # boilerplate) is embedded once, not once per occurrence.
        miss_texts: dict[str, str] = {}
        for r in rows:
            h = hashes[r["id"]]
            if h not in cached and h not in miss_texts:
                miss_texts[h] = r["text"]
        misses = list(miss_texts.items())          # [(hash, text)]

        for i in range(0, len(misses), max(1, commit_batch)):
            batch = misses[i:i + max(1, commit_batch)]
            vectors = gateway.embed([t for _, t in batch])
            with self._conn:
                for (h, _), vec in zip(batch, vectors):
                    blob = _pack(vec)
                    self._conn.execute(
                        "INSERT OR REPLACE INTO embedding_cache "
                        "(content_hash, signature, dim, vector) VALUES (?,?,?,?)",
                        (h, self._sig, self._dim, blob))
                    cached[h] = blob
            stats.embedded += len(batch)
            log.info("vecstore.embed_progress",
                     done=min(i + len(batch), len(misses)), misses=len(misses))
        stats.reused = stats.total - stats.embedded

        with self._conn:
            self._conn.execute("DELETE FROM vec_chunks")
            for r in rows:
                self._conn.execute(
                    "INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
                    (r["id"], cached[hashes[r["id"]]]))
        return stats

    # ── read side ───────────────────────────────────────────────────────────

    def knn(self, embedding: List[float], k: int) -> List[Tuple[int, float]]:
        """k nearest chunks: [(chunk_id, distance)], nearest first."""
        rows = self._conn.execute(
            "SELECT rowid, distance FROM vec_chunks "
            "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (_pack(embedding), k))
        return [(r["rowid"], float(r["distance"])) for r in rows]

    def count(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) n FROM vec_chunks").fetchone()["n"]
