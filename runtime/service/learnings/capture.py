"""Learning capture — born-as-claim writer (RFC 0005 §7.1).

An operational learning is **born directly as a v7 Claim** — there is no
candidate FILE lifecycle any more. `capture()`:

1. mints a thin v7 Source node carrying the session metadata
   (session_id / working_dir / agent_kind / hook / captured_at), then
2. writes a v7 Claim (`domain:operational`, `surfacing:query`,
   `ac_status:pending`, `generated_by:<hook-or-caller>`) that
   `derived_from` that Source.

The candidate/note/principle DIRECTORIES collapse to the `surfacing` +
`ac_status` FIELDS: promote (query→proactive) and dream (proactive→always)
are field transitions on this same claim, never directory moves.

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
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ...util import config as _config
from . import claims_io as _claims

if TYPE_CHECKING:
    from .project import ProjectResolution


def _resolve_vault_root(cfg: _config.Config) -> Path:
    """Return the single vault root. Works with both the new vault: model
    and the legacy spaces: model (librarian-territory acts as the vault)."""
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


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


def _claim_statement(observation: str, rule: Optional[str]) -> str:
    """The Claim's `statement` — the assertion itself. Prefer the applicable
    rule (the durable lesson) when present; else the observation. One line,
    whitespace-collapsed (it feeds the content-addressed entry_id)."""
    text = (rule or "").strip() or (observation or "").strip() or "(no statement)"
    return " ".join(text.split())[:400]


def capture(*, observation: str,
            why: Optional[str] = None,
            rule: Optional[str] = None,
            excerpt: Optional[str] = None,
            working_dir: Optional[str] = None,
            project_hint: Optional[str] = None,
            touches: Optional[List[str]] = None,
            session_id: Optional[str] = None,
            agent_kind: str = "claude-code",
            hook: str = "manual",
            observation_kind: str = "feedback",
            require_why: bool = True) -> Dict[str, Any]:
    """Born-as-claim: mint a thin session Source and write a v7 operational
    Claim that derives_from it (RFC 0005 §7.1). Returns metadata about the new
    claim, or `{skipped: True, reason: ...}` only for the one remaining hard gate.

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

    `project_hint`/`touches` are resolved-or-created into `is_about` Entity ids
    when present, so the claim is wired into the graph at birth.
    """
    if _is_substanceless(observation, why):
        return {"skipped": True, "reason": "no-substance",
                "detail": "empty/stub observation and no why"}
    why_present = bool((why or "").strip())

    cfg = _config.load()
    vault_root = _resolve_vault_root(cfg)
    if not vault_root.exists():
        raise FileNotFoundError(f"vault root missing: {vault_root}")

    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    resolution = _resolve_project_hint(working_dir, project_hint, cfg)
    project = resolution.slug

    statement = _claim_statement(observation, rule)
    body = _build_body(observation or "", why, rule, excerpt)

    # 1) thin session Source the claim will derive_from (PROV chain at birth).
    src = _claims.mint_session_source(
        statement=statement, session_id=session_id, working_dir=working_dir,
        agent_kind=agent_kind, hook=hook, captured_at=now, vault=vault_root,
    )

    # 2) resolve-or-create is_about entities for project + touched subjects.
    is_about: List[str] = []
    subjects = list(touches or [])
    if project:
        subjects.append(project)
    for label in dict.fromkeys(s for s in subjects if s and s.strip()):
        is_about.append(_claims._resolve_entity_id(
            label, sensitivity="public", in_scheme="inbox",
            vault=vault_root))

    # 3) the operational Claim, born at query/pending. session fields are
    #    mirrored onto the claim so the acceptance criteria heuristics
    #    (tied_to_event, has_project_tag) keep working unchanged.
    extra: Dict[str, Any] = {"captured_at": now, "ac_results": {}}
    if session_id:
        extra["session_id"] = session_id
    if working_dir:
        extra["working_dir"] = working_dir

    # generated_by is the PROV wasGeneratedBy *activity* (schema enum:
    # ingest|atomize|promote|dream) — a born-as-claim capture is `ingest`. The
    # capturing HOOK (Stop/SessionEnd/manual) is recorded in the `hook` field,
    # the agent in `agent_kind`/`attributed_to` (matches the v7 migration shape).
    claim = _claims.write_operational_claim(
        statement=statement, source_entry_id=src["entry_id"], body=body,
        generated_by="ingest",
        attributed_to=agent_kind, agent_kind=agent_kind, hook=hook,
        observation_kind=observation_kind,
        why_status="present" if why_present else "missing",
        project=project or None, is_about=is_about,
        captured_at=now, extra=extra, vault=vault_root,
    )

    # Back-fill the Source's learning_entry_id link now the claim id is known.
    _link_source_to_claim(src["path"], claim["entry_id"])

    result: Dict[str, Any] = {
        "path": claim["path"],
        "entry_id": claim["entry_id"],
        "source_entry_id": src["entry_id"],
        "project_hint": project,
        "project_known": resolution.known,
        "why_status": "present" if why_present else "missing",
        "surfacing": "query",
        "ac_status": "pending",
    }
    if not why_present and require_why:
        result["why_missing"] = True
        result["detail"] = ("captured, but flagged why_status=missing; "
                            "consider re-capturing with a 'why this matters'")
    return result


def _link_source_to_claim(source_path: str, claim_entry_id: str) -> None:
    """Record the derived claim's id back on the session Source's session block
    (mirrors the migration shape; keeps the Source self-describing)."""
    import yaml
    from ...index import parse as _parse
    p = Path(source_path)
    try:
        fm, body = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
    except Exception:                            # pragma: no cover
        return
    if not isinstance(fm, dict):
        return
    session = dict(fm.get("session") or {})
    session["learning_entry_id"] = claim_entry_id
    fm["session"] = session
    fm.pop("content_hash", None)
    fm["content_hash"] = _claims._content_hash(fm)
    serialized = yaml.safe_dump(fm, sort_keys=True, allow_unicode=True,
                                default_flow_style=False)
    p.write_text(f"---\n{serialized}---\n\n{body.strip()}\n", encoding="utf-8")
