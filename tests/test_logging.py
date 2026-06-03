"""Logging core — stdlib-based, single append-only file, structured format.

Every line: ISO-8601(local+offset) [LEVEL] [category] event k=v
Category is derived from the dotted message prefix (logger name), so existing
call sites (log.info("sync.commit", ...)) are unchanged.
"""
from __future__ import annotations

import logging as stdlib_logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from runtime.util import logging as alog

TS = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}"
_LOGGERS = ("atelier", "atelier.sync", "atelier.cli", "uvicorn",
            "uvicorn.error", "mcp")


def _clear_handlers() -> None:
    for name in _LOGGERS:
        lg = stdlib_logging.getLogger(name)
        for h in list(lg.handlers):
            lg.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
        lg.setLevel(stdlib_logging.NOTSET)
        lg.propagate = True


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch) -> Path:
    """Isolate logging to a tmp file and reset module + stdlib state per test."""
    logf = tmp_path / "atelier.log"
    monkeypatch.setenv("ATELIER_LOG_FILE", str(logf))
    monkeypatch.delenv("ATELIER_LOG_LEVEL", raising=False)
    _clear_handlers()
    alog._configured = False
    yield logf
    _clear_handlers()
    alog._configured = False


# ── format & categories ──────────────────────────────────────────────────────


def test_format_has_time_level_category_and_kv(_reset: Path) -> None:
    alog.info("vault-autosync.ready", vault="/x/gorae", interval=30, push=True)
    line = _reset.read_text().strip()
    assert re.match(
        rf"^{TS} \[INFO\] \[vault-autosync\] ready "
        rf"vault=/x/gorae interval=30 push=True$", line), line


def test_level_tokens_incl_warn_mapping(_reset: Path) -> None:
    alog.set_level("debug")
    alog.debug("c.d"); alog.info("c.i"); alog.warn("c.w"); alog.error("c.e")
    t = _reset.read_text()
    assert "[DEBUG] [c] d" in t
    assert "[INFO] [c] i" in t
    assert "[WARN] [c] w" in t          # WARNING → WARN
    assert "[ERROR] [c] e" in t


def test_category_derivation(_reset: Path) -> None:
    alog.info("sync.commit", x=1)         # dotted
    alog.info("mcp-stdio.crash")          # hyphen in category, first dot splits
    alog.warn("interrupted")              # no dot → cli
    t = _reset.read_text()
    assert "[sync] commit x=1" in t
    assert "[mcp-stdio] crash" in t
    assert "[cli] interrupted" in t


# ── append / idempotency / levels ────────────────────────────────────────────


def test_append_across_reconfigure_single_handler(_reset: Path) -> None:
    alog.info("a.one")
    alog.configure()                      # reconfigure must not truncate or dup
    alog.info("a.two")
    t = _reset.read_text()
    assert "one" in t and "two" in t
    files = [h for h in stdlib_logging.getLogger("atelier").handlers
             if getattr(h, alog._HANDLER_TAG, None) == "file"]
    assert len(files) == 1


def test_level_filtering_and_set_level(_reset: Path) -> None:
    alog.configure(level="info")
    alog.debug("x.hidden")
    assert "hidden" not in _reset.read_text()
    alog.set_level("debug")
    alog.debug("x.shown")
    assert "shown" in _reset.read_text()


def test_env_level_override(_reset: Path, monkeypatch) -> None:
    monkeypatch.setenv("ATELIER_LOG_LEVEL", "debug")
    _clear_handlers(); alog._configured = False
    alog.debug("e.shown")
    assert "shown" in _reset.read_text()


# ── stdout safety / console gating ───────────────────────────────────────────


def test_stdio_mode_never_writes_stdout(_reset: Path, capsys) -> None:
    _clear_handlers(); alog._configured = False
    alog.configure(stdio=True, console=True)   # console requested but stdio wins
    alog.error("x.boom")
    cap = capsys.readouterr()
    assert cap.out == ""
    assert "boom" in _reset.read_text()


def test_console_handler_off_when_not_a_tty(_reset: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False, raising=False)
    _clear_handlers(); alog._configured = False
    alog.configure(console=True, stdio=False)
    console = [h for h in stdlib_logging.getLogger("atelier").handlers
               if getattr(h, alog._HANDLER_TAG, None) == "console"]
    assert console == []


# ── shell helper produces the identical format ───────────────────────────────


def test_log_sh_matches_python_format(_reset: Path, tmp_path: Path) -> None:
    if not shutil.which("bash"):
        pytest.skip("bash unavailable")
    logf = tmp_path / "sh.log"
    helper = Path(__file__).resolve().parents[1] / "scripts" / "hooks" / "_log.sh"
    r = subprocess.run(
        ["bash", "-c",
         f'. "{helper}"; ATELIER_LOG_FILE="{logf}" '
         f'atelier_log info recall pushed session=abc items=fresh'],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    line = logf.read_text().strip()
    assert re.match(
        rf"^{TS} \[INFO\] \[recall\] pushed session=abc items=fresh$", line), line
