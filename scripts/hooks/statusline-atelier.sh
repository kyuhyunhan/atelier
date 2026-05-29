#!/usr/bin/env bash
# statusline-atelier.sh — statusLine wrapper that appends a persistent
# atelier dream indicator (USER-VISIBLE, bottom of the UI).
#
# Installed to: ~/.atelier/bin/statusline-atelier.sh
# Registered in: ~/.claude/settings.json statusLine.command
#
# Claude Code pipes session JSON on stdin and DISPLAYS this script's
# stdout at the bottom of the UI. We:
#   1. run the user's existing base statusline (ccstatusline) with the
#      same stdin, and
#   2. append a compact dream status (`atelier dream --status`), which is
#      empty when nothing is due — so the segment only appears when there
#      is something to act on.
#
# `BASE_STATUSLINE` is the command to wrap (default: ccstatusline).

set -u
export PATH="$HOME/.atelier/bin:$PATH"

BASE_STATUSLINE="${ATELIER_BASE_STATUSLINE:-npx -y ccstatusline@latest}"

payload="$(cat)"

# 1) base statusline (fed the same stdin payload)
base="$(printf '%s' "$payload" | eval "$BASE_STATUSLINE" 2>/dev/null)"

# 2) atelier dream segment (fast, filesystem-backed; empty when nothing due)
extra=""
if command -v atelier >/dev/null 2>&1; then
    extra="$(atelier dream --status 2>/dev/null)"
fi

if [ -n "$extra" ]; then
    printf '%s | %s\n' "$base" "$extra"
else
    printf '%s\n' "$base"
fi
