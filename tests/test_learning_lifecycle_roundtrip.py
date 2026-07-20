"""RFC 0005 §7.1 — end-to-end claim-model lifecycle round-trips.

An operational learning is BORN AS A CLAIM and walks its lifecycle entirely by
FIELD transitions (no candidate/note/principle directories):

    capture  → claim  surfacing:query   ac_status:pending   (born)
    accept   →        surfacing:query   ac_status:passed     (acceptance gate)
    promote  →        surfacing:proactive                    (behind that gate)

These tests are the net-behavior replacement for the retired file-lifecycle
tests (candidate→notes move, archived/ dir): they assert the SAME claim file
walks the lifecycle with its entry_id preserved at every step.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

from runtime.service.learnings import capture as _cap
from runtime.service.learnings import claims_io as _ci
from runtime.service.learnings import review as _rev


def _fm(path: Path) -> dict:
    from runtime.index.parse import split_frontmatter
    front, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    return front


def _capture_good(working_dir: str = "/Users/me/workspaces/lexio") -> Dict:
    return _cap.capture(
        observation="search returns nothing for tilde queries",
        why="fts5 ignores tilde tokens; need a fallback path",
        rule="enable a punctuation fallback for queries",
        working_dir=working_dir, session_id="s", hook="Stop",
    )


def test_capture_births_a_query_pending_claim(atelier_env: Dict) -> None:
    out = _capture_good()
    fm = _fm(Path(out["path"]))
    assert fm["kind"] == "claim" and fm["schema_version"] == 7
    assert fm["domain"] == "operational"
    assert fm["surfacing"] == "query"
    assert fm["ac_status"] == "pending"
    assert fm["generated_by"] == "mint"       # RFC 0007: deterministic mint (hook in `hook`)
    assert fm["hook"] == "Stop"


def test_accept_round_trip_passes_the_gate_in_place(atelier_env: Dict) -> None:
    out = _capture_good()
    eid = out["entry_id"]
    path = Path(out["path"])

    res = _rev.accept(candidate_slug=eid, target_topic="search",
                      target_project="lexio")
    assert res["entry_id"] == eid                 # id preserved
    assert Path(res["path"]) == path              # same file (field transition)
    fm = _fm(path)
    assert fm["ac_status"] == "passed"            # gate cleared
    assert fm["surfacing"] == "query"             # not yet promoted


def test_full_round_trip_capture_accept_promote(atelier_env: Dict) -> None:
    """capture → accept (passed) → promote (proactive), all on ONE claim file
    with the entry_id preserved end to end."""
    from runtime.promote import apply as _apply

    out = _capture_good()
    eid = out["entry_id"]
    path = Path(out["path"])

    _rev.accept(candidate_slug=eid, target_topic="search",
                target_project="lexio")
    assert _fm(path)["ac_status"] == "passed"

    # promote = query → proactive behind the acceptance gate (the proposal the
    # consolidation skill writes; here we drive apply directly).
    prop = atelier_env["cache"] / "promotions" / "p.md"
    prop.parent.mkdir(parents=True, exist_ok=True)
    prop.write_text(
        f"# proposal\n\n---\nentry_id: {eid}\nstatement: x\npromote: true\n",
        encoding="utf-8")
    applied = _apply.apply_proposal(prop)

    assert applied["promoted"] == [eid]
    fm = _fm(path)
    assert fm["entry_id"] == eid                   # preserved through promote
    assert fm["surfacing"] == "proactive"
    assert fm["ac_status"] == "passed"
    assert fm["generated_by"] == "promote"


def test_archive_and_retract_round_trip_are_field_only(atelier_env: Dict) -> None:
    a = _capture_good()
    arch = _rev.archive(candidate_slug=a["entry_id"], reason="noise")
    assert Path(arch["path"]) == Path(a["path"])
    assert _fm(Path(a["path"]))["ac_status"] == "failed"

    b = _capture_good(working_dir="/Users/me/workspaces/bht")
    _rev.accept(candidate_slug=b["entry_id"], target_topic="t",
                target_project="bht")
    ret = _rev.retract(slug=b["entry_id"], reason="user-said-no")
    assert Path(ret["path"]) == Path(b["path"])
    assert _fm(Path(b["path"]))["ac_status"] == "retracted"
    assert ret["from"] == "accepted"


def test_born_nodes_pass_the_v7_schema_validator(atelier_env: Dict) -> None:
    """The claim, its thin session Source, and the resolved is_about Entity that
    capture mints must all satisfy the v7 schema (required fields + enums:
    generated_by ∈ {ingest,…}, source/entity in_scheme ∈ {personal,knowledge,
    inbox,workshop}). This guards the field-spec enums the migration enforces."""
    from runtime.lint.validate_v4 import validate_paths
    out = _cap.capture(
        observation="overlay bug", why="needs a stable key", rule="stabilize keys",
        working_dir="/Users/me/workspaces/lexio", touches=["react-rendering"],
        session_id="s", hook="Stop")
    vault = atelier_env["gorae"]
    # L2 Entity/Claim nodes live FLAT under graph/atomic/ (RFC 0005 P9.4); this
    # one rglob picks up both kinds (source nodes live in raw/, not graph/), and
    # already includes the captured claim at out["path"], so dedup on resolve().
    paths = sorted(
        {p.resolve() for p in (vault / "graph" / "atomic").rglob("*.md")}
        | {Path(out["path"]).resolve()}
    )
    findings = validate_paths(paths, vault_root=vault)
    assert findings == [], [f"{f.page_slug}: {f.message}" for f in findings]


def test_accepted_claim_is_discoverable_as_accepted(atelier_env: Dict) -> None:
    """The single chokepoint store.iter_accepted_files yields a passed
    operational claim — so every accepted-pool reader (recall/search/bootstrap)
    sees it without per-reader edits."""
    from runtime.service.learnings import store as _store
    out = _capture_good()
    vault = atelier_env["gorae"]
    # pending → not yet in the accepted pool
    assert out["path"] not in {str(p) for p in _store.iter_accepted_files(vault)}
    _rev.accept(candidate_slug=out["entry_id"], target_topic="t",
                target_project="lexio")
    assert out["path"] in {str(p) for p in _store.iter_accepted_files(vault)}
