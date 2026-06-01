"""Reindex orchestrator: crawl → parse → upsert pages/chunks/links/entities."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from ..util import config, db, logging as log
from . import classify, crawl, entities, linker, parse


@dataclass
class ReindexStats:
    space: str
    pages_seen: int = 0
    pages_changed: int = 0
    chunks_written: int = 0
    links_written: int = 0
    entities_upserted: int = 0


def reindex_space(
    cfg: config.Config,
    space_name: str,
    full: bool = False,
    incremental: bool = True,
) -> ReindexStats:
    space = cfg.space(space_name)
    if not space.local.exists():
        raise FileNotFoundError(f"space {space_name!r} local path missing: {space.local}")
    log.info("reindex.start", space=space_name, root=str(space.local), full=full)

    conn = db.connect()
    stats = ReindexStats(space=space_name)

    try:
        with conn:
            # Pass 1: upsert pages + chunks
            for item in crawl.crawl_space(conn, space_name, space.local, full=full):
                stats.pages_seen += 1
                parsed = parse.parse_file(item.path)
                ptype = classify.classify(space_name, item.slug, parsed.frontmatter)
                page_id = _upsert_page(conn, space_name, item.slug, ptype, parsed.frontmatter,
                                       item.content_hash, item.mtime)
                _replace_chunks(conn, page_id, parsed.chunks)
                stats.pages_changed += 1
                stats.chunks_written += len(parsed.chunks)
                if ptype == "entity":
                    entities.upsert_entity_from_page(conn, item.slug, parsed.frontmatter)
                    stats.entities_upserted += 1

            # Pass 2: rebuild links (needs all pages present for resolution)
            stats.links_written += _rebuild_links(conn, space_name, cfg)

            # Pass 3: clean up
            entities.prune_orphan_entities(conn)

            db.set_meta(conn, f"reindex.{space_name}.last_run", _now())

        log.info("reindex.done", **vars(stats))
        return stats
    finally:
        conn.close()


def _now() -> str:
    import datetime as _dt
    return _dt.datetime.utcnow().isoformat() + "Z"


def _upsert_page(
    conn: sqlite3.Connection,
    space: str,
    slug: str,
    ptype: str,
    fm: dict,
    content_hash: str,
    mtime: float,
) -> int:
    fm_json = json.dumps(fm, ensure_ascii=False, default=str)
    row = conn.execute(
        "SELECT id FROM pages WHERE space=? AND slug=?", (space, slug)
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE pages SET page_type=?, frontmatter=?, content_hash=?, mtime=? WHERE id=?",
            (ptype, fm_json, content_hash, mtime, row["id"]),
        )
        return row["id"]
    cur = conn.execute(
        "INSERT INTO pages(slug, space, page_type, frontmatter, content_hash, mtime) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (slug, space, ptype, fm_json, content_hash, mtime),
    )
    return cur.lastrowid


def _replace_chunks(conn: sqlite3.Connection, page_id: int, chunks) -> None:
    conn.execute("DELETE FROM chunks WHERE page_id=?", (page_id,))
    for c in chunks:
        conn.execute(
            "INSERT INTO chunks(page_id, position, heading_path, text) VALUES (?, ?, ?, ?)",
            (page_id, c.position, c.heading_path, c.text),
        )


def _rebuild_links(conn: sqlite3.Connection, space: str, cfg: config.Config) -> int:
    """Full link rebuild for the given space. Resolves targets across all spaces
    and, on a slug miss, against canonical entity aliases — so the same entity
    referenced from wiki, workshop and learnings collapses to one node."""
    # Build slug→page_id lookup across all spaces (cross-space links allowed).
    by_space: dict[str, dict[str, int]] = {}
    for r in conn.execute("SELECT id, space, slug FROM pages"):
        by_space.setdefault(r["space"], {})[r["slug"]] = r["id"]

    alias_index = _build_alias_index(conn, by_space)

    n = 0
    pages = list(conn.execute(
        "SELECT id, slug FROM pages WHERE space=?", (space,)
    ))
    for p in pages:
        conn.execute("DELETE FROM links WHERE from_page=?", (p["id"],))
        body = "\n".join(
            r["text"] for r in conn.execute(
                "SELECT text FROM chunks WHERE page_id=? ORDER BY position", (p["id"],)
            )
        )
        for link in linker.extract_links(body, default_space=space):
            target_id = _resolve(by_space, link.to_space, link.to_slug, alias_index)
            conn.execute(
                "INSERT INTO links(from_page, to_target, to_page_id, link_type) "
                "VALUES (?, ?, ?, ?)",
                (p["id"], link.to_target, target_id, link.link_type),
            )
            n += 1
    return n


def _norm(s: str) -> str:
    return s.strip().lower()


def _candidate_slugs(to_slug: str) -> list[str]:
    """v3 shorthand wikilink forms:
      [[themes/foo]]              → wiki/themes/foo.md
      [[entities/foo]]            → wiki/entities/foo.md
      [[raw/path/to/file.md]]     → raw/path/to/file.md  (already exact)
      [[wiki/themes/foo.md]]      → exact
    """
    candidates = [to_slug]
    if not to_slug.endswith(".md"):
        candidates.append(to_slug + ".md")
    if not to_slug.startswith(("raw/", "wiki/", "products/", "notes/", "logs/",
                               "workshop/", "learnings/")):
        candidates.append("wiki/" + to_slug)
        if not to_slug.endswith(".md"):
            candidates.append("wiki/" + to_slug + ".md")
    return candidates


def _build_alias_index(conn: sqlite3.Connection,
                       by_space: dict) -> dict[str, int]:
    """Map normalized entity name/alias → page_id of the canonical entity page.

    Lets a bare `[[Some Person]]` / `[[김현주]]` (which the slug-form candidates
    miss, since they don't probe wiki/entities/) bind to the canonical entity
    regardless of which domain references it."""
    index: dict[str, int] = {}
    for r in conn.execute("SELECT canonical_slug, aliases FROM entities"):
        slug = r["canonical_slug"]
        pid = next((m[slug] for m in by_space.values() if slug in m), None)
        if pid is None:
            continue
        # the entity's own basename: wiki/entities/김현주.md → 김현주
        index.setdefault(_norm(slug.split("/")[-1].rsplit(".", 1)[0]), pid)
        try:
            aliases = json.loads(r["aliases"] or "[]")
        except (TypeError, ValueError):
            aliases = []
        for a in aliases:
            if isinstance(a, str) and a.strip():
                index.setdefault(_norm(a), pid)
    return index


def _resolve(by_space: dict, to_space: str, to_slug: str,
             alias_index: Optional[dict] = None) -> Optional[int]:
    candidates = _candidate_slugs(to_slug)
    # Try the named space first, then every other space. Single-vault has one
    # space (no-op); this makes cross-space links resolve in any config.
    ordered = [by_space.get(to_space, {})]
    ordered += [m for s, m in by_space.items() if s != to_space]
    for space_map in ordered:
        for c in candidates:
            if c in space_map:
                return space_map[c]
    # Alias fallback: same canonical entity referenced from any domain.
    if alias_index:
        basename = to_slug.split("/")[-1].rsplit(".", 1)[0]
        for key in (_norm(to_slug), _norm(basename)):
            pid = alias_index.get(key)
            if pid is not None:
                return pid
    return None


def canonical_spaces(cfg: config.Config) -> list[str]:
    """The deduplicated set of space names to index/compare.

    Under the single-vault model two pseudo-spaces (vault-librarian and
    vault-builder) share the same local path. Treating them as distinct would
    double-count pages (a `pages.slug` collision on write, phantom drift on
    read). Dedupe by resolved local path and keep the lexicographically-first
    name as canonical. Legacy two-space configs have distinct paths, so both
    survive unchanged.

    Single source of truth shared by `reindex_all` (write) and the doctor's
    D2 filesystem-drift check (read) — the two MUST agree or drift reappears.
    """
    seen_paths: dict[str, str] = {}
    for name, sp in cfg.spaces.items():
        key = str(sp.local.resolve())
        if key not in seen_paths or name < seen_paths[key]:
            seen_paths[key] = name
    return sorted(seen_paths.values())


def reindex_all(cfg: config.Config, full: bool = False) -> list[ReindexStats]:
    return [reindex_space(cfg, name, full=full) for name in canonical_spaces(cfg)]
