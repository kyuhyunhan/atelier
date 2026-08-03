"""PR-12: clip_image — download a remote image into the vault.

Mechanical port of the proto-engine's `clip-images` workflow:
- fetch the URL (stdlib urllib; no third-party deps for v0.2)
- choose a stable filename from the URL hash + extension
- save under `<vault>/<assets.dir>/<YYYY>/<MM>/<name>.<ext>` (default `assets/`)
- record asset metadata for the matching markdown frontmatter

R2 upload is delegated to runtime.sync.adapters.r2.push() once it's
implemented (currently a no-op stub) — this module reports the local
path and a placeholder CDN URL so the writeback path can be wired
later without changing the contract.
"""
from __future__ import annotations

import hashlib
import mimetypes
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from ...util import config as _config


def _vault_root() -> Path:
    return _config.vault_root()   # the ONE accessor (RFC 0001 §6 / #98)


def _ext_for(url: str, content_type: str | None) -> str:
    parsed = urllib.parse.urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".heic"):
        return suffix
    if content_type:
        guess = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guess:
            return guess
    return ".bin"


def _slug_from_url(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def clip_image(*, url: str,
               role: str = "librarian-territory",
               subdir: str | None = None,
               fetch: Any | None = None) -> dict[str, Any]:
    """Download `url` into the vault and return local + CDN paths.

    `fetch` lets tests inject a stub; default uses urllib.
    """
    vault = _vault_root()
    if subdir is None:
        subdir = _config.load().asset_dir()
    today = datetime.utcnow().date()
    target_dir = vault / subdir / f"{today.year:04d}" / f"{today.month:02d}"
    target_dir.mkdir(parents=True, exist_ok=True)

    content_bytes: bytes
    content_type: str | None
    if fetch is not None:
        content_bytes, content_type = fetch(url)
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "atelier-clip/0.2"})
        with urllib.request.urlopen(req, timeout=20) as resp:  # nosec
            content_bytes = resp.read()
            content_type = resp.headers.get("Content-Type")

    slug = _slug_from_url(url)
    ext = _ext_for(url, content_type)
    local = target_dir / f"{slug}{ext}"
    local.write_bytes(content_bytes)

    cdn = _resolve_cdn_url(local, vault)

    return {
        "url": url,
        "local": str(local),
        "rel": str(local.relative_to(vault)),
        "cdn": cdn,
        "size": len(content_bytes),
        "content_type": content_type,
    }


def _resolve_cdn_url(local: Path, vault: Path) -> str | None:
    """If the vault's assets.cdn is configured, return a CDN URL pointing
    at the relative path. The actual upload to R2 lives in the r2
    adapter (currently a stub) — this just computes what the URL will
    look like once that wires up."""
    cfg = _config.load()
    if cfg.vault is not None:
        cdn_base = (cfg.vault.assets or {}).get("cdn")
    else:
        try:
            cdn_base = (cfg.space_by_role("librarian-territory").assets or {}).get("cdn")
        except KeyError:
            cdn_base = None
    if not cdn_base:
        return None
    rel = local.relative_to(vault).as_posix()
    return f"{cdn_base.rstrip('/')}/{rel}"
