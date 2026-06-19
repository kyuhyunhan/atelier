"""Search + relink for the learnings domain.

`search()` is a thin filter over the existing FTS index restricted to
the learnings/* page types. If a hit's source file is no longer present
(e.g. it has been retracted), it is skipped silently. A grep fallback
walks the filesystem directly when the FTS index has not been built yet
(common in fresh installs).

`relink()` updates the `links:` frontmatter field on an accepted
learning so the curator can attach wiki/entities/* or wiki/themes/*
references after the fact.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from ...index import parse as _parse
from ...util import config as _config
from ...util import db as _db


# RFC 0005 §7.1 — an operational learning is now a v7 `claim` page (its status is
# the `ac_status` field). Every status scope therefore also fetches `claim`
# pages; the ac_status post-filter (`_AC_FOR_STATUS`) narrows the claim pool to
# the requested lifecycle state. Legacy learning_* page_types stay in scope for a
# vault that predates the migration.
_STATUS_TO_TYPES = {
    "candidate": ("learning_candidate", "claim"),
    "accepted":  ("learning_accepted", "claim"),
    "archived":  ("learning_archived", "claim"),
    "any":       ("learning_candidate", "learning_accepted", "learning_archived",
                  "claim"),
}

# Which claim ac_status values count as each search status. None = no ac_status
# filter (the legacy pages carry the distinction in their page_type instead).
_AC_FOR_STATUS = {
    "candidate": ("pending",),
    "accepted":  ("passed",),
    "archived":  ("failed", "retracted"),
    "any":       ("pending", "passed", "failed", "retracted"),
}


def _vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _facet_clause(project: Optional[str], topic: Optional[str],
                  aspect: Optional[str]) -> tuple[str, List[Any]]:
    """SQL + params for facet filtering via the indexed `learning_facets` table
    (RFC 0001). Classification lives in facets, resolved here — not in the path
    and not in a Python frontmatter scan. The 'project' facet was populated from
    `target_project or project_hint`, preserving the old OR semantics."""
    # Facet values are stored lowercased (reindex._facet_rows); lowercase the
    # query side too so the exact `=` match is case-insensitive end to end.
    pairs: List[tuple[str, str]] = []
    if project:
        pairs.append(("project", project.lower()))
    if topic:
        pairs.append(("topic", topic.lower()))
    if aspect:
        pairs.append(("aspect", aspect.lower()))
    sql = "".join(
        " AND EXISTS (SELECT 1 FROM learning_facets lf "
        "WHERE lf.page_id=p.id AND lf.kind=? AND lf.value=?)"
        for _ in pairs)
    params: List[Any] = [x for pair in pairs for x in pair]
    return sql, params


def _grep_walk(root: Path, query: str,
               *, types: Iterable[str],
               project: Optional[str],
               topic: Optional[str],
               aspect: Optional[str],
               limit: int) -> List[Dict[str, Any]]:
    """Filesystem-side fallback when FTS hasn't indexed learnings yet. No DB, so
    facets are read straight from frontmatter here (the index is unavailable).
    Facet comparison delegates to recall._fm_has_facet so it is case-insensitive
    and identical to the DB / recall-fallback paths (no silent mismatch)."""
    from . import recall as _recall
    from . import store as _store
    rx = re.compile(re.escape(query), re.I) if query else None
    out: List[Dict[str, Any]] = []
    for p, fm, body, status in _walk_searchable(root, _store):
        if not any(status == t.removeprefix("learning_") for t in types):
            continue
        if project and not _recall._fm_has_facet(fm, "project", project):
            continue
        if topic and not _recall._fm_has_facet(fm, "topic", topic):
            continue
        if aspect and not _recall._fm_has_facet(fm, "aspect", aspect):
            continue
        if rx is not None and not rx.search(body) and not rx.search(str(fm)):
            continue
        out.append({
            "path": str(p),
            "slug": p.stem,
            "status": status,
            "project": fm.get("target_project") or fm.get("project_hint"),
            "topic": fm.get("target_topic"),
            "entry_id": fm.get("entry_id"),
            "captured_at": fm.get("captured_at"),
            "snippet": body[:240].strip(),
        })
        if len(out) >= limit:
            break
    return out


def _walk_searchable(root: Path, _store):
    """Yield (path, fm, body, status) for every searchable learning — RFC 0005
    §7.1 operational CLAIMS (status derived from ac_status) plus any legacy
    notes/candidates files still on disk (back-compat). One generator so the
    grep fallback sees the same pool the DB path indexes."""
    from . import claims_io as _claims
    seen: set = set()
    # v7 claims: ac_status passed → accepted, pending → candidate.
    _AC_TO_STATUS = {"passed": "accepted", "pending": "candidate"}
    for p in _claims.iter_claim_files(root):
        got = _claims.read_claim(p)
        if got is None:
            continue
        fm, body = got
        if str(fm.get("domain") or "") != "operational":
            continue
        status = _AC_TO_STATUS.get(str(fm.get("ac_status") or ""))
        if status is None:                       # archived/retracted: not listed
            continue
        seen.add(p.resolve())
        yield p, fm, body, status
    # legacy notes/candidates files (a vault that predates the claim migration).
    learnings_root = _store.learning_root(root)
    if not learnings_root.exists():
        return
    for p in sorted(learnings_root.rglob("*.md")):
        if p.resolve() in seen or p.name == "INDEX.md":
            continue
        try:
            fm, body = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        except Exception:                        # pragma: no cover
            continue
        yield p, fm, body, (fm.get("status") or "candidate")


def _hit_from_row(row) -> Dict[str, Any]:
    """One result row (slug, page_type, space, frontmatter) → the search hit
    shape. Shared by the resolver path and the facet-only listing so the two
    can never drift in shape."""
    import json as _json
    fm = _json.loads(row["frontmatter"] or "{}")
    return {
        "slug": row["slug"],
        "page_type": row["page_type"],
        "space": row["space"],
        "entry_id": fm.get("entry_id"),
        "project": fm.get("target_project") or fm.get("project_hint"),
        "topic": fm.get("target_topic"),
        "captured_at": fm.get("captured_at"),
    }


def _claim_ac_ok(frontmatter: str, allowed: Optional[tuple]) -> bool:
    """A claim page passes the status filter only when its ac_status is in the
    allowed set. Non-claim (legacy learning_*) pages are unaffected — their
    page_type already encodes the status, so they always pass here."""
    if allowed is None:
        return True
    import json as _json
    try:
        fm = _json.loads(frontmatter or "{}")
    except (TypeError, ValueError):              # pragma: no cover
        return True
    if fm.get("kind") != "claim":
        return True
    return str(fm.get("ac_status") or "") in allowed


def _resolve_search(query: str, types: tuple, *, project: Optional[str],
                    topic: Optional[str], aspect: Optional[str],
                    limit: int,
                    ac_allowed: Optional[tuple] = None) -> List[Dict[str, Any]]:
    """Text-query search via the hybrid resolver (RFC 0002 P3).

    The resolver fuses lexical + (when available) semantic by RRF, scoped to the
    status page_types. Facets (project/topic/aspect) are a post-fusion `WHERE
    EXISTS` filter on the fused page set — the resolver's `Scope` deliberately
    doesn't know about facets (RFC §3). Over-fetch (`limit*3`) before the facet
    filter so a restrictive facet still has fused depth to draw from."""
    from ...search.engine import Scope
    from ...search import resolver as _resolver
    from . import recall as _recall

    try:
        conn = _db.connect()
    except Exception:                       # pragma: no cover - uninitialized DB
        return []
    try:
        ctx = _resolver.build_context(conn)
        try:
            cands = _resolver.resolve(query, engine=ctx.engine,
                                      scope=Scope(page_types=tuple(types)),
                                      gateway=ctx.gateway, k=limit * 3)
        finally:
            ctx.close()
        if not cands:
            return []
        # Post-fusion facet filter via the same EXISTS clause recall uses.
        facet_sql, facet_params = _recall._facet_clause(
            [("project", project), ("topic", topic), ("aspect", aspect)])
        ids = [c.page_id for c in cands]
        ph = ",".join("?" * len(ids))
        rows = {r["id"]: r for r in conn.execute(
            "SELECT p.id, p.slug, p.page_type, p.space, p.frontmatter "
            "FROM pages p WHERE p.id IN (" + ph + ") " + facet_sql,
            [*ids, *facet_params])}
        out: List[Dict[str, Any]] = []
        for c in cands:                     # iterate fused order; IN() is unordered
            r = rows.get(c.page_id)
            if r is None:                   # dropped by a facet filter
                continue
            if not _claim_ac_ok(r["frontmatter"], ac_allowed):
                continue                    # claim in the wrong lifecycle state
            out.append(_hit_from_row(r))
            if len(out) >= limit:
                break
        return out
    finally:
        conn.close()


def _listing_scan(types: tuple, facet_sql: str, facet_params: List[Any],
                  limit: int,
                  ac_allowed: Optional[tuple] = None) -> List[Dict[str, Any]]:
    """Facet-only listing (no text query): a straight `pages` scan filtered by
    page_type + facets. NOT routed through the resolver — there is no query to
    fuse on. This is the 'list every accepted learning in project X' path."""
    try:
        conn = _db.connect()
    except Exception:                       # pragma: no cover
        return []
    try:
        placeholders = ",".join("?" * len(types))
        sql = ("SELECT p.slug, p.page_type, p.space, p.frontmatter "
               "FROM pages p WHERE p.page_type IN (" + placeholders + ") "
               + facet_sql + " LIMIT ?")
        seen: set[str] = set()
        out: List[Dict[str, Any]] = []
        for row in conn.execute(sql, [*types, *facet_params, limit * 3]):
            if row["slug"] in seen:
                continue
            if not _claim_ac_ok(row["frontmatter"], ac_allowed):
                continue
            seen.add(row["slug"])
            out.append(_hit_from_row(row))
            if len(out) >= limit:
                break
        return out
    finally:
        conn.close()


def search(*, query: str = "",
           status: str = "accepted",
           project: Optional[str] = None,
           topic: Optional[str] = None,
           aspect: Optional[str] = None,
           limit: int = 20) -> Dict[str, Any]:
    from ...search import fts as _fts
    vault = _vault_root()
    types = _STATUS_TO_TYPES.get(status, _STATUS_TO_TYPES["accepted"])
    ac_allowed = _AC_FOR_STATUS.get(status, _AC_FOR_STATUS["accepted"])
    # Sanitize to decide text-vs-listing: a raw prompt with punctuation that
    # reduces to empty is a facet-only listing, not a broken MATCH.
    match = _fts.sanitize_match(query) if query else ""

    try:
        if match:
            # Text query → hybrid resolver (RFC 0002 P3), facets post-filtered.
            hits = _resolve_search(query, types, project=project, topic=topic,
                                   aspect=aspect, limit=limit,
                                   ac_allowed=ac_allowed)
        else:
            # No text query → facet-only listing scan (untouched by P3).
            facet_sql, facet_params = _facet_clause(project, topic, aspect)
            hits = _listing_scan(types, facet_sql, facet_params, limit,
                                 ac_allowed=ac_allowed)
    except Exception:
        # Schema not initialized or pages table empty — fall through to grep.
        hits = []

    if not hits:
        # Mirror the text signal: a query that sanitized to empty is a facet-only
        # listing, so don't regex-match the raw punctuation in the fallback.
        hits = _grep_walk(vault, query if match else "",
                          types=types, project=project, topic=topic,
                          aspect=aspect, limit=limit)
    return {"count": len(hits), "items": hits, "vault": str(vault)}


# ── relink ─────────────────────────────────────────────────────────────────


def relink(*, slug: str, links: List[str],
           mode: str = "replace") -> Dict[str, Any]:
    """Update the `links:` array on an accepted learning.

    mode = "replace" (default) overwrites the existing list.
    mode = "merge"   appends and deduplicates.
    """
    if mode not in ("replace", "merge"):
        raise ValueError(f"unknown mode: {mode!r}")

    from . import store as _store
    vault = _vault_root()
    # Search by slug or entry_id in the flat notes/ store (RFC 0001).
    needle = slug.removesuffix(".md")
    target: Optional[Path] = None
    for p in _store.iter_accepted_files(vault):
        if p.stem == needle:
            target = p
            break
        fm, _ = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        if str(fm.get("entry_id")) == slug:
            target = p
            break
    if target is None:
        raise FileNotFoundError(f"no accepted learning matches {slug!r}")

    fm, body = _parse.split_frontmatter(target.read_text(encoding="utf-8"))
    existing = list(fm.get("links") or [])
    new_links = list(dict.fromkeys((existing if mode == "merge" else []) + links))
    fm = dict(fm)
    fm["links"] = new_links
    # v7 nodes carry a content_hash over their frontmatter — re-derive it on
    # mutation so the relink doesn't drift the projection's self-consistency.
    if "content_hash" in fm:
        from . import claims_io as _cio
        fm.pop("content_hash", None)
        fm["content_hash"] = _cio._content_hash(fm)

    serialized = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    target.write_text(f"---\n{serialized}\n---\n{body}", encoding="utf-8")
    # One file, no mirror (RFC 0001).
    return {"path": str(target), "links": new_links}
