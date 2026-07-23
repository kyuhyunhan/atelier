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


# ── L8: a claim derived from a PRIVATE source must be private ───────────────
#
# "Private source" is two conditions, and the rule long checked only one.
# Policy 1 covers a private-DOMAIN source (personal). RFC 0008 M4 introduced
# the other: an absorbed `type: user` memory or a PII-pattern hit is demoted to
# `sensitivity: private` while staying in the `operational` domain — invisible
# to a domain-only predicate, and precisely the case M3's write-time guard
# abstains on when it cannot resolve the Source.


def _l8_source(gorae, eid, *, domain, sensitivity):
    write_page(
        gorae / "raw" / domain / f"{eid}.md",
        {"entry_id": eid, "schema_version": 7, "kind": "source",
         "title": eid, "domain": domain, "sensitivity": sensitivity},
        f"# {eid}\n",
    )


def _l8_claim(gorae, eid, *, derived_from, sensitivity):
    write_page(
        gorae / "graph" / "atomic" / f"{eid}.md",
        {"entry_id": eid, "schema_version": 7, "kind": "claim",
         "statement": f"claim {eid}", "derived_from": [derived_from],
         "domain": "operational", "sensitivity": sensitivity,
         "surfacing": "query"},
        f"## Claim\n\n{eid}\n",
    )


def _l8_run(space="gorae"):
    from runtime.service import api
    api.reindex(space=space, full=True)
    out = api.lint(space=space, rule_ids=["L8"])
    return [f for f in out["findings"] if f["severity"] == "FAIL"]


def test_L8_flags_a_public_claim_from_a_private_domain_source(atelier_env):
    """Policy 1, the original half."""
    g = atelier_env["gorae"]
    _l8_source(g, "src-personal", domain="personal", sensitivity="private")
    _l8_claim(g, "claim-leak", derived_from="src-personal", sensitivity="public")
    fails = _l8_run()
    assert any("claim-leak" in f["page_slug"] for f in fails)


def test_L8_flags_a_public_claim_from_a_sensitivity_private_source(atelier_env):
    """RFC 0008 M4: an `operational` Source demoted to private. A domain-only
    predicate is blind to this — the exact gap the abstain-on-miss path needs
    a backstop for."""
    g = atelier_env["gorae"]
    _l8_source(g, "src-demoted", domain="operational", sensitivity="private")
    _l8_claim(g, "claim-widened", derived_from="src-demoted",
              sensitivity="public")
    fails = _l8_run()
    assert any("claim-widened" in f["page_slug"] for f in fails)
    assert any("sensitivity:private" in f["message"] for f in fails)


def test_L8_passes_when_the_derived_claim_is_private(atelier_env):
    g = atelier_env["gorae"]
    _l8_source(g, "src-demoted", domain="operational", sensitivity="private")
    _l8_claim(g, "claim-ok", derived_from="src-demoted", sensitivity="private")
    assert not [f for f in _l8_run() if "claim-ok" in f["page_slug"]]


def test_L8_leaves_a_public_source_alone(atelier_env):
    """The rule tightens only — a public source's public claims are fine."""
    g = atelier_env["gorae"]
    _l8_source(g, "src-public", domain="operational", sensitivity="public")
    _l8_claim(g, "claim-public", derived_from="src-public",
              sensitivity="public")
    assert not [f for f in _l8_run() if "claim-public" in f["page_slug"]]
