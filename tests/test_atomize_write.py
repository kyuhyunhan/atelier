"""Engine write-path for knowledge atomization (`claims_io.atomize_write`).

The agent supplies judgement (which entities/claims, phrasing, attribution) as
structured input; the engine does the deterministic mechanical write — resolve-
or-create typed entities, resolve is_about labels to ids, mint content-addressed
claims, dedup, hash. This is what lets the atomize skill stop hand-writing a
per-source script (the token sink) and just return structured extraction.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

from runtime.service.learnings import claims_io as _cio
from runtime.structure import resolver as _structure


SRC = "11111111-2222-3333-4444-555555555555"
CREATED = "2026-06-16T00:00:00+00:00"

_ENTITIES = [
    {"type": "Organization", "pref_label": "Anthropic"},
    {"type": "Model", "pref_label": "Claude Fable"},
    {"type": "Person", "pref_label": "노정석"},
]
_CLAIMS = [
    {"statement": "Fable은 Opus보다 2배 비싸다.",
     "attributed_to": "노정석", "is_about": ["Claude Fable"]},
    {"statement": "노정석은 post-training이 경쟁 축이라고 본다.",
     "attributed_to": "노정석", "is_about": ["노정석", "post-training"]},
]


def _read(path: Path) -> Dict:
    from runtime.index.parse import split_frontmatter
    fm, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    return fm


def test_atomize_write_creates_entities_and_claims(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    out = _cio.atomize_write(source_entry_id=SRC, created_at=CREATED,
                             domain="knowledge", entities=_ENTITIES,
                             claims=_CLAIMS, vault=vault)
    # "post-training" is is_about but not declared → auto-created as Concept.
    assert out["entities_created"] == 4        # 3 declared + 1 auto Concept
    assert out["entities_reused"] == 0
    assert out["claims_written"] == 2

    cid = out["claim_ids"][0]
    path = next(_cio.claims_dir(vault).glob(f"*{cid[:8]}*.md"))
    fm = _read(path)
    assert fm["kind"] == "claim"
    assert fm["domain"] == "knowledge"
    assert fm["sensitivity"] == "public"           # knowledge default
    assert fm["surfacing"] == "query"
    assert fm["generated_by"] == "atomize"
    assert fm["derived_from"] == [SRC]
    assert fm["attributed_to"] == "노정석"
    # is_about resolves to the Claude Fable entity's content-addressed id
    fable_id = _structure.entry_id("entity", type="Model", pref_label="Claude Fable")
    assert fm["is_about"] == [fable_id]


def test_atomize_write_is_idempotent(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    first = _cio.atomize_write(source_entry_id=SRC, created_at=CREATED,
                               domain="knowledge", entities=_ENTITIES,
                               claims=_CLAIMS, vault=vault)
    again = _cio.atomize_write(source_entry_id=SRC, created_at=CREATED,
                               domain="knowledge", entities=_ENTITIES,
                               claims=_CLAIMS, vault=vault)
    # second pass reuses every entity and reproduces the same claim ids
    assert again["entities_created"] == 0
    assert again["entities_reused"] == first["entities_created"]
    assert again["claim_ids"] == first["claim_ids"]


def test_atomize_write_type_is_part_of_entity_id(atelier_env: Dict) -> None:
    # Same label, different type → different entity id (type is a dedup-key part),
    # so an AI model filed as Model never collides with a same-named Concept.
    as_model = _structure.entry_id("entity", type="Model", pref_label="Claude Fable")
    as_concept = _structure.entry_id("entity", type="Concept", pref_label="Claude Fable")
    assert as_model != as_concept


def test_atomize_write_undeclared_is_about_has_a_node(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    _cio.atomize_write(source_entry_id=SRC, created_at=CREATED,
                       domain="knowledge", entities=_ENTITIES,
                       claims=_CLAIMS, vault=vault)
    # "post-training" was only referenced via is_about → a Concept node exists.
    pt_id = _structure.entry_id("entity", type="Concept", pref_label="post-training")
    assert _cio.find_entity_by_entry_id(pt_id, vault) is not None


def test_atomize_write_personal_is_private(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    out = _cio.atomize_write(
        source_entry_id=SRC, created_at=CREATED, domain="personal",
        entities=[{"type": "Concept", "pref_label": "묵상"}],
        claims=[{"statement": "오늘의 묵상.", "attributed_to": "self",
                 "is_about": ["묵상"]}], vault=vault)
    cid = out["claim_ids"][0]
    path = next(_cio.claims_dir(vault).glob(f"*{cid[:8]}*.md"))
    assert _read(path)["sensitivity"] == "private"     # personal is never public
