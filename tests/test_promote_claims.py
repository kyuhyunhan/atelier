"""RFC 0005 §7.1 — promote = elevate a claim query→proactive behind the
acceptance gate. A FIELD transition in place: no candidates/→notes/ directory
move, entry_id preserved, content_hash re-derived, generated_by → promote.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

from runtime.promote import apply as _apply
from runtime.promote import propose as _propose
from runtime.service.learnings import claims_io as _ci
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
    "domain": "operational",
    "sensitivity": "public",
}


def _claim(vault: Path, entry_id: str, statement: str, *,
           surfacing: str = "query", ac_status: str = "passed") -> None:
    fm = {**_BASE, "entry_id": entry_id, "statement": statement,
          "surfacing": surfacing, "ac_status": ac_status}
    write_page(vault / "graph" / "atomic" / "claims" / f"{entry_id}.md", fm,
               f"## Claim\n\n{statement}\n")


# ── propose ──────────────────────────────────────────────────────────────────


def test_propose_lists_only_query_passed_claims(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    _claim(vault, "eligible", "use a real db", surfacing="query",
           ac_status="passed")
    _claim(vault, "pending", "unsure lesson", surfacing="query",
           ac_status="pending")            # not yet accepted → not eligible
    _claim(vault, "already", "already proactive", surfacing="proactive",
           ac_status="passed")             # past the query tier → not eligible

    out = _propose.propose_all()
    assert out["candidates"] == 1
    body = Path(out["path"]).read_text()
    assert "entry_id: eligible" in body
    assert "eligible" in body and "pending" not in body.split("eligible")[0]
    assert "promote: false" in body


def test_propose_empty_when_nothing_eligible(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    _claim(vault, "p", "pending", surfacing="query", ac_status="pending")
    out = _propose.propose_all()
    assert out["candidates"] == 0
    assert out["path"] is None


# ── apply (the field transition) ─────────────────────────────────────────────


def _write_proposal(path: Path, entry_id: str, promote: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# proposal\n\n---\n"
        f"entry_id: {entry_id}\n"
        "statement: x\n"
        f"promote: {'true' if promote else 'false'}\n",
        encoding="utf-8",
    )


def test_apply_transitions_query_to_proactive_in_place(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    _claim(vault, "c1", "use a real db", surfacing="query", ac_status="passed")
    before = _ci.find_claim_by_entry_id("c1", vault)
    assert before is not None
    before_path = before[0]

    prop = vault_env["cache"] / "promotions" / "p.md"
    _write_proposal(prop, "c1", promote=True)
    out = _apply.apply_proposal(prop)

    assert out["applied"] is True
    assert out["promoted"] == ["c1"]
    after = _ci.find_claim_by_entry_id("c1", vault)
    assert after is not None
    after_path, fm, _b = after
    # same file (no directory move), same entry_id.
    assert after_path == before_path
    assert fm["entry_id"] == "c1"
    assert fm["surfacing"] == "proactive"
    assert fm["generated_by"] == "promote"
    assert "ingest" in (fm.get("generated_by_history") or [])
    # content_hash was re-derived (no longer the placeholder).
    assert fm["content_hash"] != "h"


def test_g2_knowledge_gated_out_operational_survives_end_to_end(
        vault_env: Dict) -> None:
    """G2 pin (RFC 0009): an atomize-born knowledge claim (domain:knowledge, no
    ac_status) is NO LONGER promote-eligible, while a passed operational claim
    still is — measured through the WHOLE promote path (propose → apply).

    The knowledge claim is neither proposed by `_eligible` nor flipped by apply;
    the operational claim is proposed and actually transitions query→proactive.
    Knowledge reaches proactive/always only via dream synthesis, never here."""
    vault = vault_env["vault"]

    kfm = {**_BASE, "domain": "knowledge", "sensitivity": "public",
           "entry_id": "kc", "statement": "HBM stacks DRAM layers",
           "surfacing": "query", "generated_by": "atomize"}
    kfm.pop("ac_status", None)                # knowledge has NO ac_status
    write_page(vault / "graph" / "atomic" / "claims" / "kc.md", kfm,
               "## Claim\n\nHBM stacks DRAM layers\n")
    _claim(vault, "oc", "prefer a real db in integration tests",
           surfacing="query", ac_status="passed")   # operational, passed

    # the knowledge claim is not eligible; only the operational one is proposed
    assert _ci.is_promote_eligible(kfm) is False
    prop_out = _propose.propose_all()
    assert prop_out["candidates"] == 1
    body = Path(prop_out["path"]).read_text()
    assert "entry_id: oc" in body
    assert "entry_id: kc" not in body

    # even a hand-edited proposal naming the knowledge claim is gated at apply
    prop = vault_env["cache"] / "promotions" / "p.md"
    _write_proposal(prop, "kc", promote=True)
    out = _apply.apply_proposal(prop)
    assert out["promoted"] == []
    assert out["skipped_detail"][0]["reason"] == "acceptance-gate"
    _p, kafter, _b = _ci.find_claim_by_entry_id("kc", vault)
    assert kafter["surfacing"] == "query"     # untouched — never promoted

    # the operational claim promotes cleanly
    _write_proposal(prop, "oc", promote=True)
    out2 = _apply.apply_proposal(prop)
    assert out2["promoted"] == ["oc"]
    _p, oafter, _b = _ci.find_claim_by_entry_id("oc", vault)
    assert oafter["surfacing"] == "proactive"


def test_apply_ignores_unselected_rows(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    _claim(vault, "c1", "use a real db", surfacing="query", ac_status="passed")
    prop = vault_env["cache"] / "promotions" / "p.md"
    _write_proposal(prop, "c1", promote=False)
    out = _apply.apply_proposal(prop)
    assert out["applied"] is False
    _p, fm, _b = _ci.find_claim_by_entry_id("c1", vault)
    assert fm["surfacing"] == "query"        # untouched


def test_apply_enforces_acceptance_gate(vault_env: Dict) -> None:
    """A hand-edited/stale proposal naming a non-accepted claim is gated."""
    vault = vault_env["vault"]
    _claim(vault, "c1", "unsure", surfacing="query", ac_status="pending")
    prop = vault_env["cache"] / "promotions" / "p.md"
    _write_proposal(prop, "c1", promote=True)
    out = _apply.apply_proposal(prop)
    assert out["applied"] is False
    assert out["skipped"] == 1
    assert out["skipped_detail"][0]["reason"] == "acceptance-gate"


def test_apply_is_idempotent(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    _claim(vault, "c1", "use a real db", surfacing="query", ac_status="passed")
    prop = vault_env["cache"] / "promotions" / "p.md"
    _write_proposal(prop, "c1", promote=True)
    _apply.apply_proposal(prop)
    out2 = _apply.apply_proposal(prop)       # claim now proactive
    assert out2["applied"] is False
    assert out2["skipped_detail"][0]["reason"] == "not-query-tier"


# ── round trip through propose → apply ───────────────────────────────────────


def test_propose_then_apply_round_trip(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    _claim(vault, "c1", "prefer real db in integration tests",
           surfacing="query", ac_status="passed")
    prop = _propose.propose_all()
    # Reviewer flips promote: false → true.
    text = Path(prop["path"]).read_text().replace("promote: false",
                                                  "promote: true")
    Path(prop["path"]).write_text(text)
    out = _apply.apply_proposal(Path(prop["path"]))
    assert out["promoted"] == ["c1"]
    _p, fm, _b = _ci.find_claim_by_entry_id("c1", vault)
    assert fm["surfacing"] == "proactive"


# ── G2 (RFC 0009): promote eligibility is the operational lane only ──────────

def test_promote_gate_is_operational_lane_only() -> None:
    """`is_promote_eligible` — the ONE gate shared by the filesystem scan, the DB
    projection and the metrics counter.

    G2 narrowed eligibility to the operational lane: an operational claim still
    needs ac_status:passed (unchanged), but an atomize-born knowledge claim
    (domain:knowledge, NO ac_status — born-accepted) is NO LONGER eligible. It
    reaches proactive/always only via dream synthesis, never this promote path.
    Private is never eligible."""
    def fm(**kw):
        base = {"kind": "claim", "surfacing": "query", "sensitivity": "public"}
        base.update(kw)
        return base

    # operational passed → eligible (the lane is unchanged for operational)
    assert _ci.is_promote_eligible(fm(domain="operational", ac_status="passed")) is True
    # operational still pending → NOT eligible (accept gate not cleared)
    assert _ci.is_promote_eligible(fm(domain="operational", ac_status="pending")) is False
    # atomize-born knowledge (no ac_status) → NO LONGER eligible under G2
    assert _ci.is_promote_eligible(fm(domain="knowledge")) is False
    # even a knowledge claim carrying ac_status:passed stays out — wrong lane
    assert _ci.is_promote_eligible(fm(domain="knowledge", ac_status="passed")) is False
    # private (personal) → never eligible, even without ac_status
    assert _ci.is_promote_eligible(fm(domain="personal", sensitivity="private")) is False
    # already promoted past query → not eligible
    assert _ci.is_promote_eligible(
        fm(domain="operational", ac_status="passed", surfacing="proactive")) is False
