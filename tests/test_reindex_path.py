"""RFC 0006 Pillar ② — the change feed: single-file reindex + write-through."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from runtime.index import reindex as _reindex
from runtime.service import api as _api
from runtime.service.learnings import capture as _cap
from runtime.service.learnings import cluster as _cl
from runtime.util import config as _config
from runtime.util import db as _db


def _claim_files(vault: Path):
    return sorted((vault / "graph" / "atomic").rglob("*.md"))


def test_reindex_path_matches_full_reindex(atelier_env: Dict) -> None:
    _cap.capture(observation="obs alpha throughput", why="w", rule="r",
                 working_dir="/Users/me/workspaces/lexio", session_id="a", hook="Stop")
    vault = Path(_cl._vault_root())
    claim = _claim_files(vault)[0]
    cfg = _config.load()

    # Full reindex → the parity oracle for this slug.
    _api.reindex(space="gorae", full=True)
    from runtime.util import fs as _fs
    slug = _fs.slug_for(vault, claim)

    def _snapshot(conn):
        page = _db.fetchone(conn, "SELECT page_type, frontmatter FROM pages WHERE slug=?", slug)
        chunks = [(r["position"], r["text"]) for r in conn.execute(
            "SELECT c.position, c.text FROM chunks c JOIN pages p ON p.id=c.page_id "
            "WHERE p.slug=? ORDER BY c.position", (slug,))]
        return page, chunks

    conn = _db.connect()
    full_page, full_chunks = _snapshot(conn)
    # Wipe the page (chunks cascade), then reindex ONLY that file.
    conn.execute("DELETE FROM pages WHERE slug=?", (slug,)); conn.commit(); conn.close()

    _reindex.reindex_path(cfg, claim)

    conn = _db.connect()
    path_page, path_chunks = _snapshot(conn)
    conn.close()
    assert path_page is not None
    assert path_page["page_type"] == full_page["page_type"]
    assert json.loads(path_page["frontmatter"]) == json.loads(full_page["frontmatter"])
    assert path_chunks == full_chunks            # chunks parity, not just the page row


def test_reindex_path_ignores_non_indexable_file(atelier_env: Dict) -> None:
    # A non-indexable file (wrong suffix) must NOT get a pages row — else the next
    # full reindex would prune it (incremental != full). reindex_path no-ops it.
    vault = Path(_cl._vault_root())
    junk = vault / "raw" / "notes.txt"
    junk.parent.mkdir(parents=True, exist_ok=True)
    junk.write_text("not indexable\n")
    cfg = _config.load()
    _reindex.reindex_path(cfg, junk)
    conn = _db.connect()
    row = _db.fetchone(conn, "SELECT count(*) c FROM pages WHERE slug LIKE '%notes.txt'")
    conn.close()
    assert row["c"] == 0


def test_reindex_path_is_the_change_feed(atelier_env: Dict) -> None:
    # A capture writes markdown but does NOT auto-reindex (stale-until-reindex is
    # a deliberate system assumption — dream cadence + cold-DB fallback rely on
    # it). reindex_path is the opt-in change feed: after calling it, the write is
    # queryable with no full reindex.
    cap = _cap.capture(observation="obs bravo", why="w", rule="r",
                       working_dir="/Users/me/workspaces/lexio",
                       session_id="b", hook="Stop")
    cfg = _config.load()
    conn = _db.connect()
    before = _db.fetchone(conn, "SELECT count(*) c FROM pages WHERE page_type='claim'")["c"]
    conn.close()

    _api.reindex_path(cap["path"])              # the change feed, one file

    conn = _db.connect()
    after = _db.fetchone(conn, "SELECT count(*) c FROM pages WHERE page_type='claim'")["c"]
    conn.close()
    assert after == before + 1                  # the write is now visible


def test_routing_columns_present_and_indexed(atelier_env: Dict) -> None:
    _cap.capture(observation="obs charlie", why="w", rule="r",
                 working_dir="/Users/me/workspaces/lexio", session_id="c", hook="Stop")
    _api.reindex(space="gorae", full=True)
    conn = _db.connect()
    # table_xinfo (not table_info) lists generated columns.
    cols = {r[1] for r in conn.execute("PRAGMA table_xinfo(pages)")}
    assert {"kind", "domain", "ac_status", "surfacing"} <= cols
    # queryable via the indexed columns (not json_extract)
    n = conn.execute("SELECT count(*) c FROM pages "
                     "WHERE kind='claim' AND domain='operational'").fetchone()["c"]
    assert n >= 1
    idx = {r[1] for r in conn.execute("PRAGMA index_list(pages)")}
    conn.close()
    assert "idx_pages_kind_domain" in idx
