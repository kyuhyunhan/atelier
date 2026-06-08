"""P5 — corrected workshop-memory → flat-learnings absorb (RFC 0001 §2.2).

The bug being fixed: the old absorb mapped a note's project-local category onto
the GLOBAL target_topic and dropped also_in / typed links. The corrected absorb
maps layer→aspect (primary), also_in→aspect (secondary), preserves typed links,
leaves target_topic UNSET, and writes one flat note (no mirror).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from runtime.index.parse import split_frontmatter
from runtime.service.learnings import store as _store
from scripts.absorb_workshop_memory_to_learnings import absorb as _ab


def _seed_workshop(workshop_root: Path) -> None:
    """vault/workshop/products/lexio/memory/<dir>/<file>.md fixtures, in the
    lexio dialect (layer + also_in + typed links)."""
    base = workshop_root / "products" / "lexio" / "memory"
    (base / "cross-cutting").mkdir(parents=True)
    (base / "cross-cutting" / "release.md").write_text(
        "---\n"
        "schema_version: 3\n"
        "title: release notes\n"
        "layer: cross-cutting\n"
        "also_in:\n  - product\n"
        "links:\n  - to: '20260513T1700'\n    why: extends the policy\n"
        "---\n"
        "## Observation\nrelease cuts the wrong sha\n"
        "## Why this matters\nshipped wrong build\n",
        encoding="utf-8",
    )
    (base / "client").mkdir()
    (base / "client" / "ui.md").write_text(
        "---\nschema_version: 3\n---\nclient ui issue\n",   # no layer field
        encoding="utf-8",
    )


def _flat_files(vault: Path) -> List[Path]:
    return list(_store.iter_accepted_files(vault))


def test_dry_run_no_writes(atelier_env: Dict, capsys) -> None:
    _seed_workshop(atelier_env["gorae"] / "workshop")
    rc = _ab.absorb(apply=False)
    assert rc == 0
    assert not (atelier_env["gorae"] / "learnings").exists()
    out = capsys.readouterr().out
    assert "plans:" in out and "dry-run" in out


def test_apply_writes_flat_notes_with_aspects_no_topic(atelier_env: Dict) -> None:
    _seed_workshop(atelier_env["gorae"] / "workshop")
    rc = _ab.absorb(apply=True)
    assert rc == 0
    vault = atelier_env["gorae"]

    # Flat store, no mirror trees.
    assert not (vault / "learnings" / "accepted" / "by-topic").exists()
    assert not (vault / "learnings" / "accepted" / "by-project").exists()
    files = {p.name: p for p in _flat_files(vault)}
    assert set(files) == {"release.md", "ui.md"}
    assert all("/learnings/notes/" in str(p) for p in files.values())

    # release.md: layer→aspect primary, also_in→aspect secondary, links kept,
    # NO target_topic, schema_version 5.
    fm, body = split_frontmatter(files["release.md"].read_text(encoding="utf-8"))
    assert fm["schema_version"] == 5
    assert fm["status"] == "accepted"
    assert fm["target_project"] == "lexio"
    assert fm["aspect"] == ["cross-cutting", "product"]
    assert "target_topic" not in fm
    assert fm["links"] == [{"to": "20260513T1700", "why": "extends the policy"}]
    assert "release cuts the wrong sha" in body

    # ui.md: no layer field → aspect falls back to the memory subdirectory.
    fm2, _ = split_frontmatter(files["ui.md"].read_text(encoding="utf-8"))
    assert fm2["aspect"] == ["client"]
    assert "target_topic" not in fm2


def test_apply_is_idempotent_via_stable_entry_id(atelier_env: Dict) -> None:
    _seed_workshop(atelier_env["gorae"] / "workshop")
    assert _ab.absorb(apply=True) == 0
    # Second run: destinations already exist → reported as conflicts, refuses.
    rc = _ab.absorb(apply=True)
    assert rc == 2
    # still exactly two notes (no duplication)
    assert len(_flat_files(atelier_env["gorae"])) == 2


def test_missing_workshop_handled(atelier_env: Dict) -> None:
    assert _ab.absorb(apply=False) == 0
