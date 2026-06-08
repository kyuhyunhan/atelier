"""Map a (space, slug, frontmatter) tuple to a page_type.

Schema-driven (hard-rule #3): the (path_pattern → page_type) rules are sourced
from schema/data/*.overlay.yaml via `validate_v4.page_type_rules()`, not
hardcoded here. Classification is **space-independent** — the single-vault
model stores every page under one synthesized space (`vault-builder`), so the
slug path alone determines the type. Legacy two-space callers still work
because the rule set matches both the space-relative builder paths
(`products/…`) and their single-vault equivalents (`workshop/products/…`).
"""
from __future__ import annotations

import fnmatch
from functools import lru_cache
from typing import Any, Dict, List, Tuple


def _normalize_pattern(pattern: str) -> str:
    """The gorae overlay declares digests with the human-readable token
    ``YYYY-MM``; treat it as a glob for classification. (The validator keeps
    its own stricter ``filename_pattern`` regex, so precision is not lost.)"""
    return pattern.replace("YYYY-MM", "*")


@lru_cache(maxsize=1)
def _rules() -> Tuple[Tuple[str, str], ...]:
    """Compiled (path_pattern, page_type) rules, single-vault aware.

    For every space-relative builder pattern (``products/…``, ``notes/…``,
    ``logs/…``) we emit BOTH the original (legacy two-space) and a
    ``workshop/``-prefixed variant (single vault), adjacent so specificity
    ordering is preserved.
    """
    from ..lint.validate_v4 import page_type_rules

    out: List[Tuple[str, str]] = []
    for pattern, ptype in page_type_rules():
        pat = _normalize_pattern(pattern)
        out.append((pat, ptype))
        if pat.split("/", 1)[0] in ("products", "notes", "logs"):
            out.append(("workshop/" + pat, ptype))
    return tuple(out)


def classify(space: str, slug: str, fm: Dict[str, Any]) -> str:
    # `space` is accepted for signature stability (callers still pass it) but
    # is intentionally unused — classification keys off the slug path alone.
    for pattern, ptype in _rules():
        if fnmatch.fnmatchcase(slug, pattern) or _glob_match(pattern, slug):
            return ptype
    return "unknown"


def _glob_match(pattern: str, slug: str) -> bool:
    """Support `**` explicitly (kept in parity with validate_v4._glob_match)."""
    if "**" not in pattern:
        return False
    import re as _re

    rx = _re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
    return bool(_re.fullmatch(rx, slug))
