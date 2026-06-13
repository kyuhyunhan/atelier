"""Auto-generated indexes for the learnings tier.

Two indexes:

- `learnings/principles/INDEX.md`

Each is regenerated on demand by the operations that change the
underlying tree (accept / archive / retract / principle.add / etc.).
Regeneration is best-effort — a corrupt frontmatter on one entry must
not stop the rest from rendering.

The indexes are *generated*, not authored. They carry a frontmatter
marker so `atelier_validate` can recognize and skip them.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from ...index import parse as _parse
from ...util import config as _config
from . import store as _store


_GENERATED_BANNER = (
    "<!-- atelier:generated — do not edit by hand. "
    "Regenerated on each learning lifecycle event. -->"
)


def _vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _first_line_of_section(body: str, header_rx: re.Pattern) -> Optional[str]:
    m = header_rx.search(body)
    if not m:
        return None
    rest = body[m.end():].lstrip()
    for line in rest.splitlines():
        s = line.strip()
        if s:
            return s
    return None


_RULE_HEADER_RX = re.compile(r"^##+\s*Rule\b", re.M | re.I)
_OBS_HEADER_RX  = re.compile(r"^##+\s*Observation\b", re.M | re.I)


def _summarize(path: Path) -> Optional[Dict[str, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        fm, body = _parse.split_frontmatter(text)
    except Exception:                     # pragma: no cover
        return None
    title = fm.get("title") or path.stem
    topic = fm.get("target_topic") or fm.get("topic") or ""
    one_liner = (
        _first_line_of_section(body, _RULE_HEADER_RX)
        or _first_line_of_section(body, _OBS_HEADER_RX)
        or ""
    )
    return {
        "slug": path.stem,
        "title": str(title),
        "topic": str(topic),
        "one_liner": one_liner,
        "path": str(path),
    }


def _write_if_changed(target: Path, body: str) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_text(encoding="utf-8") == body:
        return False
    target.write_text(body, encoding="utf-8")
    return True


# The per-project INDEX.md was retired with the by-project mirror (RFC 0001):
# "lexio's learnings" is now a facet query, not a generated folder listing.


# ── principles INDEX.md ──────────────────────────────────────────────────


def regen_principles(vault: Optional[Path] = None) -> Dict[str, object]:
    vault = vault or _vault_root()
    root = _store.learning_root(vault) / "principles"
    if not root.exists():
        return {"written": False, "count": 0, "reason": "no principles dir"}

    entries: List[Dict[str, str]] = []
    for p in sorted(root.glob("*.md")):
        if p.name == "INDEX.md":
            continue
        s = _summarize(p)
        if s:
            # principles need priority/coverage too — re-read frontmatter
            try:
                fm, _ = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
                s["priority"] = str(fm.get("priority") or "")
                s["coverage"] = str(fm.get("coverage") or "")
            except Exception:                # pragma: no cover
                s["priority"] = ""
                s["coverage"] = ""
            entries.append(s)

    lines: List[str] = []
    lines.append("---")
    lines.append("schema_version: 4")
    lines.append("type: learnings_index")
    lines.append("scope: principles")
    lines.append(f"entry_count: {len(entries)}")
    lines.append("---")
    lines.append("")
    lines.append(_GENERATED_BANNER)
    lines.append("")
    lines.append("# principles — developer ethos")
    lines.append("")
    lines.append(f"_{len(entries)} principles_")
    lines.append("")

    if not entries:
        lines.append("_(none yet)_")
    else:
        # Order: always-inject first, then on-relevant-prompt, then manual-only.
        bucket_order = ["always-inject", "on-relevant-prompt", "manual-only", ""]
        for bucket in bucket_order:
            in_bucket = [e for e in entries if e.get("priority", "") == bucket]
            if not in_bucket:
                continue
            heading = bucket or "(no priority set)"
            lines.append(f"## {heading}")
            lines.append("")
            for e in in_bucket:
                lead = f"- [[{e['slug']}]] — {e['title']}"
                if e["one_liner"]:
                    lead += f": {e['one_liner']}"
                lines.append(lead)
            lines.append("")

    body = "\n".join(lines).rstrip() + "\n"
    written = _write_if_changed(root / "INDEX.md", body)
    return {"written": written, "count": len(entries),
            "path": str(root / "INDEX.md")}


# ── best-effort wrapper for callers ─────────────────────────────────────


def safe_regen_principles() -> None:
    try:
        regen_principles()
    except Exception:                        # pragma: no cover
        pass
