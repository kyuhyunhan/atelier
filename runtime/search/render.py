"""Format search results for the CLI."""
from __future__ import annotations

from typing import List

from .fts import Hit


def render_hits(hits: List[Hit], explain: bool = False) -> str:
    if not hits:
        return "(no results)"
    lines = []
    for h in hits:
        title = h.title or "(untitled)"
        head = f"{h.space:8} {h.page_type:14} {h.slug}"
        lines.append(head)
        lines.append(f"   {title}")
        if h.snippet:
            lines.append(f"   {h.snippet}")
        if explain:
            lines.append(f"   rank={h.rank:.3f}")
        lines.append("")
    return "\n".join(lines)
