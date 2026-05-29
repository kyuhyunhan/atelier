#!/usr/bin/env bash
# session-nudge.sh — Claude Code SessionStart hook adapter (USER-VISIBLE).
#
# Installed to: ~/.atelier/bin/session-nudge.sh
# Registered in: ~/.claude/settings.json under hooks.SessionStart
#
# Unlike session-bootstrap.sh (whose stdout is injected into the MODEL's
# context and is invisible to the human), this hook emits a JSON
# `systemMessage` — which Claude Code DISPLAYS TO THE USER — when the
# dream cycle wants attention. Fires once per session start (startup /
# resume / clear).
#
# Reads dream status via the local CLI (no running server required, fast,
# filesystem-backed). Always exits 0 so it never blocks session start.

set -u

export PATH="$HOME/.atelier/bin:$PATH"

command -v atelier >/dev/null 2>&1 || exit 0

INFO="$(atelier dream --status --json 2>/dev/null)"
[ -z "$INFO" ] && exit 0

printf '%s' "$INFO" | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read() or '{}')
    if d.get('due') and d.get('long'):
        # systemMessage is rendered to the user by Claude Code.
        print(json.dumps({'systemMessage': d['long']}))
except Exception:
    pass
"
exit 0
