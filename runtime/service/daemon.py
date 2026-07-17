"""launchd integration — `atelier serve` as an always-on background agent.

The engine's automation (autosync commit/push, reindex-on-commit, MCP HTTP for
hooks) all lives INSIDE `atelier serve`; when serve is down the whole system
silently degrades to manual chores. This module makes serve a login-started,
crash-restarted launchd agent — with the resource guardrails specced up front
(the statusline CPU-melt taught this codebase that runaway is a real failure
class, not a hypothetical):

  G1 single instance    — server._acquire_pidfile (already in serve itself)
  G2 no crash-loop spin — ThrottleInterval 60: a crashing serve restarts at
                          most once a minute, never in a hot loop
  G3 low priority       — ProcessType Background + Nice 10: idle polling never
                          competes with foreground work
  G4 work only on work  — in serve's own design (30s git-status poll; reindex
                          only after a quiescent commit; zero LLM in-engine)
  G5 embed cap          — vault_autosync skips the embedding pass when a commit
                          changed more than `auto_commit.embed_max_changed`
                          files (bulk edits defer vectors to a manual reindex)

Kill switch: `atelier daemon uninstall` (bootout + plist removal). Visibility:
`atelier daemon status`.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..util import logging as log

LABEL = "io.atelier.serve"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _engine_root() -> Path:
    """The engine repo root (…/atelier), derived from this file's location so
    the plist survives the repo being cloned anywhere."""
    return Path(__file__).resolve().parents[2]


def _log_dir() -> Path:
    from ..util import config as _config
    return _config.CACHE_DIR.parent / "logs"


def render_plist(*, python_exe: Optional[str] = None,
                 engine_root: Optional[Path] = None,
                 log_dir: Optional[Path] = None) -> Dict[str, Any]:
    """The launchd agent definition as a dict (pure; serialized by install).

    Guardrails G2/G3 are HERE, in the spec, not in prose: ThrottleInterval,
    ProcessType Background, Nice."""
    py = python_exe or sys.executable
    root = Path(engine_root or _engine_root())
    logs = Path(log_dir or _log_dir())
    return {
        "Label": LABEL,
        "ProgramArguments": [py, "-m", "runtime.cli", "serve", "--http"],
        "WorkingDirectory": str(root),
        "RunAtLoad": True,               # start at login
        "KeepAlive": True,               # restart on crash/kill …
        "ThrottleInterval": 60,          # … but at most once a minute (G2)
        "ProcessType": "Background",     # low scheduling priority (G3)
        "Nice": 10,                      # (G3)
        "StandardOutPath": str(logs / "daemon.out.log"),
        "StandardErrorPath": str(logs / "daemon.err.log"),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _gui_domain() -> str:
    return f"gui/{os.getuid()}"


def install() -> Dict[str, Any]:
    """Write the plist and load the agent. Idempotent: an already-loaded agent
    is booted out first so a re-install picks up plist changes."""
    p = plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    _log_dir().mkdir(parents=True, exist_ok=True)
    spec = render_plist()
    p.write_bytes(plistlib.dumps(spec))

    _launchctl("bootout", f"{_gui_domain()}/{LABEL}")     # ignore result: may not be loaded
    r = _launchctl("bootstrap", _gui_domain(), str(p))
    if r.returncode != 0:                                 # legacy fallback
        r = _launchctl("load", "-w", str(p))
    ok = r.returncode == 0
    log.info("daemon.install", plist=str(p), ok=ok,
             err=(r.stderr.strip() or None))
    return {"plist": str(p), "loaded": ok, "label": LABEL,
            "error": (r.stderr.strip() or None) if not ok else None}


def uninstall() -> Dict[str, Any]:
    """The kill switch: boot the agent out and remove the plist."""
    r = _launchctl("bootout", f"{_gui_domain()}/{LABEL}")
    if r.returncode != 0:                                 # legacy fallback
        _launchctl("unload", "-w", str(plist_path()))
    removed = False
    if plist_path().exists():
        plist_path().unlink()
        removed = True
    log.info("daemon.uninstall", removed=removed)
    return {"label": LABEL, "plist_removed": removed}


def status() -> Dict[str, Any]:
    """Is the agent installed / loaded / running? (`launchctl print` + plist.)"""
    installed = plist_path().exists()
    r = _launchctl("print", f"{_gui_domain()}/{LABEL}")
    loaded = r.returncode == 0
    pid: Optional[int] = None
    state: Optional[str] = None
    if loaded:
        for line in r.stdout.splitlines():
            ln = line.strip()
            if ln.startswith("pid ="):
                try:
                    pid = int(ln.split("=", 1)[1].strip())
                except ValueError:
                    pass
            elif ln.startswith("state ="):
                state = ln.split("=", 1)[1].strip()
    return {"label": LABEL, "installed": installed, "loaded": loaded,
            "pid": pid, "state": state, "plist": str(plist_path())}
