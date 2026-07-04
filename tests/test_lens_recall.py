"""RFC 0006 Pillar ③ — dev-lens recall excludes personal; full lens is the wall-less view."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Dict

from runtime.service import api as _api
from runtime.service.learnings import capture as _cap
from runtime.service.learnings import cluster as _cl
from runtime.service.learnings import recall_v7 as _rv
from runtime.service.learnings import review as _rev
from runtime.structure import lenses as _lenses

_TERM = "widgetscope"   # a distinctive token shared by both claims


def _capture_accept_operational(seed: str) -> None:
    cap = _cap.capture(observation=f"{_TERM} throughput {seed}", why="w", rule="r",
                       working_dir="/Users/me/workspaces/lexio", session_id=seed, hook="Stop")
    _rev.accept(candidate_slug=cap["entry_id"], target_topic="t", target_project="lexio")


def _write_personal_claim(vault: Path, seed: str) -> None:
    d = vault / "graph" / "atomic"
    d.mkdir(parents=True, exist_ok=True)
    eid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"personal-{seed}"))
    (d / f"personal-{seed}.md").write_text(
        f"---\nschema_version: 7\nentry_id: {eid}\nkind: claim\n"
        f"domain: personal\nsensitivity: public\n"
        f"statement: {_TERM} diary note {seed}\n---\n\n{_TERM} personal reflection {seed}\n",
        encoding="utf-8")


# ── unit: fm dispatch ────────────────────────────────────────────────────────

def test_lens_admits_fm_dispatch() -> None:
    assert _lenses.lens_admits_fm("dev", {"kind": "claim", "domain": "operational"})
    assert not _lenses.lens_admits_fm("dev", {"kind": "claim", "domain": "personal"})
    assert _lenses.lens_admits_fm("full", {"kind": "claim", "domain": "personal"})
    # entity via in_scheme list (all-match)
    assert _lenses.lens_admits_fm("dev", {"kind": "entity", "in_scheme": ["knowledge"]})
    assert not _lenses.lens_admits_fm("dev", {"kind": "entity", "in_scheme": ["knowledge", "personal"]})
    # unknown kind fails open
    assert _lenses.lens_admits_fm("dev", {"kind": "mystery"})


# ── integration: recall scoping ──────────────────────────────────────────────

def test_dev_lens_excludes_personal_full_keeps_it(atelier_env: Dict) -> None:
    _capture_accept_operational("ops")
    vault = Path(_cl._vault_root())
    _write_personal_claim(vault, "diary")
    _api.reindex(space="gorae", full=True)

    def _domains(hits):
        return [str((h.get("fm") or {}).get("domain") or "") for h in hits]

    dev = _rv.rank_claims(_TERM, None, tier="query", top_k=20, lens="dev")
    full = _rv.rank_claims(_TERM, None, tier="query", top_k=20, lens="full")

    dev_domains = _domains(dev)
    full_domains = _domains(full)
    assert "personal" not in dev_domains          # dev excludes personal …
    assert "operational" in dev_domains           # … but keeps the operational learning
    assert "personal" in full_domains             # full is the no-wall view
    assert len(full) >= len(dev)                  # full is a superset in count
