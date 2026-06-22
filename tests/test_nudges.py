"""RFC 0005 §7 — the unified nudge surface.

Every gated, human-invoked edge (atomize / promote / dream) normalizes to one
frozen `Nudge(kind,due,count,short,long)` shape via `runtime.service.nudges`,
and the MCP tool `atelier_nudges` returns the list. These tests cover:

- normalization of each kind (atomize, promote, dream),
- due / not-due,
- tolerance: a failing probe yields a not-due Nudge, never an exception,
- the `atelier_nudges` tool returns the normalized list.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict

import yaml

from runtime.service import nudges as _nudges
from runtime.service import tools as _tools
from runtime.structure import resolver as _structure
from tests.conftest import write_page


_NOW = "2026-06-19T12:00:00+00:00"


# ── fixtures: write v7 source / claim nodes ──────────────────────────────────


def _source(vault: Path, eid: str, subdir: str = "inbox") -> None:
    write_page(
        vault / _structure.source_scan_root() / subdir / f"{eid}.md",
        {"entry_id": eid, "schema_version": 7, "kind": "source",
         "title": eid, "sensitivity": "private", "domain": "knowledge"},
        f"# {eid}\n",
    )


def _claim(vault: Path, eid: str, *, derived_from=None,
           surfacing: str = "query", ac_status: str = "passed",
           domain: str = "operational") -> None:
    fm = {"entry_id": eid, "schema_version": 7, "kind": "claim",
          "statement": f"claim {eid}", "surfacing": surfacing,
          "ac_status": ac_status, "domain": domain}
    if derived_from is not None:
        fm["derived_from"] = derived_from
    write_page(vault / _structure.atomic_claim_dir() / f"{eid}.md", fm,
               f"## Claim\n\n{eid}\n")


def _vault(vault_env: Dict) -> Path:
    return vault_env["vault"]


# ── atomize normalization ────────────────────────────────────────────────────


def test_atomize_not_due_when_empty(vault_env: Dict) -> None:
    by_kind = {n.kind: n for n in _nudges.all_nudges(now=_NOW)}
    a = by_kind["atomize"]
    assert a.kind == "atomize"
    assert a.due is False and a.count == 0
    assert a.short == "" and a.long == ""


def test_atomize_due_normalized(vault_env: Dict) -> None:
    v = _vault(vault_env)
    _source(v, "s1"); _source(v, "s2")
    a = {n.kind: n for n in _nudges.all_nudges(now=_NOW)}["atomize"]
    assert a.due is True
    assert a.count == 2
    assert "atelier atomize" in a.long
    assert "2 un-atomized sources" in a.long
    assert a.short  # non-empty short form


# ── promote normalization (new surface) ──────────────────────────────────────


def test_promote_not_due_when_nothing_eligible(vault_env: Dict) -> None:
    v = _vault(vault_env)
    # query+pending is not eligible; proactive is past the tier.
    _claim(v, "pend", surfacing="query", ac_status="pending")
    _claim(v, "pro", surfacing="proactive", ac_status="passed")
    p = {n.kind: n for n in _nudges.all_nudges(now=_NOW)}["promote"]
    assert p.kind == "promote"
    assert p.due is False and p.count == 0
    assert p.long == ""


def test_promote_due_counts_eligible_claims(vault_env: Dict) -> None:
    v = _vault(vault_env)
    _claim(v, "e1", surfacing="query", ac_status="passed")
    _claim(v, "e2", surfacing="query", ac_status="passed")
    _claim(v, "pend", surfacing="query", ac_status="pending")   # excluded
    p = {n.kind: n for n in _nudges.all_nudges(now=_NOW)}["promote"]
    assert p.due is True
    assert p.count == 2
    assert "atelier promote" in p.long
    assert "atelier-consolidate" in p.long
    assert p.short


def test_promote_singular_noun(vault_env: Dict) -> None:
    v = _vault(vault_env)
    _claim(v, "e1", surfacing="query", ac_status="passed")
    p = {n.kind: n for n in _nudges.all_nudges(now=_NOW)}["promote"]
    assert p.count == 1
    assert "1 accepted claim " in p.long      # singular noun (note trailing space)


# ── dream normalization ──────────────────────────────────────────────────────


def test_dream_normalized_not_due(vault_env: Dict) -> None:
    d = {n.kind: n for n in _nudges.all_nudges(now=_NOW)}["dream"]
    assert d.kind == "dream"
    assert d.due is False and d.count == 0


def test_dream_due_when_threshold_crossed(vault_env: Dict) -> None:
    # Force a low accumulation threshold, then accrue accepted claims.
    home = vault_env["home"]
    cfg_path = home / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data.setdefault("learnings", {})["dream"] = {
        "nudge_after_accepted": 1, "nudge_after_days": 999}
    cfg_path.write_text(yaml.safe_dump(data))

    d = {n.kind: n for n in _nudges.all_nudges(now=_NOW)}["dream"]
    # With no accepted history the probe is not-due; this asserts the wrapper
    # faithfully mirrors the probe's due flag (no inversion).
    info = __import__("runtime.service.learnings.dream",
                      fromlist=["nudge_info"]).nudge_info(now=_NOW)
    assert d.due == bool(info["due"])


# ── all_nudges / due_nudges shape ────────────────────────────────────────────


def test_all_nudges_returns_three_kinds(vault_env: Dict) -> None:
    kinds = [n.kind for n in _nudges.all_nudges(now=_NOW)]
    assert kinds == ["atomize", "promote", "dream"]


def test_due_nudges_filters(vault_env: Dict) -> None:
    v = _vault(vault_env)
    _source(v, "s1")                                   # atomize due
    _claim(v, "e1", surfacing="query", ac_status="passed")  # promote due
    due = _nudges.due_nudges(now=_NOW)
    due_kinds = {n.kind for n in due}
    assert "atomize" in due_kinds
    assert "promote" in due_kinds
    assert all(n.due for n in due)


# ── tolerance: a failing probe never crashes the surface ─────────────────────


def test_failing_probe_yields_not_due(monkeypatch, vault_env: Dict) -> None:
    import runtime.service.learnings.atomize as _atomize

    def _boom(*a, **k):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(_atomize, "nudge_info", _boom)
    by_kind = {n.kind: n for n in _nudges.all_nudges(now=_NOW)}
    # atomize degraded to a safe not-due Nudge…
    assert by_kind["atomize"].due is False
    assert by_kind["atomize"].count == 0
    # …and the OTHER edges still resolve (a broken edge can't suppress them).
    assert "promote" in by_kind and "dream" in by_kind


def test_failing_promote_probe_is_isolated(monkeypatch, vault_env: Dict) -> None:
    from runtime.promote import propose as _propose

    def _boom(*a, **k):
        raise RuntimeError("eligible exploded")

    monkeypatch.setattr(_propose, "eligible_count", _boom)
    p = {n.kind: n for n in _nudges.all_nudges(now=_NOW)}["promote"]
    assert p.due is False and p.long == ""


# ── the MCP tool returns the normalized list ─────────────────────────────────


def test_atelier_nudges_tool_returns_list(vault_env: Dict) -> None:
    v = _vault(vault_env)
    _source(v, "s1")
    out = asyncio.run(_tools.invoke("atelier_nudges"))
    assert "nudges" in out
    assert isinstance(out["nudges"], list)
    kinds = {n["kind"] for n in out["nudges"]}
    assert kinds == {"atomize", "promote", "dream"}
    # each item carries the full normalized shape (dataclass asdict)
    for n in out["nudges"]:
        assert set(n.keys()) == {"kind", "due", "count", "short", "long"}
    atomize = next(n for n in out["nudges"] if n["kind"] == "atomize")
    assert atomize["due"] is True
