"""P0 — the damaged-record census (RFC 0001 §2.2, input to P6 repair).

Verifies the census keys on absorb provenance (`agent_kind: absorbed`), NOT on
the naive layer-token heuristic that undercounts, and that it captures the
recovery data P6 needs (primary aspect from `layer`/flattened topic, secondary
from `also_in`) while grading confidence.
"""
from __future__ import annotations

from typing import Dict

from scripts.census_damaged_learnings import census as _census
from tests.conftest import write_page


def _by_topic(vault, topic: str, name: str, fm: Dict) -> None:
    write_page(vault / "learnings" / "accepted" / "by-topic" / topic /
               f"{name}.md", fm, "## Observation\n\nbody\n")


_ACCEPTED = {
    "schema_version": 4, "status": "accepted", "ac_status": "passed",
    "observation_kind": "project", "captured_at": "2026-05-01T00:00:00Z",
    "accepted_at": "2026-05-02T00:00:00Z",
}


def test_census_keys_on_absorb_provenance_not_layer_tokens(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    # (a) absorbed, target_topic IS a canonical layer + carries layer field → high
    _by_topic(vault, "cross-cutting", "a", {
        **_ACCEPTED, "entry_id": "A", "agent_kind": "absorbed",
        "target_project": "lexio", "target_topic": "cross-cutting",
        "layer": "cross-cutting", "also_in": ["product"]})
    # (b) absorbed, target_topic is NOT a canonical layer token, no layer field →
    #     the naive heuristic would MISS this; provenance still flags it (review).
    _by_topic(vault, "legacy", "b", {
        **_ACCEPTED, "entry_id": "B", "agent_kind": "absorbed",
        "target_project": "lexio", "target_topic": "legacy"})
    # (c) native learning (not absorbed) → NOT damaged, must be excluded.
    _by_topic(vault, "surfacing-audit", "c", {
        **_ACCEPTED, "entry_id": "C", "agent_kind": "claude-code",
        "target_project": "atelier", "target_topic": "surfacing-audit"})

    rep = _census.census(vault)

    ids = {r["entry_id"] for r in rep["records"]}
    assert ids == {"A", "B"}                  # native C excluded; naive-miss B caught
    assert rep["scanned"] == 3
    assert rep["damaged"] == 2


def test_census_captures_recovery_data_and_confidence(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    _by_topic(vault, "client", "a", {
        **_ACCEPTED, "entry_id": "A", "agent_kind": "absorbed",
        "target_project": "lexio", "target_topic": "client",
        "layer": "client", "also_in": ["cross-cutting"]})
    _by_topic(vault, "overview", "b", {
        **_ACCEPTED, "entry_id": "B", "agent_kind": "absorbed",
        "target_project": "lexio", "target_topic": "overview"})

    rep = _census.census(vault)
    rec = {r["entry_id"]: r for r in rep["records"]}

    # A: explicit layer → recoverable primary aspect + secondary from also_in.
    assert rec["A"]["recoverable_primary_aspect"] == "client"
    assert rec["A"]["recoverable_secondary_aspects"] == ["cross-cutting"]
    assert rec["A"]["confidence"] == "high"
    # B: no layer field, topic not a canonical token → flagged for human review,
    #    primary aspect falls back to the flattened topic value.
    assert rec["B"]["recoverable_primary_aspect"] == "overview"
    assert rec["B"]["confidence"] == "review"

    assert rep["summary"]["high_confidence"] == 1
    assert rep["summary"]["needs_review"] == 1
    assert rep["summary"]["with_also_in"] == 1
