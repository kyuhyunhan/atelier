"""PR-20: review / accept / archive / retract for learnings."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict

import pytest

from runtime.service.learnings import capture as _cap
from runtime.service.learnings import review as _rev


def _read_fm(path: Path) -> dict:
    from runtime.index.parse import split_frontmatter
    fm, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    return fm


# ── helpers ────────────────────────────────────────────────────────────────


def _make_good_candidate(working_dir: str = "/Users/me/workspaces/lexio") -> Dict:
    """A candidate that satisfies every auto-evaluable must check.

    RFC 0005 P10: all operational claims derive_from the ONE shared source, so a
    claim's id is content-addressed on `statement` alone. The project no longer
    discriminates the id — so we fold the project basename into the statement to
    keep candidates from different projects distinct."""
    proj = Path(working_dir).name
    return _cap.capture(
        observation=f"search returns nothing for tilde queries in {proj}",
        why="fts5 ignores tilde tokens; need fallback path",
        rule=f"enable fallback for punctuation in queries ({proj})",
        working_dir=working_dir,
        session_id="abc",
        hook="Stop",
    )


def _make_thin_candidate() -> Dict:
    """A candidate missing 'why', too thin for must_pass. require_why=False
    bypasses the capture-time substance gate so the candidate still exists
    to exercise the downstream review/archive machinery (it will still
    fail must-criteria at promotion time)."""
    return _cap.capture(observation="something", hook="manual",
                        require_why=False)


# ── review_pending ─────────────────────────────────────────────────────────


def test_review_pending_returns_self_check(atelier_env: Dict) -> None:
    good = _make_good_candidate()
    thin = _make_thin_candidate()

    out = _rev.review_pending(limit=10)
    assert out["count"] == 2
    by_id = {item["entry_id"]: item for item in out["items"]}
    assert by_id[good["entry_id"]]["must_pass"] is True
    assert by_id[thin["entry_id"]]["must_pass"] is False


def test_review_pending_filters_by_project(atelier_env: Dict) -> None:
    _make_good_candidate(working_dir="/Users/me/workspaces/lexio")
    _make_good_candidate(working_dir="/Users/me/workspaces/bht")
    out = _rev.review_pending(limit=10, project="bht")
    assert out["count"] == 1
    assert out["items"][0]["project_hint"] == "bht"


# ── accept ─────────────────────────────────────────────────────────────────


def test_accept_is_an_ac_status_field_transition(atelier_env: Dict) -> None:
    """RFC 0005 §7.1: accept is a FIELD transition (ac_status pending → passed)
    on the SAME claim file — no directory move to notes/. surfacing stays query
    (the separate promote step elevates it query → proactive)."""
    from runtime.index.parse import split_frontmatter
    good = _make_good_candidate()
    before = Path(good["path"])
    fm_before, _ = split_frontmatter(before.read_text())
    assert fm_before["ac_status"] == "pending"

    result = _rev.accept(candidate_slug=good["entry_id"],
                         target_topic="search-fallback",
                         target_project="lexio")
    accepted = Path(result["path"])
    # SAME file (no move); entry_id preserved.
    assert accepted == before
    assert accepted.exists()
    assert "/learnings/notes/" not in str(accepted)
    assert result["by_project_path"] is None
    assert result["entry_id"] == good["entry_id"]
    fm = _read_fm(accepted)
    assert fm["ac_status"] == "passed"
    assert fm["surfacing"] == "query"          # promote, not accept, elevates
    assert fm["target_topic"] == "search-fallback"
    assert fm["target_project"] == "lexio"


def test_accept_refuses_when_must_fails(atelier_env: Dict) -> None:
    thin = _make_thin_candidate()
    with pytest.raises(PermissionError):
        _rev.accept(candidate_slug=thin["entry_id"],
                    target_topic="misc")


def test_accept_writes_log_entry(atelier_env: Dict) -> None:
    good = _make_good_candidate()
    _rev.accept(candidate_slug=good["entry_id"],
                target_topic="search-fallback",
                target_project="lexio")
    log = (atelier_env["gorae"] / "learnings" / "log.md").read_text()
    assert "accept" in log
    assert "search-fallback" in log


# ── archive ────────────────────────────────────────────────────────────────


def test_archive_sets_failed_ac_status_in_place(atelier_env: Dict) -> None:
    """RFC 0005 §7.1: archive is ac_status → failed on the SAME claim file
    (+ archive_reason), not a move to archived/."""
    thin = _make_thin_candidate()
    before = Path(thin["path"])
    result = _rev.archive(candidate_slug=thin["entry_id"],
                          reason="pure-meta-comment")
    assert Path(result["path"]) == before
    assert before.exists()
    assert "archived/" not in str(result["path"])
    fm = _read_fm(before)
    assert fm["ac_status"] == "failed"
    assert fm["archive_reason"] == "pure-meta-comment"


# ── retract ────────────────────────────────────────────────────────────────


def test_retract_from_accepted_sets_retracted(atelier_env: Dict) -> None:
    """Retract an accepted claim: ac_status passed → retracted, in place."""
    good = _make_good_candidate()
    accepted = _rev.accept(candidate_slug=good["entry_id"],
                           target_topic="search-fallback",
                           target_project="lexio")
    assert accepted["by_project_path"] is None       # no mirror (RFC 0001)
    out = _rev.retract(slug=Path(accepted["path"]).stem,
                       reason="user-said-no")
    assert Path(out["path"]) == Path(accepted["path"])   # same file, not moved
    assert out["from"] == "accepted"
    fm = _read_fm(Path(out["path"]))
    assert fm["ac_status"] == "retracted"
    assert fm["archive_reason"] == "user-said-no"


def test_retract_from_candidate(atelier_env: Dict) -> None:
    """Retract a still-pending claim: ac_status pending → retracted, in place."""
    thin = _make_thin_candidate()
    out = _rev.retract(slug=thin["entry_id"], reason="too-thin")
    assert Path(out["path"]) == Path(thin["path"])
    assert out["from"] == "candidate"
    assert _read_fm(Path(out["path"]))["ac_status"] == "retracted"


# ── MCP dispatch parity ────────────────────────────────────────────────────


def test_mcp_tools_registered() -> None:
    from runtime.service import tools as _tools
    names = {t.name for t in _tools.iter_tools()}
    expected = {
        "atelier_learning_review_pending",
        "atelier_learning_accept",
        "atelier_learning_archive",
        "atelier_learning_retract",
    }
    assert expected <= names


def test_mcp_dispatch_review_pending(atelier_env: Dict) -> None:
    from runtime.service import tools as _tools
    _make_good_candidate()

    async def go() -> Dict:
        return await _tools.invoke("atelier_learning_review_pending", limit=5)

    out = asyncio.run(go())
    assert out["count"] == 1


# ── override_must (PR-38) ────────────────────────────────────────────────────


def test_override_must_accepts_despite_heuristic_miss(atelier_env: Dict) -> None:
    """A reviewed candidate with free-form why (no '## Why this matters'
    section) fails has_why heuristically; override_must promotes it."""
    thin = _make_thin_candidate()
    # without override → blocked
    with pytest.raises(PermissionError):
        _rev.accept(candidate_slug=thin["entry_id"], target_topic="misc")
    # with override → accepted, and the override is recorded for audit
    out = _rev.accept(candidate_slug=thin["entry_id"], target_topic="misc",
                      target_project="lexio", override_must=True)
    fm = _read_fm(Path(out["path"]))
    assert fm["ac_status"] == "passed"
    assert "override_must" in fm["ac_results"]


def test_override_must_cannot_bypass_forbidden(atelier_env: Dict) -> None:
    """forbidden criteria (e.g. pii_leak) are NEVER overridable."""
    cap = _cap.capture(
        observation="config note",
        why="contact admin@example.com with the AKIAIOSFODNN7EXAMPLE key",
        working_dir="/Users/me/workspaces/lexio", hook="manual",
    )
    with pytest.raises(PermissionError) as ei:
        _rev.accept(candidate_slug=cap["entry_id"], target_topic="misc",
                    override_must=True)
    assert ei.value.args[0]["forbidden_triggered"]


# ── no directory churn (RFC 0005 §7.1 — fields, not folders) ─────────────────
#
# The PR-40 empty-candidate-date-folder pruning tests are obsolete: in the field
# model there is no candidates/ date-folder lifecycle to prune (a learning is one
# claim file whose state is a field). These replacements assert the lifecycle
# operations never move or delete the claim file — only its fields change.


def test_accept_does_not_move_or_delete_the_claim_file(atelier_env: Dict) -> None:
    good = _make_good_candidate()
    path = Path(good["path"])
    _rev.accept(candidate_slug=good["entry_id"], target_topic="t",
                target_project="lexio")
    assert path.exists()                  # same file stays put
    # no legacy candidates/ tree was ever created
    assert not (atelier_env["gorae"] / "learnings" / "candidates").exists()


def test_lifecycle_ops_leave_other_claims_untouched(atelier_env: Dict) -> None:
    a = _make_good_candidate()
    b = _make_good_candidate(working_dir="/Users/me/workspaces/bht")
    _rev.accept(candidate_slug=a["entry_id"], target_topic="t",
                target_project="lexio")
    # b is a different lesson → its own claim, untouched by accepting a.
    assert Path(b["path"]).exists()
    assert _read_fm(Path(b["path"]))["ac_status"] == "pending"
