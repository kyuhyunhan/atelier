"""Query-time synthesis evidence (RFC 0003 P5 / RFC 0002 P7) — the `think` layer.

gbrain's `think` composes a cited answer across retrieved results. atelier keeps
generation OFF the engine (the gateway is embeddings-only; a generation LLM would
put non-deterministic state next to the deterministic projection). So `think`
assembles the *evidence* — the ranked, cited passages plus an explicit gap signal
— and the CALLING agent (Claude, via MCP) composes the prose answer. The engine
provides retrieval + citations + gaps; the caller synthesizes. This honours the
RFC 0003 principle: the LLM produces an answer (ephemeral), never ingest state.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import recall as _recall


def think(*, query: str, project: Optional[str] = None, top_k: int = 5,
          include_candidates: bool = False) -> Dict[str, Any]:
    """Assemble a synthesis bundle over the hybrid resolver: cited evidence + gaps.

    Returns `{query, citations[], gaps[], result_count}` where each citation is
    `{slug, title, snippet, score}`. `gaps` is an explicit, honest signal of what
    the memory does NOT confidently cover — the thing a bare top-k list hides and
    a synthesised answer must own (gbrain's "what the brain doesn't know")."""
    types = ["learning_principle", "learning_accepted"]
    if include_candidates:
        types.append("learning_candidate")

    if not (query or "").strip():
        return {"query": query, "citations": [], "gaps": ["empty query"],
                "result_count": 0}

    hits = _recall.rank_hits(query, project, types, top_k=top_k)
    citations: List[Dict[str, Any]] = []
    for h in hits:
        fm = h.get("fm") or {}
        citations.append({
            "slug": h["slug"],
            "title": str(fm.get("title") or h["slug"]),
            "snippet": (h.get("snippet") or "").replace("\n", " ").strip()[:240],
            "score": round(float(h["score"]), 5),
        })

    gaps: List[str] = []
    if not hits:
        gaps.append("no relevant memory found for this query")
    elif len(hits) < top_k:
        gaps.append(f"thin coverage — only {len(hits)} relevant result(s) "
                    f"(requested {top_k})")
    return {"query": query, "citations": citations, "gaps": gaps,
            "result_count": len(hits)}
