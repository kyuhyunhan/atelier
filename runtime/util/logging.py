"""Minimal structured logger. No external deps."""
from __future__ import annotations

import sys
import time
from typing import Any


_LEVEL = "info"  # debug | info | warn | error


def set_level(level: str) -> None:
    global _LEVEL
    _LEVEL = level


_LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}


def _emit(level: str, msg: str, **kv: Any) -> None:
    if _LEVELS[level] < _LEVELS[_LEVEL]:
        return
    ts = time.strftime("%H:%M:%S")
    parts = [f"{ts}", f"[{level}]", msg]
    if kv:
        parts.append(" ".join(f"{k}={v}" for k, v in kv.items()))
    stream = sys.stderr if level in ("warn", "error") else sys.stdout
    print(" ".join(parts), file=stream)


def debug(msg: str, **kv: Any) -> None: _emit("debug", msg, **kv)
def info(msg: str,  **kv: Any) -> None: _emit("info",  msg, **kv)
def warn(msg: str,  **kv: Any) -> None: _emit("warn",  msg, **kv)
def error(msg: str, **kv: Any) -> None: _emit("error", msg, **kv)
