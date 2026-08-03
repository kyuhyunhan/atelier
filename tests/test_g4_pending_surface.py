"""RFC 0009 G4 — the pending review surface, asserted against `pending_age`.

The §7 gate: the surface returns **all** pending claims **with ages**, asserted
equal to `pending_age.count` / `.max`. Three defects this pins closed:

- the surface's `limit` silently truncated (`count` was the page size — 41
  pending looked like 20, with no signal more existed);
- items carried only a raw `captured_at`, no age, and no queue max;
- surface and metric enumerated "pending" through two different predicates
  (metric: any-domain pending; surface: operational pending) — coincidentally
  equal on the live vault, structurally free to drift (§3.2 rule 1).

The reconciled definition is the SHARED predicate `claims_io.is_pending_review`:
pending review == the operational acceptance queue (knowledge/personal claims
never pass through the acceptance gate, so a hypothetical pending one is not
reviewable and neither side counts it).
"""
from __future__ import annotations

import datetime
from pathlib import Path
from typing import Dict

from runtime.service import api as _api
from runtime.service.learnings import cluster as _cl
from runtime.service.learnings import metrics as _metrics
from runtime.service.learnings import review as _rev


def _write_claim(vault: Path, name: str, *, domain: str,
                 ac_status: str = "", created_at: str = "2026-07-01T00:00:00+00:00",
                 project_hint: str | None = None) -> None:
    import uuid
    eid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"claim-{name}"))
    d = vault / "graph" / "atomic"
    d.mkdir(parents=True, exist_ok=True)
    ac = f"ac_status: {ac_status}\n" if ac_status else ""
    ph = f"project_hint: {project_hint}\n" if project_hint else ""
    (d / f"{name}.md").write_text(
        f"---\nschema_version: 7\nentry_id: {eid}\nkind: claim\n"
        f"domain: {domain}\nsensitivity: public\nsurfacing: query\n"
        f"{ac}{ph}created_at: {created_at}\nstatement: statement of {name}\n---\n\nbody\n",
        encoding="utf-8")


def _seed_queue(vault: Path) -> None:
    """3 operational pendings of known ages + the two boundary shapes."""
    _write_claim(vault, "p-fresh", domain="operational", ac_status="pending",
                 created_at="2026-07-20T00:00:00+00:00")
    _write_claim(vault, "p-mid", domain="operational", ac_status="pending",
                 created_at="2026-07-01T00:00:00+00:00")
    _write_claim(vault, "p-stale", domain="operational", ac_status="pending",
                 created_at="2026-06-15T00:00:00+00:00")
    # boundary: an accepted operational claim and a (hypothetical) pending
    # KNOWLEDGE claim — neither is in the review queue, and after the predicate
    # unification neither side counts them.
    _write_claim(vault, "p-passed", domain="operational", ac_status="passed")
    _write_claim(vault, "k-pending", domain="knowledge", ac_status="pending")
    _api.reindex(space="wiki", full=True)


AS_OF = "2026-07-23"


def test_g4_total_is_visible_beyond_the_limit(atelier_env: Dict) -> None:
    """No silent cap: `limit` pages `items`, but `total` and `max_age_days`
    describe the WHOLE queue."""
    vault = Path(_cl._vault_root())
    _seed_queue(vault)
    got = _rev.review_pending(limit=1, as_of=AS_OF)
    assert len(got["items"]) == 1 == got["count"]
    assert got["total"] == 3                      # the queue, not the page
    assert got["max_age_days"] == 38              # 2026-06-15 → 2026-07-23


def test_g4_items_carry_age_days(atelier_env: Dict) -> None:
    vault = Path(_cl._vault_root())
    _seed_queue(vault)
    got = _rev.review_pending(as_of=AS_OF)
    ages = {i["slug"]: i["age_days"] for i in got["items"]}
    assert ages["p-fresh"] == 3
    assert ages["p-mid"] == 22
    assert ages["p-stale"] == 38


def test_g4_surface_equals_pending_age_metric(atelier_env: Dict) -> None:
    """THE gate: surface totals == `pending_age.count` / `.max` on the same
    as_of — provable only because both sides now share one predicate."""
    vault = Path(_cl._vault_root())
    _seed_queue(vault)
    surface = _rev.review_pending(as_of=AS_OF)
    metric = _metrics.pending_age(as_of=datetime.date(2026, 7, 23), vault=vault)
    assert surface["total"] == metric["count"] == 3
    assert surface["max_age_days"] == metric["max"] == 38


def test_g4_knowledge_pending_is_counted_by_neither(atelier_env: Dict) -> None:
    """The unified predicate, pinned at the boundary: a pending knowledge claim
    is not reviewable (the acceptance gate is operational-only) and neither the
    surface nor the metric counts it — the drift the old split allowed."""
    vault = Path(_cl._vault_root())
    _seed_queue(vault)
    surface = _rev.review_pending(as_of=AS_OF)
    metric = _metrics.pending_age(as_of=datetime.date(2026, 7, 23), vault=vault)
    slugs = {i["slug"] for i in surface["items"]}
    assert "k-pending" not in slugs and "p-passed" not in slugs
    assert surface["total"] == metric["count"] == 3


def test_g4_empty_queue_measures_zero_on_both_sides(atelier_env: Dict) -> None:
    """Review [MUST] regression: a DRAINED queue is a measurable max of 0 on
    BOTH sides — the surface must not abstain (omitting the key fabricates
    'could not measure' on the queue's most desirable state, and breaks the
    asserted equality with the metric's `max: 0`)."""
    vault = Path(_cl._vault_root())
    _write_claim(vault, "p-passed", domain="operational", ac_status="passed")
    _api.reindex(space="wiki", full=True)
    surface = _rev.review_pending(as_of=AS_OF)
    metric = _metrics.pending_age(as_of=datetime.date(2026, 7, 23), vault=vault)
    assert surface["total"] == metric["count"] == 0
    assert surface["max_age_days"] == metric["max"] == 0


def test_g4_undated_pending_still_counts_and_age_is_none(
        atelier_env: Dict) -> None:
    """An undated pending must not vanish from the queue (that would hide it
    from review); its age is None and max_age_days abstains — mirroring
    pending_age's dated/count split (§5.4)."""
    vault = Path(_cl._vault_root())
    _write_claim(vault, "p-undated", domain="operational", ac_status="pending",
                 created_at="")
    _api.reindex(space="wiki", full=True)
    got = _rev.review_pending(as_of=AS_OF)
    item = next(i for i in got["items"] if i["slug"] == "p-undated")
    assert item["age_days"] is None
    assert got["total"] == 1
    assert "max_age_days" not in got              # unmeasurable tail → abstain
