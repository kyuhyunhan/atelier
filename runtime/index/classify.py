"""Map a (space, slug, frontmatter) tuple to a page_type."""
from __future__ import annotations

import fnmatch
from typing import Any, Dict


# Order matters: more specific patterns must come first.
GORAE_RULES = [
    ("wiki/index.md",                 "wiki_index"),
    ("wiki/log.md",                   "wiki_log"),
    ("wiki/digests/*.md",             "digest"),
    ("wiki/sources/*.md",             "source"),
    ("wiki/entities/*.md",            "entity"),
    ("wiki/themes/*.md",              "theme"),
    ("wiki/synthesis/*.md",           "synthesis"),
    # learnings/ — the developer-self lesson-learned domain (PR-18+).
    ("learnings/log.md",              "learnings_log"),
    ("learnings/criteria.yaml",       "learnings_criteria"),
    ("learnings/candidates/**/*.md",  "learning_candidate"),
    ("learnings/accepted/**/*.md",    "learning_accepted"),
    ("learnings/archived/**/*.md",    "learning_archived"),
    # workshop/ subtree (post-absorption).
    ("workshop/products/*/README.md", "product_readme"),
    ("workshop/products/**/*.md",     "product_page"),
    ("workshop/notes/**/*.md",        "note"),
    ("workshop/log.md",               "build_log"),
    # raw/ comes last so the above more specific patterns win.
    ("raw/**/*.md",                   "raw_source"),
]

WORKSHOP_RULES = [
    ("products/*/README.md", "product_readme"),
    ("products/**/*.md",     "product_page"),
    ("notes/**/*.md",        "note"),
    ("logs/**/*.md",         "build_log"),
]


def classify(space: str, slug: str, fm: Dict[str, Any]) -> str:
    rules = GORAE_RULES if space == "gorae" else WORKSHOP_RULES
    for pattern, ptype in rules:
        if fnmatch.fnmatchcase(slug, pattern) or _glob_match(pattern, slug):
            return ptype
    return "unknown"


def _glob_match(pattern: str, slug: str) -> bool:
    """Support ** explicitly (fnmatch alone doesn't handle ** across /)."""
    if "**" not in pattern:
        return False
    # Translate ** to a regex .*
    import re as _re
    rx = _re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
    return bool(_re.fullmatch(rx, slug))
