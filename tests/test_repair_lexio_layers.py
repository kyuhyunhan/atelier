"""P6 — in-place repair of workshop-absorb damaged records (RFC 0001 §2.2)."""
from __future__ import annotations

from typing import Dict

from runtime.index.parse import split_frontmatter
from scripts.repair_lexio_layers import repair as _rp
from tests.conftest import write_page


_DAMAGED = {
    "schema_version": 5, "agent_kind": "absorbed", "status": "accepted",
    "ac_status": "passed", "observation_kind": "project",
    "captured_at": "2026-05-14T05:10:00Z", "accepted_at": "2026-05-28T00:00:00Z",
}


def _seed_workshop_note(vault, name, *, layer, also_in) -> None:
    body = ("---\n"
            f"layer: {layer}\n"
            "also_in:\n" + "".join(f"  - {a}\n" for a in also_in) +
            "---\nbody\n")
    p = vault / "workshop" / "products" / "lexio" / "memory" / layer / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_repair_moves_flattened_topic_to_aspect(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    # damaged: target_topic holds the project-local layer; no aspect, no also_in.
    write_page(vault / "provenance" / "learning" / "notes" / "2026-05" / "d1.md",
               {**_DAMAGED, "entry_id": "D1", "target_project": "lexio",
                "target_topic": "cross-cutting"}, "## Observation\n\nx\n")
    # also_in survives only in the live workshop note.
    _seed_workshop_note(vault, "d1.md", layer="cross-cutting", also_in=["product"])

    rep = _rp.repair(vault, apply=True)
    assert rep["repaired"] == 1
    assert rep["recovered_also_in"] == 1

    fm, _ = split_frontmatter(
        (vault / "provenance/learning/notes/2026-05/d1.md").read_text())
    assert fm["aspect"] == ["cross-cutting", "product"]
    assert "target_topic" not in fm


def test_repair_is_idempotent(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    write_page(vault / "provenance" / "learning" / "notes" / "2026-05" / "d1.md",
               {**_DAMAGED, "entry_id": "D1", "target_project": "lexio",
                "target_topic": "client"}, "## Observation\n\nx\n")
    _rp.repair(vault, apply=True)
    rep2 = _rp.repair(vault, apply=True)
    assert rep2["repaired"] == 0
    assert rep2["already_ok"] == 1


def test_repair_skips_native_learnings(vault_env: Dict) -> None:
    """A non-absorbed learning with a legitimate global topic is untouched."""
    vault = vault_env["vault"]
    write_page(vault / "provenance" / "learning" / "notes" / "2026-05" / "n1.md",
               {**_DAMAGED, "agent_kind": "claude-code", "entry_id": "N1",
                "target_project": "atelier", "target_topic": "surfacing-audit"},
               "## Observation\n\nx\n")
    rep = _rp.repair(vault, apply=True)
    assert rep["repaired"] == 0
    fm, _ = split_frontmatter(
        (vault / "provenance/learning/notes/2026-05/n1.md").read_text())
    assert fm["target_topic"] == "surfacing-audit"   # untouched


def test_repair_without_workshop_uses_topic_as_primary(vault_env: Dict) -> None:
    """If the workshop source is gone (no also_in to recover), the flattened
    topic still becomes the primary aspect — lossless."""
    vault = vault_env["vault"]
    write_page(vault / "provenance" / "learning" / "notes" / "2026-05" / "d2.md",
               {**_DAMAGED, "entry_id": "D2", "target_project": "lexio",
                "target_topic": "server"}, "## Observation\n\nx\n")
    rep = _rp.repair(vault, apply=True)
    assert rep["repaired"] == 1
    fm, _ = split_frontmatter(
        (vault / "provenance/learning/notes/2026-05/d2.md").read_text())
    assert fm["aspect"] == ["server"]
    assert "target_topic" not in fm
