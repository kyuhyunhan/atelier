"""RFC 0005 P1 — resolver contract + entry_id byte-for-byte snapshot.

The entry_id snapshots LOCK reproduction of today's stored ids: each expected
value is computed from the EXACT legacy discriminator string that lived inline
in the writer (capture.py, youtube.py, absorb_claude.py, principles.py,
promote/apply.py, cli.py/tools.py, learnings/capture.py). If a future edit
moves a template, these fail — by design.
"""
from __future__ import annotations

import uuid

import pytest

from runtime.structure import resolver


# --- Path API ------------------------------------------------------------
def test_roots():
    assert resolver.content_root() == "raw"
    assert resolver.graph_root() == "graph"


def test_intake_dirs():
    assert resolver.intake_dir("personal") == "raw/personal"
    assert resolver.intake_dir("knowledge") == "raw/knowledge"
    assert resolver.intake_dir("inbox") == "raw/inbox"
    assert resolver.intake_dir("workshop") == "workshop"
    # `inbox` is a first-class intake sibling of personal/knowledge (RFC 0005
    # §3), NOT a leaf under personal — captures are not personal-by-channel.
    assert resolver.inbox_dir() == "raw/inbox"


def test_intake_rejects_unknown():
    with pytest.raises(KeyError):
        resolver.intake_dir("nope")
    with pytest.raises(KeyError):
        resolver.intake_dir("inbox_subpath")


def test_homes():
    assert resolver.home("graph_source") == "graph/sources"
    assert resolver.home("graph_entity") == "graph/entities"
    assert resolver.home("graph_theme") == "graph/themes"
    assert resolver.home("learning_candidate") == "raw/learning/candidates"
    assert resolver.home("learning_note") == "raw/learning/notes"
    assert resolver.home("learning_principle") == "raw/learning/principles"
    assert resolver.home("learning_archived") == "raw/learning/archived"
    assert resolver.home("product") == "workshop/products"


def test_atomic_dirs():
    # RFC 0005 §7.2 — the v7 atomic node trees, single-sourced from structure.yaml.
    assert resolver.atomic_source_dir() == "graph/atomic/sources"
    assert resolver.atomic_claim_dir() == "graph/atomic/claims"
    assert resolver.atomic_entity_dir() == "graph/atomic/entities"


def test_home_rejects_unknown():
    with pytest.raises(KeyError):
        resolver.home("nope")


# --- Prefix aliasing: must match reindex.py constants exactly -------------
def test_prefix_aliases_match_reindex():
    from runtime.index import reindex

    assert resolver.prefix_aliases() == reindex._PREFIX_ALIASES


def test_known_prefixes_match_reindex():
    from runtime.index import reindex

    assert resolver.known_prefixes() == reindex._KNOWN_SLUG_PREFIXES


def test_shorthand_bases_match_reindex():
    from runtime.index import reindex

    assert resolver.shorthand_bases() == reindex._SHORTHAND_BASES


# --- entry_id snapshot: byte-for-byte reproduction -----------------------
def _expect(discriminator: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, discriminator))


def test_entry_id_youtube():
    # youtube.py:212 -> f"atelier:youtube:{discriminator}", discriminator=video_id
    assert resolver.entry_id("youtube", video_id="dQw4w9WgXcQ") == _expect(
        "atelier:youtube:dQw4w9WgXcQ"
    )


def test_entry_id_claude():
    # absorb_claude.py:184 -> f"learnings:claude:{mem.body_sha}"
    assert resolver.entry_id("claude", body_sha="abc123") == _expect(
        "learnings:claude:abc123"
    )


def test_entry_id_principle():
    # principles.py:67 -> f"learnings:principle:{slug}"
    assert resolver.entry_id("principle", slug="immutability-first") == _expect(
        "learnings:principle:immutability-first"
    )


def test_entry_id_promote():
    # Legacy template, retained as a snapshot. RFC 0005 §7.1 promote is now a
    # field transition (claim query→proactive, entry_id PRESERVED), so it no
    # longer mints an id; this only pins the template's byte-for-byte value.
    assert resolver.entry_id("promote", target_slug="some/target") == _expect(
        "promote:some/target"
    )


def test_entry_id_product():
    # cli.py:172 & tools.py:629 -> f"workshop:products/{name}"
    assert resolver.entry_id("product", name="my-product") == _expect(
        "workshop:products/my-product"
    )


def test_entry_id_learning_candidate():
    # learnings/capture.py:151 -> f"learnings:candidate:{date_dir}/{target.name}"
    assert resolver.entry_id(
        "learning_candidate", date="2026-06", name="2026-06-18T1200-foo.md"
    ) == _expect("learnings:candidate:2026-06/2026-06-18T1200-foo.md")


def test_entry_id_capture():
    # capture.py:54 -> now.isoformat() + slug
    iso = "2026-06-18T12:00:00+00:00"
    slug = "a-quick-thought"
    assert resolver.entry_id("capture", iso=iso, slug=slug) == _expect(iso + slug)


# --- NEW content-based template (P1; no legacy form) ---------------------
def test_entry_id_source_new():
    out = resolver.entry_id(
        "source", created_at="2026-06-18T12:00:00+00:00", discriminator="title-x"
    )
    assert out == _expect("atelier:source:2026-06-18T12:00:00+00:00|title-x")


def test_entry_id_uses_dns_namespace_only():
    # The resolver must never use any namespace other than stock NAMESPACE_DNS.
    assert resolver._namespace() == uuid.NAMESPACE_DNS


def test_entry_id_rejects_unknown_kind():
    with pytest.raises(KeyError):
        resolver.entry_id("nope")
