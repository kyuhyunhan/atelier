"""RFC 0005 §6 — recall over v7 CLAIM nodes (the atomic knowledge graph).

This is the surfacing-aware recall path that replaces the legacy `learning_*`
facet model (recall.py) for v7 claims. It implements the §6 formula:

    recall = gate(surfacing) × domain_prior(context) × vector_relevance × sensitivity_gate

over the flat `claim` page_type (kind: claim, schema_version >= 7), reusing the
existing lexical+vector RRF fusion (`recall._resolve_hits`) for the
`vector_relevance` term and layering the three new factors on top.

The factors (RFC 0005 §6):

- **surfacing ladder** `query ⊂ proactive ⊂ always` is a static eligibility
  ladder; push at recall is *context-scoped* by tier:
    - **on-query (T2)** — universal: any claim is eligible, the domain prior is
      IGNORED (a deliberate question reaches anything). `gate` = membership only.
    - **proactive (T1)** — per-turn push: claims with `surfacing` ∈
      {proactive, always}, RANKED by the domain prior for the active context.
    - **always (T0)** — unconditional within domain scope, a small HARD-CAPPED
      budget. Only `surfacing: always` claims, capped at `T0_CAP`.
  Because the ladder is a subset chain, a higher tier's eligibility set
  *contains* the lower one: an `always` claim is eligible at T1 and T2 too.

- **domain_prior(context)** — from the working dir / project. A coding session
  ranks `operational`/current-project HIGH, `knowledge` MID, `personal` LOW.
  Applied multiplicatively at T1 (and as a tie-nudge at T0); IGNORED at T2.

- **sensitivity_gate** — `sensitivity: private` claims are NEVER pushed
  proactively (T1/T0). They are reachable ONLY by explicit on-query (T2). This
  is a HARD gate (factor 0 at T1/T0), not a ranking penalty.

The result is the same `dict` hit shape recall.py produces, so the renderer and
hook adapter are unchanged.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import recall as _recall

# ── tiers ────────────────────────────────────────────────────────────────────
# The surfacing ladder is a SUBSET chain: query ⊂ proactive ⊂ always. A claim's
# `surfacing` field names the HIGHEST tier it may push at; eligibility at a tier
# means the claim's level is >= that tier on the ladder.
TIER_QUERY = "query"            # T2 — on-query, universal
TIER_PROACTIVE = "proactive"    # T1 — per-turn push, ranked by prior
TIER_ALWAYS = "always"          # T0 — unconditional, capped

# Ladder rank: a higher number contains the lower ones.
_LADDER = {TIER_QUERY: 0, TIER_PROACTIVE: 1, TIER_ALWAYS: 2}

# T0 hard budget cap (RFC 0005 §6: "T0 has a hard budget cap"). always-inject is
# the most expensive surface (it is paid on EVERY turn unconditionally), so it is
# the most tightly bounded. The cap is a count, applied AFTER ranking.
T0_CAP = 3


# ── domain prior ──────────────────────────────────────────────────────────────
# RFC 0005 §6: "coding session → operational/current-project high, knowledge mid,
# personal low". The prior is a multiplicative weight on the fused relevance. It
# is keyed by the claim's `domain` field; the current-project match is an
# additional HIGH bump on top, because §6 lists "operational/current-project"
# together as the top band.
#
# Priors are >1 for "boost", <1 for "dampen", 1.0 neutral. They are deliberately
# gentle (within ~1 order of magnitude) so a strong vector match can still beat a
# domain-favoured weak one — the prior RANKS within a tier, it does not silo.
_CODING_PRIOR: Dict[str, float] = {
    "operational": 2.0,     # operational learnings are what a coding turn wants
    "knowledge":   1.0,     # reference knowledge: neutral / mid
    "inbox":       1.0,     # undetermined-domain captures: mid
    "workshop":    1.5,     # project/workshop material: high-ish
    "personal":    0.25,    # personal claims: low in a coding context
}
_DEFAULT_PRIOR = 1.0        # an unknown domain is treated as mid (neutral)
_CURRENT_PROJECT_PRIOR = 2.0  # claim.project == active project → top band


def domain_prior(domain: Optional[str], *, project_match: bool) -> float:
    """The §6 context prior for a coding session. `domain` is the claim's domain
    field; `project_match` is True when the claim's `project` equals the active
    project. Both bands ("operational" and "current-project") are HIGH, and they
    compound when a claim is both."""
    base = _CODING_PRIOR.get((domain or "").lower(), _DEFAULT_PRIOR)
    return base * (_CURRENT_PROJECT_PRIOR if project_match else 1.0)


# ── factors ───────────────────────────────────────────────────────────────────


def surfacing_level(fm: Dict[str, Any]) -> str:
    """The claim's declared surfacing tier, defaulting to the most restrictive
    (`query`) when absent/invalid — an un-tagged claim is on-query-only, never
    silently pushed."""
    s = fm.get("surfacing")
    return s if s in _LADDER else TIER_QUERY


def gate(level: str, tier: str) -> bool:
    """surfacing eligibility: is a claim whose declared level is `level` eligible
    to surface at the requested `tier`? True iff the claim's level reaches the
    tier on the ladder (query ⊂ proactive ⊂ always)."""
    return _LADDER.get(level, 0) >= _LADDER.get(tier, 0)


def sensitivity_gate(fm: Dict[str, Any], tier: str) -> bool:
    """HARD gate: `sensitivity: private` claims are NEVER pushed at T1/T0. They
    are reachable ONLY by explicit on-query (T2). Returns True when the claim may
    pass at this tier."""
    if tier == TIER_QUERY:
        return True                         # explicit query reaches anything
    return (fm.get("sensitivity") or "").lower() != "private"


# ── scorer ────────────────────────────────────────────────────────────────────


def score_claim(hit: Dict[str, Any], *, tier: str, project: Optional[str]) -> float:
    """The §6 product for one fused claim hit:

        gate(surfacing) × domain_prior(context) × vector_relevance × sensitivity_gate

    Returns 0.0 when either hard gate (surfacing eligibility, sensitivity) blocks
    the claim — a zero is dropped by the caller. `vector_relevance` is the fused
    RRF magnitude already on the hit (positive, larger = better). The domain
    prior is IGNORED at T2 (on-query is universal, §6)."""
    fm = hit.get("fm") or {}
    relevance = hit.get("score") or 0.0

    if not gate(surfacing_level(fm), tier):
        return 0.0
    if not sensitivity_gate(fm, tier):
        return 0.0

    if tier == TIER_QUERY:
        prior = 1.0                          # §6: on-query ignores the prior
    else:
        project_match = bool(project) and (fm.get("project") == project)
        prior = domain_prior(fm.get("domain"), project_match=project_match)

    return float(relevance * prior)


# ── pipeline ──────────────────────────────────────────────────────────────────

_CLAIM_TYPES = ["claim"]


def rank_claims(query: str, project: Optional[str], *, tier: str, top_k: int,
                vault: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Retrieve + score v7 claims for one turn at a surfacing `tier`.

    Pipeline: resolver fusion (lexical+vector) scoped to page_type `claim` →
    §6 scoring (gate × prior × relevance × sensitivity_gate) → drop blocked
    (score 0) → sort descending → dedup by entry_id → tier budget. T0 enforces a
    hard count cap (`T0_CAP`) AFTER ranking, so the most relevant `always` claims
    fill the small budget."""
    vault = vault if vault is not None else _recall._vault_root()
    hits = _recall._resolve_hits(query, _CLAIM_TYPES, limit=max(top_k * 4, T0_CAP * 4))
    if not hits:
        hits = _fs_scan_claims(query, vault, limit=max(top_k * 4, T0_CAP * 4))

    scored: List[Dict[str, Any]] = []
    for h in hits:
        s = score_claim(h, tier=tier, project=project)
        if s <= 0.0:
            continue                         # blocked by a hard gate / no relevance
        h["score"] = s
        scored.append(h)

    scored.sort(key=lambda h: h["score"], reverse=True)
    scored = _recall._dedup_by_entry_id(scored)

    budget = T0_CAP if tier == TIER_ALWAYS else top_k
    return scored[:budget]


def _fs_scan_claims(query: str, vault: Path, *, limit: int) -> List[Dict[str, Any]]:
    """Fallback when the FTS index has no `claim` rows yet (fresh installs / a
    resolver outage). Token-match over the flat graph/ tree, keeping only docs
    whose frontmatter is a v7 claim. Mirrors recall._fs_scan's score convention
    (token count, larger = better)."""
    from ...index import parse as _parse

    tokens = [t.lower() for t in re.findall(r"\w+", query or "", re.UNICODE)]
    if not tokens:
        return []
    graph_root = vault / _recall_graph_root()
    if not graph_root.exists():
        return []
    out: List[Dict[str, Any]] = []
    for p in sorted(graph_root.rglob("*.md")):
        try:
            fm, body = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        except Exception:                   # pragma: no cover
            continue
        sv = fm.get("schema_version")
        if not (isinstance(sv, int) and sv >= 7 and fm.get("kind") == "claim"):
            continue
        haystack = (body + " " + str(fm)).lower()
        n = sum(1 for t in tokens if t in haystack)
        if n == 0:
            continue
        out.append({
            "slug": p.stem,
            "page_type": "claim",
            "fm": fm,
            "score": float(n),
            "snippet": body[:200].strip(),
            "path": str(p),
        })
        if len(out) >= limit:
            break
    return out


def _recall_graph_root() -> str:
    """The vault-relative graph root, single-sourced from the structure resolver
    (hard rule #3: no hardcoded paths)."""
    from ...structure import resolver as _structure
    return _structure.graph_root()


def _claim_slug(raw: str) -> str:
    """The bare identity of a claim — its filename stem (== entry_id-derived
    name), independent of the `graph/<...>.md` path shard. The resolver returns a
    full path slug; the fs-scan returns a bare stem. Normalize to the stem so a
    caller has one stable handle regardless of retrieval path."""
    name = (raw or "").rsplit("/", 1)[-1]
    return name[:-3] if name.endswith(".md") else name


def _summarize_claim(hit: Dict[str, Any]) -> Dict[str, Any]:
    fm = hit.get("fm") or {}
    statement = fm.get("statement") or hit.get("snippet") or ""
    return {
        "slug": _claim_slug(hit["slug"]),
        "page_type": "claim",
        "title": str(statement)[:120],
        "project": fm.get("project") or "",
        "topic": fm.get("domain") or "",
        "surfacing": surfacing_level(fm),
        "snippet": str(statement).replace("\n", " ").strip()[:240],
    }


def recall_claims(*, query: str,
                  project: Optional[str] = None,
                  tier: str = TIER_PROACTIVE,
                  top_k: int = 5,
                  max_chars: int = 1500) -> Dict[str, Any]:
    """RFC 0005 §6 recall over v7 claims at a surfacing `tier`.

    - `tier=query`   (T2): universal on-query; any claim, domain prior ignored,
                           private claims reachable.
    - `tier=proactive` (T1): per-turn push; proactive+always claims ranked by the
                           coding-session domain prior; private NEVER pushed.
    - `tier=always`  (T0): unconditional, hard-capped to T0_CAP `always` claims;
                           private NEVER pushed.

    Returns the same shape as recall.recall() so the hook renderer is reused."""
    if not (query or "").strip():
        return {"query": query, "project": project, "tier": tier,
                "count": 0, "items": [], "markdown": ""}

    hits = rank_claims(query, project, tier=tier, top_k=top_k)
    summaries = [_summarize_claim(h) for h in hits]
    markdown = _recall._render(summaries, project, max_chars)
    return {
        "query": query,
        "project": project,
        "tier": tier,
        "count": len(summaries),
        "items": summaries,
        "markdown": markdown,
    }
