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
from ...util import config as _config
from ...util import db as _db


def _vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


_DEFAULT_TYPES = ("learning_principle", "learning_accepted",
                  "learning_candidate")
_PRINCIPLE_PROJECT_BOOST = 1.5


def _sanitize_fts_query(q: str) -> str:
    """FTS5 MATCH expressions are picky about punctuation. Strip down to
    word-class tokens and quote phrases so user prompts don't break."""
    tokens = re.findall(r"\w+", q, flags=re.UNICODE)
    if not tokens:
        return ""
    # Quote each token to avoid prefix/operator interpretation, OR them.
    return " OR ".join(f'"{t}"' for t in tokens[:24])


def _facet_clause(facets: Optional[List[tuple]]) -> tuple:
    """SQL + params for optional facet filtering via the indexed learning_facets
    table (RFC 0001). `facets` is a list of (kind, value) pairs; empty/None adds
    nothing, so the default recall path is unchanged (project stays a *boost*,
    not a filter — see _boost). Used when a caller wants to hard-scope recall to
    an aspect/topic/project."""
    pairs = [p for p in (facets or []) if p and p[1]]
    sql = "".join(
        " AND EXISTS (SELECT 1 FROM learning_facets lf "
        "WHERE lf.page_id=p.id AND lf.kind=? AND lf.value=?)"
        for _ in pairs)
    params = [x for pair in pairs for x in pair]
    return sql, params


def _fts_search(query: str, types: List[str], limit: int,
                facets: Optional[List[tuple]] = None) -> List[Dict[str, Any]]:
    safe = _sanitize_fts_query(query)
    if not safe:
        return []
    try:
        conn = _db.connect()
    except Exception:                       # pragma: no cover
        return []
    try:
        placeholders = ",".join("?" * len(types))
        facet_sql, facet_params = _facet_clause(facets)
        sql = (
            "SELECT p.slug, p.page_type, p.space, p.frontmatter, "
            "       f.rank AS score, "
            "       snippet(chunks_fts, 0, '', '', '...', 24) AS snip "
            "FROM chunks_fts f "
            "JOIN chunks c ON c.rowid=f.rowid "
            "JOIN pages  p ON p.id=c.page_id "
            "WHERE chunks_fts MATCH ? "
            "  AND p.page_type IN (" + placeholders + ") "
            + facet_sql +
            " ORDER BY f.rank "
            "LIMIT ?"
        )
        try:
            rows = list(conn.execute(sql, [safe, *types, *facet_params, limit]))
        except Exception:                   # SQLite FTS error
            return []
        out: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for r in rows:
            slug = r["slug"]
            if slug in seen:
                continue
            seen.add(slug)
            fm = json.loads(r["frontmatter"] or "{}")
            out.append({
                "slug": slug,
                "page_type": r["page_type"],
                "fm": fm,
                "score": float(r["score"] or 0.0),
                "snippet": r["snip"] or "",
            })
        return out
    finally:
        conn.close()


def _fm_has_facet(fm: Dict[str, Any], kind: str, value: str) -> bool:
    """Frontmatter-side mirror of a learning_facets row, for the no-DB fallback."""
    if kind == "project":
        return value in (fm.get("target_project"), fm.get("project_hint"))
    if kind == "topic":
        return fm.get("target_topic") == value
    if kind == "aspect":
        a = fm.get("aspect")
        return isinstance(a, list) and value in a
    if kind == "touches":
        t = fm.get("touches")
        return isinstance(t, list) and value in t
    return False


def _fs_scan(query: str, vault: Path, types: List[str],
             limit: int,
             facets: Optional[List[tuple]] = None) -> List[Dict[str, Any]]:
    """Fallback when the FTS index has no learning_* rows yet.

    Token-based match: hit counts when *any* token from the query
    appears in body or stringified frontmatter. Score is `-token_count`
    so multi-match entries rank higher (more negative = better).
    """
    tokens = [t.lower() for t in re.findall(r"\w+", query or "", re.UNICODE)]
    if not tokens:
        return []
    facet_pairs = [p for p in (facets or []) if p and p[1]]
    out: List[Dict[str, Any]] = []
    # (ptype, iterable-of-paths). Accepted learnings live in the flat notes/
    # store (RFC 0001) — store.iter_accepted_files spans it plus the legacy
    # by-topic tree during migration and excludes the by-project mirror.
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
            if "by-project" in p.parts:
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
                # Negative = better. More token hits → more negative.
                "score": -float(hits_in_doc),
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
    Public: the surfacing audit shares this tokenizer."""
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
    score = hit["score"] or 0.0
    fm = hit.get("fm") or {}
    if project and (
        fm.get("target_project") == project
        or fm.get("project_hint") == project
    ):
        # FTS rank is negative — multiplying by >1 amplifies the magnitude;
        # we instead subtract a constant to push it up the order.
        score -= 1.0
    # Concept-overlap: the learning is *about* something the query names, even
    # if the body doesn't lexically match. This is the retrieval payoff of the
    # concept index — surfacing by idea, not just by word. No LLM.
    if query_tokens and (set(concept_tokens(fm)) & query_tokens):
        score -= 0.75
    if hit["page_type"] == "learning_principle":
        score -= 0.5
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
    """An accepted learning lives on disk twice by design — the by-topic
    canonical and its by-project mirror share one entry_id. Keep the first
    occurrence per entry_id (hits are pre-sorted, so that is the best-ranked
    copy); hits without an entry_id pass through unchanged."""
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
    indexed facet table; `project` remains a ranking *boost*, not a filter."""
    vault = vault if vault is not None else _vault_root()
    hits = _fts_search(query, types, limit=top_k * 4, facets=facets)
    if not hits:
        hits = _fs_scan(query, vault, types, limit=top_k * 4, facets=facets)

    # Drop generated projections (INDEX/TAXONOMY) regardless of source path —
    # the FTS path does not exclude them the way _fs_scan does.
    hits = [h for h in hits if not is_noise(h["slug"])]

    query_tokens = frozenset(
        t for t in re.findall(r"\w+", (query or "").lower(), re.UNICODE) if t
    )
    for h in hits:
        _boost(h, project, query_tokens)

    if relevance_threshold is not None:
        hits = [h for h in hits if h["score"] <= relevance_threshold]

    hits.sort(key=lambda h: h["score"])
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
