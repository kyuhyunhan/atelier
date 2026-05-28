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


def _fts_search(query: str, types: List[str], limit: int) -> List[Dict[str, Any]]:
    safe = _sanitize_fts_query(query)
    if not safe:
        return []
    try:
        conn = _db.connect()
    except Exception:                       # pragma: no cover
        return []
    try:
        placeholders = ",".join("?" * len(types))
        sql = (
            "SELECT p.slug, p.page_type, p.space, p.frontmatter, "
            "       f.rank AS score, "
            "       snippet(chunks_fts, 0, '', '', '...', 24) AS snip "
            "FROM chunks_fts f "
            "JOIN chunks c ON c.rowid=f.rowid "
            "JOIN pages  p ON p.id=c.page_id "
            "WHERE chunks_fts MATCH ? "
            "  AND p.page_type IN (" + placeholders + ") "
            "ORDER BY f.rank "
            "LIMIT ?"
        )
        try:
            rows = list(conn.execute(sql, [safe, *types, limit]))
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


def _fs_scan(query: str, vault: Path, types: List[str],
             limit: int) -> List[Dict[str, Any]]:
    """Fallback when the FTS index has no learning_* rows yet.

    Token-based match: hit counts when *any* token from the query
    appears in body or stringified frontmatter. Score is `-token_count`
    so multi-match entries rank higher (more negative = better).
    """
    tokens = [t.lower() for t in re.findall(r"\w+", query or "", re.UNICODE)]
    if not tokens:
        return []
    out: List[Dict[str, Any]] = []
    roots: List[tuple[str, Path]] = []
    if "learning_principle" in types:
        roots.append(("learning_principle", vault / "learnings" / "principles"))
    if "learning_accepted" in types:
        roots.append(("learning_accepted", vault / "learnings" / "accepted"))
    if "learning_candidate" in types:
        roots.append(("learning_candidate", vault / "learnings" / "candidates"))
    for ptype, root in roots:
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.md")):
            if p.name == "INDEX.md":
                continue
            if "by-project" in p.parts:
                continue
            text = p.read_text(encoding="utf-8")
            try:
                fm, body = _parse.split_frontmatter(text)
            except Exception:               # pragma: no cover
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


def _boost(hit: Dict[str, Any], project: Optional[str]) -> float:
    score = hit["score"] or 0.0
    fm = hit.get("fm") or {}
    if project and (
        fm.get("target_project") == project
        or fm.get("project_hint") == project
    ):
        # FTS rank is negative — multiplying by >1 amplifies the magnitude;
        # we instead subtract a constant to push it up the order.
        score -= 1.0
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


def recall(*, query: str,
           project: Optional[str] = None,
           top_k: int = 5,
           max_chars: int = 1500,
           include_candidates: bool = False,
           relevance_threshold: Optional[float] = None,
           ) -> Dict[str, Any]:
    """Return top-K learnings relevant to `query` (one prompt's worth)."""
    vault = _vault_root()
    types = ["learning_principle", "learning_accepted"]
    if include_candidates:
        types.append("learning_candidate")

    # Empty query → no recall (the caller's prompt was empty; nothing to
    # match on, so the fallback scan would otherwise dump everything).
    if not (query or "").strip():
        return {"query": query, "project": project, "count": 0,
                "items": [], "markdown": ""}

    hits = _fts_search(query, types, limit=top_k * 4)
    if not hits:
        hits = _fs_scan(query, vault, types, limit=top_k * 4)

    for h in hits:
        _boost(h, project)

    if relevance_threshold is not None:
        hits = [h for h in hits if h["score"] <= relevance_threshold]

    hits.sort(key=lambda h: h["score"])
    hits = hits[:top_k]
    summaries = [_summarize_hit(vault, h) for h in hits]
    markdown = _render(summaries, project, max_chars)

    return {
        "query": query,
        "project": project,
        "count": len(summaries),
        "items": summaries,
        "markdown": markdown,
    }
