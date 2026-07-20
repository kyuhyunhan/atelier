"""RFC 0007 M1 — born-as-Source + deterministic mint (additive; writers unwired).

These cover the engine primitives only: the content-addressed operational Source
id, the deterministic 1:1 mint, idempotency (the property that replaces the shared
anchor's dedup role), the acceptance-criteria field mirror, and additive-enum
validation. capture.py / absorb_claude.py are NOT yet wired to these (that is M2),
so the live write path is unchanged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

from runtime.lint import validate_v4 as _val
from runtime.service.learnings import claims_io as _ci
from runtime.structure import resolver as _structure


def _op_dir(vault: Path) -> Path:
    return vault / _structure.operational_source_dir()


# ── content-addressed Source id (no wall-clock) ──────────────────────────────


def test_source_id_is_pure_function_of_statement() -> None:
    a = _ci.operational_source_content_id("Integration tests must hit a real DB")
    b = _ci.operational_source_content_id("integration tests   must hit a REAL db")
    c = _ci.operational_source_content_id("A different lesson entirely")
    # Whitespace-collapsed + lowercased → the same lesson maps to one id.
    assert a == b
    assert a != c
    # Deterministic across calls (no created_at / randomness in the id).
    assert a == _ci.operational_source_content_id("Integration tests must hit a real DB")
    assert a.startswith("")  # a uuid5 string
    assert len(a) == 36


# ── the mint: Source + Claim, LLM-free ───────────────────────────────────────


def test_mint_writes_operational_source_and_claim(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    out = _ci.mint_operational_claim(
        statement="Prefer composition over inheritance for mixins",
        body="## Observation\nseen again today\n",
        project="lexio", session_id="s1",
        working_dir="/Users/me/workspaces/lexio", vault=vault)

    src, claim = out["source"], out["claim"]
    # Source: its own node in raw/operational/, domain operational, kind source.
    sp = Path(src["path"])
    assert sp.parent == _op_dir(vault)
    sfm, _ = _parse(sp)
    assert sfm["kind"] == "source"
    assert sfm["domain"] == "operational"
    # Claim: generated_by mint, derived_from its OWN source (not the anchor).
    found = _ci.find_claim_by_entry_id(claim["entry_id"])
    assert found is not None
    _, cfm, _body = found
    assert cfm["generated_by"] == "mint"
    df = cfm["derived_from"]
    df = df if isinstance(df, list) else [df]
    assert src["entry_id"] in df


def _parse(path: Path):
    from runtime.index import parse as _p
    return _p.split_frontmatter(path.read_text(encoding="utf-8"))


# ── idempotency: the property that replaces the anchor's dedup role ───────────


def test_mint_same_lesson_dedups_to_one_source_and_one_claim(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    kw = dict(statement="Never mutate source material — only the vault",
              body="## Observation\nhard rule\n", project="atelier", vault=vault)

    first = _ci.mint_operational_claim(session_id="sA",
                                       working_dir="/w/a", **kw)
    second = _ci.mint_operational_claim(session_id="sB",
                                        working_dir="/w/b", **kw)

    # Same lesson → same Source id AND same Claim id, regardless of session.
    assert first["source"]["entry_id"] == second["source"]["entry_id"]
    assert first["claim"]["entry_id"] == second["claim"]["entry_id"]
    # Exactly one Source file and one Claim file on disk.
    assert len(list(_op_dir(vault).glob("*.md"))) == 1
    claims = [p for p in (vault / _structure.atomic_claim_dir()).glob("*.md")]
    minted = [p for p in claims
              if _parse(p)[0].get("generated_by") == "mint"]
    assert len(minted) == 1


# ── acceptance-criteria mirror (criteria.py reads these off the CLAIM) ────────


def test_mint_mirrors_session_fields_onto_claim(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    out = _ci.mint_operational_claim(
        statement="Ship each PR via the ship-pr flow",
        body="## Observation\nworkflow\n", project="lexio",
        session_id="sess-xyz", working_dir="/Users/me/workspaces/lexio",
        vault=vault)
    _, cfm, _b = _ci.find_claim_by_entry_id(out["claim"]["entry_id"])
    # tied_to_event reads session_id/working_dir; has_project_tag reads project.
    assert cfm.get("session_id") == "sess-xyz"
    assert cfm.get("working_dir") == "/Users/me/workspaces/lexio"
    assert cfm.get("project_hint") == "lexio"
    # Provenance ALSO lives on the Source (mirrored, not moved).
    sfm, _ = _parse(Path(out["source"]["path"]))
    assert sfm.get("session_id") == "sess-xyz"


# ── additive enums validate end-to-end ───────────────────────────────────────


def test_minted_nodes_pass_v7_validation(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    out = _ci.mint_operational_claim(
        statement="Markdown is truth; the DB is a projection",
        body="## Observation\ninvariant\n", project="atelier", vault=vault)
    paths = [Path(out["source"]["path"]),
             _ci.find_claim_by_entry_id(out["claim"]["entry_id"])[0]]
    findings = _val.validate_paths(paths, vault_root=vault)
    # `domain: operational` (source) and `generated_by: mint` (claim) must be
    # accepted by the additive enums — else V0 FAILs here.
    assert findings == [], [f.message for f in findings]


def test_generated_by_and_domain_enums_are_additive() -> None:
    specs = _val._v7_specs()
    src_domain = specs["source"]["field_specs"]["domain"]["enum"]
    claim_gen = specs["claim"]["field_specs"]["generated_by"]["enum"]
    ent_scheme = specs["entity"]["field_specs"]["in_scheme"]["items"]["enum"]
    assert "operational" in src_domain
    assert "mint" in claim_gen
    assert "operational" in ent_scheme
    # Old values preserved (additive, not a replacement).
    assert {"personal", "knowledge", "inbox", "workshop"} <= set(src_domain)
    assert {"ingest", "atomize", "promote", "dream"} <= set(claim_gen)
