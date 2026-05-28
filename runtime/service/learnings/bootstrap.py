"""Session-start context injection for Claude Code (and any MCP client).

Returns a single markdown block that the calling hook prints on stdout
so Claude Code includes it as additional_context for the session's first
turn. Two sections:

§ A — Principles (priority == always-inject)
   Universal developer ethos. Same in every session, irrespective of cwd.

§ B — Project-specific learnings
   Walks `learnings/accepted/by-project/<basename(cwd)>/` and emits
   either the auto-generated INDEX.md (preferred) or a one-line list
   reconstructed on the fly.

The whole block is truncated to `max_chars` (default 6000). Sections
shrink in reverse priority — principles never get clipped before
project entries.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ...index import parse as _parse
from ...util import config as _config
from . import principles as _principles


def _vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _project_slug(working_dir: Optional[str]) -> Optional[str]:
    if not working_dir:
        return None
    base = Path(working_dir).expanduser().name
    return base or None


def _render_principles(items: List[Dict[str, Any]]) -> str:
    if not items:
        return ""
    lines = ["## atelier — principles (always-inject)", ""]
    for it in items:
        title = it.get("title") or it["slug"]
        # Read the body for the Rule one-liner.
        rule_line = _first_rule_line(Path(it["path"]))
        if rule_line:
            lines.append(f"- **{title}** — {rule_line}")
        else:
            lines.append(f"- **{title}**")
    return "\n".join(lines)


_RULE_HEADER_RX = re.compile(r"^##+\s*Rule\b", re.M | re.I)


def _first_rule_line(path: Path) -> Optional[str]:
    try:
        _, body = _parse.split_frontmatter(path.read_text(encoding="utf-8"))
    except Exception:        # pragma: no cover
        return None
    m = _RULE_HEADER_RX.search(body)
    if not m:
        return None
    rest = body[m.end():].lstrip()
    for line in rest.splitlines():
        s = line.strip()
        if s:
            return s
    return None


def _project_index_path(vault: Path, project: str) -> Path:
    return vault / "learnings" / "accepted" / "by-project" / project / "INDEX.md"


def _project_files(vault: Path, project: str) -> List[Path]:
    root = vault / "learnings" / "accepted" / "by-project" / project
    if not root.exists():
        return []
    return [p for p in sorted(root.glob("*.md")) if p.name != "INDEX.md"]


def _render_project_section(vault: Path, project: str) -> str:
    if not project:
        return ""
    index = _project_index_path(vault, project)
    if index.exists():
        body = index.read_text(encoding="utf-8")
        # Strip frontmatter if present.
        _, body = _parse.split_frontmatter(body)
        if body.strip():
            return f"## atelier — learnings for project `{project}`\n\n{body.strip()}"
    # Fallback: build a list inline.
    files = _project_files(vault, project)
    if not files:
        return ""
    lines = [f"## atelier — learnings for project `{project}`", ""]
    for p in files:
        try:
            fm, _ = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        except Exception:           # pragma: no cover
            continue
        title = fm.get("title") or p.stem
        topic = fm.get("target_topic") or "general"
        lines.append(f"- ({topic}) **{title}** — [[{p.relative_to(vault).as_posix()}]]")
    return "\n".join(lines)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cutoff = text.rfind("\n", 0, max_chars - 32)
    if cutoff < 0:
        cutoff = max_chars - 32
    return text[:cutoff].rstrip() + "\n\n_(truncated)_\n"


def _days_between(iso_a: Optional[str], iso_b: str) -> Optional[float]:
    """Whole-ish days between two ISO timestamps; None if `iso_a` absent
    or unparseable."""
    if not iso_a:
        return None
    from datetime import datetime
    try:
        a = datetime.fromisoformat(iso_a)
        b = datetime.fromisoformat(iso_b)
    except ValueError:
        return None
    return (b - a).total_seconds() / 86400.0


def _dream_nudge(*, now: str) -> str:
    """One-line session-start nudge when the dream cycle wants attention.

    Two independent triggers (either fires):
    - accumulation: accepted_since_last_dream >= nudge_after_accepted, OR
      days_since_last_dream >= nudge_after_days
    - pending review: proposed drafts exist (an interrupted or unreviewed
      dream); since last_dream_at only advances on a clean pass, an
      interrupted dream keeps the accumulation trigger armed too.
    Returns "" when nothing is due.
    """
    from . import cluster as _cluster
    from . import principles as _principles_mod

    cfg = _config.load()
    dream_cfg = (cfg.raw.get("learnings") or {}).get("dream") or {}
    after_accepted = int(dream_cfg.get("nudge_after_accepted", 15))
    after_days = int(dream_cfg.get("nudge_after_days", 7))

    try:
        status = _cluster.dream_status()
    except Exception:                       # pragma: no cover - defensive
        return ""
    since = int(status.get("accepted_since_last_dream", 0))
    last = status.get("last_dream_at")
    days = _days_between(last, now)

    pending = 0
    try:
        pending = _principles_mod.review_proposed(limit=200).get("count", 0)
    except Exception:                       # pragma: no cover
        pending = 0

    accumulation_due = (
        since >= after_accepted
        or (days is not None and days >= after_days)
        or (last is None and since >= after_accepted)
    )

    if not accumulation_due and pending == 0:
        return ""

    bits: List[str] = []
    if pending > 0:
        bits.append(
            f"{pending} proposed principle(s) await review "
            f"(`atelier_principle_review_proposed`)"
        )
    if accumulation_due:
        when = (f"{since} new learnings"
                if since else "enough time")
        if days is not None and days >= after_days and since < after_accepted:
            when = f"{int(days)} days"
        bits.append(
            f"{when} since the last dream — ask me to run a dream pass "
            f"(`atelier_learning_cluster` → synthesize proposals)"
        )
    return "💡 **atelier dream** — " + "; ".join(bits) + "."


def bootstrap(*, working_dir: Optional[str] = None,
              max_chars: int = 6000,
              now: Optional[str] = None) -> Dict[str, Any]:
    vault = _vault_root()
    project = _project_slug(working_dir)
    # status="accepted" only — proposed dream-drafts must NOT be injected
    # until a curator promotes them.
    items = _principles.list_all(priority="always-inject", status="accepted")

    if now is None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    nudge = _dream_nudge(now=now)

    parts: List[str] = []
    if nudge:
        parts.append(nudge)
    principles_md = _render_principles(items)
    if principles_md:
        parts.append(principles_md)
    project_md = _render_project_section(vault, project) if project else ""
    if project_md:
        parts.append(project_md)

    block = "\n\n".join(parts).strip()
    if not block:
        block = (
            "## atelier\n\n_(no principles or per-project learnings yet — "
            "use `atelier_learning_capture` and `atelier_principle_add` to "
            "start accumulating)_"
        )
    block = _truncate(block, max_chars)

    return {
        "project": project,
        "principles_count": len(items),
        "nudge": bool(nudge),
        "char_count": len(block),
        "markdown": block,
    }
