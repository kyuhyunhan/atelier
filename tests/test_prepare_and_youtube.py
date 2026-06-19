"""PR-11/PR-16 capability port tests."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

from runtime.service.jobs import prepare as _prep
from runtime.service.jobs import youtube as _yt


def _write(path: Path, fm: Dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    path.write_text(f"---\n{serialized}\n---\n{body}", encoding="utf-8")


# ── prepare_commit ─────────────────────────────────────────────────────────


def test_prepare_resolves_pending_entry_id(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    p = vault / "raw" / "personal" / "diary" / "n.md"
    _write(p, {"schema_version": 4, "entry_id": "PENDING"},
           "hello world body of three words")
    out = _prep.prepare_commit(paths=[str(p)])
    assert out["modified"]
    fm, _ = _read(p)
    assert fm["entry_id"] != "PENDING"


def test_prepare_recalculates_word_count(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    p = vault / "raw" / "personal" / "diary" / "n.md"
    _write(p, {"schema_version": 4, "entry_id": "abc", "word_count": 99},
           "alpha beta gamma delta")
    out = _prep.prepare_commit(paths=[str(p)])
    fm, _ = _read(p)
    assert fm["word_count"] == 4
    assert out["modified"]


def test_prepare_detects_embedded_assets(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    body = "see ![alt](images/cover.png) and ![](images/sub.png)\n"
    p = vault / "raw" / "k" / "with-images.md"
    _write(p, {"schema_version": 4, "entry_id": "abc"}, body)
    _prep.prepare_commit(paths=[str(p)])
    fm, _ = _read(p)
    assert fm["embedded_assets"] == ["images/cover.png", "images/sub.png"]


def test_prepare_dry_run_no_writes(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    p = vault / "raw" / "x" / "stale.md"
    _write(p, {"schema_version": 4, "entry_id": "abc"}, "hello")
    before = p.read_text()
    out = _prep.prepare_commit(paths=[str(p)], dry_run=True)
    assert out["modified"]
    assert p.read_text() == before


def _read(p: Path):
    from runtime.index.parse import split_frontmatter
    return split_frontmatter(p.read_text(encoding="utf-8"))


# ── youtube ────────────────────────────────────────────────────────────────


_FAKE_METADATA = {
    "id": "abc123",
    "title": "How yt-dlp works",
    "upload_date": "20260520",
    "subtitles": {
        "en": [
            {"ext": "vtt", "url": "https://example/captions/en.vtt"},
        ]
    },
}

_FAKE_VTT = (
    "WEBVTT\n"
    "\n"
    "00:00:01.000 --> 00:00:04.000\n"
    "hello world\n"
    "\n"
    "00:00:05.000 --> 00:00:08.000\n"
    "second cue\n"
    "\n"
)


def test_youtube_writes_md_with_captions(atelier_env: Dict) -> None:
    out = _yt.youtube_ingest(
        url="https://youtube.com/watch?v=abc123",
        metadata_runner=lambda url: _FAKE_METADATA,
        text_fetcher=lambda url: _FAKE_VTT,
    )
    p = Path(out["path"])
    assert p.exists()
    text = p.read_text()
    assert out["status"] == "captioned"
    assert "[00:01]" in text and "hello world" in text
    fm, _ = _read(p)
    assert fm["youtube_video_id"] == "abc123"
    assert fm["source"] == "youtube"
    # RFC 0005 §3.2 — the Source lands DIRECTLY in raw/knowledge/, not a `_new/`
    # staging tree. The Web-Clipper/youtube default no longer assumes staging.
    assert "_new" not in p.parts
    assert p.parent.name == "knowledge"


def test_youtube_marks_needs_stt_when_no_captions(atelier_env: Dict,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    no_subs = dict(_FAKE_METADATA)
    no_subs["subtitles"] = {}
    no_subs["automatic_captions"] = {}
    # Make sure openai+key gate fails.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = _yt.youtube_ingest(
        url="https://youtube.com/watch?v=abc123",
        metadata_runner=lambda url: no_subs,
    )
    assert out["status"] == "needs-stt"
    text = Path(out["path"]).read_text()
    assert "(no captions available)" in text


# ── MCP dispatch ───────────────────────────────────────────────────────────


def test_mcp_dispatch_prepare(atelier_env: Dict) -> None:
    from runtime.service import tools as _tools
    vault = atelier_env["gorae"]
    p = vault / "raw" / "x" / "p.md"
    _write(p, {"schema_version": 4, "entry_id": "PENDING"}, "body")
    async def go() -> Dict:
        return await _tools.invoke("atelier_prepare_commit",
                                   paths=[str(p)], dry_run=False)
    out = asyncio.run(go())
    assert out["modified"]


def test_mcp_youtube_tool_registered() -> None:
    from runtime.service import tools as _tools
    names = {t.name for t in _tools.iter_tools()}
    assert "atelier_youtube" in names
    assert "atelier_prepare_commit" in names
