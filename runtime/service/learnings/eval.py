"""Retrieval eval harness — the P@k / R@k / MRR baseline (RFC 0002, P0 / §10).

The surfacing audit answers "is this learning retrievable at all?" (omission).
This harness adds the *quality* question: "when retrieval runs, how good is the
ranking?" — the number every later phase must improve and never regress.

It is read-only and seeded entirely from the vault (no LLM, no query history, no
human labels), using two auto-generated probe sets that answer two questions:

  self-probe (single gold = the learning itself, queried by its own concept)
      → Recall@k + MRR + the dark gate. Reuses `surfacing.snapshot`, so the
        omission definition cannot drift from the audit.

  concept-grouped (multi gold = all learnings sharing a concept)
      → P@k + R@k. The only probe set where precision is meaningful, because a
        single-gold key caps P@k at 1/k. gbrain-comparable.

`run()` emits one baseline dict holding both; freeze it and diff later phases
against it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Sequence, Set

from ...index import parse as _parse
# `_concept_targets` is the canonical definition of a learning's concept edges
# (touches + target_topic) used by the indexer. The eval groups by the EXACT
# same edges retrieval ranks on — reuse, not re-derive, so they cannot diverge.
from ...index import reindex as _reindex
from . import recall as _recall
from . import store as _store
from . import surfacing as _surfacing

_TYPES = _surfacing._TYPES


# ── pure metric math ────────────────────────────────────────────────────────

def precision_at_k(ranked_ids: Sequence[str], gold: Set[str], k: int) -> float:
    """Of the top-k returned, the fraction that are gold. 0 when k<=0."""
    if k <= 0:
        return 0.0
    top = ranked_ids[:k]
    return sum(1 for x in top if x in gold) / k


def recall_at_k(ranked_ids: Sequence[str], gold: Set[str], k: int) -> float:
    """Of all gold docs, the fraction that landed in the top-k. 0 on empty gold."""
    if not gold or k <= 0:
        return 0.0
    top = ranked_ids[:k]
    return sum(1 for x in top if x in gold) / len(gold)


def reciprocal_rank(ranked_ids: Sequence[str], gold: Set[str]) -> float:
    """1 / (1-based position of the first gold doc), or 0 if none present."""
    for i, x in enumerate(ranked_ids):
        if x in gold:
            return 1.0 / (i + 1)
    return 0.0


def _mean(xs: Sequence[float]) -> float:
    return (sum(xs) / len(xs)) if xs else 0.0


# ── concept-grouped probe set ───────────────────────────────────────────────

def _enumerate_with_concepts(vault: Path) -> List[tuple[str, List[str]]]:
    """(entry_id, concept-edges) per accepted learning. Same pool and noise/
    entry_id rules as the surfacing audit, so the two harnesses probe the same
    corpus."""
    out: List[tuple[str, List[str]]] = []
    for p in _store.iter_accepted_files(vault):
        if _recall.is_noise(p.name):
            continue
        try:
            fm, _ = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        except Exception:               # pragma: no cover
            continue
        eid = fm.get("entry_id")
        if not eid:
            continue
        out.append((str(eid), _reindex._concept_targets(fm)))
    return out


def concept_probes(vault: Path) -> List[Dict[str, Any]]:
    """Multi-gold probes: one per concept shared by >=2 learnings. A concept with
    a single learning is the self-probe's job (single gold) — excluded here so
    P@k stays meaningful."""
    groups: Dict[str, Set[str]] = {}
    for eid, concepts in _enumerate_with_concepts(vault):
        for c in concepts:
            key = c.strip().lower()
            if key:
                groups.setdefault(key, set()).add(eid)
    return [
        {"concept": key, "query": key, "gold": sorted(g)}
        for key, g in sorted(groups.items())
        if len(g) >= 2
    ]


# ── run ─────────────────────────────────────────────────────────────────────

def _self_probe_block(k: int) -> Dict[str, Any]:
    """Known-item metrics from the audit's own snapshot at depth k — so Recall@k
    and the dark count share the surfacing audit's exact omission definition."""
    snap = _surfacing.snapshot(probe_k=k)
    rows = [s for s in snap.values() if (s.get("probe") or "").strip()]
    recalls = [1.0 if s["visible"] else 0.0 for s in rows]
    rrs = [1.0 / (s["rank"] + 1) if s["rank"] is not None else 0.0 for s in rows]
    return {
        "probes": len(rows),
        "recall_at_k": _mean(recalls),
        "mrr": _mean(rrs),
        "dark_count": sum(1 for s in rows if not s["visible"]),
    }


def _concept_block(vault: Path, k: int) -> Dict[str, Any]:
    probes = concept_probes(vault)
    ps: List[float] = []
    rs: List[float] = []
    for pr in probes:
        hits = _recall.rank_hits(pr["query"], None, _TYPES, top_k=k, vault=vault)
        ranked = [str((h.get("fm") or {}).get("entry_id")) for h in hits]
        gold = set(pr["gold"])
        ps.append(precision_at_k(ranked, gold, k))
        rs.append(recall_at_k(ranked, gold, k))
    return {
        "probes": len(probes),
        "precision_at_k": _mean(ps),
        "recall_at_k": _mean(rs),
    }


def _vault_root() -> Path:
    return _surfacing._vault_root()


def gate(before: Dict[str, Dict[str, Any]],
         after: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """The hard omission gate every phase must pass (RFC 0001/0002 discipline):
    given two `surfacing.snapshot`s, a change is allowed only if NO learning that
    was visible went dark. Wraps `surfacing.diff` and adds `passed` so the gate
    is one call. Rank drops are reported but do not fail the gate (they are a
    quality signal, not an omission)."""
    d = _surfacing.diff(before, after)
    return {**d, "passed": not d["newly_dark"]}


def run(*, k: int = 5, vault: Path | None = None) -> Dict[str, Any]:
    """Compute both probe sets' metrics over the current (FTS-only at P0) path.

    Returns a JSON-serializable baseline. `engine` names the live retrieval mode
    so a frozen baseline records *what* it measured, not just the numbers."""
    vault = vault if vault is not None else _vault_root()
    return {
        "k": k,
        "engine": "fts-only",
        "self_probe": _self_probe_block(k),
        "concept_grouped": _concept_block(vault, k),
    }
