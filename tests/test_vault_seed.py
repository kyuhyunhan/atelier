"""The examples/vault-seed demo vault must stay ALIVE, not just present.

An adopter's first five minutes run through this seed (open-sourcing track
item 3), so it is pinned like any other contract: it must reindex cleanly,
validate against the live schema, lint clean, and keep demonstrating one node
of every kind and surfacing tier. A schema change that would silently rot the
demo fails here instead."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict

import yaml

SEED = Path(__file__).resolve().parents[1] / "examples" / "vault-seed"


def _install_seed(atelier_env: Dict) -> Path:
    vault = atelier_env["wiki"]
    for sub in SEED.iterdir():
        if sub.name == "README.md":
            continue                      # tour doc, not vault content
        dest = vault / sub.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(sub, dest)
    return vault


def test_seed_reindexes_validates_and_lints_clean(atelier_env: Dict) -> None:
    _install_seed(atelier_env)
    from runtime.service import api
    stats = api.reindex(space="wiki", full=True)
    assert sum(s.get("pages_seen", 0) for s in stats) >= 14

    report = api.validate()
    assert report["scanned"] >= 14          # the guard must actually LOOK at it
    fails = [f for f in report["findings"] if f.get("severity") == "FAIL"]
    assert not report["failed"] and not fails, \
        f"seed does not validate: {str(fails)[:400]}"

    lint = api.lint(space="wiki")
    errs = [f for f in (lint.get("findings") or [])
            if str(f.get("severity", "")).lower() in ("error", "fail")]
    assert not errs, f"seed does not lint clean: {str(errs)[:400]}"


def test_seed_keeps_every_kind_and_tier(atelier_env: Dict) -> None:
    vault = _install_seed(atelier_env)
    nodes = []
    for p in (vault / "graph" / "atomic").glob("*.md"):
        fm = yaml.safe_load(p.read_text().split("---")[1])
        nodes.append(fm)
    kinds = {n["kind"] for n in nodes}
    assert kinds == {"source", "entity", "claim"}
    tiers = {n.get("surfacing") for n in nodes if n["kind"] == "claim"}
    assert {"query", "proactive", "always"} <= tiers
    # the lens-wall demo: at least one private personal claim
    assert any(n.get("domain") == "personal" and n.get("sensitivity") == "private"
               for n in nodes if n["kind"] == "claim")
    # the accept-gate demo: both a pending and a passed learning
    acs = {n.get("ac_status") for n in nodes if n["kind"] == "claim"}
    assert {"pending", "passed"} <= acs
