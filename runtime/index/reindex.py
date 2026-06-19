"""Reindex orchestrator: crawl → parse → upsert pages/chunks/links/entities."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from ..structure import resolver as _structure
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
    chunks_embedded: int = 0     # semantic substrate (RFC 0002 P2): cache misses paid
    chunks_reused: int = 0       # cache hits — no gateway work


# Sentinel: "resolve the embedding gateway from config" (auto-when-reachable).
# Pass None to skip the embed pass, or an explicit gateway (tests, tools).
_AUTO_GATEWAY = object()


def reindex_space(
    cfg: config.Config,
    space_name: str,
    full: bool = False,
    incremental: bool = True,
    embed_gateway=_AUTO_GATEWAY,
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
                # Structured files (.yaml/.yml/.json) are a `data` page by format
                # (RFC 0002 P1b) — a file-format fact, kept out of the schema-driven
                # `classify` (which keys off overlay md path patterns, hard-rule #3).
                if parse.is_data_path(item.path):
                    parsed = parse.parse_data_file(item.path)
                    ptype = "data"
                else:
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

        # Pass 4 (RFC 0002 P2): semantic substrate — embed stale chunks into the
        # vectors.db sidecar. Strictly optional: no gateway (provider down,
        # ATELIER_EMBED=off, sqlite-vec missing) → skip with a log line; the
        # lexical index above is already complete and committed.
        gw = (_resolve_gateway(cfg) if embed_gateway is _AUTO_GATEWAY
              else embed_gateway)
        if gw is not None:
            try:
                _embed_pass(conn, gw, stats)
            except Exception as e:
                # A provider that drops MID-pass must not fail a reindex whose
                # lexical passes already committed. Completed embed batches are
                # durable (streamed commits); the next reindex resumes the rest.
                log.info("reindex.embed_aborted", error=str(e))

        log.info("reindex.done", **vars(stats))
        return stats
    finally:
        conn.close()


def _resolve_gateway(cfg: config.Config):
    """Auto-when-reachable: a live gateway from the `embedding:` config block,
    or None. Import is local so atelier without the semantic extra never pays
    for (or breaks on) the AI layer at reindex time."""
    from ..ai import gateway as _gw
    return _gw.from_config(_gw.settings_from(cfg.raw))


def _embed_pass(conn: sqlite3.Connection, gw, stats: ReindexStats) -> None:
    from ..search.engine.vecstore import VecStore
    store = VecStore.open(gateway_signature=gw.signature, dim=gw.dim)
    if store is None:
        log.info("reindex.embed_skipped", reason="sqlite-vec unavailable")
        return
    try:
        s = store.sync(conn, gw)
        stats.chunks_embedded = s.embedded
        stats.chunks_reused = s.reused
        log.info("reindex.embedded", embedded=s.embedded, reused=s.reused,
                 total=s.total)
    finally:
        store.close()


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
        "SELECT id, slug, page_type, frontmatter FROM pages WHERE space=?", (space,)
    ))
    for p in pages:
        conn.execute("DELETE FROM links WHERE from_page=?", (p["id"],))
        body = "\n".join(
            r["text"] for r in conn.execute(
                # Exclude the synthetic frontmatter chunk (RFC 0002 P1a): it is
                # for FTS only, never body — link extraction must not parse a
                # frontmatter value as a wikilink.
                "SELECT text FROM chunks WHERE page_id=? "
                "AND (heading_path IS NULL OR heading_path != ?) ORDER BY position",
                (p["id"], parse.FRONTMATTER_HEADING),
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
        # Concept edges — a learning becomes a node in the *concept* graph so
        # cross-project learnings that share an idea connect (index by idea, not
        # folder). Deterministic: derived from frontmatter, never an LLM.
        # RFC 0005 §7.1 — an operational CLAIM is the v7 form of an accepted
        # learning, so it earns the same concept-edge + facet projection the
        # legacy learning_* pages got (so recall/search filter it by facet).
        _is_op_claim = (p["page_type"] == "claim"
                        and _claim_is_operational(p["frontmatter"]))
        if (p["page_type"] or "").startswith("learning_") or _is_op_claim:
            try:
                fm = json.loads(p["frontmatter"] or "{}")
            except (TypeError, ValueError):      # pragma: no cover
                fm = {}
            for concept in _concept_targets(fm):
                target_id = _resolve(by_space, space, concept, alias_index)
                conn.execute(
                    "INSERT INTO links(from_page, to_target, to_page_id, link_type) "
                    "VALUES (?, ?, ?, ?)",
                    (p["id"], concept, target_id, "concept"),
                )
                n += 1
            # Facet index (RFC 0001) — clear-and-repopulate per page so a re-run
            # is idempotent. Classification the resolver filters on at query time.
            conn.execute("DELETE FROM learning_facets WHERE page_id=?", (p["id"],))
            for kind, value in _facet_rows(fm):
                conn.execute(
                    "INSERT INTO learning_facets(page_id, kind, value) "
                    "VALUES (?, ?, ?)",
                    (p["id"], kind, value),
                )
    return n


def _claim_is_operational(frontmatter_json) -> bool:
    """True for a v7 claim whose domain is operational (the accepted-learning
    form). Tolerant of a malformed frontmatter blob."""
    try:
        fm = json.loads(frontmatter_json or "{}")
    except (TypeError, ValueError):              # pragma: no cover
        return False
    return isinstance(fm, dict) and str(fm.get("domain") or "") == "operational"


def _concept_targets(fm: dict) -> list[str]:
    """The concept edges a learning contributes: its explicit `touches` plus
    its `target_topic`. Deduplicated, order-stable, no LLM, no body re-parse
    (body `[[...]]` are already extracted as `wikilink` edges). `aspect` is NOT
    a concept edge — it is a coarse project-local facet for filtering, not a
    free-text recall concept (RFC 0001)."""
    items: list[str] = []
    raw = fm.get("touches")
    if isinstance(raw, list):
        items.extend(raw)
    topic = fm.get("target_topic")
    if isinstance(topic, str):
        items.append(topic)
    out: list[str] = []
    seen: set[str] = set()
    for c in items:
        if isinstance(c, str) and c.strip():
            key = c.strip()
            if key.lower() not in seen:
                seen.add(key.lower())
                out.append(key)
    return out


def _facet_rows(fm: dict) -> list[tuple[str, str]]:
    """The (kind, value) facet rows a learning contributes (RFC 0001).

    Classification the resolver filters on at query time, projected from
    frontmatter — never a folder, never an LLM. Deduplicated within a kind,
    order-stable. Kinds:
      project ← target_project | project_hint   (single, project-local)
      aspect  ← aspect[]                          (many,   project-local)
      topic   ← target_topic                      (single, global, optional)
      touches ← touches[]                          (many,   global concepts)
    """
    rows: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(kind: str, value) -> None:
        if isinstance(value, str) and value.strip():
            # Store lowercased: facet values are matched with exact `=` (SQLite is
            # case-sensitive by default), and the rest of the system normalizes to
            # lowercase (_slugify, concept tokens). The query side lowercases too.
            norm = value.strip().lower()
            key = (kind, norm)
            if key not in seen:
                seen.add(key)
                rows.append((kind, norm))

    _add("project", fm.get("target_project") or fm.get("project_hint"))
    aspect = fm.get("aspect")
    if isinstance(aspect, list):
        for a in aspect:
            _add("aspect", a)
    _add("topic", fm.get("target_topic"))
    touches = fm.get("touches")
    if isinstance(touches, list):
        for t in touches:
            _add("touches", t)
    return rows


def _norm(s: str) -> str:
    return s.strip().lower()


# Slug-resolution prefix data is single-sourced from schema/data/structure.yaml
# via the resolver (RFC 0005 P1). These module names are kept (and re-exported)
# so `_candidate_slugs` and the resolver-parity tests reference one in-process
# copy of the canonical data, not a second hand-maintained literal.
#
# _KNOWN_SLUG_PREFIXES — top-level slug prefixes; a `to_slug` already starting
# with one is a full path, not a bare shorthand. _SHORTHAND_BASES — bare-shorthand
# expansion bases, tried in order. _PREFIX_ALIASES — RFC 0003 rename aliasing
# (raw/<->provenance/, wiki/<->graph/, learnings/<->provenance/learning/) so the
# `git mv` never dangles the ~980 explicit [[raw/...]]/[[wiki/...]] body links.
_KNOWN_SLUG_PREFIXES = _structure.known_prefixes()
_SHORTHAND_BASES = _structure.shorthand_bases()
_PREFIX_ALIASES = _structure.prefix_aliases()


def _candidate_slugs(to_slug: str) -> list[str]:
    """v3 shorthand wikilink forms:
      [[entities/foo]]            → graph/entities/foo.md (or wiki/entities/foo.md)
      [[themes/foo]]              → graph/themes/foo.md   (or wiki/themes/foo.md)
      [[provenance/path/file.md]] → exact (already prefixed)
      [[wiki/themes/foo.md]]      → exact
    """
    candidates = [to_slug]
    if not to_slug.endswith(".md"):
        candidates.append(to_slug + ".md")
    for old, new in _PREFIX_ALIASES.items():
        if to_slug.startswith(old):
            alias = new + to_slug[len(old):]
            candidates.append(alias)
            if not alias.endswith(".md"):
                candidates.append(alias + ".md")
    if not to_slug.startswith(_KNOWN_SLUG_PREFIXES):
        for base in _SHORTHAND_BASES:
            candidates.append(base + to_slug)
            if not to_slug.endswith(".md"):
                candidates.append(base + to_slug + ".md")
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
