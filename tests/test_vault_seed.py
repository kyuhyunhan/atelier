"""The examples/vault-seed demo vault must stay ALIVE, not just present.

An adopter's first five minutes run through this seed (open-sourcing track
item 3), so it is pinned like any other contract: it must reindex under the
DOCUMENTED single-vault config shape, validate against the live schema, carry
no lint FAILs, keep one node of every kind and surfacing tier, keep the
atomize nudge genuinely due, and keep every entry_id equal to the engine's
own content-addressed templates. A schema change that would silently rot the
demo fails here instead."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict

import yaml

SEED = Path(__file__).resolve().parents[1] / "examples" / "vault-seed"


def _install_seed(vault_env: Dict) -> Path:
    vault = vault_env["vault"]
    for sub in SEED.iterdir():
        if sub.name == "README.md" and sub.parent == SEED:
            continue                      # the tour doc, not vault content
        dest = vault / sub.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(sub, dest)
    return vault


def _atomic_nodes(vault: Path):
    for p in sorted((vault / "graph" / "atomic").glob("*.md")):
        yield p, yaml.safe_load(p.read_text().split("---")[1])


def _sources(vault: Path):
    for p in sorted((vault / "raw").rglob("*.md")):
        fm = yaml.safe_load(p.read_text().split("---")[1])
        if fm.get("kind") == "source":
            yield p, fm


def test_seed_reindexes_validates_no_lint_fails(vault_env: Dict) -> None:
    _install_seed(vault_env)
    from runtime.service import api
    stats = api.reindex(full=True)
    assert sum(s.get("pages_seen", 0) for s in stats) >= 13

    report = api.validate()
    assert report["scanned"] >= 13          # the guard must actually LOOK at it
    fails = [f for f in report["findings"] if f.get("severity") == "FAIL"]
    assert not report["failed"] and not fails, \
        f"seed does not validate: {str(fails)[:400]}"

    lint = api.lint()
    lint_fails = [f for f in (lint.get("findings") or [])
                  if str(f.get("severity", "")).upper() == "FAIL"]
    assert not lint_fails, f"seed has lint FAILs: {str(lint_fails)[:400]}"

    # the README's entity-wikilink row must be TRUE: [[SQLite]]/[[홍길동]] in
    # claim bodies resolve through the alias index (round-2 MUST). A dangling
    # alias link has to_page NULL, so resolved-count is the honest signal.
    from runtime.util import db as _db
    conn = _db.connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM links WHERE to_page_id IS NOT NULL").fetchone()[0]
    finally:
        conn.close()
    assert n >= 3, f"entity wikilinks do not resolve (resolved links: {n})"


def test_seed_atomize_nudge_is_actually_due(vault_env: Dict) -> None:
    """The README promises `atelier nudges` shows work — pin it. Two Sources
    (the inbox capture and the soil note) have no derived Claim."""
    _install_seed(vault_env)
    from runtime.service import api
    api.reindex(full=True)
    from runtime.service.learnings import atomize as _at
    info = _at.nudge_info()
    assert info["count"] >= 2 and info["due"], f"atomize nudge not due: {info}"


def test_seed_keeps_every_kind_tier_and_gate(vault_env: Dict) -> None:
    vault = _install_seed(vault_env)
    claims = [fm for _, fm in _atomic_nodes(vault) if fm["kind"] == "claim"]
    kinds = ({fm["kind"] for _, fm in _atomic_nodes(vault)}
             | {fm["kind"] for _, fm in _sources(vault)})
    # exact on purpose: a NEW node kind must update the seed (and this set)
    assert kinds == {"source", "entity", "claim"}
    assert {"query", "proactive", "always"} <= {c["surfacing"] for c in claims}
    assert any(c["domain"] == "personal" and c["sensitivity"] == "private"
               for c in claims)
    assert {"pending", "passed"} <= {c.get("ac_status") for c in claims}
    # dream provenance is real: non-empty derived_from + a refines link
    dream = [c for c in claims if c["generated_by"] == "dream"]
    assert dream and all(c["derived_from"] for c in dream)
    assert any(l.get("rel") == "refines" for c in dream for l in c.get("links", []))


def test_seed_entry_ids_follow_the_engine_templates(vault_env: Dict) -> None:
    """SHOULD-3 of the seed review: ids must be the engine's own dedup keys,
    or atomizing the seed's raw notes mints DUPLICATE entities. Recomputing
    through resolver.entry_id makes the convention self-verifying — the seed
    needs no committed generator."""
    vault = _install_seed(vault_env)
    from runtime.structure import resolver as R
    for p, fm in _atomic_nodes(vault):
        if fm["kind"] == "entity":
            want = R.entry_id("entity", type=fm["type"], pref_label=fm["pref_label"])
        else:
            want = R.entry_id("claim", statement=fm["statement"],
                              derived_from="|".join(sorted(fm["derived_from"])))
        assert fm["entry_id"] == want, f"{p.name}: unconventional entry_id"
    import hashlib
    from runtime.service.learnings.claims_io import _content_hash
    for p, fm in _sources(vault):
        # discriminator class is video_id|url|HASH (structure.yaml) — title is
        # mutable prose, so the seed keys sources on the body hash. Recompute
        # from the BODY, so a drifted content_hash cannot self-certify.
        body = p.read_text().split("---", 2)[2]
        ch = "sha256:" + hashlib.sha256(body.strip().encode()).hexdigest()
        assert fm["content_hash"] == ch, f"{p.name}: content_hash != body hash"
        want = R.entry_id("source", created_at=fm["created_at"],
                          discriminator=ch)
        assert fm["entry_id"] == want, f"{p.name}: unconventional entry_id"
    # claims/entities: the engine hashes the frontmatter sans content_hash
    for p, fm in _atomic_nodes(vault):
        assert fm["content_hash"] == _content_hash(fm), \
            f"{p.name}: content_hash not the claims_io convention"
