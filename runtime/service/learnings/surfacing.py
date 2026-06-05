"""Surfacing audit — observability for the *retrieval* layer (Phase 5a).

A nervous-system memory reorganizes itself; the dangerous failure of that is
**silent omission** — a learning that quietly stops surfacing where it used to
matter. A git diff shows what *moved* on disk; it cannot show what stopped being
*recalled*. This module is the missing instrument: it measures the corpus's own
retrievability and makes a drop visible.

The probe is **self-referential and deterministic** (no LLM, no query history):
for each accepted learning, query recall with *its own concept* (`touches` +
`target_topic`, the same signal that builds the concept index) and check whether
the learning still appears in its own top-K. A learning that cannot be retrieved
by its own concept has gone *dark*.

`snapshot()` captures that state; `diff()` compares two snapshots so a
reorganization pass (the future mutator, 5b) can be audited in *behavior*, not
just in content. The audit is read-only — it never mutates the vault.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...index import parse as _parse
from . import recall as _recall

# A learning should be findable within this many results when searched by its
# own concept; deeper than this it is effectively drowned out → "dark".
DEFAULT_PROBE_K = 10

_TYPES = ["learning_principle", "learning_accepted"]


def _concept_probe(fm: Dict[str, Any]) -> str:
    """The query that asks 'can this learning be found by what it is about?' —
    its `touches` concepts plus `target_topic`, split into words. Falls back to
    the title when a learning carries no concept tags."""
    parts: List[str] = []
    raw = fm.get("touches")
    if isinstance(raw, list):
        parts.extend(c for c in raw if isinstance(c, str))
    topic = fm.get("target_topic")
    if isinstance(topic, str):
        parts.append(topic)
    if not parts and isinstance(fm.get("title"), str):
        parts.append(fm["title"])
    words: List[str] = []
    for p in parts:
        words.extend(w for w in re.split(r"[\s\-_/]+", p) if w)
    return " ".join(words)


def _enumerate_accepted(vault: Path) -> List[Dict[str, Any]]:
    """The canonical accepted pool (by-topic) — one row per learning, keyed by
    entry_id. The by-project view is never read (it is a projection)."""
    root = vault / "learnings" / "accepted" / "by-topic"
    out: List[Dict[str, Any]] = []
    if not root.exists():
        return out
    for p in sorted(root.glob("**/*.md")):
        if p.name == "INDEX.md":
            continue
        try:
            fm, _ = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        except Exception:               # pragma: no cover
            continue
        eid = fm.get("entry_id")
        if not eid:
            continue
        out.append({
            "entry_id": str(eid),
            "title": str(fm.get("title") or p.stem),
            "project": fm.get("target_project") or fm.get("project_hint"),
            "topic": fm.get("target_topic") or "",
            "probe": _concept_probe(fm),
        })
    return out


def snapshot(*, probe_k: int = DEFAULT_PROBE_K) -> Dict[str, Dict[str, Any]]:
    """Map entry_id → {visible, rank, probe, project, topic, title}. `rank` is
    the 0-based position a learning occupies when searched by its own concept,
    or None when it does not appear within `probe_k` (i.e. it is dark)."""
    vault = _recall._vault_root()
    snap: Dict[str, Dict[str, Any]] = {}
    for it in _enumerate_accepted(vault):
        eid, probe = it["entry_id"], it["probe"]
        rank: Optional[int] = None
        if probe.strip():
            hits = _recall._rank_hits(probe, it["project"], _TYPES, top_k=probe_k)
            for i, h in enumerate(hits):
                if str((h.get("fm") or {}).get("entry_id")) == eid:
                    rank = i
                    break
        snap[eid] = {
            "visible": rank is not None,
            "rank": rank,
            "probe": probe,
            "project": it["project"],
            "topic": it["topic"],
            "title": it["title"],
        }
    return snap


def audit(*, probe_k: int = DEFAULT_PROBE_K) -> Dict[str, Any]:
    """Standalone diagnostic — which accepted learnings are unreachable by their
    own concept *right now*. Useful even without a reorganization pass: it finds
    memory that has already gone effectively dead."""
    snap = snapshot(probe_k=probe_k)
    dark = [
        {"entry_id": eid, "title": s["title"], "project": s["project"],
         "topic": s["topic"], "probe": s["probe"]}
        for eid, s in snap.items() if not s["visible"]
    ]
    dark.sort(key=lambda d: (str(d["project"]), str(d["topic"]), d["title"]))
    return {
        "total": len(snap),
        "visible": len(snap) - len(dark),
        "dark": dark,
        "dark_count": len(dark),
        "probe_k": probe_k,
    }


def diff(before: Dict[str, Dict[str, Any]],
         after: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Compare two snapshots — the surfacing delta of whatever happened between
    them (a reorganization pass, an edit, an absorb). `newly_dark` is the one
    that matters most: learnings that *stopped* surfacing for their own concept
    and would otherwise vanish unnoticed."""
    newly_dark: List[Dict[str, Any]] = []
    newly_visible: List[Dict[str, Any]] = []
    rank_drops: List[Dict[str, Any]] = []

    for eid, a in after.items():
        b = before.get(eid)
        if b is None:
            continue                    # new learning — not an omission
        if b["visible"] and not a["visible"]:
            newly_dark.append({"entry_id": eid, "title": a["title"],
                               "project": a["project"], "probe": a["probe"]})
        elif not b["visible"] and a["visible"]:
            newly_visible.append({"entry_id": eid, "title": a["title"],
                                  "project": a["project"]})
        elif (b["visible"] and a["visible"]
              and b["rank"] is not None and a["rank"] is not None
              and a["rank"] > b["rank"]):
            rank_drops.append({"entry_id": eid, "title": a["title"],
                               "from": b["rank"], "to": a["rank"]})

    dropped = [eid for eid in before if eid not in after]
    return {
        "newly_dark": newly_dark,
        "newly_visible": newly_visible,
        "rank_drops": rank_drops,
        "removed": dropped,
        "regressions": len(newly_dark) + len(dropped),
    }
