"""Phase 5b v1 — the lateral mutator: the dream cycle goes sideways.

Governance encoded from the manual passes that spec'd this:
- suggestions are derived FROM the body (body-echo by construction),
- apply is snapshot-wrapped and reports `newly_dark` (omission guard),
- a tag with no body echo is REJECTED, not written (the 1611 lesson as code),
- merges are flag-only (human-gated); no auto-merge in v1.
"""
from __future__ import annotations

from typing import Dict

from runtime.service import api
from runtime.service.learnings import lateral as _lat
from tests.conftest import write_page


_BASE = {
    "schema_version": 4, "agent_kind": "claude-code", "status": "accepted",
    "ac_status": "passed", "observation_kind": "feedback",
    "captured_at": "2026-01-01T00:00:00Z", "accepted_at": "2026-01-02T00:00:00Z",
}


def _accepted(vault, topic, entry_id, body, *, touches=None, project=None):
    fm = {**_BASE, "entry_id": entry_id, "target_topic": topic}
    if touches:
        fm["touches"] = touches
    if project:
        fm["target_project"] = project
    write_page(vault / "raw" / "learning" / "notes" / "2026-01" /
               f"{entry_id}.md", fm, body)


# ── plan_tags ───────────────────────────────────────────────────────────────


def test_plan_tags_suggests_body_echoing_terms(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    _accepted(vault, "client", "untagged",
              "## Observation\n\nkeychain entitlements hardened runtime "
              "sandbox posture for the overlay panel\n")
    _accepted(vault, "client", "tagged",
              "## Observation\n\npasteboard capture simulation\n",
              touches=["pasteboard"])

    plan = _lat.plan_tags()
    slugs = {it["entry_id"] for it in plan["untagged"]}
    assert "untagged" in slugs
    assert "tagged" not in slugs
    item = next(it for it in plan["untagged"] if it["entry_id"] == "untagged")
    body_words = {"keychain", "entitlements", "hardened", "runtime",
                  "sandbox", "posture", "overlay", "panel"}
    assert item["suggestions"], "must propose candidate tags"
    assert set(item["suggestions"]) <= body_words, item["suggestions"]
    # the coarse topic is already in the probe — never suggest it back
    assert "client" not in item["suggestions"]


def test_plan_tags_skips_views_and_flags_inert_tags(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    # a navigational view (noise) must not be teed up for tagging
    write_page(vault / "raw" / "learning" / "notes" / "2026-01" /
               "TAXONOMY.md", {**_BASE, "entry_id": "tax",
                               "target_topic": "general"}, "vocabulary\n")
    # tags with zero body echo are inert — flag for attention
    _accepted(vault, "client", "inert",
              "## Observation\n\nthe panel layout uses fixed spacing\n",
              touches=["quantum-flux"])

    plan = _lat.plan_tags()
    assert all(it["entry_id"] != "tax" for it in plan["untagged"])
    inert_ids = {it["entry_id"] for it in plan["inert_tagged"]}
    assert "inert" in inert_ids


# ── apply_tags ──────────────────────────────────────────────────────────────


def test_apply_tags_inserts_snapshot_wrapped(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    _accepted(vault, "client", "aaa",
              "## Observation\n\nkeychain stores the sensitive token data\n")
    api.reindex(full=True)

    out = _lat.apply_tags({"aaa": ["keychain", "sensitive-data"]})
    assert out["applied"] == 1
    text = (vault / "raw" / "learning" / "notes" / "2026-01" /
            "aaa.md").read_text()
    assert "touches:" in text and "- keychain" in text
    # snapshot-wrapped: the omission guard is part of the result contract
    assert "newly_dark" in out["diff"]
    assert out["diff"]["newly_dark"] == []
    # idempotent: a second apply skips, never duplicates
    again = _lat.apply_tags({"aaa": ["keychain"]})
    assert again["applied"] == 0 and again["skipped"] == 1


def test_apply_rejects_tags_without_body_echo(vault_env: Dict) -> None:
    """The 1611 lesson as a mechanical gate: FTS indexes bodies, so a tag whose
    tokens never appear in the body is inert — refuse to write it."""
    vault = vault_env["vault"]
    _accepted(vault, "client", "bbb",
              "## Observation\n\npasteboard capture via command c simulation\n")
    api.reindex(full=True)

    out = _lat.apply_tags({"bbb": ["pasteboard", "quantum-flux"]})
    assert out["applied"] == 1
    rejected = out["rejected"]["bbb"]
    assert rejected == ["quantum-flux"]
    text = (vault / "raw" / "learning" / "notes" / "2026-01" /
            "bbb.md").read_text()
    assert "- pasteboard" in text
    assert "quantum-flux" not in text


# ── plan_merges ─────────────────────────────────────────────────────────────


def test_plan_merges_flags_near_duplicates_only(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    dup = ("## Observation\n\nthe keychain stores sensitive tokens and the "
           "pasteboard never holds secrets in the overlay panel\n")
    _accepted(vault, "client", "dup1", dup)
    _accepted(vault, "client", "dup2", dup + "minor trailing remark\n")
    _accepted(vault, "server", "distinct",
              "## Observation\n\ndynamodb usage records table schema design\n")

    plan = _lat.plan_merges()
    assert plan["groups"], "near-duplicates must be flagged"
    members = set(plan["groups"][0]["entry_ids"])
    assert members == {"dup1", "dup2"}
    assert all("distinct" not in g["entry_ids"] for g in plan["groups"])
    # v1 is flag-only: the plan must not contain any write/merge action
    assert "applied" not in plan


# ── MCP dispatch ────────────────────────────────────────────────────────────


def test_lateral_mcp_dispatch(vault_env: Dict) -> None:
    import asyncio
    from runtime.service import tools as _tools

    vault = vault_env["vault"]
    _accepted(vault, "client", "untagged",
              "## Observation\n\nkeychain entitlements sandbox posture\n")
    api.reindex(full=True)

    plan = asyncio.run(_tools.invoke("atelier_lateral_plan"))
    assert plan["tags"]["untagged"][0]["entry_id"] == "untagged"
    assert "groups" in plan["merges"]

    out = asyncio.run(_tools.invoke(
        "atelier_lateral_apply", mapping={"untagged": ["keychain"]}))
    assert out["applied"] == 1
    assert out["diff"]["newly_dark"] == []


# ── review round 1: M1 + S3 + S4 ────────────────────────────────────────────


def test_insert_touches_idempotent_on_long_frontmatter(vault_env: Dict) -> None:
    """M1: the idempotency guard must scan the WHOLE frontmatter, not a fixed
    line window — touches sitting past line 80 must still block a re-insert
    (the bug double-inserted a second touches block)."""
    vault = vault_env["vault"]
    p = vault / "raw" / "learning" / "notes" / "2026-01" / "long.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    fm = (["---"] + [f"field_{i}: {i}" for i in range(85)]
          + ["entry_id: long", "target_topic: client",
             "touches:", "- keychain", "---"])
    p.write_text("\n".join(fm) + "\nkeychain body\n")

    assert _lat._insert_touches(p, ["keychain"]) is False
    assert p.read_text().count("touches:") == 1


def test_apply_reports_unknown_and_fully_rejected(vault_env: Dict) -> None:
    """S1+S3: an unknown entry_id and a fully-rejected mapping must each be
    visible in the result — not folded into applied/skipped silence."""
    vault = vault_env["vault"]
    _accepted(vault, "client", "real",
              "## Observation\n\npasteboard capture body\n")
    api.reindex(full=True)

    out = _lat.apply_tags({"ghost-id": ["pasteboard"],
                           "real": ["quantum-flux"]})       # zero body echo
    assert out["unknown"] == ["ghost-id"]
    assert out["applied"] == 0 and out["skipped"] == 0
    assert out["fully_rejected"] == 1
    assert out["rejected"]["real"] == ["quantum-flux"]


def test_apply_tags_writes_the_flat_note(vault_env: Dict) -> None:
    """RFC 0001: one flat note, no mirror — the tag lands in the note itself."""
    vault = vault_env["vault"]
    _accepted(vault, "client", "mm",
              "## Observation\n\nkeychain sensitive token body\n",
              project="lexio")
    canonical = (vault / "raw" / "learning" / "notes" / "2026-01" /
                 "mm.md")
    api.reindex(full=True)

    out = _lat.apply_tags({"mm": ["keychain"]})
    assert out["applied"] == 1
    assert out["mirror_skipped"] == 0          # no mirror exists (RFC 0001)
    assert "- keychain" in canonical.read_text()


def test_insert_touches_refuses_empty_tags(vault_env: Dict) -> None:
    """NEW-1: never write a bare `touches:` header."""
    vault = vault_env["vault"]
    _accepted(vault, "client", "zz", "## Observation\n\nbody words here\n")
    p = vault / "raw" / "learning" / "notes" / "2026-01" / "zz.md"
    assert _lat._insert_touches(p, []) is False
    assert "touches:" not in p.read_text()


# RFC 0001 retired the by-project mirror, so mirror-divergence (the old
# mirror_skipped counter) can no longer occur. The counter remains in the
# return contract, pinned at 0 by test_apply_tags_writes_the_flat_note.
