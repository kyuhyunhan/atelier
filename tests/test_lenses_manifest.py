"""RFC 0006 Pillar ① — lens vocabulary + vault manifest."""
from __future__ import annotations

from pathlib import Path

from runtime.structure import lenses as _lenses
from runtime.structure import manifest as _manifest


# ── lens vocabulary ─────────────────────────────────────────────────────────

def test_lens_names_and_default() -> None:
    assert set(_lenses.lens_names()) == {"dev", "life", "full"}
    assert _lenses.default_lens() == "full"


def test_dev_lens_admits_operational_and_knowledge_not_personal() -> None:
    assert _lenses.matches("dev", "claim", "operational")
    assert _lenses.matches("dev", "claim", "knowledge")
    assert _lenses.matches("dev", "source", "knowledge")
    assert _lenses.matches("dev", "entity", "knowledge")   # in_scheme membership
    # the whole point: personal is excluded from a coding session
    assert not _lenses.matches("dev", "claim", "personal")
    assert not _lenses.matches("dev", "source", "personal")
    assert not _lenses.matches("dev", "entity", "personal")


def test_life_and_full_lenses() -> None:
    assert _lenses.matches("life", "claim", "personal")
    assert _lenses.matches("life", "source", "knowledge")
    assert not _lenses.matches("life", "claim", "operational")   # life is not dev
    # full is the no-wall lens: admits everything
    for k in ("claim", "source", "entity"):
        for d in ("personal", "knowledge", "operational", "inbox", "workshop"):
            assert _lenses.matches("full", k, d)


def test_dev_lens_all_match_entity_semantics() -> None:
    # all-match: a single-scheme knowledge entity is admitted …
    assert _lenses.admits_entity("dev", ["knowledge"])
    # … but a mixed entity carrying personal is NOT (no personal leak into dev)
    assert not _lenses.admits_entity("dev", ["knowledge", "personal"])
    assert not _lenses.admits_entity("dev", ["personal"])
    # empty in_scheme: only the wildcard (full) lens admits
    assert _lenses.admits_entity("full", [])
    assert not _lenses.admits_entity("dev", [])
    # full admits the mixed entity (no wall)
    assert _lenses.admits_entity("full", ["knowledge", "personal"])


def test_coverage_gate_holds() -> None:
    pairs = [(k, d) for k in ("claim", "source", "entity")
             for d in ("personal", "knowledge", "inbox", "workshop", "operational")]
    v = _lenses.validate_coverage(pairs)
    assert v["ok"] is True
    assert v["uncovered"] == []
    assert v["dev_personal_leaks"] == []


# ── vault manifest ───────────────────────────────────────────────────────────

def test_manifest_ensure_is_idempotent(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    m1 = _manifest.ensure(vault)
    assert (vault / ".atelier-vault.yaml").is_file()
    assert m1["structure_version"] == _manifest.CURRENT_STRUCTURE_VERSION
    assert m1["vault_id"]
    # second ensure must NOT mint a new id
    m2 = _manifest.ensure(vault)
    assert m2["vault_id"] == m1["vault_id"]


def test_manifest_validate(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    assert _manifest.validate(vault)["ok"] is False        # absent → not ok
    _manifest.ensure(vault)
    v = _manifest.validate(vault)
    assert v["ok"] is True and v["present"] and v["version_ok"]
