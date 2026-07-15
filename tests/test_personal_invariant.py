"""Policy 1 (2026-07) — the personal invariant: "never LEAKS", not "never atomized".

A claim derived from a private-domain (personal) source MUST be
sensitivity: private. Enforced two ways:
- lint L8 (audit — catches direct markdown writes and post-hoc re-domaining);
- the dream guard in claims_io.write_synthesized_claim (write-time — the one
  engine path that could launder personal into an always-surfaced principle).
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Dict

from runtime.service import api as _api
from runtime.service.learnings import claims_io as _claims
from runtime.service.learnings import cluster as _cl
from runtime.structure import resolver as _structure


def _write_source(vault: Path, name: str, domain: str) -> str:
    eid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"src-{name}"))
    p = vault / "raw" / domain / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\nschema_version: 7\nentry_id: {eid}\nkind: source\n"
        f"domain: {domain}\nsensitivity: private\ntitle: {name}\n---\n\nbody {name}\n",
        encoding="utf-8")
    return eid


def _write_claim(vault: Path, name: str, *, derived_from: str,
                 domain: str, sensitivity: str) -> str:
    eid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"claim-{name}"))
    d = vault / "graph" / "atomic"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(
        f"---\nschema_version: 7\nentry_id: {eid}\nkind: claim\n"
        f"domain: {domain}\nsensitivity: {sensitivity}\n"
        f"statement: statement of {name}\nderived_from: [{derived_from}]\n---\n\nbody\n",
        encoding="utf-8")
    return eid


def test_schema_declares_personal_private() -> None:
    assert "personal" in _structure.atomize_private_source_domains()


# ── lint L8 ──────────────────────────────────────────────────────────────────

def test_L8_flags_public_claim_from_personal_source(atelier_env: Dict) -> None:
    vault = Path(_cl._vault_root())
    src = _write_source(vault, "diary1", "personal")
    _write_claim(vault, "leaky", derived_from=src,
                 domain="personal", sensitivity="public")   # the violation
    _api.reindex(space="gorae", full=True)

    out = _api.lint(rule_ids=["L8"])
    l8 = [f for f in out["findings"] if f["rule_id"] == "L8"]
    assert len(l8) == 1
    assert "private" in l8[0]["message"]


def test_L8_green_when_personal_claim_is_private(atelier_env: Dict) -> None:
    vault = Path(_cl._vault_root())
    src = _write_source(vault, "diary2", "personal")
    _write_claim(vault, "safe", derived_from=src,
                 domain="personal", sensitivity="private")   # the invariant held
    know = _write_source(vault, "kdoc", "knowledge")
    _write_claim(vault, "know", derived_from=know,
                 domain="knowledge", sensitivity="public")   # non-private domain: fine
    _api.reindex(space="gorae", full=True)

    out = _api.lint(rule_ids=["L8"])
    assert [f for f in out["findings"] if f["rule_id"] == "L8"] == []


# ── dream guard ──────────────────────────────────────────────────────────────

def test_dream_synthesis_inherits_private_from_personal_upstream(
        atelier_env: Dict) -> None:
    vault = Path(_cl._vault_root())
    src = _write_source(vault, "diary3", "personal")
    upstream = _write_claim(vault, "up-personal", derived_from=src,
                            domain="personal", sensitivity="private")

    out = _claims.write_synthesized_claim(
        statement="a generalization touching personal material",
        source_claim_ids=[upstream], sensitivity="public",   # asks for public …
        vault=vault)
    fm, _ = _claims.read_claim(Path(out["path"]))
    assert fm["sensitivity"] == "private"                    # … guard escalates


def test_dream_synthesis_stays_public_for_operational_upstream(
        atelier_env: Dict) -> None:
    vault = Path(_cl._vault_root())
    know = _write_source(vault, "kdoc2", "knowledge")
    upstream = _write_claim(vault, "up-op", derived_from=know,
                            domain="operational", sensitivity="public")

    out = _claims.write_synthesized_claim(
        statement="an operational generalization",
        source_claim_ids=[upstream], sensitivity="public",
        vault=vault)
    fm, _ = _claims.read_claim(Path(out["path"]))
    assert fm["sensitivity"] == "public"                     # untouched


def test_dream_guard_abstains_on_unresolvable_upstream(atelier_env: Dict) -> None:
    vault = Path(_cl._vault_root())
    ghost = str(uuid.uuid5(uuid.NAMESPACE_DNS, "no-such-claim"))
    out = _claims.write_synthesized_claim(
        statement="synthesis from a dangling id",
        source_claim_ids=[ghost], sensitivity="public",
        vault=vault)
    fm, _ = _claims.read_claim(Path(out["path"]))
    assert fm["sensitivity"] == "public"                     # abstain-on-miss
