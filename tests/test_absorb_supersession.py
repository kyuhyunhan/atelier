"""RFC 0008 M2 — supersession: the path-indexed ledger.

The dedup ledger keyed by body hash alone cannot tell an upstream EDIT from a
NEW memory: the edited body hashes differently, so a naive absorb mints a second
claim and leaves the first live and stale. M2 adds a machine-independent
`by_path` index, which turns "same file, new hash" into a deterministic fact —
this memory was revised.

Which KIND of revision decides everything, and it is not about the body. The
claim id is `f(statement, source_id)` and the operational Source id is
`f(statement)` alone, where the statement is the memory's `description`. So:

- description unchanged → the SAME nodes. Refresh the Source body; never touch
  the claim (its lifecycle may have moved on).
- description changed → genuinely new nodes. Link `refines` and retract the old
  claim — unless another memory file still owns it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

import yaml

from runtime.index.parse import split_frontmatter
from runtime.service.learnings import absorb_claude as _ac
from runtime.service.learnings import claims_io as _claims
from runtime.structure import resolver as _structure


def _seed(root: Path, project_dir: str, name: str, *, description: str,
          body: str, type_: str = "feedback") -> Path:
    p = root / project_dir / "memory" / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\nname: {name}\ndescription: {description}\n"
                 f"type: {type_}\n---\n{body}", encoding="utf-8")
    return p


def _croot(env: Dict) -> Path:
    return env["claude_projects"]


def _fm(path) -> Dict:
    fm, _ = split_frontmatter(Path(path).read_text(encoding="utf-8"))
    return fm


def _ledger(env: Dict) -> Dict:
    p = env["gorae"] / ".absorbed-from-claude.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _mutate_claim(path, **fields) -> None:
    fm, body = split_frontmatter(Path(path).read_text(encoding="utf-8"))
    fm.update(fields)
    Path(path).write_text(
        "---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True)
        + "---\n" + body, encoding="utf-8")


# ── the machine-independent key ──────────────────────────────────────────────


def test_memory_key_drops_the_absolute_path() -> None:
    """The ledger is git-tracked so dedup holds across machines; an absolute
    `/Users/<someone>/.claude/...` key would never match elsewhere and
    supersession would silently never fire there."""
    k = _ac.memory_key("/Users/anyone/.claude/projects/-w-proj/memory/m1.md")
    assert k == "-w-proj/m1.md"
    # the same memory seen from a different home resolves to the SAME key
    assert k == _ac.memory_key("/home/other/.claude/projects/-w-proj/memory/m1.md")


# ── ledger migration ─────────────────────────────────────────────────────────


def test_flat_ledger_migrates_to_indexed_shape(atelier_env: Dict) -> None:
    """A pre-M2 flat ledger is rebuilt on read, deriving `by_path` from each
    entry's own `source_path`. Lossless and one-time."""
    legacy = {
        "sha-a": {"source_path": "/x/.claude/projects/-w-p1/memory/a.md",
                  "absorbed_at": "2026-07-01T00:00:00+00:00",
                  "project": "p1", "type": "feedback"},
        "sha-b": {"source_path": "/x/.claude/projects/-w-p2/memory/b.md",
                  "absorbed_at": "2026-07-02T00:00:00+00:00",
                  "project": "p2", "type": "reference"},
    }
    out = _ac._migrate_ledger(legacy)
    assert set(out) == {"by_sha", "by_path"}
    assert out["by_sha"] == legacy                       # nothing lost
    assert out["by_path"] == {"-w-p1/a.md": "sha-a", "-w-p2/b.md": "sha-b"}
    # membership still answers through the one accessor
    assert _ac._is_absorbed(out, "sha-a") is True
    assert _ac._is_absorbed(out, "nope") is False


def test_migration_is_idempotent(atelier_env: Dict) -> None:
    already = {"by_sha": {"s": {"source_path": "/x/-w-p/memory/m.md"}},
               "by_path": {"-w-p/m.md": "s"}}
    assert _ac._migrate_ledger(dict(already)) == already


def test_entry_without_source_path_gets_no_path_index(atelier_env: Dict) -> None:
    """Grandfathered entries simply cannot supersede — forward-only, the same
    posture RFC 0007 took with the legacy anchor."""
    out = _ac._migrate_ledger({"sha-x": {"absorbed_at": "2026-01-01T00:00:00Z"}})
    assert out["by_sha"] == {"sha-x": {"absorbed_at": "2026-01-01T00:00:00Z"}}
    assert out["by_path"] == {}


# ── (a) body-only revision: claim untouched, Source refreshed ────────────────


def test_body_only_revision_leaves_a_promoted_claim_byte_identical(
        atelier_env: Dict) -> None:
    """The pin that matters most: a claim promoted after its first absorb must
    survive a re-absorb of its revised memory completely unchanged."""
    root = _croot(atelier_env)
    p = _seed(root, "-w-p1", "m1", description="a durable rule", body="v1\n")
    first = _ac.absorb(dry_run=False, source_root=root)
    claim_path = Path(first["accepted"][0]["path"])

    # the lifecycle moves on
    _mutate_claim(claim_path, surfacing="proactive", ac_status="passed",
                  accepted_at="2026-07-01T00:00:00+00:00",
                  links=[{"to": "some-other", "rel": "refines",
                          "why": "curated by hand"}])
    before = claim_path.read_text(encoding="utf-8")

    p.write_text(p.read_text().replace("v1", "v2 much longer body"),
                 encoding="utf-8")
    second = _ac.absorb(dry_run=False, source_root=root)

    assert claim_path.read_text(encoding="utf-8") == before      # untouched
    assert second["accepted"][0]["body_refreshed"] is True
    assert "supersedes" not in second["accepted"][0]


def test_body_only_revision_updates_source_body_sha_and_keeps_id(
        atelier_env: Dict) -> None:
    root = _croot(atelier_env)
    p = _seed(root, "-w-p1", "m1", description="a durable rule", body="v1\n")
    _ac.absorb(dry_run=False, source_root=root)
    src_dir = atelier_env["gorae"] / _structure.operational_source_dir()
    src_file = next(iter(src_dir.glob("*.md")))
    before = _fm(src_file)

    p.write_text(p.read_text().replace("v1", "v2"), encoding="utf-8")
    _ac.absorb(dry_run=False, source_root=root)

    after = _fm(src_file)
    assert after["entry_id"] == before["entry_id"]          # address preserved
    assert after["created_at"] == before["created_at"]      # birth preserved
    assert after["body_sha"] != before["body_sha"]          # tracks upstream
    assert after["revised_at"]
    assert len(list(src_dir.glob("*.md"))) == 1             # no fork


def test_by_path_advances_to_the_new_sha(atelier_env: Dict) -> None:
    root = _croot(atelier_env)
    p = _seed(root, "-w-p1", "m1", description="a rule", body="v1\n")
    _ac.absorb(dry_run=False, source_root=root)
    v1_sha = _ledger(atelier_env)["by_path"]["-w-p1/m1.md"]

    p.write_text(p.read_text().replace("v1", "v2"), encoding="utf-8")
    _ac.absorb(dry_run=False, source_root=root)
    led = _ledger(atelier_env)
    assert led["by_path"]["-w-p1/m1.md"] != v1_sha           # advanced
    assert v1_sha in led["by_sha"]                           # history kept
    assert len(led["by_sha"]) == 2


# ── (b) description revision: retract + refines ──────────────────────────────


def test_description_change_supersedes_the_old_claim(atelier_env: Dict) -> None:
    root = _croot(atelier_env)
    p = _seed(root, "-w-p1", "m1", description="the old wording", body="b\n")
    first = _ac.absorb(dry_run=False, source_root=root)
    old_path = Path(first["accepted"][0]["path"])
    old_id = _fm(old_path)["entry_id"]

    p.write_text(p.read_text().replace("the old wording", "a sharper wording"),
                 encoding="utf-8")
    second = _ac.absorb(dry_run=False, source_root=root)
    rec = second["accepted"][0]
    new_path = Path(rec["path"])

    assert new_path != old_path                              # a genuinely new claim
    assert rec["supersedes"] == old_id and rec["superseded"] is True
    # old claim retracted through the SAME field every retraction uses
    old_fm = _fm(old_path)
    assert old_fm["ac_status"] == "retracted"
    assert old_fm["archived_at"] and "superseded by" in old_fm["archive_reason"]
    # the refines edge rides the NEW claim
    links = _fm(new_path).get("links") or []
    assert any(l.get("to") == old_id and l.get("rel") == "refines"
               for l in links)


def test_retracted_supersedee_leaves_promote_eligibility(
        atelier_env: Dict) -> None:
    """Retraction must actually gate the old claim, not just annotate it."""
    root = _croot(atelier_env)
    p = _seed(root, "-w-p1", "m1", description="old wording", body="b\n")
    first = _ac.absorb(dry_run=False, source_root=root)
    old_path = Path(first["accepted"][0]["path"])
    _mutate_claim(old_path, surfacing="query", ac_status="passed")
    assert _claims.is_promote_eligible(_fm(old_path)) is True

    p.write_text(p.read_text().replace("old wording", "new wording"),
                 encoding="utf-8")
    _ac.absorb(dry_run=False, source_root=root)
    assert _claims.is_promote_eligible(_fm(old_path)) is False


# ── (c) shared-description guard ─────────────────────────────────────────────


def test_retract_is_skipped_while_another_memory_owns_the_claim(
        atelier_env: Dict) -> None:
    """Two memory files sharing one description collapse onto ONE
    content-addressed claim. Revising one must not retract a claim the other
    still owns."""
    root = _croot(atelier_env)
    shared = "a rule two projects share"
    p1 = _seed(root, "-w-p1", "m1", description=shared, body="body one\n")
    _seed(root, "-w-p2", "m2", description=shared, body="body two\n")
    first = _ac.absorb(dry_run=False, source_root=root)
    # both memories minted to the same claim
    paths = {r["path"] for r in first["accepted"]}
    assert len(paths) == 1
    shared_path = Path(next(iter(paths)))

    p1.write_text(p1.read_text().replace(shared, "p1 goes its own way"),
                  encoding="utf-8")
    second = _ac.absorb(dry_run=False, source_root=root)
    rec = next(r for r in second["accepted"] if r["src"] == str(p1))

    assert rec["supersedes"]                       # the link is still recorded
    assert "superseded" not in rec                 # but nothing was retracted
    assert _fm(shared_path)["ac_status"] != "retracted"


# ── re-run stays a no-op ─────────────────────────────────────────────────────


def test_rerun_after_supersession_is_a_no_op(atelier_env: Dict) -> None:
    root = _croot(atelier_env)
    p = _seed(root, "-w-p1", "m1", description="wording one", body="v1\n")
    _ac.absorb(dry_run=False, source_root=root)
    p.write_text(p.read_text().replace("wording one", "wording two"),
                 encoding="utf-8")
    _ac.absorb(dry_run=False, source_root=root)

    led_before = _ledger(atelier_env)
    out = _ac.absorb(dry_run=False, source_root=root)
    assert out["accepted"] == [] and out["candidates"] == []
    assert len(out["deduped"]) == 1
    assert _ledger(atelier_env) == led_before


def test_new_memory_on_an_unknown_path_is_not_a_supersession(
        atelier_env: Dict) -> None:
    root = _croot(atelier_env)
    _seed(root, "-w-p1", "m1", description="rule one", body="a\n")
    _ac.absorb(dry_run=False, source_root=root)
    _seed(root, "-w-p1", "m2", description="rule two", body="b\n")
    out = _ac.absorb(dry_run=False, source_root=root)
    rec = out["accepted"][0]
    assert "supersedes" not in rec and "body_refreshed" not in rec


# ── the fourth case the RFC did not anticipate: description-only edit ────────


def test_description_only_edit_still_supersedes(atelier_env: Dict) -> None:
    """Dedup hashes the BODY (frontmatter excluded), so re-titling a memory
    without touching its content arrives with an unchanged hash — yet the
    statement IS the description, so the claim id moves. Without this branch
    the old claim would be stranded exactly as an un-superseded body edit
    would strand it."""
    root = _croot(atelier_env)
    p = _seed(root, "-w-p1", "m1", description="first wording",
              body="the body never changes\n")
    first = _ac.absorb(dry_run=False, source_root=root)
    old_path = Path(first["accepted"][0]["path"])
    old_id = _fm(old_path)["entry_id"]

    p.write_text(p.read_text().replace("first wording", "second wording"),
                 encoding="utf-8")
    second = _ac.absorb(dry_run=False, source_root=root)

    assert second["deduped"] == []                   # NOT silently deduped
    rec = second["accepted"][0]
    assert rec["supersedes"] == old_id and rec["superseded"] is True
    assert _fm(old_path)["ac_status"] == "retracted"
    assert _fm(rec["path"])["statement"] == "second wording"


def test_unchanged_memory_still_dedups_cheaply(atelier_env: Dict) -> None:
    """The guard must not turn every re-run into work: an untouched memory
    still takes the plain dedup path."""
    root = _croot(atelier_env)
    _seed(root, "-w-p1", "m1", description="stable wording", body="stable\n")
    _ac.absorb(dry_run=False, source_root=root)
    out = _ac.absorb(dry_run=False, source_root=root)
    assert len(out["deduped"]) == 1
    assert out["accepted"] == [] and out["candidates"] == []
