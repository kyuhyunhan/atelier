"""Lint rules end-to-end on a small synthetic gorae."""
from __future__ import annotations

from tests.conftest import write_page


def test_L1_catches_broken_raw_link(atelier_env):
    from runtime.service import api
    gorae = atelier_env["gorae"]
    write_page(
        gorae / "wiki" / "sources" / "ghost.md",
        {"title": "ghost", "type": "source", "raw": "[[raw/doesnt-exist.md]]",
         "visibility": "private", "created": "2026-05-27", "updated": "2026-05-27"},
        "see [[raw/personal/writings/nonexistent.md]]",
    )
    api.reindex(space="gorae", full=True)
    out = api.lint(space="gorae", rule_ids=["L1"])
    fails = [f for f in out["findings"] if f["severity"] == "FAIL"]
    assert fails, "L1 should fail on broken raw link"
    assert out["failed"]


def test_L1_catches_broken_provenance_link_from_graph(atelier_env):
    """Canonical post-GP1 coverage: a graph/ page with a broken [[provenance/...]]
    link is flagged (the legacy wiki/raw test above only exercises the old branch)."""
    from runtime.service import api
    gorae = atelier_env["gorae"]
    write_page(
        gorae / "graph" / "sources" / "ghost.md",
        {"title": "ghost", "type": "source",
         "raw": "[[provenance/doesnt-exist.md]]",
         "visibility": "private", "created": "2026-05-27", "updated": "2026-05-27"},
        "see [[provenance/personal/writings/nonexistent.md]]",
    )
    api.reindex(space="gorae", full=True)
    out = api.lint(space="gorae", rule_ids=["L1"])
    fails = [f for f in out["findings"] if f["severity"] == "FAIL"]
    assert fails, "L1 should fail on a broken provenance link from a graph page"


def test_L3_source_count_drift_and_fix(atelier_env):
    from runtime.service import api
    from runtime.util import db

    gorae = atelier_env["gorae"]
    # Entity declares source_count=10 but only 0 inbound links.
    write_page(
        gorae / "wiki" / "entities" / "drift.md",
        {"title": "drift", "type": "entity", "category": "concept",
         "first_mention": "2026-01", "source_count": 10,
         "created": "2026-05-27", "updated": "2026-05-27"},
        "(drift)",
    )
    api.reindex(space="gorae", full=True)
    out = api.lint(space="gorae", rule_ids=["L3"])
    warns = [f for f in out["findings"] if f["severity"] == "WARN"]
    assert warns, "L3 should warn on big source_count drift"

    # Apply the fix and re-lint
    out2 = api.lint(space="gorae", rule_ids=["L3"], apply_fixes=True)
    assert out2["fixes_applied"] >= 1
    api.reindex(space="gorae", full=True)
    out3 = api.lint(space="gorae", rule_ids=["L3"])
    assert not [f for f in out3["findings"] if f["severity"] == "WARN"]


def test_L5_orphan(atelier_env):
    """Orphan detection must cover the canonical graph/ tree (post-GP1). A
    graph/entities page with zero inbound links is an orphan; before the fix L5
    only scanned wiki/ and silently missed every real entity."""
    from runtime.service import api
    gorae = atelier_env["gorae"]
    write_page(
        gorae / "graph" / "entities" / "lonely.md",
        {"title": "lonely", "type": "entity", "category": "concept",
         "source_count": 0, "created": "2026-05-27", "updated": "2026-05-27"},
        "(no inbound links)",
    )
    api.reindex(space="gorae", full=True)
    out = api.lint(space="gorae", rule_ids=["L5"])
    orphans = [f for f in out["findings"] if "lonely" in (f["page_slug"] or "")]
    assert orphans
