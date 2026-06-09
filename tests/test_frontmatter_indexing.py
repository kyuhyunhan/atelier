"""RFC 0002 P1a — frontmatter is indexed into FTS via a synthetic chunk.

The dark-learnings root: a learning whose concept lives only in `touches:` /
`target_topic:` (never in the body) was invisible to body-only FTS. P1a emits a
`frontmatter` chunk so FTS can match it — without letting that chunk leak into
the body-derived links graph.
"""
from __future__ import annotations

from typing import Dict

from runtime.index import parse as _parse
from tests.conftest import write_page


# ── unit: field selection ───────────────────────────────────────────────────

def test_frontmatter_chunk_indexes_semantic_fields():
    fm = {
        "title": "Depend on protocols",
        "touches": ["dependency-direction", "ports-and-adapters"],
        "target_topic": "architecture",
        # plumbing — must NOT be indexed
        "entry_id": "abc123", "status": "accepted", "schema_version": 4,
        "captured_at": "2026-01-01T00:00:00Z", "confidence": 0.9,
    }
    c = _parse.frontmatter_chunk(fm, position=3)
    assert c is not None
    assert c.heading_path == "frontmatter"
    assert c.position == 3
    text = c.text.lower()
    assert "dependency-direction" in text
    assert "ports-and-adapters" in text
    assert "architecture" in text
    assert "depend on protocols" in text
    # plumbing keys/values absent
    assert "abc123" not in text
    assert "accepted" not in text
    assert "2026-01-01" not in text


def test_frontmatter_chunk_none_when_no_searchable_values():
    fm = {"entry_id": "x", "status": "accepted", "schema_version": 4}
    assert _parse.frontmatter_chunk(fm) is None


def test_parse_file_appends_frontmatter_chunk(tmp_path):
    p = tmp_path / "n.md"
    p.write_text("---\ntitle: T\ntouches:\n  - widget-concept\n---\n# Body\n\nplain body.\n")
    parsed = _parse.parse_file(p)
    fm_chunks = [c for c in parsed.chunks if c.heading_path == "frontmatter"]
    assert len(fm_chunks) == 1
    assert "widget-concept" in fm_chunks[0].text


# ── e2e: the dark vector is closed ──────────────────────────────────────────

_BASE = {
    "schema_version": 4, "agent_kind": "claude-code", "status": "accepted",
    "ac_status": "passed", "observation_kind": "feedback",
    "captured_at": "2026-01-01T00:00:00Z", "accepted_at": "2026-01-02T00:00:00Z",
}


def test_frontmatter_only_concept_is_now_fts_findable(vault_env: Dict):
    """A learning whose concept is ONLY in `touches` (body avoids the words) used
    to be dark; with frontmatter indexed it surfaces by its own concept.

    Decoys whose *bodies* contain the probe words keep the FTS path non-empty, so
    the fs-scan fallback never fires — `ghost` is found ONLY because frontmatter
    is now FTS-indexed. This is the exact omission vector test_surfacing flipped."""
    vault = vault_env["vault"]
    fm = {**_BASE, "entry_id": "ghost", "target_topic": "architecture",
          "touches": ["dependency-direction"]}
    write_page(vault / "learnings" / "notes" / "2026-01" / "ghost.md", fm,
               "## Observation\n\nfoo bar baz qux\n")  # body avoids the concept words
    for i in range(3):
        write_page(vault / "learnings" / "notes" / "2026-01" / f"decoy{i}.md",
                   {**_BASE, "entry_id": f"decoy{i}", "target_topic": "architecture"},
                   f"## Observation\n\narchitecture dependency direction note {i}\n")
    from runtime.service import api
    api.reindex(full=True)

    from runtime.service.learnings import recall as _recall
    hits = _recall.rank_hits("dependency direction", None,
                             ["learning_accepted"], top_k=5, vault=vault)
    assert any(str((h.get("fm") or {}).get("entry_id")) == "ghost" for h in hits)


def test_frontmatter_wikilink_does_not_pollute_links_graph(vault_env: Dict):
    """The frontmatter chunk must be excluded from body-derived link extraction —
    a `[[...]]`-looking value in frontmatter must not create a links-table edge."""
    vault = vault_env["vault"]
    fm = {**_BASE, "entry_id": "lk", "target_topic": "architecture",
          "touches": ["[[entities/should-not-link]]"]}
    write_page(vault / "learnings" / "notes" / "2026-01" / "lk.md", fm,
               "## Observation\n\nreal body with no links.\n")
    from runtime.service import api
    api.reindex(full=True)

    from runtime.util import db
    conn = db.connect()
    try:
        rows = list(conn.execute(
            "SELECT l.to_target FROM links l JOIN pages p ON p.id=l.from_page "
            "WHERE p.slug LIKE '%lk.md' AND l.link_type='wikilink'"))
    finally:
        conn.close()
    assert all("should-not-link" not in r["to_target"] for r in rows)
