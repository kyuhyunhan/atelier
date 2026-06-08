"""P4 — the on-disk flatten migration (RFC 0001)."""
from __future__ import annotations

from typing import Dict

from runtime.index.parse import split_frontmatter
from scripts.migrate_learnings_flat import migrate as _mig
from tests.conftest import write_page


_ACC = {
    "schema_version": 4, "agent_kind": "claude-code", "status": "accepted",
    "ac_status": "passed", "observation_kind": "project",
    "captured_at": "2026-05-14T05:10:00Z", "accepted_at": "2026-05-28T00:00:00Z",
}


def _seed(vault) -> None:
    write_page(vault / "learnings" / "accepted" / "by-topic" / "cross-cutting" /
               "n1.md", {**_ACC, "entry_id": "E1", "target_project": "lexio"},
               "## Observation\n\nbody one\n")
    write_page(vault / "learnings" / "accepted" / "by-topic" / "client" /
               "n2.md", {**_ACC, "entry_id": "E2", "captured_at":
                         "2026-03-02T00:00:00Z"}, "## Observation\n\nbody two\n")
    # an INDEX file must NOT be moved
    write_page(vault / "learnings" / "accepted" / "by-topic" / "client" /
               "INDEX.md", {"schema_version": 4, "entry_id": "ix",
                            "type": "learnings_index"}, "generated\n")


def test_dry_run_moves_nothing(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    _seed(vault)
    rep = _mig.migrate(vault, apply=False)
    assert rep["counts"]["moved"] == 2
    # nothing actually moved
    assert (vault / "learnings/accepted/by-topic/cross-cutting/n1.md").exists()
    assert not (vault / "learnings/notes").exists()


def test_apply_flattens_by_month_and_bumps_v5(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    _seed(vault)
    _mig.migrate(vault, apply=True)
    # sharded by captured_at month, slug preserved
    d1 = vault / "learnings/notes/2026-05/n1.md"
    d2 = vault / "learnings/notes/2026-03/n2.md"
    assert d1.exists() and d2.exists()
    # source removed
    assert not (vault / "learnings/accepted/by-topic/cross-cutting/n1.md").exists()
    # schema_version bumped
    fm, _ = split_frontmatter(d1.read_text())
    assert fm["schema_version"] == 5
    # INDEX.md left in place (not a learning)
    assert (vault / "learnings/accepted/by-topic/client/INDEX.md").exists()


def test_apply_is_idempotent(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    _seed(vault)
    _mig.migrate(vault, apply=True)
    rep2 = _mig.migrate(vault, apply=True)      # second run: all already placed
    assert rep2["counts"]["moved"] == 0
    assert rep2["counts"]["skipped"] == 0       # sources already gone


def test_readers_find_flattened_notes(vault_env: Dict) -> None:
    """End-to-end: after flattening + reindex, recall surfaces a moved note."""
    from runtime.service import api
    from runtime.service.learnings import recall as _rc
    vault = vault_env["vault"]
    _seed(vault)
    _mig.migrate(vault, apply=True)
    api.reindex(full=True)
    out = _rc.recall(query="body one", top_k=5)
    assert out["count"] >= 1
    assert any("n1" in it["slug"] for it in out["items"])
