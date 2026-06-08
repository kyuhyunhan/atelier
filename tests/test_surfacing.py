"""Phase 5a — the surfacing observer: can the system see what stops surfacing?

The headline test reproduces a real silent-omission vector: a learning whose
concept lives only in its frontmatter tag (not its body) is found by the
filesystem fallback but goes DARK once the FTS index is the live path — exactly
the kind of drop a git diff cannot show. The observer catches it.
"""
from __future__ import annotations

from typing import Dict

import yaml

from runtime.service import api
from runtime.service.learnings import surfacing as _sf
from tests.conftest import write_page


_BASE = {
    "schema_version": 4, "agent_kind": "claude-code", "status": "accepted",
    "ac_status": "passed", "observation_kind": "feedback",
    "captured_at": "2026-01-01T00:00:00Z", "accepted_at": "2026-01-02T00:00:00Z",
}


def _accepted(vault, topic, entry_id, body, *, project=None, touches=None):
    fm = {**_BASE, "entry_id": entry_id, "target_topic": topic}
    if project:
        fm["target_project"] = project
    if touches:
        fm["touches"] = touches
    write_page(vault / "learnings" / "notes" / "2026-01" /
               f"{entry_id}.md", fm, body)


def test_snapshot_marks_self_findable_learning_visible(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    _accepted(vault, "architecture", "v1",
              "## Observation\n\ndepend on protocols not implementations\n",
              touches=["dependency-direction"])
    api.reindex(full=True)
    snap = _sf.snapshot()
    assert snap["v1"]["visible"] is True
    assert snap["v1"]["rank"] is not None


def test_audit_flags_learning_dark_under_fts(vault_env: Dict) -> None:
    """A learning whose concept is only in its frontmatter `touches`/`topic` —
    never in its body — is invisible to body-indexed FTS. The audit flags it as
    dark; a content diff never would."""
    vault = vault_env["vault"]
    # `ghost`'s concept lives only in its tags; its body avoids the words.
    _accepted(vault, "architecture", "ghost",
              "## Observation\n\nfoo bar baz qux\n",
              touches=["dependency-direction"])
    # Competition: decoys whose *bodies* contain the probe words. FTS returns
    # these (non-empty) so the fs fallback never fires — and `ghost`, absent
    # from the body index, is buried out of reach: the real omission vector.
    for i in range(3):
        _accepted(vault, "architecture", f"decoy{i}",
                  f"## Observation\n\narchitecture dependency direction note {i}\n")
    api.reindex(full=True)

    report = _sf.audit()
    dark_ids = {d["entry_id"] for d in report["dark"]}
    assert "ghost" in dark_ids          # buried under FTS competition → dark
    assert "decoy0" not in dark_ids     # body-findable → visible


def test_diff_detects_newly_dark(vault_env: Dict) -> None:
    """The omission detector: a learning visible before but not after is the
    signal a reorganization pass must surface."""
    before = {"x": {"visible": True, "rank": 0, "title": "X", "project": "p",
                    "probe": "x"}}
    after = {"x": {"visible": False, "rank": None, "title": "X", "project": "p",
                   "probe": "x"}}
    d = _sf.diff(before, after)
    assert [e["entry_id"] for e in d["newly_dark"]] == ["x"]
    assert d["regressions"] == 1


def test_surfacing_audit_mcp_dispatch(vault_env: Dict) -> None:
    import asyncio
    from runtime.service import tools as _tools

    _accepted(vault_env["vault"], "architecture", "v1",
              "## Observation\n\ndepend on protocols not implementations\n",
              touches=["dependency-direction"])
    api.reindex(full=True)

    out = asyncio.run(_tools.invoke("atelier_learning_surfacing_audit"))
    assert "dark" in out and "total" in out
    assert out["total"] == 1


def test_diff_separates_deletions_from_regressions(vault_env: Dict) -> None:
    """A curated deletion (in `before`, gone from `after`) is reported under
    `removed`, NOT counted as a retrieval regression — else every retire pass
    would raise a false alarm (review SHOULD-3)."""
    before = {"keep": {"visible": True, "rank": 0, "title": "K", "project": "p",
                       "probe": "k"},
              "gone": {"visible": True, "rank": 1, "title": "G", "project": "p",
                       "probe": "g"}}
    after = {"keep": {"visible": True, "rank": 0, "title": "K", "project": "p",
                      "probe": "k"}}
    d = _sf.diff(before, after)
    assert d["removed"] == ["gone"]
    assert d["removed_count"] == 1
    assert d["regressions"] == 0          # deletion is curation, not omission
    assert d["newly_dark"] == []


def test_audit_excludes_navigational_views(vault_env: Dict) -> None:
    """INDEX/TAXONOMY are generated/navigational views that recall's noise
    filter can never return — probing them makes them dark BY CONSTRUCTION
    (observed on the live vault: TAXONOMY was permanently dark). The audit
    must share recall's noise predicate and skip them entirely."""
    vault = vault_env["vault"]
    _accepted(vault, "general", "real",
              "## Observation\n\nreal learning body words\n")
    # an absorbed memory-model view: has an entry_id, but is a view, not a learning
    write_page(vault / "learnings" / "notes" / "2026-01" / "TAXONOMY.md",
               {**_BASE, "entry_id": "tax", "target_topic": "general"},
               "vocabulary tables\n")
    api.reindex(full=True)

    snap = _sf.snapshot()
    assert "tax" not in snap, "views must not be probed (dark by construction)"
    assert "real" in snap
