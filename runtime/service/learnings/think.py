"""Query-time synthesis (RFC 0003 P5 / RFC 0002 P7) — the `think` layer.

gbrain's `think` composes a cited answer across retrieved results. atelier keeps
generation OFF the engine (the gateway is embeddings-only; a generation LLM would
put non-deterministic state next to the deterministic projection). So `think`
assembles the *evidence* — ranked, cited passages, each with a stable 1-based
index `n`, plus an explicit gap signal — and ships a fixed composition CONTRACT.
The CALLING agent (Claude, via MCP) composes the prose answer following the
contract. The engine provides deterministic retrieval + citations + gaps + the
contract; the caller synthesises the wording.

Consistency (RFC 0003 GP5, Pure B): the *facts* are deterministic (retrieval is
reproducible) and the *structure* is fixed (the contract). Wording flexes in the
caller's prose. `compose()` is a deterministic, non-LLM floor — pure string
assembly over a bundle — so headless callers still get a contract-conformant
cited answer, and it is the executable definition the tests gate on. This honours
the RFC 0003 principle: the LLM produces an answer (ephemeral), never ingest state.
"""
from __future__ import annotations

from typing import Any, Dict, List

from . import recall as _recall


# The fixed answer shape every caller composes to. Single source of truth: carried
# in the think() payload (so any caller has it inline) and referenced by the
# atelier_think MCP tool description (so Claude sees it at call time). `compose()`
# below is the deterministic, executable form of this contract.
SYNTHESIS_CONTRACT = """\
SYNTHESIS COMPOSITION CONTRACT (RFC 0003 GP5)
Compose a prose answer from a deterministic evidence bundle. The engine did
retrieval; you do wording. Obey exactly.

## Answer
Prose answering the query. EVERY factual claim ends with an inline [n] marker
(n = 1-based citation index). Never assert what the bundle does not support —
move it to Caveats.

## Caveats
One bullet per `gaps` entry — what memory does NOT confidently cover. Never
paper over a gap; never invent. Empty gaps -> "None.".

## Sources
One line per cited citation, index order: [n] slug — title. Only list citations
actually referenced. Zero citations -> Answer says memory has nothing on this
query, Caveats carries the gap, no fabricated source.

RULES: (1) no claim without [n]; (2) surface gaps, never invent; (3) never cite
an index absent from the bundle; (4) zero-coverage -> honest "no memory", not a guess.
"""


def think(*, query: str, project: str | None = None, top_k: int = 5,
          include_candidates: bool = False) -> Dict[str, Any]:
    """Assemble a synthesis bundle over the hybrid resolver: cited evidence,
    gaps, and the composition contract.

    Returns `{query, citations[], gaps[], contract, result_count}` where each
    citation is `{n, slug, title, snippet, score}` (`n` is a stable 1-based index
    over the deterministic rank order). `gaps` is an explicit, honest signal of
    what the memory does NOT confidently cover — the thing a bare top-k list hides
    and a synthesised answer must own (gbrain's "what the brain doesn't know")."""
    types = ["learning_principle", "learning_accepted"]
    if include_candidates:
        types.append("learning_candidate")

    if not (query or "").strip():
        return {"query": query, "citations": [], "gaps": ["empty query"],
                "contract": SYNTHESIS_CONTRACT, "result_count": 0}

    hits = _recall.rank_hits(query, project, types, top_k=top_k)
    citations: List[Dict[str, Any]] = []
    for idx, h in enumerate(hits, start=1):
        fm = h.get("fm") or {}
        citations.append({
            "n": idx,
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
            "contract": SYNTHESIS_CONTRACT, "result_count": len(hits)}


def compose(bundle: Dict[str, Any]) -> str:
    """The deterministic, non-LLM floor answer for a think() bundle — pure string
    assembly, no I/O, no generation. Same bundle -> byte-identical output. It is
    the executable form of SYNTHESIS_CONTRACT: a contract-conformant cited answer a
    headless caller can use as-is, while an LLM caller composes nicer flexed prose
    over the same evidence. Generates ZERO citation markers when there is no
    evidence (honest "no memory"), never fabricating a source."""
    citations = bundle.get("citations") or []
    gaps = bundle.get("gaps") or []
    out: List[str] = ["## Answer"]
    if not citations:
        out.append("Memory has nothing on this query.")
    else:
        for c in citations:
            claim = c.get("snippet") or c.get("title") or c["slug"]
            out.append(f"{claim} [{c['n']}]")
    out += ["", "## Caveats"]
    out += [f"- {g}" for g in gaps] if gaps else ["None."]
    out += ["", "## Sources"]
    out += [f"[{c['n']}] {c['slug']} — {c['title']}" for c in citations]
    return "\n".join(out)
