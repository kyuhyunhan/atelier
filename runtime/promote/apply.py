"""Apply a reviewed promotion proposal.

Parses the proposal markdown, finds rows with `promote: true`, and writes
the corresponding `wiki/synthesis/*.md` page through the Librarian's
writer (single-writer-per-space invariant preserved).

A PROMOTION_LOG.md is appended in ~/.atelier/cache/.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from ..util import config

PROMOTION_LOG = config.CACHE_DIR / "PROMOTION_LOG.md"


def _parse_proposal(path: Path) -> List[Dict[str, str]]:
    blocks: List[Dict[str, str]] = []
    cur: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() == "---":
            if cur:
                blocks.append(cur)
            cur = {}
            continue
        m = re.match(r"^([a-z_]+):\s*(.*)$", line.strip())
        if m and cur is not None:
            cur[m.group(1)] = m.group(2)
    if cur:
        blocks.append(cur)
    return [b for b in blocks if b.get("source")]


def apply_proposal(path: Path) -> Dict[str, Any]:
    cfg = config.load()
    wiki_root = cfg.space_by_role("librarian-territory").local
    workshop_root = cfg.space_by_role("builder-territory").local

    blocks = _parse_proposal(path)
    selected = [b for b in blocks if b.get("promote", "false").lower() == "true"]
    written: List[str] = []

    for b in selected:
        target = wiki_root / b["target_slug"]
        if target.exists():
            continue
        source_path = workshop_root / b["source"]
        if not source_path.exists():
            continue

        now = datetime.now(timezone.utc).date().isoformat()
        eid = uuid.uuid5(uuid.NAMESPACE_DNS, "promote:" + b["target_slug"])
        title = b.get("title") or "(promoted)"

        body = source_path.read_text(encoding="utf-8")
        body = re.sub(r"^---[\s\S]*?---\s*", "", body, count=1)

        fm = (
            "---\n"
            "schema_version: 4\n"
            f"entry_id: {eid}\n"
            f"title: \"{title}\"\n"
            "type: synthesis\n"
            "scope: cross-domain\n"
            f"query: \"(promoted from {b['source']})\"\n"
            f"created: {now}\n"
            f"updated: {now}\n"
            "---\n\n"
        )
        cite = f"\n\n## Sources\n- [[workshop:{b['source']}|{b['source']}]]\n"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(fm + body.lstrip() + cite, encoding="utf-8")
        written.append(b["target_slug"])

    _append_log(path, selected, written)
    return {
        "applied": bool(written),
        "written": written,
        "selected": len(selected),
        "skipped": len(selected) - len(written),
    }


def _append_log(proposal_path: Path, selected: List[Dict], written: List[str]) -> None:
    PROMOTION_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat() + "Z"
    lines = [f"\n## [{ts}] proposal={proposal_path.name}"]
    for w in written:
        lines.append(f"- WROTE  {w}")
    for s in selected:
        if s["target_slug"] not in written:
            lines.append(f"- SKIP   {s['source']} → {s['target_slug']}")
    with PROMOTION_LOG.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
