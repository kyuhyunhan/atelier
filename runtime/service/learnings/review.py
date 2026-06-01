"""Review / accept / archive / retract for the learnings domain.

The 4 operations form the lifecycle:

    candidates/<date>/<slug>.md
            │
            ├──→  accepted/by-topic/<topic>/<slug>.md
            │     (+ accepted/by-project/<project>/<slug>.md as a hardlink-style copy)
            │
            ├──→  archived/<slug>.md  (with archive_reason)
            │
            └──→  archived/<slug>.md  (retracted=True)

`accept` enforces the criteria `must` checks; `should` is informational.
`retract` works on candidates and on already-accepted entries.

A single-line entry per operation is appended to `learnings/log.md`.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from ...index import parse as _parse
from ...util import config as _config
from . import criteria as _crit


# ── Filesystem helpers ───────────────────────────────────────────────────────


def _vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _iter_candidates(vault: Path) -> Iterable[Path]:
    root = vault / "learnings" / "candidates"
    if not root.exists():
        return
    for p in sorted(root.rglob("*.md")):
        yield p


def _iter_accepted(vault: Path) -> Iterable[Path]:
    root = vault / "learnings" / "accepted"
    if not root.exists():
        return
    for p in sorted(root.rglob("*.md")):
        # Skip the by-project mirror so each entry shows up once.
        if "by-project" in p.parts:
            continue
        yield p


def _accepted_entry_ids(vault: Path) -> List[str]:
    ids: List[str] = []
    for p in _iter_accepted(vault):
        fm, _ = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        eid = fm.get("entry_id")
        if eid:
            ids.append(str(eid))
    return ids


_SLUG_RX = re.compile(r"[^a-z0-9-]+")


def _slugify(value: str, *, fallback: str = "x") -> str:
    text = (value or fallback).strip().lower()
    text = _SLUG_RX.sub("-", text).strip("-")
    return text[:60] or fallback


def _find_candidate(vault: Path, slug: str) -> Path:
    """Locate a candidate by either its filename slug or its entry_id.

    The `slug` argument may be:
    - the bare filename (e.g. "1432-foo-bar.md" or "1432-foo-bar")
    - a relative path under candidates/ (e.g. "2026-05-28/1432-foo-bar.md")
    - an entry_id (uuid)
    """
    candidates = list(_iter_candidates(vault))
    needle = slug.removesuffix(".md")

    # 1) exact filename or relative-path match
    for p in candidates:
        rel = p.relative_to(vault / "learnings" / "candidates").as_posix()
        if rel == needle or rel == slug:
            return p
        if p.stem == needle:
            return p

    # 2) entry_id match
    for p in candidates:
        fm, _ = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        if str(fm.get("entry_id")) == slug:
            return p

    raise FileNotFoundError(f"no candidate matches {slug!r}")


def _find_accepted(vault: Path, slug: str) -> Path:
    needle = slug.removesuffix(".md")
    for p in _iter_accepted(vault):
        if p.stem == needle:
            return p
        fm, _ = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        if str(fm.get("entry_id")) == slug:
            return p
    raise FileNotFoundError(f"no accepted learning matches {slug!r}")


def _append_log(vault: Path, line: str) -> None:
    log = vault / "learnings" / "log.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _rewrite_frontmatter(path: Path, updates: Dict[str, Any], removes: Iterable[str] = ()) -> None:
    text = path.read_text(encoding="utf-8")
    fm, body = _parse.split_frontmatter(text)
    fm = dict(fm)
    for k in removes:
        fm.pop(k, None)
    for k, v in updates.items():
        fm[k] = v
    serialized = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    path.write_text(f"---\n{serialized}\n---\n{body}", encoding="utf-8")


# ── review_pending ──────────────────────────────────────────────────────────


def review_pending(*, limit: int = 20, project: Optional[str] = None,
                   since: Optional[str] = None) -> Dict[str, Any]:
    vault = _vault_root()
    accepted_ids = _accepted_entry_ids(vault)
    criteria = _crit.load(vault)

    items: List[Dict[str, Any]] = []
    for p in _iter_candidates(vault):
        fm, body = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        if project and fm.get("project_hint") != project:
            continue
        if since and str(fm.get("captured_at", "")) < since:
            continue
        check = _crit.check(fm, body, accepted_index=accepted_ids,
                            criteria=criteria)
        items.append({
            "slug": p.stem,
            "path": str(p),
            "captured_at": fm.get("captured_at"),
            "project_hint": fm.get("project_hint"),
            "hook": fm.get("hook"),
            "entry_id": fm.get("entry_id"),
            "must_pass": check.must_pass(),
            "forbidden_clear": check.forbidden_clear(),
            "must": check.must,
            "should": check.should,
            "forbidden": check.forbidden,
        })
        if len(items) >= limit:
            break
    return {
        "count": len(items),
        "items": items,
        "vault": str(vault),
    }


# ── accept ─────────────────────────────────────────────────────────────────


def accept(*, candidate_slug: str, target_topic: str,
           target_project: Optional[str] = None,
           links: Optional[List[str]] = None,
           override_unknown: bool = False,
           override_must: bool = False) -> Dict[str, Any]:
    vault = _vault_root()
    src = _find_candidate(vault, candidate_slug)

    fm, body = _parse.split_frontmatter(src.read_text(encoding="utf-8"))
    accepted_ids = _accepted_entry_ids(vault)
    check = _crit.check(fm, body, accepted_index=accepted_ids,
                        vault_root=vault)

    # `must` must pass — any explicit False blocks; unknown (None) blocks
    # unless override_unknown is set.
    #
    # override_must lets a *curator* (human, or a trusted review pass) accept
    # despite must-failures: the rule-based check is a safety net against
    # un-reviewed auto-accepts, and human review is exactly the judgement
    # that may override it (e.g. free-form prose carrying a real "why" that
    # the section-header heuristic misses). forbidden (pii/pure-meta) is
    # NEVER overridable.
    failures = [k for k, v in check.must.items() if v is False]
    unknowns = [k for k, v in check.must.items() if v is None]
    forbidden = [k for k, v in check.forbidden.items() if v is True]

    blocked = bool(forbidden) or (
        not override_must and (failures or (unknowns and not override_unknown))
    )
    if blocked:
        raise PermissionError({
            "reason": "acceptance criteria not satisfied",
            "must_failed": failures,
            "must_unknown": unknowns,
            "forbidden_triggered": forbidden,
            "hint": ("forbidden criteria cannot be overridden"
                     if forbidden else
                     "pass override_must=true to accept a reviewed candidate "
                     "despite a must heuristic miss"),
        })

    topic = _slugify(target_topic, fallback="general")
    dest_dir = vault / "learnings" / "accepted" / "by-topic" / topic
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    # Collision avoidance — two captures with the same minute + slug from
    # different sessions would otherwise overwrite one another.
    n = 1
    while dest.exists():
        stem = Path(src.name).stem
        dest = dest_dir / f"{stem}-{n}{Path(src.name).suffix}"
        n += 1

    fm = dict(fm)
    fm["status"] = "accepted"
    fm["ac_status"] = "passed"
    fm["accepted_at"] = _now_iso()
    fm["target_topic"] = topic
    if target_project:
        fm["target_project"] = target_project
    if links:
        fm["links"] = list(dict.fromkeys(list(fm.get("links") or []) + links))
    fm["ac_results"] = {
        "must": check.must,
        "should": check.should,
        "forbidden": check.forbidden,
    }
    if override_must and failures:
        fm["ac_results"]["override_must"] = failures   # audit: curator override

    serialized = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    dest.write_text(f"---\n{serialized}\n---\n{body}", encoding="utf-8")
    src.unlink()

    # by-project mirror — copy rather than symlink (portable across
    # filesystems and easier to backup).
    project_path: Optional[Path] = None
    if target_project:
        project_path = (vault / "learnings" / "accepted" / "by-project"
                        / _slugify(target_project, fallback="misc") / dest.name)
        project_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dest, project_path)

    _append_log(vault,
                f"- {fm['accepted_at']}  accept  {topic}/{src.stem}  "
                f"project={target_project or '-'}")

    from . import indexes as _indexes
    if target_project:
        _indexes.safe_regen_project(target_project)

    return {
        "path": str(dest),
        "by_project_path": str(project_path) if project_path else None,
        "topic": topic,
        "project": target_project,
        "entry_id": fm.get("entry_id"),
    }


# ── archive ────────────────────────────────────────────────────────────────


def archive(*, candidate_slug: str, reason: str) -> Dict[str, Any]:
    vault = _vault_root()
    src = _find_candidate(vault, candidate_slug)
    dest_dir = vault / "learnings" / "archived"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name

    _rewrite_frontmatter(src, {
        "status": "archived",
        "ac_status": "failed",
        "archived_at": _now_iso(),
        "archive_reason": reason,
    })
    shutil.move(str(src), str(dest))

    _append_log(vault,
                f"- {_now_iso()}  archive  {src.stem}  reason={reason!r}")
    return {"path": str(dest), "slug": src.stem}


# ── retract ────────────────────────────────────────────────────────────────


def retract(*, slug: str, reason: str = "retracted") -> Dict[str, Any]:
    vault = _vault_root()
    try:
        src = _find_accepted(vault, slug)
        from_state = "accepted"
    except FileNotFoundError:
        src = _find_candidate(vault, slug)
        from_state = "candidate"

    dest_dir = vault / "learnings" / "archived"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name

    _rewrite_frontmatter(src, {
        "status": "archived",
        "ac_status": "retracted",
        "archived_at": _now_iso(),
        "archive_reason": reason,
    })
    shutil.move(str(src), str(dest))

    # If retracting an accepted, also remove the by-project mirror.
    if from_state == "accepted":
        for p in (vault / "learnings" / "accepted" / "by-project").rglob(src.name) \
                 if (vault / "learnings" / "accepted" / "by-project").exists() else []:
            p.unlink(missing_ok=True)

    _append_log(vault,
                f"- {_now_iso()}  retract  {src.stem}  from={from_state} "
                f"reason={reason!r}")

    if from_state == "accepted":
        # We lost the project mapping by the time we get here; safest is
        # to regen every project that had a by-project mirror for this file.
        from . import indexes as _indexes
        proj_root = vault / "learnings" / "accepted" / "by-project"
        if proj_root.exists():
            for proj_dir in proj_root.iterdir():
                if proj_dir.is_dir():
                    _indexes.safe_regen_project(proj_dir.name)

    return {"path": str(dest), "slug": src.stem, "from": from_state}
