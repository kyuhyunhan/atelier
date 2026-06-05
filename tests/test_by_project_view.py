"""Phase 4 — `by-project/` is a *generated view*, not a source of truth.

After the read-path cutover (bootstrap/recall select by the `target_project`
frontmatter facet, never by folder), the by-project mirror tree exists only as
a human-browsable projection of the by-topic canonical. This locks in that it
is fully regenerable: delete the whole tree, run the reconcile routine, and it
is reproduced from canonical — so project is a derived facet, not a placement
decision.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict

from runtime.service.learnings import capture as _cap
from runtime.service.learnings import reconcile as _rec
from runtime.service.learnings import review as _rev


def _accept(project: str, topic: str) -> None:
    cap = _cap.capture(observation=f"obs {project}", why=f"why {project}",
                       rule=f"when working on {project}, do the thing",
                       working_dir=f"/Users/me/workspaces/{project}", hook="Stop")
    _rev.accept(candidate_slug=cap["entry_id"], target_topic=topic,
                target_project=project)


def test_by_project_is_a_regenerable_view(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    _accept("lexio", "architecture")
    _accept("app", "layering")

    bp = vault / "learnings" / "accepted" / "by-project"
    before = {p.relative_to(bp).as_posix(): p.read_text()
              for p in bp.rglob("*.md") if p.name != "INDEX.md"}
    assert before, "accept should have eagerly materialized the view"

    # Delete the entire view (the projection), keep only the canonical.
    shutil.rmtree(bp)
    assert not bp.exists()

    # Regenerate purely from the by-topic canonical.
    counts = _rec.repair(vault)
    assert counts["missing_created"] == len(before)

    after = {p.relative_to(bp).as_posix(): p.read_text()
             for p in bp.rglob("*.md") if p.name != "INDEX.md"}
    # Same learning bodies reappear under the same project dirs.
    assert set(after) == set(before), (set(before), set(after))
