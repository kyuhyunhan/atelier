#!/usr/bin/env bash
# session-nudge.sh — Claude Code SessionStart hook adapter (USER-VISIBLE).
#
# Installed to: ~/.atelier/bin/session-nudge.sh
#   (re-synced from this repo source by ./scripts/setup — edit HERE, then setup)
# Registered in: ~/.claude/settings.json under hooks.SessionStart
#
# Unlike session-bootstrap.sh (whose stdout is injected into the MODEL's
# context and is invisible to the human), this hook emits a JSON
# `systemMessage` — which Claude Code DISPLAYS TO THE USER — when any
# GATED edge (atomize / promote / dream) wants attention. Fires once per
# session start (startup / resume / clear).
#
# Surfaces the UNIFIED nudge surface (RFC 0005 §7): all three edges
# normalized to one shape by `atelier nudges --json`. We join every DUE
# nudge's `long` message into a single systemMessage. Reads via the local
# CLI (no running server required, fast, filesystem-backed). Always exits 0
# so it never blocks session start.
#
# Also the session-anchored daemon's revival point: `atelier daemon ensure`
# spawns `serve --http` iff nothing already holds its pidfile (idempotent,
# <100ms when already running). Running here — as a child of the caller's
# process tree — means the spawned serve inherits the caller's TCC grants,
# so a vault under ~/Documents works with zero manual permission steps on
# any machine. Backgrounded so a cold spawn never delays session start.

set -u

export PATH="$HOME/.atelier/bin:$PATH"

command -v atelier >/dev/null 2>&1 || exit 0

atelier daemon ensure >/dev/null 2>&1 &
disown 2>/dev/null || true

INFO="$(atelier nudges --json 2>/dev/null)"
[ -z "$INFO" ] && exit 0

printf '%s' "$INFO" | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read() or '{}')
    due = [n for n in d.get('nudges', [])
           if n.get('due') and n.get('long')]
    if due:
        msg = '\n\n'.join(n['long'] for n in due)
        # systemMessage is rendered to the user by Claude Code.
        print(json.dumps({'systemMessage': msg}))
except Exception:
    pass
"
exit 0
