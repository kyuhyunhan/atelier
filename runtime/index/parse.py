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


def parse_file(path: Path) -> ParsedPage:
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body = split_frontmatter(text)
    chunks = chunk_body(body)
    return ParsedPage(frontmatter=fm, body=body, chunks=chunks)
