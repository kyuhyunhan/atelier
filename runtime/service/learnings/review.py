"""Review / accept / archive / retract for the learnings domain (RFC 0005 §7.1).

The candidate/note/principle DIRECTORIES are gone. A learning is one v7 Claim
whose lifecycle is two FIELDS, not a path:

    ac_status:  pending → passed   (accept)   |  failed/retracted (archive/retract)
    surfacing:  query   → proactive (promote) → always (dream)

So the four operations are FIELD transitions on the claim, performed in place
via `claims_io` (entry_id preserved, content_hash re-derived, file never moved):

- review_pending — list query/pending operational claims with their self-check.
- accept        — ac_status pending → passed (the acceptance GATE). surfacing
                  stays `query`; the separate promote step (RFC 0005 §7.1, behind
                  this same gate) is what elevates a passed claim query→proactive.
- archive       — ac_status → failed   (+ archive_reason).
- retract       — ac_status → retracted (+ archive_reason), from pending OR passed.

`accept` enforces the criteria `must` checks; `should` is informational.
A single-line entry per operation is appended to `<content_root>/learning/log.md`
(`raw/learning/log.md` today).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ...util import config as _config
from . import claims_io as _claims
from . import criteria as _crit
from . import store as _store


# ── filesystem helpers ───────────────────────────────────────────────────────


def _vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _is_operational(fm: Dict[str, Any]) -> bool:
    return str(fm.get("domain") or "") == "operational"


def _iter_pending(vault: Path) -> Iterable[tuple]:
    """(path, fm, body) for every operational claim still awaiting acceptance
    (ac_status pending). These are what review surfaces — the old candidates/."""
    for p in _claims.iter_claim_files(vault):
        got = _claims.read_claim(p)
        if got is None:
            continue
        fm, body = got
        if _is_operational(fm) and str(fm.get("ac_status") or "") == "pending":
            yield p, fm, body


def _accepted_entry_ids(vault: Path) -> List[str]:
    """entry_ids of operational claims that already passed the gate — the
    novelty index (don't re-accept an id already accepted)."""
    ids: List[str] = []
    for p in _claims.iter_claim_files(vault):
        got = _claims.read_claim(p)
        if got is None:
            continue
        fm, _ = got
        if _is_operational(fm) and str(fm.get("ac_status") or "") == "passed":
            eid = fm.get("entry_id")
            if eid:
                ids.append(str(eid))
    return ids


def _find(vault: Path, needle: str):
    found = _claims.find_claim_by_slug_or_id(needle, vault)
    if found is None:
        raise FileNotFoundError(f"no claim matches {needle!r}")
    return found


def _append_log(vault: Path, line: str) -> None:
    log = _store.learning_root(vault) / "log.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ── review_pending ──────────────────────────────────────────────────────────


def review_pending(*, limit: int = 20, project: Optional[str] = None,
                   since: Optional[str] = None) -> Dict[str, Any]:
    vault = _vault_root()
    accepted_ids = _accepted_entry_ids(vault)
    criteria = _crit.load(vault)

    items: List[Dict[str, Any]] = []
    for p, fm, body in _iter_pending(vault):
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
    return {"count": len(items), "items": items, "vault": str(vault)}


# ── accept (the acceptance gate: ac_status pending → passed) ──────────────────


def accept(*, candidate_slug: str, target_topic: Optional[str] = None,
           target_project: Optional[str] = None,
           links: Optional[List[str]] = None,
           override_unknown: bool = False,
           override_must: bool = False) -> Dict[str, Any]:
    vault = _vault_root()
    path, fm, body = _find(vault, candidate_slug)

    accepted_ids = _accepted_entry_ids(vault)
    check = _crit.check(fm, body, accepted_index=accepted_ids, vault_root=vault)

    # `must` must pass — any explicit False blocks; unknown (None) blocks unless
    # override_unknown is set. override_must lets a curator accept despite a
    # must-heuristic miss (e.g. free-form prose carrying a real why). forbidden
    # (pii/pure-meta) is NEVER overridable.
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

    ac_results: Dict[str, Any] = {
        "must": check.must,
        "should": check.should,
        "forbidden": check.forbidden,
    }
    if override_must and failures:
        ac_results["override_must"] = failures        # audit: curator override

    new_links = list(links or [])
    new_fm = _claims.set_ac_status(path, fm, body, new_status="passed",
                                   links=new_links or None,
                                   ac_results=ac_results)
    # target_topic/target_project are optional facet hints (RFC 0001): record
    # them on the claim, but they do NOT move the file (one node, facets only).
    if target_topic or target_project:
        if target_topic:
            new_fm["target_topic"] = target_topic
        if target_project:
            new_fm["target_project"] = target_project
        _rewrite(path, new_fm, body)

    _append_log(vault,
                f"- {_now_iso()}  accept  {target_topic or '-'}/{path.stem}  "
                f"project={target_project or '-'}")

    return {
        "path": str(path),
        "by_project_path": None,             # mirror retired (RFC 0001)
        "topic": target_topic or "",
        "project": target_project,
        "entry_id": new_fm.get("entry_id"),
        "ac_status": "passed",
        "surfacing": _claims.surfacing_of(new_fm),   # stays query until promote
    }


# ── archive ──────────────────────────────────────────────────────────────────


def archive(*, candidate_slug: str, reason: str) -> Dict[str, Any]:
    vault = _vault_root()
    path, fm, body = _find(vault, candidate_slug)
    new_fm = _claims.set_ac_status(path, fm, body, new_status="failed",
                                   archive_reason=reason)
    _append_log(vault, f"- {_now_iso()}  archive  {path.stem}  reason={reason!r}")
    return {"path": str(path), "slug": path.stem,
            "entry_id": new_fm.get("entry_id"), "ac_status": "failed"}


# ── retract ──────────────────────────────────────────────────────────────────


def retract(*, slug: str, reason: str = "retracted") -> Dict[str, Any]:
    vault = _vault_root()
    path, fm, body = _find(vault, slug)
    from_state = "accepted" if str(fm.get("ac_status")) == "passed" else "candidate"
    new_fm = _claims.set_ac_status(path, fm, body, new_status="retracted",
                                   archive_reason=reason)
    _append_log(vault,
                f"- {_now_iso()}  retract  {path.stem}  from={from_state} "
                f"reason={reason!r}")
    return {"path": str(path), "slug": path.stem, "from": from_state,
            "entry_id": new_fm.get("entry_id"), "ac_status": "retracted"}


# ── helper ────────────────────────────────────────────────────────────────────


def _rewrite(path: Path, fm: Dict[str, Any], body: str) -> None:
    """Re-emit a claim file with re-derived content_hash (facet hint update)."""
    fm = dict(fm)
    fm.pop("content_hash", None)
    fm.pop("_prev_surfacing", None)
    fm["content_hash"] = _claims._content_hash(fm)
    import yaml
    serialized = yaml.safe_dump(fm, sort_keys=True, allow_unicode=True,
                                default_flow_style=False)
    path.write_text(f"---\n{serialized}---\n\n{body.strip()}\n", encoding="utf-8")
