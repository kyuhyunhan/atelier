"""Cloudflare R2 asset sync — stub.

Full implementation deferred to v0.2 (needs boto3 + S3-compat config). Phase 5
exposes the function shape so callers can integrate.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class AssetStatus:
    space: str
    local_only: List[str]
    remote_only: List[str]
    in_sync: int


def status(space_name: str, local: Path, bucket: str | None = None) -> AssetStatus:
    """Compare local _attachments / gorae-resources / etc. to R2 bucket listing.

    v0.1: returns empty status — no R2 call made.
    """
    return AssetStatus(space=space_name, local_only=[], remote_only=[], in_sync=0)


def push(space_name: str, local: Path, bucket: str | None = None) -> int:
    """Upload local-only assets to R2. v0.1: no-op stub. Returns 0."""
    return 0


def pull(space_name: str, local: Path, bucket: str | None = None) -> int:
    """Download remote-only assets to local cache. v0.1: no-op stub."""
    return 0
