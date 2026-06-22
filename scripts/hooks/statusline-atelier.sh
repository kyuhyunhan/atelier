#!/usr/bin/env bash
# statusline-atelier.sh — statusLine wrapper that appends one USER-VISIBLE
# atelier segment to the base statusline (bottom of the Claude Code UI):
#   1. an activity heartbeat — the most recent atelier engine action, parsed
#      from the unified log, so the otherwise-invisible hook-driven loop
#      (recall / bootstrap / capture / reindex) is observable WITHOUT engaging
#      with it. Renders e.g. "⟲ recall · 4s"; an error call gets a "!" suffix.
#
# The dream nudge is NOT rendered here. It already surfaces once per session as
# a SessionStart `systemMessage` (scripts/hooks/session-nudge.sh). Calling
# `atelier dream --status` on every statusline render booted the full Python
# app and walked the whole vault (O(accepted claims)); the renders re-fire
# faster than the call completes, so the processes stacked and pinned multiple
# CPU cores. One cheap per-session surface beats a costly per-render one.
#
# Installed to: ~/.atelier/bin/statusline-atelier.sh
# Registered in: ~/.claude/settings.json statusLine.command
#
# Claude Code pipes session JSON on stdin and DISPLAYS this script's stdout at
# the bottom of the UI. Every segment degrades to empty on any error — the
# statusline must never break.
#
# Env:
#   ATELIER_BASE_STATUSLINE      command to wrap (default: ccstatusline)
#   ATELIER_STATUSLINE_ACTIVITY  set to 0 to hide the activity heartbeat
#   ATELIER_LOG_FILE             log source (default: ~/.atelier/logs/atelier.log)

set -u
export PATH="$HOME/.atelier/bin:$PATH"

BASE_STATUSLINE="${ATELIER_BASE_STATUSLINE:-npx -y ccstatusline@latest}"
LOG_FILE="${ATELIER_LOG_FILE:-$HOME/.atelier/logs/atelier.log}"

payload="$(cat)"

# 1) base statusline (fed the same stdin payload)
base="$(printf '%s' "$payload" | eval "$BASE_STATUSLINE" 2>/dev/null)"

# 2) activity heartbeat — last meaningful engine event from the unified log.
#    `mcp-call` lines carry `tool=NAME`; the domain categories name the silent
#    hook loop the user can't otherwise see. The bounded tail keeps the parse
#    cheap as the log grows (the latest named event is always near the tail).
atelier_activity() {
    [ "${ATELIER_STATUSLINE_ACTIVITY:-1}" = "1" ] || return 0
    [ -r "$LOG_FILE" ] || return 0

    local line
    line="$(tail -n 400 "$LOG_FILE" 2>/dev/null \
            | grep -E '\[(mcp-call|recall|capture|bootstrap|reindex)\]' \
            | tail -n 1)"
    [ -n "$line" ] || return 0

    # Line shape: "<ts> [LEVEL] [category] <rest...>"
    local ts categ rest label
    ts="${line%% *}"
    categ="$(printf '%s' "$line" | sed -E 's/^[^]]*\] \[([^]]*)\].*/\1/')"
    rest="$(printf '%s'  "$line" | sed -E 's/^[^]]*\] \[[^]]*\] //')"

    case "$categ" in
        mcp-call)
            # "ok tool=atelier_recall" | "error tool=… detail=…"
            local status tool
            status="${rest%% *}"
            tool="$(printf '%s' "$rest" | sed -nE 's/.*tool=([A-Za-z0-9_]+).*/\1/p')"
            tool="${tool#atelier_}"
            label="${tool:-call}"
            [ "$status" = "ok" ] || label="${label}!"
            ;;
        *) label="$categ" ;;
    esac

    # Compact age. The log offset is +HH:MM; strip the colon for BSD `date -j`.
    local zts epoch now diff ago=""
    zts="$(printf '%s' "$ts" | sed -E 's/([0-9]{2}):([0-9]{2})$/\1\2/')"
    epoch="$(date -j -f "%Y-%m-%dT%H:%M:%S%z" "$zts" +%s 2>/dev/null)"
    now="$(date +%s 2>/dev/null)"
    if [ -n "$epoch" ] && [ -n "$now" ]; then
        diff=$(( now - epoch )); [ "$diff" -lt 0 ] && diff=0
        if   [ "$diff" -lt 60 ];    then ago="${diff}s"
        elif [ "$diff" -lt 3600 ];  then ago="$(( diff / 60 ))m"
        elif [ "$diff" -lt 86400 ]; then ago="$(( diff / 3600 ))h"
        else                              ago="$(( diff / 86400 ))d"
        fi
    fi

    if [ -n "$ago" ]; then printf '⟲ %s · %s' "$label" "$ago"
    else                   printf '⟲ %s' "$label"; fi
}

extra_activity="$(atelier_activity 2>/dev/null)"

# Join non-empty segments with " | " — never emit a leading separator when the
# base statusline is empty (e.g. ccstatusline absent).
out="$base"
for seg in "$extra_activity"; do
    [ -n "$seg" ] || continue
    if [ -n "$out" ]; then out="$out | $seg"; else out="$seg"; fi
done
printf '%s\n' "$out"
