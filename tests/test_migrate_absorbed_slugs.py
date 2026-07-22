"""One-time migration: repair absorbed claims keyed under a mangled slug.

The migration rewrites live vault content, so the property that matters most is
not "does it fix things" but **"can it ever make things worse"**. Claude Code's
directory encoding is lossy, and `absorb.derive_project` deliberately falls back
to the naive basename when it cannot verify the path against a real filesystem —
which is exactly the mangled slug this migration repairs. So on a machine where
those project directories are absent, an unguarded migration would INVERT
itself: rewrite repaired claims back to `frontend`/`hub`, re-mint the bare-noun
entities, and retire the good ones.

These tests pin the guard, the no-op, and the repair.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path
from typing import Dict

import yaml

from runtime.index import parse as _parse
from runtime.service.learnings import absorb_claude as _ac
from runtime.service.learnings import claims_io as _claims
from runtime.structure import resolver as _structure

_SCRIPT = (Path(__file__).resolve().parents[1]
           / "scripts" / "migrate_absorbed_project_slugs")


def _run(*argv: str) -> None:
    """Execute the migration script in-process with the given argv."""
    old = sys.argv
    sys.argv = [str(_SCRIPT), *argv]
    try:
        runpy.run_path(str(_SCRIPT), run_name="__main__")
    except SystemExit as exc:
        assert exc.code in (0, None), f"script exited {exc.code}"
    finally:
        sys.argv = old


def _write_claim(vault: Path, *, eid: str, project: str, source_path: str,
                 is_about: list) -> Path:
    d = vault / _structure.atomic_claim_dir()
    d.mkdir(parents=True, exist_ok=True)
    fm = {"entry_id": eid, "schema_version": 7, "kind": "claim",
          "statement": f"claim {eid}", "domain": "operational",
          "sensitivity": "public", "surfacing": "query", "ac_status": "passed",
          "derived_from": ["src-1"], "is_about": is_about,
          "attributed_to": "absorbed", "agent_kind": "absorbed",
          "generated_by": "mint", "hook": "manual",
          "observation_kind": "feedback", "why_status": "present",
          "links": [], "project": project, "project_hint": project,
          "source_path": source_path}
    p = d / f"{eid}.md"
    p.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False)
                 + "---\n# body\n", encoding="utf-8")
    return p


def _write_entity(vault: Path, *, eid: str, label: str) -> Path:
    d = vault / _structure.atomic_claim_dir()
    d.mkdir(parents=True, exist_ok=True)
    fm = {"entry_id": eid, "schema_version": 7, "kind": "entity",
          "type": "Concept", "pref_label": label, "sensitivity": "public",
          "in_scheme": ["operational"], "links": []}
    p = d / f"entity-{eid}.md"
    p.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False)
                 + "---\n# entity\n", encoding="utf-8")
    return p


def _fm(p: Path) -> Dict:
    fm, _ = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
    return fm


def _memory_path(atelier_env: Dict, real_dir: Path) -> str:
    """The `source_path` absorb records: a file under a Claude Code project
    directory whose NAME is the working dir with `/` replaced by `-`."""
    encoded = str(real_dir).replace("/", "-")
    return str(atelier_env["claude_projects"] / encoded / "memory" / "m1.md")


def _seed(atelier_env: Dict, tmp_path: Path, *, make_real_dir: bool):
    """A claim absorbed from `<tmp>/org/identity-hub`, mangled to `hub`."""
    vault = atelier_env["gorae"]
    real = tmp_path / "org" / "identity-hub"
    if make_real_dir:
        real.mkdir(parents=True)
    ent = _write_entity(vault, eid="ent-hub", label="hub")
    claim = _write_claim(vault, eid="c1", project="hub",
                         source_path=_memory_path(atelier_env, real),
                         is_about=["ent-hub"])
    _ac.derive_project.cache_clear()
    return vault, claim, ent


# ── the repair ───────────────────────────────────────────────────────────────


def test_apply_repairs_slug_and_retires_bare_noun_entity(
        atelier_env: Dict, tmp_path: Path) -> None:
    vault, claim, ent = _seed(atelier_env, tmp_path, make_real_dir=True)
    before = _fm(claim)
    _run("--apply")

    after = _fm(claim)
    assert after["project"] == "identity-hub"          # not the mangled "hub"
    assert after["project_hint"] == after["project"]
    assert after["entry_id"] == before["entry_id"]     # id is NOT re-derived
    assert after["surfacing"] == "query"               # lifecycle untouched
    assert after["ac_status"] == "passed"
    # is_about repointed off the bare-noun entity, and that entity retired
    assert after["is_about"] != before["is_about"]
    assert not ent.exists()
    # the new target resolves to a real entity node
    target = after["is_about"][0]
    labels = {_fm(p).get("pref_label")
              for p in (vault / _structure.atomic_claim_dir()).glob("*.md")
              if _fm(p).get("kind") == "entity"}
    assert "identity-hub" in labels and target


def test_second_apply_is_a_no_op(atelier_env: Dict, tmp_path: Path) -> None:
    vault, claim, _ = _seed(atelier_env, tmp_path, make_real_dir=True)
    _run("--apply")
    first = claim.read_text(encoding="utf-8")
    _run("--apply")
    assert claim.read_text(encoding="utf-8") == first


def test_dry_run_writes_nothing(atelier_env: Dict, tmp_path: Path) -> None:
    vault, claim, ent = _seed(atelier_env, tmp_path, make_real_dir=True)
    before = claim.read_text(encoding="utf-8")
    _run()                                             # no --apply
    assert claim.read_text(encoding="utf-8") == before
    assert ent.exists()                                # nothing retired


def test_dry_run_announces_the_entity_it_would_retire(
        atelier_env: Dict, tmp_path: Path, capsys) -> None:
    """The unlink is the one step a dry run most needs to disclose. This
    assertion exists because the first implementation compared Paths by
    object identity — `rglob` returns fresh objects, so the check was always
    False and the preview silently reported 'none' while --apply retired."""
    _seed(atelier_env, tmp_path, make_real_dir=True)
    _run()
    out = capsys.readouterr().out
    assert "entities to RETIRE (1)" in out
    assert "'hub'" in out


def test_dry_run_preview_matches_what_apply_retires(
        atelier_env: Dict, tmp_path: Path, capsys) -> None:
    """The preview must not under-report: what it names is exactly what the
    apply run unlinks."""
    vault, _claim, ent = _seed(atelier_env, tmp_path, make_real_dir=True)
    _run()
    previewed = "'hub'" in capsys.readouterr().out
    _run("--apply")
    assert previewed is (not ent.exists())


# ── the guard: never guess, never invert ─────────────────────────────────────


def test_absent_project_dir_is_skipped_not_guessed(
        atelier_env: Dict, tmp_path: Path) -> None:
    """The project directory does not exist here, so the decode cannot be
    verified. The script must leave the claim alone rather than rewrite it
    from a guessed path."""
    vault, claim, ent = _seed(atelier_env, tmp_path, make_real_dir=False)
    before = claim.read_text(encoding="utf-8")
    _run("--apply")
    assert claim.read_text(encoding="utf-8") == before
    assert ent.exists()


def test_guard_does_not_invert_an_already_repaired_claim(
        atelier_env: Dict, tmp_path: Path) -> None:
    """The dangerous case: a claim ALREADY carrying the correct slug, on a
    machine missing the project dir. An unguarded run would compute the naive
    basename, see a 'mismatch', and rewrite it back to the mangled value."""
    vault = atelier_env["gorae"]
    gone = tmp_path / "org" / "identity-hub"           # never created
    _write_entity(vault, eid="ent-ok", label="identity-hub")
    claim = _write_claim(vault, eid="c1", project="identity-hub",
                         source_path=_memory_path(atelier_env, gone),
                         is_about=["ent-ok"])
    _ac.derive_project.cache_clear()
    before = claim.read_text(encoding="utf-8")
    _run("--apply")
    assert _fm(claim)["project"] == "identity-hub"     # NOT re-mangled to "hub"
    assert claim.read_text(encoding="utf-8") == before


def test_pre_mint_claims_without_source_path_are_untouched(
        atelier_env: Dict, tmp_path: Path) -> None:
    """Pre-RFC-0007 absorbs carry no `source_path`, so there is nothing to key
    on — they must be skipped, not guessed at."""
    vault = atelier_env["gorae"]
    d = vault / _structure.atomic_claim_dir()
    d.mkdir(parents=True, exist_ok=True)
    fm = {"entry_id": "legacy", "schema_version": 7, "kind": "claim",
          "statement": "a legacy absorbed claim", "domain": "operational",
          "sensitivity": "public", "surfacing": "proactive",
          "ac_status": "passed", "derived_from": ["anchor"], "is_about": [],
          "attributed_to": "absorbed", "agent_kind": "absorbed",
          "generated_by": "ingest", "hook": "manual",
          "observation_kind": "project", "why_status": "present", "links": []}
    p = d / "legacy.md"
    p.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False)
                 + "---\n# body\n", encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    _run("--apply")
    assert p.read_text(encoding="utf-8") == before


# ── entity retirement is conservative ────────────────────────────────────────


def test_entity_still_linked_from_another_entity_is_not_retired(
        atelier_env: Dict, tmp_path: Path) -> None:
    """Retirement must scan entity→entity `links`, not just claims'
    `is_about` — an entity referenced only from the entity layer would
    otherwise be orphaned."""
    vault, claim, ent = _seed(atelier_env, tmp_path, make_real_dir=True)
    other = _write_entity(vault, eid="ent-other", label="something else")
    fm = _fm(other)
    fm["links"] = [{"to": "ent-hub", "rel": "refines", "why": "keeps it alive"}]
    other.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False)
                     + "---\n# entity\n", encoding="utf-8")

    _run("--apply")
    assert _fm(claim)["project"] == "identity-hub"     # claim still repaired
    assert ent.exists()                                # but the entity survives
