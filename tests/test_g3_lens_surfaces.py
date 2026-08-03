"""RFC 0009 G3 — the lens is HONOURED, not merely accepted (§5.5).

`metrics.lens_param_present` can only prove a handler *accepts* `lens`; these
tests are the behavioural half of the bar: on a seeded corpus, the newly-wired
surfaces return DIFFERENT result sets under lens='dev' vs lens='full' (dev
excludes personal, full includes — the `verify._check_dev_lens_no_personal`
shape), and the dev-session claim path is project-scoped (foreign-project
claims are not pushed; G3 part B).
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any, Dict, List

import yaml as _yaml

from runtime.service import api as _api
from runtime.service import tools as _tools
from runtime.service.learnings import bootstrap as _bs
from runtime.service.learnings import cluster as _cl
from runtime.service.learnings import recall_v7 as _rv
from runtime.service.learnings import search as _ls

_TERM = "zebrakite"        # distinctive token shared by every seeded node


def _claim_md(name: str, *, domain: str, project: str = "",
              surfacing: str = "proactive", ac_status: str = "passed") -> str:
    eid = str(uuid.uuid5(uuid.NAMESPACE_DNS, name))
    proj_line = f"project: {project}\n" if project else ""
    return (f"---\nschema_version: 7\nentry_id: {eid}\nkind: claim\n"
            f"domain: {domain}\nsensitivity: public\nsurfacing: {surfacing}\n"
            f"ac_status: {ac_status}\n{proj_line}"
            f"created_at: 2026-07-01T00:00:00+00:00\n"
            f"statement: {_TERM} finding {name}\n---\n\n{_TERM} body {name}\n")


def _seed_mixed_claims(vault: Path) -> None:
    """One operational own-project claim + one personal claim, both carrying
    the probe token — the minimal corpus on which dev and full MUST differ."""
    d = vault / "graph" / "atomic"
    d.mkdir(parents=True, exist_ok=True)
    (d / "g3-op.md").write_text(
        _claim_md("g3-op", domain="operational", project="projA"),
        encoding="utf-8")
    (d / "g3-personal.md").write_text(
        _claim_md("g3-personal", domain="personal"), encoding="utf-8")
    _api.reindex(space="wiki", full=True)


def _run(tool: str, **params: Any) -> Dict[str, Any]:
    return asyncio.run(_tools.invoke(tool, **params))


# ── the newly-wired surfaces: dev vs full result sets DIFFER ────────────────


def test_list_pages_dev_excludes_personal_full_includes(
        atelier_env: Dict) -> None:
    vault = Path(_cl._vault_root())
    _seed_mixed_claims(vault)
    dev = _run("atelier_list_pages", lens="dev")["pages"]
    full = _run("atelier_list_pages", lens="full")["pages"]
    dev_slugs = {p["slug"] for p in dev}
    full_slugs = {p["slug"] for p in full}
    assert dev_slugs != full_slugs                       # the sets DIFFER
    assert not any("g3-personal" in s for s in dev_slugs)
    assert any("g3-personal" in s for s in full_slugs)
    assert any("g3-op" in s for s in dev_slugs)          # dev still serves work
    assert dev_slugs <= full_slugs                       # full is the superset


def test_search_dev_excludes_personal_full_includes(atelier_env: Dict) -> None:
    vault = Path(_cl._vault_root())
    _seed_mixed_claims(vault)
    dev = _run("atelier_search", query=_TERM, lens="dev")["hits"]
    full = _run("atelier_search", query=_TERM, lens="full")["hits"]
    dev_slugs = {h["slug"] for h in dev}
    full_slugs = {h["slug"] for h in full}
    assert dev_slugs != full_slugs
    assert not any("g3-personal" in s for s in dev_slugs)
    assert any("g3-personal" in s for s in full_slugs)
    assert any("g3-op" in s for s in dev_slugs)


def test_learning_search_dev_excludes_personal_full_includes(
        atelier_env: Dict) -> None:
    """The claim pool behind learning_search is domain-mixed (page_type
    `claim` covers every domain), so this surface could leak a personal claim
    before G3 wired the lens through its result set."""
    vault = Path(_cl._vault_root())
    _seed_mixed_claims(vault)
    dev = _run("atelier_learning_search", query=_TERM, status="accepted",
               lens="dev")["items"]
    full = _run("atelier_learning_search", query=_TERM, status="accepted",
                lens="full")["items"]
    dev_slugs = {h["slug"] for h in dev}
    full_slugs = {h["slug"] for h in full}
    assert dev_slugs != full_slugs
    assert not any("g3-personal" in s for s in dev_slugs)
    assert any("g3-personal" in s for s in full_slugs)


def test_session_bootstrap_dev_excludes_personal_full_includes(
        atelier_env: Dict) -> None:
    """The highest-leverage surface (§5.5): it pushes content the caller never
    asked for. A personal-domain node in the accepted pool must be absent from
    the dev block and present in the full block — accept-and-discard would
    fail this test."""
    vault = Path(_cl._vault_root())
    notes = vault / "raw" / "learning" / "notes" / "2026-07"
    notes.mkdir(parents=True, exist_ok=True)
    for name, domain in (("g3-note-op", "operational"),
                         ("g3-note-personal", "personal")):
        fm = {"schema_version": 7, "kind": "claim", "domain": domain,
              "ac_status": "passed", "sensitivity": "public",
              "entry_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, name)),
              "title": f"{name} title", "target_project": "lexio",
              "target_topic": "t"}
        (notes / f"{name}.md").write_text(
            "---\n" + _yaml.safe_dump(fm, sort_keys=False) + "---\nbody\n",
            encoding="utf-8")
    dev = _bs.bootstrap(working_dir="/Users/me/workspaces/lexio", lens="dev")
    full = _bs.bootstrap(working_dir="/Users/me/workspaces/lexio", lens="full")
    assert dev["markdown"] != full["markdown"]           # the blocks DIFFER
    assert "g3-note-personal" not in dev["markdown"]
    assert "g3-note-personal" in full["markdown"]
    assert "g3-note-op" in dev["markdown"]               # dev still serves work


def test_unknown_lens_is_rejected_on_every_wired_surface(
        atelier_env: Dict) -> None:
    """Validation goes through the ONE vocabulary (`lenses.lens_names()`), on
    every surface — not a per-handler list that can drift."""
    import pytest
    calls = [
        ("atelier_search", {"query": "x"}),
        ("atelier_list_pages", {}),
        ("atelier_learning_search", {"query": "x"}),
        ("atelier_session_bootstrap", {}),
        ("atelier_think", {"query": "x"}),
        ("atelier_recall", {"query": "x"}),
    ]
    for tool, params in calls:
        with pytest.raises(ValueError):
            _run(tool, lens="no-such-lens", **params)


def test_think_accepts_and_applies_the_lens(atelier_env: Dict) -> None:
    """think's legacy pool (learning_principle/learning_accepted) is
    operational by construction, so dev == full there today — the lens is
    still validated and applied to the result set (fail-open on legacy nodes
    per `lens_admits_fm`), which this exercises end to end."""
    out = _run("atelier_think", query=_TERM, lens="dev")
    assert out["contract"]
    assert isinstance(out["citations"], list)


# ── G3 part B: the dev-session claim path is project-scoped ─────────────────


def test_project_scope_gate_semantics() -> None:
    own = {"project": "projA", "domain": "operational"}
    foreign = {"project": "projB", "domain": "operational"}
    unowned = {"domain": "knowledge"}
    # push tiers: foreign blocked, own/unowned pass
    for tier in (_rv.TIER_PROACTIVE, _rv.TIER_ALWAYS):
        assert _rv.project_scope_gate(own, tier, "projA")
        assert _rv.project_scope_gate(unowned, tier, "projA")
        assert not _rv.project_scope_gate(foreign, tier, "projA")
    # explicit on-query reaches anything (the sensitivity-gate shape)
    assert _rv.project_scope_gate(foreign, _rv.TIER_QUERY, "projA")
    # no session project → no scope to enforce
    assert _rv.project_scope_gate(foreign, _rv.TIER_PROACTIVE, None)


def test_dev_push_excludes_foreign_projects_but_query_reaches_them(
        atelier_env: Dict) -> None:
    """The serving-path proof: at the push tier a foreign-project operational
    claim is absent from a projA session's results, while the same claim stays
    reachable by explicit on-query (T2) — scoped, not erased."""
    vault = Path(_cl._vault_root())
    d = vault / "graph" / "atomic"
    d.mkdir(parents=True, exist_ok=True)
    (d / "g3-own.md").write_text(
        _claim_md("g3-own", domain="operational", project="projA"),
        encoding="utf-8")
    (d / "g3-foreign.md").write_text(
        _claim_md("g3-foreign", domain="operational", project="projB"),
        encoding="utf-8")
    _api.reindex(space="wiki", full=True)

    def _slugs(hits: List[Dict[str, Any]]) -> set:
        return {h["slug"].rsplit("/", 1)[-1] for h in hits}

    push = _slugs(_rv.rank_claims(_TERM, "projA", tier="proactive",
                                  top_k=20, lens="dev"))
    assert not any("g3-foreign" in s for s in push)      # foreign not pushed
    assert any("g3-own" in s for s in push)              # own still served

    on_query = _slugs(_rv.rank_claims(_TERM, "projA", tier="query", top_k=20,
                                      lens="dev"))
    assert any("g3-foreign" in s for s in on_query)      # T2 reaches it
