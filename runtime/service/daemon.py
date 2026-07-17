"""`atelier serve` as an always-on background agent — two mechanisms.

The engine's automation (autosync commit/push, reindex-on-commit, MCP HTTP for
hooks) all lives INSIDE `atelier serve`; when serve is down the whole system
silently degrades to manual chores. Guardrails, specced up front (the
statusline CPU-melt taught this codebase that runaway is a real failure
class, not a hypothetical):

  G1 single instance    — server._acquire_pidfile (already in serve itself)
  G2 no crash-loop spin — see per-mechanism note below
  G3 low priority       — see per-mechanism note below
  G4 work only on work  — in serve's own design (30s git-status poll; reindex
                          only after a quiescent commit; zero LLM in-engine)
  G5 embed cap          — vault_autosync skips the embedding pass when a commit
                          changed more than `auto_commit.embed_max_changed`
                          files (bulk edits defer vectors to a manual reindex)

DEFAULT — session-anchored (`ensure` / `stop`): a Claude Code SessionStart
hook calls `atelier daemon ensure` every session. It spawns `serve --http`
detached ONLY if the pidfile shows nothing alive (G1 makes this idempotent —
a duplicate `ensure` is a fast no-op). Because the hook runs as a child of
the interactive Terminal/Claude Code process tree, the spawned serve INHERITS
the user's TCC grants — vault paths under ~/Documents work with zero manual
permission steps, which matters because atelier is meant to be distributed to
machines its author never touches. G2 here is structural, not a throttle:
there is no auto-restart-on-crash at all, so a crash loop cannot occur by
construction — serve only ever restarts when a NEW session starts, which is
naturally rate-limited by human activity. G3 is `os.nice()` in the spawn's
preexec_fn. Coverage gap (accepted): a reboot where the vault is only ever
touched via Obsidian (no Claude Code session opens) delays the next autosync
commit until a session finally starts — files are safe on disk the whole
time, only git sync lags.

OPT-IN / ADVANCED — launchd (`install` / `uninstall` / `status`): a login-
started, crash-restarted LaunchAgent. Kept for users who want serve alive
even with no Claude Code session running (e.g. a headless automation box).
NOT the default because launchd-spawned processes do NOT inherit the
interactive user's TCC grants — a vault under ~/Documents, ~/Desktop, or
~/Downloads fails with a misleading permission error unless Full Disk Access
is granted by hand via System Settings, a manual per-machine GUI step this
project won't ask distributed users to perform. `install()` prints that
warning when it detects a protected vault path. Here G2 is `ThrottleInterval:
60` (a crashing serve restarts at most once a minute) and G3 is
`ProcessType: Background` + `Nice: 10` in the plist.

Kill switches: `atelier daemon stop` (session-anchored) / `atelier daemon
uninstall` (launchd — bootout + plist removal). Visibility: `atelier daemon
status`.
"""
from __future__ import annotations

import os
import plistlib
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..util import logging as log
from . import server as _server

LABEL = "io.atelier.serve"

# TCC (Transparency, Consent, and Control) protects these on macOS: a process
# spawned by launchd does not inherit the interactive user's grant to read
# them, even though the identical command works fine from a Terminal shell.
TCC_PROTECTED_DIRS = ("Documents", "Desktop", "Downloads")


def is_tcc_protected(path: Path) -> bool:
    """True if *path* lives under a TCC-gated folder (~/Documents etc.)."""
    try:
        home = Path.home().resolve()
        rp = Path(path).resolve()
    except OSError:
        return False
    try:
        rel = rp.relative_to(home)
    except ValueError:
        return False
    return bool(rel.parts) and rel.parts[0] in TCC_PROTECTED_DIRS


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
    is booted out first so a re-install picks up plist changes.

    Advanced/opt-in path — the session-anchored `ensure()` is the default.
    Warns (does not refuse) when the configured vault sits under a
    TCC-protected folder, since a launchd-spawned serve will fail there
    without a manual Full Disk Access grant."""
    from ..util import config as _config
    try:
        vault_local = _config.load().vault.local
    except Exception:  # unconfigured / unloadable — nothing to warn about
        vault_local = None
    if vault_local is not None and is_tcc_protected(vault_local):
        log.warn("daemon.install.tcc-risk", vault=str(vault_local),
                  hint="launchd cannot read this path without manual Full "
                       "Disk Access (System Settings). Prefer the default "
                       "session-anchored daemon: `atelier daemon ensure` "
                       "(wired into every Claude Code session start).")

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
    """Combined visibility: is ANY serve alive (session-anchored, the default
    path), and is the opt-in launchd agent additionally installed/loaded?"""
    session_running, session_pid, _pf = _pidfile_state()

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
    return {"label": LABEL, "running": session_running,
            "session_pid": session_pid,
            "installed": installed, "loaded": loaded,
            "pid": pid, "state": state, "plist": str(plist_path())}


# ── session-anchored daemon (default) ────────────────────────────────────────
#
# No launchd, no plist, no crash-restart. `ensure()` is a fast idempotent
# check-then-spawn a SessionStart hook can call on every session with
# negligible cost when serve is already up. `stop()` is the manual kill
# switch (there is no auto-restart to fight, so a stopped serve stays down
# until the next session start calls `ensure()` again).


def _pidfile_state():
    """(running, pid_or_None, pidfile_path). "running" is decided by the
    flock (`server.is_locked()`), never by the pidfile's content: the
    content is left stale on a clean stop (see server._release_pidfile's
    docstring), so a content+kill(pid,0) check would misread a reused pid
    as "still running" indefinitely. `running` and `pid` are reported
    separately on purpose — pid is best-effort display/signalling only
    (can legitimately be None while running, in the rare window right
    after acquire but before the write, or if content is unparsable) and
    callers must never treat pid-is-None as "not running"."""
    pf = _server._pidfile_path()
    if not _server.is_locked():
        return False, None, pf
    try:
        pid = int(pf.read_text().strip() or "0") or None
    except (ValueError, OSError):
        pid = None
    return True, pid, pf


def ensure() -> Dict[str, Any]:
    """Spawn `serve --http` detached iff nothing alive holds the pidfile.

    Runs as a child of the caller's process tree (Terminal / Claude Code),
    so the spawned serve inherits the caller's TCC grants — the whole point
    of this mode over launchd. G3 (low priority) is applied to the child via
    `os.nice` in the fork; G1 (single instance) is enforced by serve itself
    re-checking the same pidfile on startup, so a race between two `ensure`
    calls resolves the same way an `install`-based instance would.
    """
    running, pid, pf = _pidfile_state()
    if running:
        return {"started": False, "already_running": True, "pid": pid}

    root = _engine_root()
    logs = _log_dir()
    logs.mkdir(parents=True, exist_ok=True)
    out_log = open(logs / "daemon.out.log", "a")
    err_log = open(logs / "daemon.err.log", "a")

    def _lower_priority() -> None:
        try:
            os.nice(10)                 # (G3) — no-op / unavailable on Windows
        except (AttributeError, OSError):
            pass

    proc = subprocess.Popen(
        [sys.executable, "-m", "runtime.cli", "serve", "--http"],
        cwd=str(root), stdin=subprocess.DEVNULL,
        stdout=out_log, stderr=err_log,
        # start_new_session: detach from the caller's session so serve
        # outlives it (no controlling terminal to hang up on when the
        # spawning shell/hook exits).
        start_new_session=True,
        preexec_fn=(_lower_priority if os.name == "posix" else None),
        close_fds=True,
    )
    out_log.close()
    err_log.close()
    log.info("daemon.ensure.spawned", pid=proc.pid, pidfile=str(pf))
    return {"started": True, "already_running": False, "pid": proc.pid}


def stop() -> Dict[str, Any]:
    """Kill switch for the session-anchored daemon: SIGTERM the pidfile's
    owner (serve's own shutdown handler releases the pidfile on exit)."""
    running, pid, pf = _pidfile_state()
    if not running:
        return {"stopped": False, "was_running": False}
    if pid is None:                    # locked, but content unavailable/racing
        return {"stopped": False, "was_running": True, "pid": None,
                "error": "serve is running but its pid could not be read "
                         "from the pidfile; retry in a moment"}
    os.kill(pid, signal.SIGTERM)
    log.info("daemon.stop.signalled", pid=pid, pidfile=str(pf))
    return {"stopped": True, "was_running": True, "pid": pid}
