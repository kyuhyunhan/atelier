"""Detect (and optionally repair) drift between the by-topic canonical
accepted learnings and their by-project mirrors.

`review.accept` writes each accepted learning to

    learnings/accepted/by-topic/<topic>/<name>.md          (canonical)

and, when `target_project` is set, copies it to

    learnings/accepted/by-project/<slug(project)>/<name>.md (mirror)

`retract` removes mirrors and `relink` propagates edits — i.e. forward
propagation already exists. What was missing is a *drift detector* for the
cases those paths don't cover: a hand-deleted canonical, a hand-edited
mirror, a project re-tag, or an accept interrupted between the two writes.

The join key is `entry_id` (unique per learning), not the filename — two
topics may legitimately hold same-named files. Generated index/readme files
are not learnings and are skipped.
"""
from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from ...index import parse as _parse
from . import indexes as _indexes
from .review import _slugify, _vault_root

# Only INDEX.md is engine-generated. A learning may legitimately be NAMED
# README.md (e.g. an absorbed project README), so the real discriminator is
# the presence of an `entry_id` — generated files have none.
_GENERATED = {"INDEX.md"}


@dataclass
class Drift:
    kind: str                    # "orphan" | "missing" | "divergent" | "duplicate"
    entry_id: Optional[str]
    by_topic: Optional[str]      # vault-relative path to the canonical (if any)
    by_project: Optional[str]    # vault-relative path to the mirror (if any)
    project_dir: Optional[str]   # slugified project dir the mirror belongs in
    detail: str


def _read(path: Path):
    fm, body = _parse.split_frontmatter(path.read_text(encoding="utf-8"))
    return fm or {}, body or ""


def _body_hash(body: str) -> str:
    return hashlib.sha256(body.strip().encode("utf-8")).hexdigest()[:16]


def _iter_md(root: Path):
    if not root.exists():
        return
    for p in sorted(root.rglob("*.md")):
        if p.name in _GENERATED:
            continue
        yield p


def check(vault: Optional[Path] = None) -> List[Drift]:
    """Pure, read-only. Return the list of mirror/canonical drifts."""
    vault = vault or _vault_root()
    acc = vault / "learnings" / "accepted"
    bt_root, bp_root = acc / "by-topic", acc / "by-project"

    def rel(p: Path) -> str:
        return str(p.relative_to(vault))

    # by-topic canonical: entry_id -> {path, name, project, hash}
    canonical: Dict[str, dict] = {}
    for p in _iter_md(bt_root):
        fm, body = _read(p)
        eid = fm.get("entry_id")
        if eid:
            canonical[eid] = {"path": p, "name": p.name,
                              "project": fm.get("target_project"),
                              "hash": _body_hash(body)}

    # by-project mirrors: entry_id -> [paths]
    mirror_by_eid: Dict[str, List[Path]] = {}
    for p in _iter_md(bp_root):
        fm, _ = _read(p)
        eid = fm.get("entry_id")
        if eid:
            mirror_by_eid.setdefault(eid, []).append(p)

    drifts: List[Drift] = []

    for eid, mirrors in mirror_by_eid.items():
        src = canonical.get(eid)
        if src is None:
            for m in mirrors:
                drifts.append(Drift("orphan", eid, None, rel(m), m.parent.name,
                                    "mirror has no by-topic canonical"))
            continue
        want = _slugify(src["project"], fallback="misc") if src["project"] else None
        correct, wrong = [], []
        for m in mirrors:
            (correct if (want and m.parent.name == want) else wrong).append(m)
        for m in wrong:
            drifts.append(Drift("orphan", eid, rel(src["path"]), rel(m), m.parent.name,
                                f"mirror in '{m.parent.name}' but canonical project "
                                f"is {want!r}"))
        if correct:
            # keep the mirror whose name matches the canonical; extras are dupes
            keep = next((m for m in correct if m.name == src["name"]), correct[0])
            for m in correct:
                if m is keep:
                    _, mbody = _read(m)
                    if _body_hash(mbody) != src["hash"]:
                        drifts.append(Drift("divergent", eid, rel(src["path"]),
                                            rel(m), want,
                                            "mirror body differs from canonical"))
                else:
                    drifts.append(Drift("duplicate", eid, rel(src["path"]),
                                        rel(m), want,
                                        "duplicate mirror for the same learning"))

    for eid, src in canonical.items():
        if not src["project"]:
            continue
        want = _slugify(src["project"], fallback="misc")
        if not any(m.parent.name == want for m in mirror_by_eid.get(eid, [])):
            drifts.append(Drift("missing", eid, rel(src["path"]), None, want,
                                f"no by-project mirror under {want!r}"))

    return drifts


def repair(vault: Optional[Path] = None) -> Dict[str, object]:
    """Reconcile mirrors to the by-topic canonical source of truth.

    orphan → delete mirror; missing → copy canonical into the right project
    dir; divergent → overwrite mirror from canonical. Affected project indexes
    are regenerated.
    """
    vault = vault or _vault_root()
    drifts = check(vault)
    counts = {"orphan_removed": 0, "duplicate_removed": 0,
              "missing_created": 0, "divergent_fixed": 0}
    touched: set[str] = set()

    # Deletes (orphan/duplicate) MUST run before creates (missing): two
    # learnings may share a filename + project, so a create has to collision-
    # avoid against the post-delete state, exactly as review.accept does.
    order = {"orphan": 0, "duplicate": 0, "divergent": 1, "missing": 2}
    for d in sorted(drifts, key=lambda x: order.get(x.kind, 9)):
        if d.kind in ("orphan", "duplicate"):
            (vault / d.by_project).unlink(missing_ok=True)
            counts["orphan_removed" if d.kind == "orphan"
                   else "duplicate_removed"] += 1
            if d.project_dir:
                touched.add(d.project_dir)
        elif d.kind == "divergent":
            shutil.copy2(vault / d.by_topic, vault / d.by_project)
            counts["divergent_fixed"] += 1
            if d.project_dir:
                touched.add(d.project_dir)
        elif d.kind == "missing":
            dest_dir = vault / "learnings" / "accepted" / "by-project" / d.project_dir
            dest_dir.mkdir(parents=True, exist_ok=True)
            src = vault / d.by_topic
            dest = dest_dir / src.name
            n = 1
            while dest.exists():
                dest = dest_dir / f"{src.stem}-{n}{src.suffix}"
                n += 1
            shutil.copy2(src, dest)
            counts["missing_created"] += 1
            touched.add(d.project_dir)

    for proj in sorted(touched):
        _indexes.safe_regen_project(proj)

    counts["projects_regenerated"] = sorted(touched)
    return counts
