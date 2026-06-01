"""Extract [[wikilinks]] from markdown body text."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple

WIKILINK_RE = re.compile(r"\[\[([^\[\]\|]+)(?:\|([^\[\]]+))?\]\]")


@dataclass
class Link:
    to_target: str      # exact text as written
    to_space: str       # caller's space for bare links; scheme for scoped links
    to_slug: str        # space-relative path (no scheme prefix)
    link_type: str      # 'wikilink' | 'gorae' | 'workshop'


def extract_links(body: str, default_space: str = "") -> List[Link]:
    out: List[Link] = []
    for m in WIKILINK_RE.finditer(body):
        target = m.group(1).strip()
        if ":" in target:
            scheme, _, rest = target.partition(":")
            scheme = scheme.strip()
            slug = rest.strip()
            if scheme in ("gorae", "workshop"):
                out.append(Link(
                    to_target=target,
                    to_space=scheme,
                    to_slug=slug,
                    link_type=scheme,
                ))
                continue
        # bare wikilink — assume default space (backward compat)
        out.append(Link(
            to_target=target,
            to_space=default_space,
            to_slug=target,
            link_type="wikilink",
        ))
    return out
