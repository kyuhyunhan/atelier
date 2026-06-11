"""atelier_recall — per-turn signal detector for the learnings domain.

Given the current user prompt and the working directory, return the
top-K most relevant learnings (accepted entries + principles +,
optionally, candidates). The hook adapter `signal-recall.sh` calls this
on every `UserPromptSubmit` and pipes the returned markdown block on
stdout so Claude Code injects it as additional_context for the upcoming
turn.

Stateless w.r.t. session — per-session dedup (don't push the same
learning twice in one session) is the hook's job, not the engine's.

Retrieval strategy (v0.2.1, simple but effective):
- FTS5 over chunks_fts joined onto pages, scoped to learning_*
  page_types.
- Per-page best chunk → rank.
- Boost: page.frontmatter project_hint OR target_project == current
  project → score × 2.
- Threshold: keep only hits with the engine's BM25 below 0 (smaller =
  better in FTS5's `rank`).
- Fall back to a filesystem scan when the index lacks the page_types
  (fresh installs).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ...index import parse as _parse
from ...search.resolver import C_RRF
from ...util import config as _config
from ...util import db as _db


def _vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


_DEFAULT_TYPES = ("learning_principle", "learning_accepted",
                  "learning_candidate")

# Post-fusion boost magnitudes (RFC 0002 P3). The resolver's fused score is a
# small POSITIVE RRF magnitude, and RRF scores are COMPRESSED by construction:
# rank-0 is 1/60 ≈ 0.01667, rank-1 is 1/61 ≈ 0.01639 — a gap of only ~0.00027.
# A whole mode-vote (1/60) added to one hit would vault it up ~60 ranks and
# obliterate fusion order; the gate (surfacing audit) proved this empirically.
# So a boost is scaled to the inter-rank GAP and expressed as "worth ~N rank
# positions near the top": a gentle nudge that refines order without overriding
# the modes' own agreement. The relative hierarchy (project > concept > principle)
# mirrors the pre-P3 BM25 constants (1.0 / 0.75 / 0.5).
_RANK_GAP = 1.0 / C_RRF - 1.0 / (C_RRF + 1)      # ≈ 0.00027, the rank0→rank1 step
_PROJECT_BOOST = 6.0 * _RANK_GAP                 # current-project preference
_CONCEPT_BOOST = 3.0 * _RANK_GAP                 # concept-overlap (P4: relational)
_PRINCIPLE_BOOST = 2.0 * _RANK_GAP               # principles edge out ties


def _facet_clause(facets: Optional[List[tuple]]) -> tuple:
    """SQL + params for optional facet filtering via the indexed learning_facets
    table (RFC 0001). `facets` is a list of (kind, value) pairs; empty/None adds
    nothing, so the default recall path is unchanged (project stays a *boost*,
    not a filter — see _boost). Used when a caller wants to hard-scope recall to
    an aspect/topic/project."""
    # Lowercase values to match the lowercased facet rows (reindex._facet_rows).
    pairs = [(k, v.lower()) for (k, v) in (facets or []) if k and v]
    sql = "".join(
        " AND EXISTS (SELECT 1 FROM learning_facets lf "
        "WHERE lf.page_id=p.id AND lf.kind=? AND lf.value=?)"
        for _ in pairs)
    params = [x for pair in pairs for x in pair]
    return sql, params


def _resolve_hits(query: str, types: List[str], limit: int,
                  facets: Optional[List[tuple]] = None) -> List[Dict[str, Any]]:
    """Hybrid retrieval via the resolver (RFC 0002 P3), shaped back into the
    `dict` hits the ranking pipeline expects.

    Replaces the old single-stage FTS query: the resolver fuses lexical + (when
    available) semantic by RRF, and we rehydrate each fused `Candidate` with its
    frontmatter so downstream boosts / entry_id dedup keep working. Facets are a
    post-fusion `WHERE EXISTS` filter on the fused page set (RFC §3) — the
    resolver's `Scope` deliberately doesn't know about facets.

    Lexical-only is the automatic degrade (semantic slot None when embeddings are
    off / Ollama down). A new main-DB connection per call mirrors the old
    `_fts_search`; the per-call `build_context` opens the vec sidecar only when a
    gateway resolves, so the `ATELIER_EMBED=off` path pays nothing extra."""
    from ...search.engine import Scope
    from ...search import resolver as _resolver

    try:
        conn = _db.connect()
    except Exception:                       # pragma: no cover
        return []
    ctx = None
    try:
        ctx = _resolver.build_context(conn)
        cands = _resolver.resolve(
            query, engine=ctx.engine,
            scope=Scope(page_types=tuple(types)),
            gateway=ctx.gateway, k=limit)
        if not cands:
            return []
        return _rehydrate(conn, cands, facets)
    except Exception:
        # A resolver/sidecar failure (e.g. a corrupt vectors.db) must degrade to
        # rank_hits' fs-scan fallback, never crash the per-prompt recall hook.
        # Mirrors search._resolve_search's resilience.
        return []
    finally:
        if ctx is not None:
            ctx.close()
        conn.close()


def _rehydrate(conn, cands, facets: Optional[List[tuple]]) -> List[Dict[str, Any]]:
    """Attach frontmatter to fused candidates, applying the facet post-filter.

    One `WHERE id IN (...)` over `pages` (frontmatter is `NOT NULL`), optionally
    AND-ed with the facet EXISTS clauses. We iterate the *fused candidate order*
    and look rows up by id — SQL `IN (...)` does not preserve order, and the fused
    order is the whole point. A candidate whose id is absent from the result was
    filtered out by a facet."""
    ids = [c.page_id for c in cands]
    placeholders = ",".join("?" * len(ids))
    facet_sql, facet_params = _facet_clause(facets)
    sql = (
        "SELECT p.id, p.slug, p.page_type, p.frontmatter "
        "FROM pages p WHERE p.id IN (" + placeholders + ") " + facet_sql
    )
    rows = {r["id"]: r for r in conn.execute(sql, [*ids, *facet_params])}
    out: List[Dict[str, Any]] = []
    for c in cands:
        r = rows.get(c.page_id)
        if r is None:                       # dropped by a facet filter
            continue
        out.append({
            "slug": c.slug,
            "page_type": c.page_type,
            "fm": json.loads(r["frontmatter"] or "{}"),
            "score": float(c.score),
            "snippet": c.snippet or "",
        })
    return out


def _fm_has_facet(fm: Dict[str, Any], kind: str, value: str) -> bool:
    """Frontmatter-side mirror of a learning_facets row, for the no-DB fallback.
    Case-insensitive, matching the lowercased facet rows / query side."""
    v = (value or "").lower()

    def _lc(x) -> str:
        return x.lower() if isinstance(x, str) else ""

    def _lc_list(x) -> list:
        return [e.lower() for e in x if isinstance(e, str)] if isinstance(x, list) else []

    if kind == "project":
        return v in (_lc(fm.get("target_project")), _lc(fm.get("project_hint")))
    if kind == "topic":
        return _lc(fm.get("target_topic")) == v
    if kind == "aspect":
        return v in _lc_list(fm.get("aspect"))
    if kind == "touches":
        return v in _lc_list(fm.get("touches"))
    return False


def _fs_scan(query: str, vault: Path, types: List[str],
             limit: int,
             facets: Optional[List[tuple]] = None) -> List[Dict[str, Any]]:
    """Fallback when the FTS index has no learning_* rows yet.

    Token-based match: hit counts when *any* token from the query
    appears in body or stringified frontmatter. Score is `+token_count`
    so multi-match entries rank higher (larger = better) — the same
    descending convention as the resolver's RRF score, so rank_hits can
    sort both paths identically.
    """
    tokens = [t.lower() for t in re.findall(r"\w+", query or "", re.UNICODE)]
    if not tokens:
        return []
    facet_pairs = [p for p in (facets or []) if p and p[1]]
    out: List[Dict[str, Any]] = []
    # (ptype, iterable-of-paths). Accepted learnings live in the flat notes/
    # store (RFC 0001) via store.iter_accepted_files.
    from . import store as _store
    roots: List[tuple[str, Any]] = []
    if "learning_principle" in types:
        roots.append(("learning_principle",
                      sorted((vault / "learnings" / "principles").rglob("*.md"))
                      if (vault / "learnings" / "principles").exists() else []))
    if "learning_accepted" in types:
        roots.append(("learning_accepted", _store.iter_accepted_files(vault)))
    if "learning_candidate" in types:
        roots.append(("learning_candidate",
                      sorted((vault / "learnings" / "candidates").rglob("*.md"))
                      if (vault / "learnings" / "candidates").exists() else []))
    for ptype, paths in roots:
        for p in paths:
            if is_noise(p.name):        # shared predicate (INDEX + TAXONOMY)
                continue
            text = p.read_text(encoding="utf-8")
            try:
                fm, body = _parse.split_frontmatter(text)
            except Exception:               # pragma: no cover
                continue
            if facet_pairs and not all(
                    _fm_has_facet(fm, k, v) for k, v in facet_pairs):
                continue
            haystack = (body + " " + str(fm)).lower()
            hits_in_doc = sum(1 for t in tokens if t in haystack)
            if hits_in_doc == 0:
                continue
            out.append({
                "slug": p.stem,
                "page_type": ptype,
                "fm": fm,
                # Larger = better. More token hits → higher score.
                "score": float(hits_in_doc),
                "snippet": body[:200].strip(),
                "path": str(p),
            })
            if len(out) >= limit:
                return out
    return out


# Slug separators — the one place concept strings are tokenized, shared by
# ranking (_boost) and the surfacing audit so the split can never drift.
_CONCEPT_SPLIT = re.compile(r"[\s\-_/]+")


def concept_tokens(fm: Dict[str, Any]) -> List[str]:
    """Ordered tokens of the concepts a learning is *about* (`touches` +
    `target_topic`), split on slug separators so `dependency-direction` matches
    a query word `dependency`. Deterministic — mirrors reindex's concept edges.
    Public: the surfacing audit shares this tokenizer.

    NOTE: `aspect` is deliberately NOT a concept token. aspect values are coarse,
    project-local buckets (client/server/cross-cutting) shared by many records;
    putting them in the probe recreates the coarse-bucket competition the facet
    redesign set out to break. aspect is for FACET FILTERING, not free-text recall.
    """
    concepts: List[str] = []
    raw = fm.get("touches")
    if isinstance(raw, list):
        concepts.extend(c for c in raw if isinstance(c, str))
    topic = fm.get("target_topic")
    if isinstance(topic, str):
        concepts.append(topic)
    toks: List[str] = []
    for c in concepts:
        toks.extend(t for t in _CONCEPT_SPLIT.split(c.lower()) if t)
    return toks


def _boost(hit: Dict[str, Any], project: Optional[str],
           query_tokens: frozenset = frozenset()) -> float:
    """Post-fusion boosts on the resolver's RRF score (RFC 0002 P3).

    The base `score` is now a POSITIVE RRF magnitude (larger = better), so each
    boost ADDS a fraction of a mode-vote — the inverse of the old BM25 path,
    which subtracted from a negative rank. Sorting is descending (see rank_hits).
    """
    score = hit["score"] or 0.0
    fm = hit.get("fm") or {}
    if project and (
        fm.get("target_project") == project
        or fm.get("project_hint") == project
    ):
        score += _PROJECT_BOOST
    # Concept-overlap: the learning is *about* something the query names, even if
    # the body doesn't lexically match. A hand-rolled stand-in for semantic recall
    # — kept post-fusion for P3 so concept-probe metrics don't regress before the
    # relational mode lands. P4: subsumed by the relational (concept-edge) mode.
    if query_tokens and (set(concept_tokens(fm)) & query_tokens):
        score += _CONCEPT_BOOST
    if hit["page_type"] == "learning_principle":
        score += _PRINCIPLE_BOOST
    hit["score"] = score
    return score


def _summarize_hit(vault: Path, hit: Dict[str, Any]) -> Dict[str, Any]:
    fm = hit.get("fm") or {}
    title = fm.get("title") or hit["slug"]
    target_project = fm.get("target_project") or fm.get("project_hint") or ""
    target_topic   = fm.get("target_topic") or ""
    snippet = hit.get("snippet") or ""
    return {
        "slug": hit["slug"],
        "page_type": hit["page_type"],
        "title": str(title),
        "project": target_project,
        "topic": target_topic,
        "snippet": snippet.replace("\n", " ").strip()[:240],
    }


def _render(hits: List[Dict[str, Any]], project: Optional[str],
            max_chars: int) -> str:
    if not hits:
        return ""
    lines = [f"## atelier — relevant memory" + (
        f" (project `{project}`)" if project else "")]
    lines.append("")
    for h in hits:
        bullet = (
            f"- **[{h['page_type'].replace('learning_', '')}] "
            f"{h['title']}** ({h['project'] or '-'}/{h['topic'] or '-'}): "
            f"{h['snippet']}"
        )
        lines.append(bullet)
    out = "\n".join(lines)
    if len(out) > max_chars:
        cut = out.rfind("\n", 0, max_chars - 32)
        cut = cut if cut > 0 else max_chars - 32
        out = out[:cut].rstrip() + "\n_(truncated)_\n"
    return out


# Generated projections (per-project / per-topic INDEX, taxonomy listings)
# are indexed as learning_* pages but are not content — they must never be
# surfaced as "relevant memory". Matched on the slug's bare stem so it works
# for both FTS slugs (`…/INDEX.md`) and fs-scan slugs (bare `INDEX`).
# The public entry point over this set is `is_noise` — extend the set there,
# never with ad-hoc filename checks at call sites.
_GENERATED_STEMS = frozenset({"INDEX", "TAXONOMY"})


def is_noise(slug: str) -> bool:
    """True for generated/navigational projections (INDEX, TAXONOMY) that must
    never surface as memory. Public: the single noise predicate shared by the
    ranking pipeline and the surfacing audit — if recall can never return a
    page, the audit must not probe it (it would be dark by construction).
    Accepts a full slug, a bare filename, or a bare stem (normalized below)."""
    name = slug.rsplit("/", 1)[-1]
    stem = name[:-3] if name.endswith(".md") else name
    return stem in _GENERATED_STEMS


def _dedup_by_entry_id(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """An accepted learning has exactly one file in the flat notes/ store
    (RFC 0001), but FTS can still return the same page via several matching
    chunks, and the FTS + fs-scan paths can overlap. Keep the first occurrence
    per entry_id (hits are pre-sorted, so that is the best-ranked one); hits
    without an entry_id pass through unchanged."""
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for h in hits:
        eid = (h.get("fm") or {}).get("entry_id")
        if eid is not None:
            if str(eid) in seen:
                continue
            seen.add(str(eid))
        out.append(h)
    return out


def rank_hits(query: str, project: Optional[str], types: List[str], *,
              top_k: int,
              relevance_threshold: Optional[float] = None,
              facets: Optional[List[tuple]] = None,
              vault: Optional[Path] = None) -> List[Dict[str, Any]]:
    """The shared ranking pipeline: FTS (→ fs fallback) → noise filter → boost
    → sort → dedup → top-K. Returns hits *with* their `fm` (so callers that need
    entry_id — e.g. the surfacing audit — can match), not rendered summaries.
    Single source of retrieval order, shared by recall() and surfacing. Pass
    `vault` to avoid a redundant config read when the caller already has it.
    `facets` (optional (kind, value) pairs) hard-scopes the result to the
    indexed facet table; `project` remains a ranking *boost*, not a filter.

    Scores are POSITIVE and sorted descending (larger = better) since P3 —
    the resolver's RRF magnitude and the fs-fallback's token count both follow
    this convention. `relevance_threshold`, if given, is a *floor* on that score
    (keep `>= threshold`), the inverse of the pre-P3 BM25 ceiling."""
    vault = vault if vault is not None else _vault_root()
    hits = _resolve_hits(query, types, limit=top_k * 4, facets=facets)
    if not hits:
        hits = _fs_scan(query, vault, types, limit=top_k * 4, facets=facets)

    # Drop generated projections (INDEX/TAXONOMY) regardless of source path —
    # the resolver path does not exclude them the way _fs_scan does.
    hits = [h for h in hits if not is_noise(h["slug"])]

    query_tokens = frozenset(
        t for t in re.findall(r"\w+", (query or "").lower(), re.UNICODE) if t
    )
    for h in hits:
        _boost(h, project, query_tokens)

    if relevance_threshold is not None:
        hits = [h for h in hits if h["score"] >= relevance_threshold]

    hits.sort(key=lambda h: h["score"], reverse=True)
    # Collapse the by-topic / by-project duplicate pair before truncating, so
    # a dropped duplicate never crowds a distinct learning out of the top-K.
    hits = _dedup_by_entry_id(hits)
    return hits[:top_k]


def recall(*, query: str,
           project: Optional[str] = None,
           top_k: int = 5,
           max_chars: int = 1500,
           include_candidates: bool = False,
           relevance_threshold: Optional[float] = None,
           aspect: Optional[str] = None,
           topic: Optional[str] = None,
           ) -> Dict[str, Any]:
    """Return top-K learnings relevant to `query` (one prompt's worth).

    `project` boosts the current project's learnings without excluding others.
    `aspect`/`topic` (optional) hard-scope the result via the indexed facet table
    — e.g. recall only lexio's `client`-aspect lessons."""
    vault = _vault_root()
    types = ["learning_principle", "learning_accepted"]
    if include_candidates:
        types.append("learning_candidate")

    # Empty query → no recall (the caller's prompt was empty; nothing to
    # match on, so the fallback scan would otherwise dump everything).
    if not (query or "").strip():
        return {"query": query, "project": project, "count": 0,
                "items": [], "markdown": ""}

    facets = [("aspect", aspect), ("topic", topic)]
    hits = rank_hits(query, project, types, top_k=top_k,
                     relevance_threshold=relevance_threshold,
                     facets=facets, vault=vault)
    summaries = [_summarize_hit(vault, h) for h in hits]
    markdown = _render(summaries, project, max_chars)

    return {
        "query": query,
        "project": project,
        "count": len(summaries),
        "items": summaries,
        "markdown": markdown,
    }
