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
from typing import Any, Dict, Optional

import yaml

from ...util import config as _config


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
                          vault_root: Path) -> Optional[str]:
    """Prefer the explicit hint; otherwise derive from working_dir's
    basename. Working directory inside the vault is tagged
    `atelier-self` so dogfooding doesn't pollute project tags."""
    if explicit:
        return explicit
    if not working_dir:
        return None
    wd = Path(working_dir).expanduser().resolve()
    try:
        wd.relative_to(vault_root.resolve())
        return "atelier-self"
    except (ValueError, RuntimeError):
        pass
    name = wd.name
    return name or None


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


def capture(*, observation: str,
            why: Optional[str] = None,
            rule: Optional[str] = None,
            excerpt: Optional[str] = None,
            working_dir: Optional[str] = None,
            project_hint: Optional[str] = None,
            session_id: Optional[str] = None,
            agent_kind: str = "claude-code",
            hook: str = "manual",
            observation_kind: str = "feedback") -> Dict[str, Any]:
    """Write a single candidate. Returns metadata about the new file.

    This function performs no validation against the acceptance criteria
    — that happens at promotion time. The point of the candidate stage
    is to capture *everything* with minimal friction.
    """
    cfg = _config.load()
    vault_root = _resolve_vault_root(cfg)
    if not vault_root.exists():
        raise FileNotFoundError(f"vault root missing: {vault_root}")

    now = datetime.now(timezone.utc).astimezone()
    date_dir = now.date().isoformat()
    time_prefix = now.strftime("%H%M")
    candidates_root = vault_root / "learnings" / "candidates" / date_dir
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
        "ac_results": {},
        "links": [],
    }
    if session_id:
        fm["session_id"] = session_id
    if working_dir:
        fm["working_dir"] = working_dir
    project = _resolve_project_hint(working_dir, project_hint, vault_root)
    if project:
        fm["project_hint"] = project

    body = _build_body(observation or "", why, rule, excerpt)
    serialized = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    target.write_text(f"---\n{serialized}\n---\n{body}", encoding="utf-8")

    return {
        "path": str(target),
        "entry_id": entry_id,
        "project_hint": project,
        "candidate_dir": str(candidates_root),
    }
