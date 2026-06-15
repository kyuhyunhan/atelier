"""Learning capture — append-only writer for learnings/candidates/.

Called by:
- the MCP tool `atelier_learning_capture` (Claude itself, mid-session)
- the `~/.atelier/bin/capture-learning.sh` hook adapter (Stop / SessionEnd)

Both routes converge on `capture()`. The function never raises on
plausible inputs — a learning capture is *non-blocking* by contract; the
hook script must never break the user's flow. Real errors (e.g. the
vault directory does not exist) still raise, but only when the engine
genuinely cannot proceed.
"""
from __future__ import annotations

import re
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

import yaml

from ...util import config as _config
from . import store as _store

if TYPE_CHECKING:
    from .project import ProjectResolution


_SLUG_RX = re.compile(r"[^a-z0-9-]+")


def _resolve_vault_root(cfg: _config.Config) -> Path:
    """Return the single vault root. Works with both the new vault: model
    and the legacy spaces: model (librarian-territory acts as the vault)."""
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _slug_from_observation(observation: str, *, fallback: str) -> str:
    """Short kebab-case slug — first ~6 meaningful words."""
    text = (observation or fallback or "untitled").strip().lower()
    text = _SLUG_RX.sub("-", text).strip("-")
    parts = [p for p in text.split("-") if p][:6]
    out = "-".join(parts) or "note"
    return out[:60]


def _resolve_project_hint(working_dir: Optional[str],
                          explicit: Optional[str],
                          cfg: _config.Config) -> "ProjectResolution":
    """Resolve the project tag through the shared accessor so capture,
    bootstrap, and recall cannot diverge (learning `1446`). The layered
    chain still honors an explicit hint first and the vault-self
    dogfooding guard; see `project.resolve_project`. Returns the full
    resolution (slug + source + `known`) so callers can warn when a
    capture lands under a project no accepted learning carries yet."""
    from . import project as _project
    return _project.resolve_project(working_dir, explicit=explicit, cfg=cfg)


def _build_body(observation: str, why: Optional[str],
                rule: Optional[str], excerpt: Optional[str]) -> str:
    parts = ["## Observation", observation.strip() or "(no observation)"]
    parts.append("")
    parts.append("## Why this matters")
    parts.append((why or "").strip())
    if rule and rule.strip():
        parts += ["", "## Applicable rule", rule.strip()]
    if excerpt and excerpt.strip():
        parts += ["", "## Source excerpt", "```", excerpt.strip()[:3000], "```"]
    return "\n".join(parts) + "\n"


_STUB_RX = re.compile(r"^\(hook=\w+\)\s*session_id=", re.I)


def _is_substanceless(observation: str, why: Optional[str]) -> bool:
    """True when there is nothing worth capturing: no real observation
    (empty or a bare hook stub like "(hook=Stop) session_id=...") AND no
    why. This is the signature of a blind hook capture that no LLM
    filled in."""
    obs = (observation or "").strip()
    if obs and not _STUB_RX.match(obs):
        return False                # genuine observation present
    return not (why or "").strip()  # stub/empty obs → substanceless unless why


def capture(*, observation: str,
            why: Optional[str] = None,
            rule: Optional[str] = None,
            excerpt: Optional[str] = None,
            working_dir: Optional[str] = None,
            project_hint: Optional[str] = None,
            session_id: Optional[str] = None,
            agent_kind: str = "claude-code",
            hook: str = "manual",
            observation_kind: str = "feedback",
            require_why: bool = True) -> Dict[str, Any]:
    """Write a single candidate. Returns metadata about the new file, or
    `{skipped: True, reason: ...}` only for the one remaining hard gate.

    Substance gate: a capture with no real observation AND no why is a
    blind hook stub — rejected outright (`no-substance`). That is the ONLY
    rejection.

    Empty `why` is NOT a rejection (RFC 0004 phase 2). A genuine
    observation with no why is written and tagged `why_status: missing`;
    the result carries `why_missing: True` (when `require_why=True`) as a
    soft nudge so a live agent can re-capture with a why. This realizes
    "generous capture, strict promotion": nothing real is dropped at the
    door, and promotion-time criteria (where `has_why` is a SHOULD, not a
    MUST) decide quality later. `require_why=False` suppresses the nudge
    for sources that legitimately carry no template why (e.g. session-end
    hook captures, absorbed Claude memory).
    """
    if _is_substanceless(observation, why):
        return {"skipped": True, "reason": "no-substance",
                "detail": "empty/stub observation and no why"}
    # A genuine observation with an empty `why` is NO LONGER rejected: it is
    # written and flagged `why_status: missing` (RFC 0004 phase 2 — "generous
    # capture, strict promotion"). Losing a real lesson to enforce one optional
    # field was throwing the baby out with the bathwater. `require_why` now only
    # controls whether the *result* nudges the caller to add a why; promotion-
    # time curation judges quality (has_why is a SHOULD, not a MUST).
    why_present = bool((why or "").strip())

    cfg = _config.load()
    vault_root = _resolve_vault_root(cfg)
    if not vault_root.exists():
        raise FileNotFoundError(f"vault root missing: {vault_root}")

    now = datetime.now(timezone.utc).astimezone()
    date_dir = now.date().isoformat()
    time_prefix = now.strftime("%H%M")
    candidates_root = _store.learning_root(vault_root) / "candidates" / date_dir
    candidates_root.mkdir(parents=True, exist_ok=True)

    slug = _slug_from_observation(observation, fallback=hook)
    base_name = f"{time_prefix}-{slug}.md"
    target = candidates_root / base_name
    # Avoid collision: hooks firing in the same minute get a suffix.
    n = 1
    while target.exists():
        target = candidates_root / f"{time_prefix}-{slug}-{n}.md"
        n += 1

    entry_id = str(_uuid.uuid5(
        _uuid.NAMESPACE_DNS,
        f"learnings:candidate:{date_dir}/{target.name}",
    ))

    fm: Dict[str, Any] = {
        "schema_version": 4,
        "entry_id": entry_id,
        "captured_at": now.isoformat(timespec="seconds"),
        "agent_kind": agent_kind or "unknown",
        "hook": hook or "manual",
        "status": "candidate",
        "ac_status": "pending",
        "observation_kind": observation_kind,
        "why_status": "present" if why_present else "missing",
        "ac_results": {},
        "links": [],
    }
    if session_id:
        fm["session_id"] = session_id
    if working_dir:
        fm["working_dir"] = working_dir
    resolution = _resolve_project_hint(working_dir, project_hint, cfg)
    project = resolution.slug
    if project:
        fm["project_hint"] = project

    body = _build_body(observation or "", why, rule, excerpt)
    serialized = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    target.write_text(f"---\n{serialized}\n---\n{body}", encoding="utf-8")

    result: Dict[str, Any] = {
        "path": str(target),
        "entry_id": entry_id,
        "project_hint": project,
        "project_known": resolution.known,
        "why_status": "present" if why_present else "missing",
        "candidate_dir": str(candidates_root),
    }
    # Soft nudge (the candidate WAS written): when a why was expected but not
    # given, tell the caller so a live agent can re-capture with one. Curation
    # can still promote without it (has_why is a SHOULD).
    if not why_present and require_why:
        result["why_missing"] = True
        result["detail"] = ("captured, but flagged why_status=missing; "
                            "consider re-capturing with a 'why this matters'")
    return result
