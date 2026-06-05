"""Session-start context injection for Claude Code (and any MCP client).

Returns a single markdown block that the calling hook prints on stdout
so Claude Code includes it as additional_context for the session's first
turn. Two sections:

§ A — Principles (priority == always-inject)
   Universal developer ethos. Same in every session, irrespective of cwd.

§ B — Relevant learnings, retrieved by facet (not folder)
   Scans the canonical accepted pool and selects by the `target_project`
   frontmatter facet, plus a `related by concept` group for cross-project
   learnings that explicitly `touches` a shared concept. The by-project
   tree is never read — project is a signal, not a storage location.

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
from . import project as _project


def _vault_root(cfg: Optional[_config.Config] = None) -> Path:
    cfg = cfg or _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _unknown_project_banner(res: "_project.ProjectResolution") -> str:
    """Loud, in-context notice when the resolved project has no by-project
    learnings dir. Either it's a genuinely new project (fine), or the slug
    is wrong (a renamed/typo'd cwd resolving to an unexpected key) — in
    which case captures scatter under a key nothing recalls. Surfacing it
    turns the silent empty-§B failure (learning `1446`) into a visible
    signal the reader can act on."""
    return (
        f"ℹ️ **atelier** — project resolved to `{res.slug}` via {res.source}, "
        f"which has no learnings yet. If that isn't the project you expect, "
        f"set `learnings.project_map` in ~/.atelier/config.yaml or add a "
        f"`.atelier-project` marker so captures land under the right key."
    )


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


def _explicit_concepts(fm: Dict[str, Any]) -> set:
    """The concepts a curator *explicitly* tagged (`touches`). Session-start
    cross-pollination fires only on these — high-signal, intentional — never on
    the coarse `target_topic` bucket (which still builds the index graph in
    reindex, but would over-connect here)."""
    raw = fm.get("touches")
    if not isinstance(raw, list):
        return set()
    return {c.strip().lower() for c in raw if isinstance(c, str) and c.strip()}


def _scan_accepted(vault: Path) -> List[Dict[str, Any]]:
    """Read the canonical accepted pool (by-topic), folder-free w.r.t. project.
    The by-project tree is never read — project is a frontmatter facet, not a
    storage location (nervous-system: index by idea, not folder)."""
    root = vault / "learnings" / "accepted" / "by-topic"
    items: List[Dict[str, Any]] = []
    if not root.exists():
        return items
    for p in sorted(root.glob("**/*.md")):
        if p.name == "INDEX.md":
            continue
        try:
            fm, _ = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        except Exception:               # pragma: no cover
            continue
        items.append({"path": p, "fm": fm})
    return items


def _bullet(vault: Path, it: Dict[str, Any]) -> str:
    fm = it["fm"]
    title = fm.get("title") or it["path"].stem
    topic = fm.get("target_topic") or "general"
    return (f"- ({topic}) **{title}** — "
            f"[[{it['path'].relative_to(vault).as_posix()}]]")


def _render_project_section(vault: Path, project: str) -> str:
    """§B — this session's relevant learnings, retrieved by *facet*, not folder.
    Own = learnings whose `target_project` is this project; a `related by
    concept` group adds cross-project learnings that explicitly `touches` a
    concept this project's learnings also touch."""
    if not project:
        return ""
    items = _scan_accepted(vault)
    if not items:
        return ""
    own = [it for it in items if it["fm"].get("target_project") == project]
    own_paths = {it["path"] for it in own}
    own_concepts: set = set()
    for it in own:
        own_concepts |= _explicit_concepts(it["fm"])
    neighbors = [
        it for it in items
        if it["path"] not in own_paths and (own_concepts & _explicit_concepts(it["fm"]))
    ] if own_concepts else []

    if not own and not neighbors:
        return ""
    lines = [f"## atelier — learnings for project `{project}`", ""]
    for it in own[:12]:
        lines.append(_bullet(vault, it))
    if neighbors:
        lines.append("")
        lines.append("### related by concept")
        for it in neighbors[:6]:
            lines.append(_bullet(vault, it))
    return "\n".join(lines)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cutoff = text.rfind("\n", 0, max_chars - 32)
    if cutoff < 0:
        cutoff = max_chars - 32
    return text[:cutoff].rstrip() + "\n\n_(truncated)_\n"


def _dream_nudge(*, now: str) -> str:
    """The model-context nudge line. Single source of truth lives in
    dream.nudge_info() so the SessionStart systemMessage hook and the
    statusline share the exact same decision."""
    from . import dream as _dream
    return _dream.nudge_info(now=now)["long"]


def bootstrap(*, working_dir: Optional[str] = None,
              max_chars: int = 6000,
              now: Optional[str] = None) -> Dict[str, Any]:
    cfg = _config.load()
    vault = _vault_root(cfg)
    resolution = _project.resolve_project(working_dir, cfg=cfg)
    project = resolution.slug
    # status="accepted" only — proposed dream-drafts must NOT be injected
    # until a curator promotes them.
    items = _principles.list_all(priority="always-inject", status="accepted")

    if now is None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    nudge = _dream_nudge(now=now)

    # Content and the unknown-project banner are assembled separately: the
    # banner must not make an otherwise-empty vault look non-empty (else the
    # friendly placeholder never shows). The banner is then laid on top so
    # it leads the block and survives end-truncation.
    content_parts: List[str] = []
    if nudge:
        content_parts.append(nudge)
    principles_md = _render_principles(items)
    if principles_md:
        content_parts.append(principles_md)
    project_md = _render_project_section(vault, project) if project else ""
    if project_md:
        content_parts.append(project_md)

    content = "\n\n".join(content_parts).strip()
    if not content:
        content = (
            "## atelier\n\n_(no principles or per-project learnings yet — "
            "use `atelier_learning_capture` and `atelier_principle_add` to "
            "start accumulating)_"
        )

    # Loud-on-unknown: a resolved-but-unbacked project leads the block; a
    # None project (no working_dir) gets no banner.
    banner = (_unknown_project_banner(resolution)
              if (project and not resolution.known) else "")
    block = "\n\n".join(p for p in (banner, content) if p).strip()
    block = _truncate(block, max_chars)

    return {
        "project": project,
        "project_source": resolution.source,
        "project_known": resolution.known,
        "principles_count": len(items),
        "nudge": bool(nudge),
        "char_count": len(block),
        "markdown": block,
    }
