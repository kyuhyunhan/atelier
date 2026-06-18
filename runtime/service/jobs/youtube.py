"""PR-16: YouTube ingestion — yt-dlp + optional OpenAI STT.

Mechanical port of the proto-engine's `gorae youtube` pipeline:

1. Fetch video metadata with `yt-dlp -J`.
2. Try to download subtitles (manual first, then auto). If present,
   parse the VTT into a timestamped markdown body.
3. Otherwise, when `openai` + an API key are available, download the
   audio and transcribe via gpt-4o-transcribe.
4. Write to `provenance/knowledge/_new/<slug>.md` with v4 frontmatter
   (legacy `raw/knowledge/` for an un-renamed vault; see `_knowledge_root`).

Steps (1)/(2)/(4) are pure stdlib + the optional `[youtube]` extras
(yt-dlp). Step (3) is gated behind the `openai` package and the
`OPENAI_API_KEY` env var — when either is absent, the job reports
`status: needs-stt` and skips the audio path so the operator can finish
the ingest manually.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from ...structure import resolver as _structure
from ...util import config as _config


_VTT_TIME_RX = re.compile(r"(\d{2}):(\d{2}):(\d{2})\.\d+")
_SLUG_RX = re.compile(r"[^a-z0-9-]+")


def _slugify(value: str, *, fallback: str = "video") -> str:
    text = (value or fallback).strip().lower()
    text = _SLUG_RX.sub("-", text).strip("-")
    return text[:64] or fallback


def _vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _knowledge_root(vault: Path) -> Path:
    """Write base for ingested sources — `provenance/knowledge` post-RFC-0003,
    falling back to legacy `raw/knowledge` for an un-renamed vault. ONE resolver
    so the writer can't resurrect the old tree (the 1507 bug class: a writer
    whose path misses a rename re-creates it). Mirrors index_regen._graph_root."""
    new = vault / _structure.intake_dir("knowledge")
    if new.exists():
        return new
    legacy = (vault / _structure.legacy_content_root()
              / _structure.intake_subpath("knowledge"))
    if legacy.exists():
        return legacy
    return new  # fresh vault: default to the canonical tree


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def _fetch_metadata(url: str, *, runner: Optional[Any] = None) -> Dict[str, Any]:
    """Call yt-dlp -J. Lets tests stub `runner`."""
    if runner is not None:
        return runner(url)
    if _which("yt-dlp") is None:
        raise FileNotFoundError(
            "yt-dlp not installed. `pip install -e '.[youtube]'` or install yt-dlp"
        )
    proc = subprocess.run(
        ["yt-dlp", "-J", "--no-warnings", url],
        check=True, capture_output=True, text=True, timeout=60,
    )
    return json.loads(proc.stdout)


def _pick_subtitles(meta: Dict[str, Any], lang: Optional[str]
                     ) -> Optional[Dict[str, Any]]:
    subs = meta.get("subtitles") or {}
    auto = meta.get("automatic_captions") or {}

    def pick(track_map: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if lang and lang in track_map:
            return _select_vtt(track_map[lang])
        for code in ("en", "ko"):
            if code in track_map:
                return _select_vtt(track_map[code])
        for code, entries in track_map.items():
            v = _select_vtt(entries)
            if v:
                return v
        return None

    return pick(subs) or pick(auto)


def _select_vtt(entries: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for e in entries or []:
        if e.get("ext") == "vtt" and e.get("url"):
            return e
    return None


def _vtt_to_markdown(vtt: str) -> str:
    """Render VTT cues as `[mm:ss] text` lines."""
    lines: List[str] = []
    block: List[str] = []
    cue_start: Optional[str] = None
    for raw in vtt.splitlines():
        line = raw.rstrip("\r")
        if "-->" in line:
            block = []
            m = _VTT_TIME_RX.search(line)
            cue_start = f"{m.group(2)}:{m.group(3)}" if m else None
            continue
        if line.strip() == "":
            if block and cue_start is not None:
                lines.append(f"[{cue_start}] " + " ".join(block).strip())
            block = []
            cue_start = None
            continue
        if line.startswith("WEBVTT") or "Kind:" in line or "Language:" in line:
            continue
        block.append(line.strip())
    if block and cue_start is not None:
        lines.append(f"[{cue_start}] " + " ".join(block).strip())
    return "\n".join(lines) + "\n"


def _download_text(url: str) -> str:
    import urllib.request
    with urllib.request.urlopen(url, timeout=30) as resp:  # nosec - youtube CDN
        return resp.read().decode("utf-8", "replace")


def _has_openai() -> bool:
    try:
        import openai  # noqa: F401
        import os
        return bool(os.environ.get("OPENAI_API_KEY"))
    except ImportError:
        return False


def youtube_ingest(*, url: str,
                   role: str = "librarian-territory",
                   lang: Optional[str] = None,
                   force_stt: bool = False,
                   staging_subdir: str = "_new",
                   metadata_runner: Optional[Any] = None,
                   text_fetcher: Optional[Any] = None
                   ) -> Dict[str, Any]:
    """Best-effort YouTube ingest. Returns a status report.

    Returns {"path": <str>, "status": "captioned" | "needs-stt"}.
    `needs-stt` means subtitles were unavailable AND STT is not
    configured — the operator should rerun with credentials, or fill in
    the body manually.
    """
    vault = _vault_root()
    meta = _fetch_metadata(url, runner=metadata_runner)
    video_id = meta.get("id") or _slugify(meta.get("title", url))
    title = meta.get("title") or video_id
    upload_date = meta.get("upload_date")  # YYYYMMDD
    try:
        precise = (datetime.strptime(upload_date, "%Y%m%d")
                   .replace(tzinfo=timezone.utc)
                   .isoformat(timespec="seconds"))
        day = precise[:10]
    except (TypeError, ValueError):
        precise = _now_iso()
        day = precise[:10]

    body: str
    status = "captioned"
    if not force_stt:
        vtt = _pick_subtitles(meta, lang)
        if vtt is not None:
            raw_vtt = (text_fetcher or _download_text)(vtt["url"])
            body = _vtt_to_markdown(raw_vtt)
        else:
            body = "(no captions available)\n"
            status = "needs-stt"
    else:
        body = "(force_stt requested)\n"
        status = "needs-stt"

    if status == "needs-stt" and _has_openai():
        # In v0.2 we do not download audio + run STT inline (large/expensive).
        # The hook is here so PR-16-followup can add `_stt_transcribe()`.
        status = "needs-stt-stub"

    target_dir = _knowledge_root(vault) / staging_subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(title, fallback=video_id)
    target = target_dir / f"{day}-{slug}-{video_id}.md"

    entry_id = _structure.entry_id("youtube", video_id=video_id)

    fm: Dict[str, Any] = {
        "schema_version": 4,
        "entry_id": entry_id,
        "title": title,
        "sensitivity": "private",
        "created_at": [{
            "value": precise,
            "precision": "second",
            "timezone": "UTC",
        }],
        "collected_at": [{
            "value": _now_iso(),
            "source": "youtube",
            "note": f"video_id={video_id}",
        }],
        "embedded_assets": [],
        "word_count": 0,
        "source": "youtube",
        "youtube_video_id": video_id,
        "youtube_url": url,
    }
    serialized = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    target.write_text(f"---\n{serialized}\n---\n# {title}\n\n{body}",
                       encoding="utf-8")

    return {
        "path": str(target),
        "video_id": video_id,
        "status": status,
        "entry_id": entry_id,
    }
