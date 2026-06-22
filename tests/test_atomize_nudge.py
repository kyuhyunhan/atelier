"""RFC 0005 §7.2 — the atomize nudge: surface 'N un-atomized sources'.

An un-atomized source is a DERIVED state: a Source node with no Claim
`derived_from` it. The nudge mirrors the dream nudge (single source of truth in
`atomize.nudge_info`, surfaced at session bootstrap), so the human runs
`vault-ingest` — no blind cron.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import yaml

from runtime.service.learnings import atomize as _atomize
from runtime.service.learnings import bootstrap as _bs
from runtime.structure import resolver as _structure


# ── fixtures: write v7 source / claim nodes into the atomic graph dirs ────────


def _write_node(vault: Path, dirpath: str, name: str, fm: Dict) -> None:
    d = vault / dirpath
    d.mkdir(parents=True, exist_ok=True)
    serialized = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True)
    (d / f"{name}.md").write_text(f"---\n{serialized}---\n# {name}\n",
                                  encoding="utf-8")


def _source(vault: Path, eid: str, subdir: str = "inbox") -> None:
    # RFC 0005 §3: an L1 Source lives in the content tree (raw/…), classified by
    # the `kind` FIELD, not the path. Default raw/inbox (thin session source);
    # pass subdir="knowledge" to mimic an artifact-backed source under raw/<domain>/.
    _write_node(vault, f"{_structure.source_scan_root()}/{subdir}", eid, {
        "entry_id": eid, "schema_version": 7, "kind": "source",
        "title": eid, "sensitivity": "private", "domain": "knowledge",
    })


def _claim(vault: Path, eid: str, derived_from) -> None:
    _write_node(vault, _structure.atomic_claim_dir(), eid, {
        "entry_id": eid, "schema_version": 7, "kind": "claim",
        "statement": f"claim {eid}", "derived_from": derived_from,
        "surfacing": "query", "domain": "knowledge",
    })


def _set_atomize_cfg(home: Path, *, after: int) -> None:
    cfg_path = home / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data.setdefault("learnings", {})["atomize"] = {"nudge_after_sources": after}
    cfg_path.write_text(yaml.safe_dump(data))


def _vault(atelier_env: Dict) -> Path:
    return atelier_env["gorae"]


# ── count: |sources| − |sources with a derived claim| ────────────────────────


def test_count_zero_when_no_sources(atelier_env: Dict) -> None:
    assert _atomize.unatomized_count(vault=_vault(atelier_env)) == 0


def test_count_all_sources_unatomized(atelier_env: Dict) -> None:
    v = _vault(atelier_env)
    _source(v, "s1"); _source(v, "s2"); _source(v, "s3")
    assert _atomize.unatomized_count(vault=v) == 3


def test_count_excludes_atomized_sources(atelier_env: Dict) -> None:
    v = _vault(atelier_env)
    _source(v, "s1"); _source(v, "s2"); _source(v, "s3")
    _claim(v, "c1", ["s1"])             # s1 atomized
    _claim(v, "c2", ["s2"])             # s2 atomized
    assert _atomize.unatomized_count(vault=v) == 1   # only s3 remains


def test_count_handles_scalar_derived_from(atelier_env: Dict) -> None:
    v = _vault(atelier_env)
    _source(v, "s1")
    _claim(v, "c1", "s1")              # bare scalar, not a list
    assert _atomize.unatomized_count(vault=v) == 0


def test_count_ignores_dangling_claim(atelier_env: Dict) -> None:
    """A claim derived_from a nonexistent source cannot lower the count below
    the real un-atomized set (we intersect with the real source ids)."""
    v = _vault(atelier_env)
    _source(v, "s1")
    _claim(v, "c1", ["ghost"])         # references a source that doesn't exist
    assert _atomize.unatomized_count(vault=v) == 1   # s1 still un-atomized


# ── nudge_info: {due, count, short, long} ────────────────────────────────────


def test_nudge_not_due_when_no_backlog(atelier_env: Dict) -> None:
    info = _atomize.nudge_info(vault=_vault(atelier_env))
    assert info["due"] is False
    assert info["count"] == 0
    assert info["long"] == "" and info["short"] == ""


def test_nudge_due_with_backlog(atelier_env: Dict) -> None:
    v = _vault(atelier_env)
    _source(v, "s1"); _source(v, "s2")
    info = _atomize.nudge_info(vault=v)
    assert info["due"] is True
    assert info["count"] == 2
    assert "2 un-atomized sources" in info["long"]
    assert "vault-ingest" in info["long"]
    assert "2 to atomize" in info["short"]


def test_nudge_threshold_respected(atelier_env: Dict) -> None:
    _set_atomize_cfg(atelier_env["home"], after=3)
    v = _vault(atelier_env)
    _source(v, "s1"); _source(v, "s2")
    info = _atomize.nudge_info(vault=v)
    assert info["due"] is False            # 2 < 3 threshold
    _source(v, "s3")
    assert _atomize.nudge_info(vault=v)["due"] is True


def test_nudge_singular_noun(atelier_env: Dict) -> None:
    v = _vault(atelier_env)
    _source(v, "s1")
    info = _atomize.nudge_info(vault=v)
    assert "1 un-atomized source" in info["long"]
    assert "sources" not in info["long"]   # singular, not plural


# ── bootstrap integration: the atomize nudge surfaces in model context ───────


def test_bootstrap_surfaces_atomize_nudge(atelier_env: Dict) -> None:
    v = _vault(atelier_env)
    _source(v, "s1"); _source(v, "s2")
    out = _bs.bootstrap(working_dir=str(v.parent / "someproj"),
                        now="2026-06-19T12:00:00+00:00")
    assert out["atomize_nudge"] is True
    assert "atelier atomize" in out["markdown"]
    assert "2 un-atomized sources" in out["markdown"]


def test_bootstrap_no_atomize_nudge_when_empty(atelier_env: Dict) -> None:
    out = _bs.bootstrap(working_dir=str(_vault(atelier_env).parent / "someproj"),
                        now="2026-06-19T12:00:00+00:00")
    assert out["atomize_nudge"] is False
    assert "atelier atomize" not in out["markdown"]
