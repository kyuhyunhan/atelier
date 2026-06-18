"""Capture endpoint — function-shaped for mobile compatibility.

Reserved for v0.3 mobile activation. v0.1 exposes the function so the CLI
can exercise it locally:

    atelier capture --text "..." --source web-clipper

writes to gorae/raw/personal/inbox/{ts}-{slug}.md with inbox_status=pending.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..structure import resolver as _structure
from ..util import config
from . import claims


def _slugify(text: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^\wÀ-￿\s-]", "", text.strip().lower())
    s = re.sub(r"\s+", "-", s)
    return s[:maxlen] or "untitled"


def capture(
    text: str,
    source: str = "manual",
    title: Optional[str] = None,
    ctx: Optional[claims.CallContext] = None,
) -> Path:
    """Land a capture into gorae/provenance/personal/inbox/. Returns the new file path."""
    ctx = ctx or claims.local_cli_context()
    claims.require(ctx, claims.Claim.MOBILE_CLAIM)

    cfg = config.load()
    librarian_root = cfg.space_by_role("librarian-territory").local
    # Canonical intake dir (provenance/personal) sourced from the resolver; fall
    # back to the legacy un-renamed tree (raw/personal) only when it alone exists
    # (mirrors youtube._knowledge_root — never resurrect the dead raw/ tree).
    inbox = librarian_root / _structure.inbox_dir()
    if not inbox.parent.exists():
        legacy_personal = (librarian_root / _structure.legacy_content_root()
                           / _structure.intake_subpath("personal"))
        if legacy_personal.exists():
            inbox = legacy_personal / _structure.inbox_subpath()
    inbox.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%dT%H%M%S")
    slug = _slugify(title or text.split("\n", 1)[0])
    fname = f"{ts}-{slug}.md"
    path = inbox / fname

    eid = _structure.entry_id("capture", iso=now.isoformat(), slug=slug)
    fm = (
        "---\n"
        "schema_version: 4\n"
        f"entry_id: {eid}\n"
        f"title: {title or 'null'}\n"
        "summary: null\n"
        "sensitivity: private\n"
        "created_at:\n"
        f"  - value: '{now.isoformat()}'\n"
        "    precision: second\n"
        "    timezone: UTC\n"
        "collected_at:\n"
        f"  - value: '{now.isoformat()}'\n"
        f"    source: {source}\n"
        "edited_at: []\n"
        "embedded_assets: []\n"
        "word_count: 0\n"
        f"source: {source}\n"
        "inbox_status: pending\n"
        "---\n\n"
    )
    path.write_text(fm + text, encoding="utf-8")
    return path
