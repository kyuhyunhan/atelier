# shellcheck shell=bash
# Shared logging helper for atelier shell hooks.
#
# Appends ONE line to the consolidated atelier log in the SAME format the Python
# core (runtime/util/logging.py) produces:
#
#   2026-06-03T16:04:25+09:00 [INFO] [recall] pushed session=abc items=fresh
#
# Usage:  atelier_log <level> <category> <event> [k=v ...]
# Override the sink with $ATELIER_LOG_FILE (matches the Python override).
atelier_log() {
    local level="$1" category="$2" event="$3"
    shift 3 2>/dev/null || true
    local kv="$*"
    # ISO-8601 local time with a colon in the offset (+0900 -> +09:00).
    local ts
    ts="$(date +%FT%T%z | sed -E 's/([0-9]{2})([0-9]{2})$/\1:\2/')"
    local up
    up="$(printf '%s' "$level" | tr '[:lower:]' '[:upper:]')"
    local line="$ts [$up] [$category] $event"
    [ -n "$kv" ] && line="$line $kv"
    local logfile="${ATELIER_LOG_FILE:-$HOME/.atelier/logs/atelier.log}"
    mkdir -p "$(dirname "$logfile")" 2>/dev/null || true
    printf '%s\n' "$line" >>"$logfile" 2>/dev/null || true
}
