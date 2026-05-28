"""Bounded remediation for FAIL/WARN diagnoses.

`--max-usd N` caps any LLM-backed remediation spend. In v0.1 no remediation
calls LLMs (all fixers are mechanical), so the cap is plumbing only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .diagnostics import Diagnosis
from ..util import config, db, logging as log


@dataclass
class RemediationResult:
    diagnosis_id: str
    action: str
    success: bool
    cost_usd: float = 0.0
    notes: str = ""


def remediate(
    cfg: config.Config,
    diagnoses: List[Diagnosis],
    max_usd: float = 0.0,
) -> List[RemediationResult]:
    spent = 0.0
    results: List[RemediationResult] = []
    for d in diagnoses:
        if d.severity == "OK":
            continue
        budget_left = max_usd - spent
        action_fn = _ACTIONS.get(d.id)
        if not action_fn:
            results.append(RemediationResult(d.id, "none", False, 0.0,
                                             "no remediator registered"))
            continue
        r = action_fn(cfg, d, budget_left)
        spent += r.cost_usd
        results.append(r)
        if r.cost_usd > 0:
            log.info("remediate.spend", id=d.id, usd=r.cost_usd, cumulative=spent)
    return results


# ── Per-D remediation strategies ─────────────────────────────────────────────

def _r_D2(cfg, d, budget_left) -> RemediationResult:
    """D2 (FS drift) → run a full reindex."""
    from ..index import reindex
    for name in cfg.spaces:
        if cfg.space(name).local.exists():
            reindex.reindex_space(cfg, name, full=True)
    return RemediationResult(d.id, "reindex --full", True)


def _r_D6(cfg, d, budget_left) -> RemediationResult:
    """D6 (FTS desync) → rebuild FTS from chunks."""
    conn = db.connect()
    try:
        with conn:
            conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    finally:
        conn.close()
    return RemediationResult(d.id, "fts rebuild", True)


_ACTIONS = {
    "D2": _r_D2,
    "D6": _r_D6,
}
