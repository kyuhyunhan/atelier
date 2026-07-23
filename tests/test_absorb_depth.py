"""RFC 0008 M3 — depth: the mint stays 1:1; deep atomize is additive.

The measurement in RFC 0008 §2 killed the obvious design. The draft would have
routed long memories away from the deterministic mint and into LLM atomization,
but 88% of the real backlog is "long" — that routing would push nearly every
absorb into the expensive lane absorb exists to avoid, and throw away the free,
already-curated claim sitting in each memory's `description`.

So the mint is unconditional, and depth is OPTIONAL: `atelier-atomize` may run
on an already-minted operational Source, adding claims alongside the minted one.
This module pins the three properties that makes safe:

1. a minted Source never enters the atomize backlog (it has a derived claim),
2. deep atomization is purely ADDITIVE — the mint claim is untouched,
3. derived claims never widen the Source's sensitivity (M4's demotion holds).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

from runtime.index.parse import split_frontmatter
from runtime.service.learnings import absorb_claude as _ac
from runtime.service.learnings import atomize as _atomize
from runtime.service.learnings import claims_io as _claims
from runtime.structure import resolver as _structure


def _seed(root: Path, name: str, *, description: str, body: str,
          type_: str = "feedback", project_dir: str = "-w-p1") -> Path:
    p = root / project_dir / "memory" / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\nname: {name}\ndescription: {description}\n"
                 f"type: {type_}\n---\n{body}", encoding="utf-8")
    return p


def _fm(path) -> Dict:
    fm, _ = split_frontmatter(Path(path).read_text(encoding="utf-8"))
    return fm


def _source_file(env: Dict) -> Path:
    d = env["gorae"] / _structure.operational_source_dir()
    return next(iter(sorted(d.glob("*.md"))))


# ── 1. the mint keeps a Source out of the atomize backlog ────────────────────


def test_minted_source_is_not_in_the_atomize_backlog(atelier_env: Dict) -> None:
    """A minted Source HAS a derived claim, so the un-atomized predicate (a
    Source no Claim is derived_from) excludes it — the deterministic lane never
    generates LLM work for itself."""
    root = atelier_env["claude_projects"]
    _seed(root, "m1", description="a durable rule",
          body="## Rule\n" + "word " * 400)          # deliberately LONG
    _ac.absorb(dry_run=False, source_root=root)
    assert _atomize.unatomized_count(vault=atelier_env["gorae"]) == 0
    assert _atomize.nudge_info(vault=atelier_env["gorae"])["due"] is False


def test_long_memories_still_mint_rather_than_route_to_atomize(
        atelier_env: Dict) -> None:
    """§5's reversal, pinned: length does NOT change the lane. The statement
    comes from the curated description, so a 400-word memory still yields one
    deterministic claim and zero atomize backlog."""
    root = atelier_env["claude_projects"]
    _seed(root, "short", description="short rule", body="tiny\n")
    _seed(root, "long", description="long rule", body="x " * 500)
    out = _ac.absorb(dry_run=False, source_root=root)
    assert len(out["accepted"]) == 2
    statements = {_fm(r["path"])["statement"] for r in out["accepted"]}
    assert statements == {"short rule", "long rule"}
    assert _atomize.unatomized_count(vault=atelier_env["gorae"]) == 0


# ── 2. deep atomize is additive: the mint claim survives ─────────────────────


def test_deep_atomize_adds_claims_without_touching_the_mint(
        atelier_env: Dict) -> None:
    root = atelier_env["claude_projects"]
    _seed(root, "m1", description="the headline rule",
          body="## Rule\nfirst fact. second fact.\n")
    out = _ac.absorb(dry_run=False, source_root=root)
    mint_path = Path(out["accepted"][0]["path"])
    mint_before = mint_path.read_text(encoding="utf-8")
    src_id = _fm(_source_file(atelier_env))["entry_id"]

    res = _claims.atomize_write(
        source_entry_id=src_id, created_at="2026-07-23T00:00:00+00:00",
        domain="operational",
        entities=[{"type": "Concept", "pref_label": "deep concept"}],
        claims=[{"statement": "first fact holds", "attributed_to": "self",
                 "is_about": ["deep concept"]},
                {"statement": "second fact holds", "attributed_to": "self",
                 "is_about": ["deep concept"]}],
        vault=atelier_env["gorae"])

    assert res["claims_written"] == 2
    # the minted claim is byte-identical — atomization is purely additive
    assert mint_path.read_text(encoding="utf-8") == mint_before
    # and all three claims derive from the SAME Source
    claim_dir = atelier_env["gorae"] / _structure.atomic_claim_dir()
    derived = [fm for fm in (_fm(p) for p in claim_dir.glob("*.md"))
               if fm.get("kind") == "claim"
               and src_id in (fm.get("derived_from") or [])]
    assert len(derived) == 3
    assert {fm["generated_by"] for fm in derived} == {"mint", "atomize"}


# ── 3. derived claims never widen the Source's sensitivity ───────────────────


def test_deep_atomize_inherits_a_private_source(atelier_env: Dict) -> None:
    """M4 demotes a `type: user` memory to private. Deep-atomizing it must not
    mint PUBLIC claims off its body — that would route the very content M4
    narrowed straight back into proactive push."""
    root = atelier_env["claude_projects"]
    _seed(root, "who", type_="user", description="the user prefers terse work",
          body="## Detail\nsomething personal about the user\n")
    out = _ac.absorb(dry_run=False, source_root=root)
    assert _fm(out["candidates"][0]["path"])["sensitivity"] == "private"
    src_id = _fm(_source_file(atelier_env))["entry_id"]
    assert _fm(_source_file(atelier_env))["sensitivity"] == "private"

    res = _claims.atomize_write(
        source_entry_id=src_id, created_at="2026-07-23T00:00:00+00:00",
        domain="operational",          # domain default alone would say PUBLIC
        entities=[{"type": "Concept", "pref_label": "a preference"}],
        claims=[{"statement": "a derived detail", "attributed_to": "self",
                 "is_about": ["a preference"]}],
        vault=atelier_env["gorae"])

    claim_dir = atelier_env["gorae"] / _structure.atomic_claim_dir()
    derived = [fm for fm in (_fm(p) for p in claim_dir.glob("*.md"))
               if fm.get("generated_by") == "atomize"]
    assert len(derived) == res["claims_written"] == 1
    assert derived[0]["sensitivity"] == "private"      # inherited, not widened


def test_deep_atomize_inherits_a_pii_demoted_source(atelier_env: Dict) -> None:
    """The same guard for the other M4 trigger: a PII pattern hit."""
    (atelier_env["home"] / "pii_patterns.txt").write_text(
        "SECRETNAME\n", encoding="utf-8")
    root = atelier_env["claude_projects"]
    _seed(root, "leaky", description="deploy checklist",
          body="ask SECRETNAME before deploying\n")
    _ac.absorb(dry_run=False, source_root=root)
    src_id = _fm(_source_file(atelier_env))["entry_id"]

    _claims.atomize_write(
        source_entry_id=src_id, created_at="2026-07-23T00:00:00+00:00",
        domain="operational",
        entities=[{"type": "Concept", "pref_label": "deploy"}],
        claims=[{"statement": "a deploy step exists", "attributed_to": "self",
                 "is_about": ["deploy"]}],
        vault=atelier_env["gorae"])

    claim_dir = atelier_env["gorae"] / _structure.atomic_claim_dir()
    derived = [fm for fm in (_fm(p) for p in claim_dir.glob("*.md"))
               if fm.get("generated_by") == "atomize"]
    assert derived and all(fm["sensitivity"] == "private" for fm in derived)


def test_public_source_still_yields_public_claims(atelier_env: Dict) -> None:
    """The guard tightens only — it must not make everything private."""
    root = atelier_env["claude_projects"]
    _seed(root, "ok", description="a shareable rule", body="nothing secret\n")
    _ac.absorb(dry_run=False, source_root=root)
    src_id = _fm(_source_file(atelier_env))["entry_id"]

    _claims.atomize_write(
        source_entry_id=src_id, created_at="2026-07-23T00:00:00+00:00",
        domain="operational",
        entities=[{"type": "Concept", "pref_label": "a topic"}],
        claims=[{"statement": "a derived public fact", "attributed_to": "self",
                 "is_about": ["a topic"]}],
        vault=atelier_env["gorae"])

    claim_dir = atelier_env["gorae"] / _structure.atomic_claim_dir()
    derived = [fm for fm in (_fm(p) for p in claim_dir.glob("*.md"))
               if fm.get("generated_by") == "atomize"]
    assert derived and all(fm["sensitivity"] == "public" for fm in derived)


def test_personal_domain_stays_private_when_the_source_is_public(
        atelier_env: Dict) -> None:
    """Policy 1 is unchanged: `domain: personal` is private regardless of what
    the Source says. The new inheritance only ADDS a tightening path."""
    vault = atelier_env["gorae"]
    src = _claims.write_operational_source(
        statement="a public operational source", body="body\n", vault=vault)
    _claims.atomize_write(
        source_entry_id=src["entry_id"],
        created_at="2026-07-23T00:00:00+00:00", domain="personal",
        entities=[{"type": "Concept", "pref_label": "a life topic"}],
        claims=[{"statement": "a personal derived fact",
                 "attributed_to": "self", "is_about": ["a life topic"]}],
        vault=vault)
    claim_dir = vault / _structure.atomic_claim_dir()
    derived = [fm for fm in (_fm(p) for p in claim_dir.glob("*.md"))
               if fm.get("generated_by") == "atomize"]
    assert derived and all(fm["sensitivity"] == "private" for fm in derived)
