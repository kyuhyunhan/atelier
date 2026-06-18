"""RFC 0005 P4a — v7 node schema: entry_id, classify-by-kind, validation.

Locks the three content-addressed entry_id formulas (RFC 0005 §5) byte-for-byte,
the field-first classification (§3 invariant: projection reads fields not path),
and frontmatter validation of hand-written valid/broken source/entity/claim docs.
"""
from __future__ import annotations

import uuid

from runtime.index.classify import classify
from runtime.lint import validate_v4
from runtime.structure import resolver


# --- entry_id formulas (RFC 0005 §5), recomputed byte-for-byte -----------
def _expect(discriminator: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, discriminator))


def _norm(s: str) -> str:
    return s.strip().lower()


def test_entry_id_source_formula():
    # source = uuid5(NS, created_at | discriminator)
    out = resolver.entry_id(
        "source", created_at="2026-06-18T12:00:00+00:00", discriminator="vid123"
    )
    assert out == _expect("atelier:source:2026-06-18T12:00:00+00:00|vid123")


def test_entry_id_entity_formula_is_dedup_key():
    # entity = uuid5(NS, type | normalize(pref_label))  -> dedup key.
    out = resolver.entry_id("entity", type="Concept", pref_label="Immutability")
    assert out == _expect("atelier:entity:Concept|" + _norm("Immutability"))
    # Casing/whitespace variants of the same subject collapse to one id.
    variant = resolver.entry_id(
        "entity", type="Concept", pref_label="  IMMUTABILITY  "
    )
    assert variant == out


def test_entry_id_claim_formula():
    # claim = uuid5(NS, normalize(statement) | derived_from)
    statement = "Pure functions ease testing."
    derived = "src-abc"
    out = resolver.entry_id("claim", statement=statement, derived_from=derived)
    assert out == _expect("atelier:claim:" + _norm(statement) + "|" + derived)
    # statement is normalized -> casing variant is the same claim.
    variant = resolver.entry_id(
        "claim", statement="  PURE FUNCTIONS EASE TESTING.  ", derived_from=derived
    )
    assert variant == out


# --- classify by FIELD (kind), not path (RFC 0005 §3) -------------------
def test_classify_by_kind_v7():
    # A flat graph/<id>.md is classified by `kind`, before legacy path globs.
    for kind in ("source", "entity", "claim"):
        fm = {"schema_version": 7, "kind": kind}
        assert classify("gorae", "graph/some-uuid.md", fm) == kind


def test_classify_legacy_still_path_based():
    # v5 graph/entities/ page (no v7 kind) still classifies by path.
    fm = {"schema_version": 5, "type": "entity"}
    assert classify("gorae", "graph/entities/foo.md", fm) == "entity"


# --- validation: valid passes, broken fails -----------------------------
def _write(tmp_path, rel, fm_lines):
    import textwrap
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    body = "---\n" + "\n".join(fm_lines) + "\n---\n\nbody\n"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


_EID = str(uuid.uuid4())


def _valid_source_fm():
    return [
        f"entry_id: {_EID}",
        "schema_version: 7",
        "kind: source",
        "created_at:",
        "  - value: 2026-06-18T12:00:00+00:00",
        "content_hash: abc123",
        "title: A talk",
        "sensitivity: private",
        "domain: knowledge",
        "attributed_to: youtube",
        "source_type: youtube",
        "source_url: https://example.test/x",
        "embedded_assets: []",
    ]


def _valid_entity_fm():
    return [
        f"entry_id: {_EID}",
        "schema_version: 7",
        "kind: entity",
        "created_at:",
        "  - value: 2026-06-18T12:00:00+00:00",
        "content_hash: abc123",
        "sensitivity: private",
        "pref_label: Immutability",
        "type: Concept",
        "in_scheme: [knowledge]",
    ]


def _valid_claim_fm():
    return [
        f"entry_id: {_EID}",
        "schema_version: 7",
        "kind: claim",
        "created_at:",
        "  - value: 2026-06-18T12:00:00+00:00",
        "content_hash: abc123",
        "statement: Pure functions ease testing.",
        "is_about: [other-id]",
        "derived_from: [src-id]",
        "attributed_to: speaker",
        "generated_by: atomize",
        "surfacing: query",
        "domain: knowledge",
        "sensitivity: private",
    ]


def _errs(tmp_path, p):
    findings = validate_v4.validate_paths([p], vault_root=tmp_path)
    return [f.message for f in findings]


def test_valid_source_passes(tmp_path):
    p = _write(tmp_path, "graph/a.md", _valid_source_fm())
    assert _errs(tmp_path, p) == []


def test_valid_entity_passes(tmp_path):
    p = _write(tmp_path, "graph/b.md", _valid_entity_fm())
    assert _errs(tmp_path, p) == []


def test_valid_claim_passes(tmp_path):
    p = _write(tmp_path, "graph/c.md", _valid_claim_fm())
    assert _errs(tmp_path, p) == []


def test_broken_claim_missing_required_fails(tmp_path):
    fm = [ln for ln in _valid_claim_fm() if not ln.startswith("statement:")]
    p = _write(tmp_path, "graph/d.md", fm)
    errs = _errs(tmp_path, p)
    assert any("statement" in e for e in errs)


def test_broken_claim_bad_enum_fails(tmp_path):
    fm = [
        "generated_by: telepathy" if ln.startswith("generated_by:") else ln
        for ln in _valid_claim_fm()
    ]
    p = _write(tmp_path, "graph/e.md", fm)
    errs = _errs(tmp_path, p)
    assert any("generated_by" in e for e in errs)


def test_broken_entity_bad_type_fails(tmp_path):
    fm = [
        "type: Sandwich" if ln.startswith("type:") else ln
        for ln in _valid_entity_fm()
    ]
    p = _write(tmp_path, "graph/f.md", fm)
    errs = _errs(tmp_path, p)
    assert any("type" in e for e in errs)


def test_schema_version_7_accepted(tmp_path):
    assert 7 in validate_v4._allowed_schema_versions()
    assert 4 in validate_v4._allowed_schema_versions()
    assert 5 in validate_v4._allowed_schema_versions()
