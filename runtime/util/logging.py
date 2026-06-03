"""Structured logging on top of the stdlib `logging` module.

One consolidated, append-only sink: `~/.atelier/logs/atelier.log` (override with
`ATELIER_LOG_FILE` or `logging.file` in config). Every line carries time and
category:

    2026-06-03T16:04:25+09:00 [INFO] [vault-autosync] ready vault=/…/gorae interval=30

The public façade (`debug/info/warn/error(msg, **kv)`, `set_level`) is unchanged
so existing call sites need no edits. The first dotted segment of `msg` is the
*category* (mapped to the logger name `atelier.<category>`); the rest is the
event. Messages with no dot fall under category `cli`.

Design notes:
- `configure()` is idempotent — it never adds a second file handler.
- `logging` must never raise from a log call, so config is imported lazily and
  defensively (it is imported across the codebase; a fresh machine may have no
  config yet).
- No handler ever targets stdout (the MCP stdio transport owns stdout for
  JSON-RPC frames). The optional console handler is stderr-only and TTY-gated.
"""
from __future__ import annotations

import logging as _logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_ROOT = "atelier"
_HANDLER_TAG = "_atelier_handler"          # marks handlers we own (idempotency)
_BRIDGE_LOGGERS = ("uvicorn", "uvicorn.error", "mcp")

_LEVELS = {"debug": _logging.DEBUG, "info": _logging.INFO,
           "warn": _logging.WARNING, "error": _logging.ERROR}
_RENDER_LEVEL = {"WARNING": "WARN"}        # keep the project's existing vocabulary

_configured = False


# ── formatter ────────────────────────────────────────────────────────────────


class AtelierFormatter(_logging.Formatter):
    """Renders: `<iso±offset> [LEVEL] [category] message`."""

    def format(self, record: _logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).astimezone().isoformat(
            timespec="seconds")
        level = _RENDER_LEVEL.get(record.levelname, record.levelname)
        cat = _category_of(record.name)
        line = f"{ts} [{level}] [{cat}] {record.getMessage()}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def _category_of(logger_name: str) -> str:
    if logger_name == _ROOT:
        return "general"
    if logger_name.startswith(_ROOT + "."):
        return logger_name[len(_ROOT) + 1:]
    return logger_name.split(".")[0]       # bridged libs: uvicorn / mcp


# ── config-defensive resolution (never raises) ───────────────────────────────


def _resolve_log_path(explicit: Optional[Path]) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("ATELIER_LOG_FILE")
    if env:
        return Path(env).expanduser()
    from . import config as _config       # lazy: config imports widely
    try:
        cfg_file = _config.load().logging.file
    except Exception:
        cfg_file = None
    if cfg_file:
        return Path(cfg_file).expanduser()
    return Path(_config.CACHE_DIR).parent / "logs" / "atelier.log"


def _resolve_level(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    env = os.environ.get("ATELIER_LOG_LEVEL")
    if env:
        return env.lower()
    from . import config as _config
    try:
        return _config.load().logging.level
    except Exception:
        return "info"


def _resolve_console(explicit: Optional[bool]) -> bool:
    if explicit is not None:
        return explicit
    from . import config as _config
    try:
        return _config.load().logging.console
    except Exception:
        return True


def _find_tagged(logger: _logging.Logger, tag: str) -> Optional[_logging.Handler]:
    for h in logger.handlers:
        if getattr(h, _HANDLER_TAG, None) == tag:
            return h
    return None


# ── configuration ────────────────────────────────────────────────────────────


def configure(*, level: Optional[str] = None, stdio: bool = False,
              console: Optional[bool] = None, bridge_libraries: bool = False,
              log_file: Optional[Path] = None) -> None:
    """Set up (idempotently) the single file sink and optional stderr console."""
    global _configured
    logger = _logging.getLogger(_ROOT)
    logger.setLevel(_LEVELS.get(_resolve_level(level), _logging.INFO))
    logger.propagate = False               # don't double-emit via root

    fh = _find_tagged(logger, "file")
    if fh is None:
        path = _resolve_log_path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = _logging.FileHandler(path, mode="a", encoding="utf-8")
        fh.setFormatter(AtelierFormatter())
        setattr(fh, _HANDLER_TAG, "file")
        logger.addHandler(fh)

    # stderr console: only interactive (TTY), never in stdio mode, never stdout.
    ch = _find_tagged(logger, "console")
    if _resolve_console(console) and not stdio and sys.stderr.isatty():
        if ch is None:
            ch = _logging.StreamHandler(sys.stderr)
            ch.setFormatter(AtelierFormatter())
            setattr(ch, _HANDLER_TAG, "console")
            logger.addHandler(ch)
    elif ch is not None:
        logger.removeHandler(ch)
        ch.close()

    if bridge_libraries:                   # consolidate uvicorn / mcp into our file
        for name in _BRIDGE_LOGGERS:
            lib = _logging.getLogger(name)
            lib.propagate = False
            if fh not in lib.handlers:
                lib.addHandler(fh)         # reuse the one handle, no duplication
        _logging.getLogger("uvicorn").setLevel(_logging.WARNING)

    _configured = True


def set_level(level: str) -> None:
    if not _configured:
        configure(level=level)
        return
    _logging.getLogger(_ROOT).setLevel(_LEVELS.get(level, _logging.INFO))


# ── façade (unchanged signatures) ────────────────────────────────────────────


def _emit(level: str, msg: str, **kv: Any) -> None:
    if not _configured:
        configure()
    category, sep, event = msg.partition(".")
    if not sep:                            # no dot → generic cli/general message
        category, event = "cli", msg
    text = event
    if kv:
        text += " " + " ".join(f"{k}={v}" for k, v in kv.items())
    _logging.getLogger(f"{_ROOT}.{category}").log(_LEVELS[level], text)


def debug(msg: str, **kv: Any) -> None: _emit("debug", msg, **kv)
def info(msg: str,  **kv: Any) -> None: _emit("info",  msg, **kv)
def warn(msg: str,  **kv: Any) -> None: _emit("warn",  msg, **kv)
def error(msg: str, **kv: Any) -> None: _emit("error", msg, **kv)
