"""RFC 0005 P3 — lossless extraction coverage test.

This is the gate that proves the P3 link-map loses NOTHING, so P4 atomization
can be bounded by it (the P3->P4 link-set diff must be empty). It asserts:

  (a) every source/entity page on disk appears in the extract;
  (b) ROUND-TRIP edge coverage = 100% (re-extract, set-equality of edges);
  (c) attribute coverage = 100% (every frontmatter key on disk is in the record);
  (d) cross-check oracle: every graph-originating DB `links` row is covered by
      the extracted edge set (markdown truth may be a superset, must MISS NONE).

The test runs against the real configured vault. When the vault (or its DB) is
not present (e.g. a clean CI box without ~/.atelier), it skips — this is a
migration-tooling coverage test, not a unit test of pure functions.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts.rfc_0005 import extract
from runtime.index.parse import split_frontmatter
from runtime.util import config


def _vault_root() -> Path:
    try:
        return extract.vault_root()
    except Exception as e:  # config absent / unreadable on this machine
        pytest.skip(f"no configured vault: {e}")


@pytest.fixture(scope="module")
def vault_root() -> Path:
    root = _vault_root()
    gdir = extract.graph_dir(root)
    if not gdir.is_dir():
        pytest.skip(f"graph dir not on disk: {gdir}")
    return root


@pytest.fixture(scope="module")
def data(vault_root: Path) -> dict:
    return extract.extract(vault_root)


def _ondisk_pages(root: Path) -> list[Path]:
    gdir = extract.graph_dir(root)
    out: list[Path] = []
    for sub in ("sources", "entities"):
        d = gdir / sub
        if d.is_dir():
            out.extend(p for p in d.glob("*.md")
                       if p.name not in extract._SKIP_BASENAMES)
    return out


# ── (a) every page present ──────────────────────────────────────────────────

def test_page_count_matches_disk(vault_root: Path, data: dict) -> None:
    on_disk = {p.relative_to(vault_root).as_posix() for p in _ondisk_pages(vault_root)}
    in_extract = {p["slug"] for p in data["pages"]}
    assert in_extract == on_disk, {
        "missing_from_extract": sorted(on_disk - in_extract)[:10],
        "extra_in_extract": sorted(in_extract - on_disk)[:10],
    }
    # Report the exact on-disk count (expected ~880 = 277 sources + 603 entities).
    assert len(data["pages"]) == len(on_disk)
    assert len(data["pages"]) >= 880, f"expected >=880 pages, got {len(data['pages'])}"


def test_identity_fields_present(data: dict) -> None:
    for p in data["pages"]:
        assert p["slug"]
        assert p["entry_id"]                      # falls back to slug when absent
        assert p["kind"] in ("source", "entity")
        assert isinstance(p["frontmatter"], dict)


# ── (b) round-trip edge coverage = 100% ─────────────────────────────────────

def _edge_set(d: dict) -> set:
    return {
        (e["from_slug"], e["to_target_raw"], e["to_slug_resolved"],
         e["link_type"], e["edge_source"])
        for e in d["edges"]
    }


def test_round_trip_edge_set_equality(vault_root: Path, data: dict) -> None:
    again = extract.extract(vault_root)
    assert _edge_set(again) == _edge_set(data)
    # Also lock the page set is deterministic.
    assert {p["slug"] for p in again["pages"]} == {p["slug"] for p in data["pages"]}


# ── (c) attribute coverage = 100% ───────────────────────────────────────────

def test_attribute_coverage(vault_root: Path, data: dict) -> None:
    recs = {p["slug"]: p for p in data["pages"]}
    mismatches: list[tuple[str, list, list]] = []
    for slug, rec in recs.items():
        fm, _ = split_frontmatter(
            (vault_root / slug).read_text(encoding="utf-8", errors="replace")
        )
        disk_keys = set(fm.keys())
        rec_keys = set(rec["frontmatter"].keys())
        if disk_keys != rec_keys:
            mismatches.append((slug, sorted(disk_keys - rec_keys),
                               sorted(rec_keys - disk_keys)))
    assert not mismatches, mismatches[:10]


# ── (d) DB oracle cross-check: cover every graph-originating link ────────────

def test_db_oracle_covered(data: dict) -> None:
    db_path = config.DB_PATH
    if not db_path.exists():
        pytest.skip(f"no DB at {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        db_edges = {
            (r["fs"], r["to_target"], r["link_type"])
            for r in conn.execute(
                "SELECT p.slug AS fs, l.to_target, l.link_type "
                "FROM links l JOIN pages p ON l.from_page=p.id "
                "WHERE p.slug LIKE 'graph/sources/%' "
                "   OR p.slug LIKE 'graph/entities/%'"
            )
        }
    finally:
        conn.close()
    if not db_edges:
        pytest.skip("DB has no graph-originating links (stale/empty index)")

    md_edges = {
        (e["from_slug"], e["to_target_raw"], e["link_type"])
        for e in data["edges"]
    }
    missed = db_edges - md_edges
    assert not missed, {"count": len(missed), "sample": sorted(missed)[:20]}
