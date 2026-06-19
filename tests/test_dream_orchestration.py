"""RFC 0005 §7.1 — dream orchestration on CLAIM FIELDS.

dream is the T0-budget curator: it clusters PROACTIVE claims, the agent
generalizes a cluster into a NEW synthesized always-claim (linked refines/
supports, derived_from the sources), and may distill strong proactive claims
into the capped T0 budget. All transitions are FIELD edits in place — no
candidates/notes/principles directory moves.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict, List

import pytest

from runtime.service.learnings import claims_io as _ci
from runtime.service.learnings import dream as _dr
from tests.conftest import write_page


_BASE = {
    "schema_version": 7,
    "kind": "claim",
    "created_at": "2026-01-01T00:00:00Z",
    "content_hash": "h",
    "is_about": [],
    "derived_from": ["src1"],
    "attributed_to": "claude-code",
    "generated_by": "ingest",
}


def _claim(vault: Path, entry_id: str, statement: str, *,
           surfacing: str = "proactive",
           domain: str = "operational",
           ac_status: str = "passed",
           project: str = "") -> None:
    fm = {**_BASE, "entry_id": entry_id, "statement": statement,
          "surfacing": surfacing, "domain": domain, "sensitivity": "public",
          "ac_status": ac_status}
    if project:
        fm["project"] = project
    write_page(vault / "graph" / "atomic" / "claims" / f"{entry_id}.md", fm,
               f"## Claim\n\n{statement}\n")


def _seed_cluster(vault: Path) -> List[str]:
    """Two proactive claims sharing salient terms → one cluster."""
    _claim(vault, "c1",
           "integration tests must use a real database not database mocks")
    _claim(vault, "c2",
           "database mocks diverge from the production database under load")
    return ["c1", "c2"]


# ── plan ────────────────────────────────────────────────────────────────────


def test_plan_returns_cluster_with_previews_and_call(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    _seed_cluster(vault)
    out = _dr.plan()
    assert out["candidate_count"] >= 1
    assert out["proactive_scanned"] == 2
    c = out["clusters"][0]
    assert len(c["members"]) == 2
    assert all(m["statement"] for m in c["members"])
    call = c["synthesize_call"]["args"]
    assert c["synthesize_call"]["tool"] == "atelier_dream_synthesize"
    assert set(call["source_claim_ids"]) == {"c1", "c2"}
    assert call["rel"] == "refines"
    assert "cadence" in out and "instructions" in out


def test_plan_only_clusters_proactive_claims(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    # query-tier claims are NOT eligible for a dream pass (only proactive is).
    _claim(vault, "q1", "shared retry idempotency token concept here",
           surfacing="query")
    _claim(vault, "q2", "shared retry idempotency token concept there",
           surfacing="query")
    out = _dr.plan()
    assert out["candidate_count"] == 0
    assert out["proactive_scanned"] == 0


# ── synthesize ──────────────────────────────────────────────────────────────


def test_synthesize_writes_new_always_claim_linked_to_sources(
        vault_env: Dict) -> None:
    vault = vault_env["vault"]
    ids = _seed_cluster(vault)
    out = _dr.synthesize(
        source_claim_ids=ids,
        statement="prefer a real database over mocks in integration tests",
        why="mocks drift from production schema",
        rel="refines",
    )
    assert out["skipped"] is False
    found = _ci.find_claim_by_entry_id(out["entry_id"], vault)
    assert found is not None
    _p, fm, _b = found
    assert fm["generated_by"] == "dream"
    assert fm["surfacing"] == "always"           # synthesized → T0 tier
    link_targets = {ln["to"] for ln in fm["links"]}
    assert link_targets == {"c1", "c2"}
    assert all(ln["rel"] == "refines" for ln in fm["links"])
    # content-addressed id is stable + non-empty.
    assert fm["entry_id"] == out["entry_id"]


def test_synthesize_is_idempotent(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    ids = _seed_cluster(vault)
    _dr.synthesize(source_claim_ids=ids,
                   statement="real db beats mocks", why="drift")
    again = _dr.synthesize(source_claim_ids=ids,
                           statement="real db beats mocks again", why="drift")
    assert again["skipped"] is True
    assert again["reason"] == "already-covered"


def test_plan_filters_already_synthesized(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    ids = _seed_cluster(vault)
    _dr.synthesize(source_claim_ids=ids,
                   statement="real db beats mocks", why="drift")
    out = _dr.plan()
    assert out["candidate_count"] == 0
    assert out["skipped_already_covered"] >= 1


# ── distill (proactive → always) ─────────────────────────────────────────────


def test_distill_elevates_proactive_to_always(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    _claim(vault, "p1", "cache invalidation needs explicit keys")
    out = _dr.distill(claim_ids=["p1"])
    assert out["elevated"] == ["p1"]
    found = _ci.find_claim_by_entry_id("p1", vault)
    assert found is not None
    _p, fm, _b = found
    assert fm["surfacing"] == "always"
    assert fm["generated_by"] == "dream"
    # original provenance preserved in history (entry_id unchanged).
    assert fm["entry_id"] == "p1"
    assert "ingest" in (fm.get("generated_by_history") or [])


def test_distill_skips_non_proactive(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    _claim(vault, "a1", "already always claim", surfacing="always")
    _claim(vault, "q1", "query-only claim", surfacing="query")
    out = _dr.distill(claim_ids=["a1", "q1", "missing"])
    assert out["elevated"] == []
    reasons = {s["entry_id"]: s["reason"] for s in out["skipped"]}
    assert reasons["a1"] == "not-proactive"
    assert reasons["q1"] == "not-proactive"
    assert reasons["missing"] == "not-found"


# ── complete ────────────────────────────────────────────────────────────────


def test_complete_advances_cadence(vault_env: Dict) -> None:
    from runtime.service.learnings import cluster as _cl
    out = _dr.complete(when="2026-05-28T21:00:00+09:00")
    assert out["last_dream_at"] == "2026-05-28T21:00:00+09:00"
    after = _cl.dream_status()
    assert after["last_dream_at"] == "2026-05-28T21:00:00+09:00"


# ── full handshake ──────────────────────────────────────────────────────────


def test_full_dream_handshake(vault_env: Dict) -> None:
    """plan → synthesize the cluster → distill a source → complete → re-plan
    finds nothing."""
    vault = vault_env["vault"]
    _seed_cluster(vault)
    plan = _dr.plan()
    for c in plan["clusters"]:
        args = c["synthesize_call"]["args"]
        _dr.synthesize(source_claim_ids=args["source_claim_ids"],
                       statement="integration tests use a real database",
                       why="mocks drift from prod")
    _dr.distill(claim_ids=["c1"])
    _dr.complete(when="2026-05-28T21:00:00+09:00")
    replan = _dr.plan()
    assert replan["candidate_count"] == 0


# ── MCP dispatch ────────────────────────────────────────────────────────────


def test_mcp_dream_tools_registered() -> None:
    from runtime.service import tools as _tools
    names = {t.name for t in _tools.iter_tools()}
    assert {"atelier_dream_plan", "atelier_dream_synthesize",
            "atelier_dream_distill", "atelier_dream_complete",
            "atelier_dream_status"} <= names


def test_mcp_dispatch_dream_plan(vault_env: Dict) -> None:
    from runtime.service import tools as _tools
    vault = vault_env["vault"]
    _seed_cluster(vault)

    async def go() -> Dict:
        return await _tools.invoke("atelier_dream_plan")

    out = asyncio.run(go())
    assert out["candidate_count"] >= 1


def test_mcp_dispatch_dream_synthesize(vault_env: Dict) -> None:
    from runtime.service import tools as _tools
    vault = vault_env["vault"]
    ids = _seed_cluster(vault)

    async def go() -> Dict:
        return await _tools.invoke(
            "atelier_dream_synthesize",
            source_claim_ids=ids,
            statement="real database over mocks for integration tests",
            why="mocks drift")

    out = asyncio.run(go())
    assert out["skipped"] is False
    assert _ci.find_claim_by_entry_id(out["entry_id"], vault) is not None
