"""PR-16: YouTube ingestion — yt-dlp + optional OpenAI STT.

Mechanical port of the proto-engine's `gorae youtube` pipeline:

1. Fetch video metadata with `yt-dlp -J`.
2. Try to download subtitles (manual first, then auto). If present,
   parse the VTT into a timestamped markdown body.
3. Otherwise, when `openai` + an API key are available, download the
   audio and transcribe via gpt-4o-transcribe.
4. Write to `raw/knowledge/<slug>.md` with v4 frontmatter (RFC 0005 §3.2:
   a raw Source lands DIRECTLY in its domain dir — there is no `_new/` staging.
   "Awaiting atomization" is a derived state, not a place; see `_knowledge_root`).

Steps (1)/(2)/(4) are pure stdlib + the optional `[youtube]` extras
(yt-dlp). Step (3) is gated behind the `openai` package and the
`OPENAI_API_KEY` env var — when either is absent, the job reports
`status: needs-stt` and skips the audio path so the operator can finish
the ingest manually.
"""
from __future__ import annotations

import hashlib
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
_VTT_TAG_RX = re.compile(r"<[^>]+>")          # any inline tag (strip): <00:00:06>, <c>, <i>, <v …>
_VTT_TS_TAG_RX = re.compile(r"<\d{2}:\d{2}:\d{2}[.,]\d{3}>")  # ASR-only word-timing tag
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
    """Write base for ingested sources — `raw/knowledge` (structure.yaml's
    content_root), falling back to a legacy `provenance/knowledge` tree if
    that's what the vault still has. ONE resolver
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


def _fetch_metadata(url: str, *, runner: Optional[Any] = None,
                    cookies_from_browser: Optional[str] = None) -> Dict[str, Any]:
    """Call yt-dlp -J. Lets tests stub `runner`.

    Two flags earn their keep against current YouTube:
    - `--ignore-no-formats-error`: `-J` resolves media formats even when we only
      want metadata + subtitle tracks; recent YouTube player-API changes make
      that resolution fail ("Requested format is not available") and abort the
      whole dump. Ignoring it yields the metadata we actually need.
    - `--cookies-from-browser <b>`: clears the bot-detection wall (see
      YouTubeConfig). Only added when configured; absent ⇒ unauthenticated."""
    if runner is not None:
        return runner(url)
    if _which("yt-dlp") is None:
        raise FileNotFoundError(
            "yt-dlp not installed. `pip install -e '.[youtube]'` or install yt-dlp"
        )
    args = ["yt-dlp", "-J", "--no-warnings", "--ignore-no-formats-error"]
    if cookies_from_browser:
        args += ["--cookies-from-browser", cookies_from_browser]
    args.append(url)
    proc = subprocess.run(
        args, check=True, capture_output=True, text=True, timeout=120,
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
    """Render VTT cues as `[mm:ss] text` lines, cleaning auto-caption noise.

    YouTube ASR (auto) captions carry two artifacts this cleans:
    - **inline word-timing tags** (`<00:00:06><c>word</c>`) — always stripped;
    - **rolling duplication** — each cue re-emits the tail of the previous cue
      plus a few new words, doubling every line.

    The rolling collapse (token-level suffix/prefix overlap: drop the longest
    cue prefix already present as the accumulated stream's suffix) is applied
    **only when an ASR word-timing tag** (`<00:00:06.010>`, `_VTT_TS_TAG_RX`)
    was seen — that tag is unique to auto-captions. Manual subtitles are
    emitted **verbatim**: their cues are independent sentences, so the collapse
    would silently delete real words whenever a cue's leading token coincides
    with the previous cue's trailing token (a common boundary word like
    "the"/"이"). The gate deliberately does NOT trip on styling/voice tags
    (`<i>`, `<v Speaker>`) that manual subs legally carry — those are stripped
    for the text but never enable the collapse. "Markdown is truth" — never
    lossy on the exact human track."""
    cues: List[tuple] = []                 # (mm:ss, cleaned text)
    block: List[str] = []
    cue_start: Optional[str] = None
    had_tags = False                       # inline <...> seen ⇒ ASR/rolling

    def _flush() -> None:
        if block and cue_start is not None:
            text = _VTT_TAG_RX.sub("", " ".join(block)).strip()
            if text:
                cues.append((cue_start, text))

    for raw in vtt.splitlines():
        line = raw.rstrip("\r")
        if "-->" in line:
            _flush()
            block = []
            m = _VTT_TIME_RX.search(line)
            cue_start = f"{m.group(2)}:{m.group(3)}" if m else None
            continue
        if line.strip() == "":
            _flush()
            block = []
            cue_start = None
            continue
        if line.startswith("WEBVTT") or "Kind:" in line or "Language:" in line:
            continue
        if _VTT_TS_TAG_RX.search(line):    # ASR word-timing tag ⇒ rolling track
            had_tags = True                # (NOT styling/voice tags like <i>/<v>,
        block.append(line.strip())         #  which manual subs legally carry)
    _flush()

    if not had_tags:                       # manual subtitles → verbatim, lossless
        return "\n".join(f"[{ts}] {text}" for ts, text in cues) + "\n"

    out: List[str] = []                    # running deduped token stream
    anchors: List[tuple] = []              # (start_index_in_out, mm:ss)
    for ts, text in cues:
        toks = text.split()
        if not toks:
            continue
        maxk = min(len(out), len(toks))
        k = 0
        for cand in range(maxk, 0, -1):
            if out[-cand:] == toks[:cand]:
                k = cand
                break
        new = toks[k:]
        if not new:
            continue
        anchors.append((len(out), ts))
        out.extend(new)

    lines: List[str] = []
    for i, (idx, ts) in enumerate(anchors):
        end = anchors[i + 1][0] if i + 1 < len(anchors) else len(out)
        seg = " ".join(out[idx:end]).strip()
        if seg:
            lines.append(f"[{ts}] {seg}")
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
                   staging_subdir: str = "",
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
    cookies = None
    if metadata_runner is None:
        try:
            from ...util import config as _config
            cookies = _config.load().youtube.cookies_from_browser
        except Exception:                 # unconfigured / unloadable — go without
            cookies = None
    meta = _fetch_metadata(url, runner=metadata_runner,
                           cookies_from_browser=cookies)
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

    # RFC 0005 §3.2 — no `_new/` staging: a Source lands directly in its domain
    # dir. `staging_subdir` defaults to "" (the knowledge root itself); a caller
    # may still pass a subdir for an explicit shard, but the Web Clipper / youtube
    # default no longer assumes a staging tree.
    target_dir = (_knowledge_root(vault) / staging_subdir
                  if staging_subdir else _knowledge_root(vault))
    target_dir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(title, fallback=video_id)
    target = target_dir / f"{day}-{slug}-{video_id}.md"

    entry_id = _structure.entry_id("youtube", video_id=video_id)

    # RFC 0005 §4.1 — the raw artifact IS the L1 v7 Source node (`kind: source`,
    # `schema_version: 7`), exactly like a personal reading/diary source. This is
    # what makes it visible to the atomize nudge (`_source_ids` counts kind:source)
    # and a valid `derived_from` target for its Claims. A youtube source is
    # knowledge (public), not private — the old schema-4 raw_source wrote neither
    # `kind` nor `domain` and hard-coded `sensitivity: private`, so ingested talks
    # were invisible to v7 source accounting.
    channel = meta.get("channel") or meta.get("uploader")
    fm: Dict[str, Any] = {
        "schema_version": 7,
        "entry_id": entry_id,
        "kind": "source",
        "title": title,
        "domain": "knowledge",
        "sensitivity": "public",
        "attributed_to": channel or "youtube",   # PROV-O wasAttributedTo: the channel
        "content_hash": "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest(),
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
        "word_count": len(re.sub(r"\[\d+:\d+\]", "", body).split()),
        "source_type": "youtube",
        "source_url": url,
    }
    if channel:
        fm["channel"] = channel
    channel_url = meta.get("channel_url") or meta.get("uploader_url")
    if channel_url:
        fm["channel_url"] = channel_url
    duration = meta.get("duration")
    if isinstance(duration, (int, float)):
        fm["duration_sec"] = int(duration)
    language = meta.get("language") or lang
    if language:
        fm["language"] = language
    serialized = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    target.write_text(f"---\n{serialized}\n---\n# {title}\n\n{body}",
                       encoding="utf-8")

    return {
        "path": str(target),
        "video_id": video_id,
        "status": status,
        "entry_id": entry_id,
    }
