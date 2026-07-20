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
    # RFC 0005 §4.1 — the raw artifact is a v7 Source node, not a schema-4
    # raw_source: it carries kind:source so the atomize nudge (which counts
    # kind:source) sees it, domain:knowledge + sensitivity:public (a talk is
    # public knowledge, not private), and source_type/source_url extensions.
    assert fm["schema_version"] == 7
    assert fm["kind"] == "source"
    assert fm["domain"] == "knowledge"
    assert fm["sensitivity"] == "public"
    assert fm["source_type"] == "youtube"
    assert fm["source_url"] == "https://youtube.com/watch?v=abc123"
    assert fm["content_hash"].startswith("sha256:")
    # RFC 0005 §3.2 — the Source lands DIRECTLY in raw/knowledge/, not a `_new/`
    # staging tree. The Web-Clipper/youtube default no longer assumes staging.
    assert "_new" not in p.parts
    assert p.parent.name == "knowledge"


def test_youtube_source_node_is_schema_valid(atelier_env: Dict) -> None:
    # The produced raw file must validate as a v7 `source` node (the gate that
    # would catch a missing required field like domain/attributed_to/content_hash).
    from runtime.lint import validate_v4 as _v
    out = _yt.youtube_ingest(
        url="https://youtube.com/watch?v=abc123",
        metadata_runner=lambda url: _FAKE_METADATA,
        text_fetcher=lambda url: _FAKE_VTT,
    )
    vault = atelier_env["gorae"]
    findings = _v.validate_paths([Path(out["path"])], vault_root=vault)
    v0 = [f for f in findings if f.rule_id == "V0"]
    assert v0 == [], f"source node schema-invalid: {[f.message for f in v0]}"


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


# ── VTT cleaning: auto-caption tags + rolling dedup (YouTube ingest recovery) ─


_ROLLING_VTT = (
    "WEBVTT\n"
    "Kind: captions\n"
    "Language: ko\n"
    "\n"
    "00:00:01.000 --> 00:00:03.000\n"
    "hello world\n"
    "\n"
    "00:00:03.000 --> 00:00:05.000 align:start position:0%\n"
    "hello world<00:00:04.000><c> this</c><00:00:04.500><c> is</c>\n"
    "\n"
    "00:00:05.000 --> 00:00:07.000\n"
    "this is a test\n"
    "\n"
)


def test_vtt_strips_inline_word_timing_tags() -> None:
    md = _yt._vtt_to_markdown(_ROLLING_VTT)
    assert "<c>" not in md and "<" not in md          # all inline tags gone


def test_vtt_collapses_rolling_duplicates() -> None:
    md = _yt._vtt_to_markdown(_ROLLING_VTT)
    flat = " ".join(l.split("] ", 1)[1] for l in md.splitlines() if "] " in l)
    # each word appears exactly once despite the rolling cue overlap
    assert flat == "hello world this is a test"
    assert md.count("hello world") == 1


_MANUAL_VTT = (
    "WEBVTT\n"
    "\n"
    "00:00:01.000 --> 00:00:04.000\n"
    "First sentence here.\n"
    "\n"
    "00:00:05.000 --> 00:00:08.000\n"
    "Completely different line.\n"
    "\n"
)


def test_vtt_keeps_manual_subtitles_intact() -> None:
    # Non-rolling human subtitles have no inline tags → emitted verbatim.
    md = _yt._vtt_to_markdown(_MANUAL_VTT)
    assert "[00:01] First sentence here." in md
    assert "[00:05] Completely different line." in md


_MANUAL_BOUNDARY_OVERLAP_VTT = (
    "WEBVTT\n"
    "\n"
    "00:00:01.000 --> 00:00:04.000\n"
    "the cat sat on the mat\n"
    "\n"
    "00:00:05.000 --> 00:00:08.000\n"
    "the mat was red\n"
    "\n"
)


def test_vtt_manual_boundary_repeat_is_not_dropped() -> None:
    # Regression: a manual sub whose next cue starts with words that coincide
    # with the previous cue's tail ("the mat") must NOT have them collapsed —
    # only ASR (timestamp-tagged) captions roll. Silent word loss on the human
    # track is the worst failure shape ("markdown is truth").
    md = _yt._vtt_to_markdown(_MANUAL_BOUNDARY_OVERLAP_VTT)
    assert "[00:01] the cat sat on the mat" in md
    assert "[00:05] the mat was red" in md            # "the mat" survives intact


_MANUAL_STYLED_OVERLAP_VTT = (
    "WEBVTT\n"
    "\n"
    "00:00:01.000 --> 00:00:04.000\n"
    "the cat sat on the <i>mat</i>\n"
    "\n"
    "00:00:05.000 --> 00:00:08.000\n"
    "<v Bob>the mat was red\n"
    "\n"
)


def test_vtt_manual_styling_tags_do_not_enable_collapse() -> None:
    # A manual sub carrying legal WebVTT styling/voice tags (<i>, <v>) must
    # still pass verbatim — the collapse gate is the ASR *timestamp* tag only,
    # not any angle-bracket tag. Styling tags are stripped from the text but
    # never trip the rolling dedup.
    md = _yt._vtt_to_markdown(_MANUAL_STYLED_OVERLAP_VTT)
    assert "<i>" not in md and "<v" not in md          # styling stripped
    assert "[00:01] the cat sat on the mat" in md
    assert "[00:05] the mat was red" in md             # "the mat" survives intact


def test_youtube_computes_word_count(atelier_env: Dict) -> None:
    out = _yt.youtube_ingest(
        url="https://youtube.com/watch?v=abc123",
        metadata_runner=lambda url: _FAKE_METADATA,
        text_fetcher=lambda url: _FAKE_VTT,
    )
    fm, _ = _read(Path(out["path"]))
    # "hello world" + "second cue" = 4 words; timestamps are not counted
    assert fm["word_count"] == 4


def test_fetch_metadata_adds_ignore_formats_and_cookies(
        monkeypatch: pytest.MonkeyPatch) -> None:
    captured: Dict[str, Any] = {}

    class _R:
        stdout = '{"id": "x"}'

    def fake_run(args, **kw):
        captured["args"] = list(args)
        return _R()

    monkeypatch.setattr(_yt.subprocess, "run", fake_run)
    monkeypatch.setattr(_yt, "_which", lambda name: "/usr/bin/yt-dlp")

    _yt._fetch_metadata("https://y/x", cookies_from_browser="chrome")
    assert "--ignore-no-formats-error" in captured["args"]
    assert captured["args"][captured["args"].index("--cookies-from-browser") + 1] == "chrome"

    _yt._fetch_metadata("https://y/x")                # unconfigured → no cookies
    assert "--ignore-no-formats-error" in captured["args"]
    assert "--cookies-from-browser" not in captured["args"]


def test_config_loads_youtube_cookies_from_browser(tmp_path: Path) -> None:
    from runtime.util import config as _config
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "vault:\n  local: ~/v\nyoutube:\n  cookies_from_browser: firefox\n",
        encoding="utf-8")
    cfg = _config.load(cfg_path)
    assert cfg.youtube.cookies_from_browser == "firefox"


def test_config_youtube_cookies_default_none(tmp_path: Path) -> None:
    from runtime.util import config as _config
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("vault:\n  local: ~/v\n", encoding="utf-8")
    cfg = _config.load(cfg_path)
    assert cfg.youtube.cookies_from_browser is None


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
