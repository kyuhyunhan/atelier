"""Parse a markdown file into (frontmatter dict, body str, chunks)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z",
    re.DOTALL,
)

# The synthetic chunk carrying frontmatter values into FTS (RFC 0002 P1a) is
# tagged with this heading_path. It is a real chunk for search, but link
# rebuild excludes it so frontmatter text is never parsed as body (reindex.py).
FRONTMATTER_HEADING = "frontmatter"

# Mechanical/plumbing frontmatter keys whose values carry no retrieval signal
# (ids, hashes, enums, timestamps). Denylisting these is noise control, not a
# schema decision (hard-rule #3): they are mechanical regardless of schema, so
# this set stays valid as the schema evolves. Anything ending in `_at` (any
# timestamp field) is also skipped, below.
_FM_PLUMBING_KEYS = frozenset({
    "entry_id", "schema_version", "status", "ac_status", "agent_kind",
    "observation_kind", "content_hash", "mtime", "confidence", "source_count",
    "created", "updated", "sensitivity", "first_mention", "id", "hash",
})


@dataclass
class Chunk:
    position: int
    heading_path: Optional[str]
    text: str


@dataclass
class ParsedPage:
    frontmatter: Dict[str, Any]
    body: str
    chunks: List[Chunk]


def split_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Return (frontmatter, body). frontmatter is {} if absent."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw_fm, body = m.group(1), m.group(2)
    try:
        fm = yaml.safe_load(raw_fm) or {}
        if not isinstance(fm, dict):
            fm = {}
    except yaml.YAMLError:
        fm = {}
    return fm, body


def chunk_body(body: str) -> List[Chunk]:
    """Split body into paragraph-level chunks, tracking heading path."""
    chunks: List[Chunk] = []
    heading_stack: List[str] = []  # stack of (level, text) flattened by replacement
    levels: List[int] = []
    pos = 0
    buf: List[str] = []

    def flush() -> None:
        nonlocal pos, buf
        text = "\n".join(buf).strip()
        buf = []
        if not text:
            return
        path = " > ".join(heading_stack) if heading_stack else None
        chunks.append(Chunk(position=pos, heading_path=path, text=text))
        pos += 1

    for line in body.splitlines():
        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if m:
            flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            while levels and levels[-1] >= level:
                levels.pop()
                heading_stack.pop()
            levels.append(level)
            heading_stack.append(title)
            continue
        if not line.strip():
            flush()
            continue
        buf.append(line)
    flush()
    return chunks


def _searchable_fm_values(fm: Dict[str, Any]) -> List[str]:
    """Ordered string values worth indexing from frontmatter: scalars and
    list-of-string members, minus the plumbing denylist and any `*_at` field.
    Non-string scalars (ints, bools) and nested structures are skipped — they
    are not free-text retrieval signal."""
    out: List[str] = []
    for key, val in fm.items():
        if not isinstance(key, str):
            continue
        k = key.lower()
        if k in _FM_PLUMBING_KEYS or k.endswith("_at"):
            continue
        if isinstance(val, str):
            v = val.strip()
            if v:
                out.append(v)
        elif isinstance(val, list):
            for e in val:
                if isinstance(e, str) and e.strip():
                    out.append(e.strip())
    return out


def frontmatter_chunk(fm: Dict[str, Any], position: int = 0) -> Optional[Chunk]:
    """A synthetic chunk carrying frontmatter values into FTS (RFC 0002 P1a), so
    a page whose concept lives only in a tag (`touches`, `target_topic`, …) is
    retrievable. Returns None when frontmatter has no searchable text."""
    values = _searchable_fm_values(fm)
    if not values:
        return None
    return Chunk(position=position, heading_path=FRONTMATTER_HEADING,
                 text="\n".join(values))


def parse_file(path: Path) -> ParsedPage:
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body = split_frontmatter(text)
    chunks = chunk_body(body)
    # Append the frontmatter chunk last so body chunk positions are unchanged.
    fmc = frontmatter_chunk(fm, position=len(chunks))
    if fmc is not None:
        chunks.append(fmc)
    return ParsedPage(frontmatter=fm, body=body, chunks=chunks)


# Structured (.yaml/.yml/.json) page handling — RFC 0002 P1b. Suffixes come from
# fs (single source of truth) so the walk and this parse dispatch never diverge.
def is_data_path(path: Path) -> bool:
    from ..util import fs as _fs
    return path.suffix.lower() in _fs.DATA_SUFFIXES


def _flatten(value: Any, prefix: str = "") -> List[str]:
    """Flatten a nested yaml/json structure into `key.path: scalar` lines so FTS
    can match both the keys and the leaf values. Lists index their members by
    index path. Scalars render as `prefix: value`."""
    lines: List[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            lines.extend(_flatten(v, key))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            lines.extend(_flatten(v, f"{prefix}[{i}]" if prefix else f"[{i}]"))
    else:
        rendered = "" if value is None else str(value)
        lines.append(f"{prefix}: {rendered}".strip())
    return lines


def parse_data_file(path: Path) -> ParsedPage:
    """Parse a structured file into a `data` page. yaml/json is loaded and
    flattened to searchable text; on a parse error we fall back to indexing the
    raw text so a malformed file is still discoverable (and never crashes the
    indexer). Frontmatter is the top-level mapping when present, so `title` and
    friends still populate the generated columns."""
    text = path.read_text(encoding="utf-8", errors="replace")
    fm: Dict[str, Any] = {}
    lines: List[str]
    try:
        if path.suffix.lower() == ".json":
            import json as _json
            data = _json.loads(text)
        else:
            data = yaml.safe_load(text)
        lines = _flatten(data) if data is not None else []
        if isinstance(data, dict):
            fm = data
    except (yaml.YAMLError, ValueError):
        lines = []
    body = "\n".join(lines) if lines else text.strip()
    chunks = [Chunk(position=0, heading_path=None, text=body)] if body else []
    return ParsedPage(frontmatter=fm if isinstance(fm, dict) else {},
                      body=body, chunks=chunks)
