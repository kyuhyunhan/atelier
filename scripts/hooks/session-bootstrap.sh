#!/usr/bin/env bash

export PATH="$HOME/.atelier/bin:$PATH"

# Defensive: ensure ATELIER_MCP_HTTP_TOKEN is in env even if Claude Code was
# launched from a context that did not source ~/.zshrc (e.g. the GUI app).
[ -r "$HOME/.atelier/secrets/.env" ] && \
  { set -a; . "$HOME/.atelier/secrets/.env"; set +a; }

# session-bootstrap.sh — Claude Code UserPromptSubmit hook adapter.
#
# Installed to: ~/.atelier/bin/session-bootstrap.sh
# Registered in: ~/.claude/settings.json under hooks.UserPromptSubmit
#
# On the *first* UserPromptSubmit of each Claude session, emit a
# markdown block (atelier session_bootstrap output) on stdout — Claude
# Code includes this as additional_context for the next turn. Subsequent
# turns of the same session are no-ops.
#
# Session-id dedup is kept in ~/.atelier/cache/seen-sessions.txt
# (one id per line). The cache file is touched only on success.
#
# Design constraint: loose coupling. atelier does NOT modify
# ~/.claude/CLAUDE.md or any user-owned file. If atelier-mcp-call
# is missing or the engine isn't running, this script exits 0 silently.

set -u

LOG="$HOME/.atelier/logs/bootstrap.log"
CACHE_DIR="$HOME/.atelier/cache"
SEEN_FILE="$CACHE_DIR/seen-sessions.txt"
mkdir -p "$(dirname "$LOG")" "$CACHE_DIR" 2>/dev/null || true

log() { printf '%s  %s\n' "$(date -u +%FT%TZ)" "$*" >>"$LOG" 2>/dev/null || true; }

# Claude Code pipes the hook payload (JSON) on stdin.
PAYLOAD="$(cat 2>/dev/null || true)"

# Extract session_id and cwd without requiring jq — they're small JSON
# fields with stable shapes.
extract() {
    local key="$1"
    printf '%s' "$PAYLOAD" | python3 -c "
import json, sys
try:
    blob = json.loads(sys.stdin.read() or '{}')
    val = blob.get('$key') or ''
    print(val)
except Exception:
    pass
"
}

SESSION_ID="$(extract session_id)"
WORKING_DIR="$(extract cwd)"
[ -z "$WORKING_DIR" ] && WORKING_DIR="$PWD"

if [ -z "$SESSION_ID" ]; then
    log "skip: no session_id in payload"
    exit 0
fi

if grep -qxF "$SESSION_ID" "$SEEN_FILE" 2>/dev/null; then
    # Not the first turn — silent no-op.
    exit 0
fi

if ! command -v atelier-mcp-call >/dev/null 2>&1; then
    log "skip: atelier-mcp-call not on PATH"
    exit 0
fi

OUT="$(atelier-mcp-call atelier_session_bootstrap \
    --json "{\"working_dir\": \"$WORKING_DIR\", \"max_chars\": 6000}" \
    --strict 2>/dev/null || true)"

if [ -z "$OUT" ]; then
    log "skip: atelier returned empty (engine down?)"
    exit 0
fi

# atelier-mcp-call --strict writes the tool result JSON to stdout; we
# extract the `markdown` field with stdlib python.
MARKDOWN="$(printf '%s' "$OUT" | python3 -c "
import json, sys
try:
    data = json.loads(sys.stdin.read() or '{}')
    # The MCP tools/call response wraps the result in a content list.
    if isinstance(data, dict) and 'result' in data:
        data = data['result']
    if isinstance(data, dict) and 'content' in data:
        # MCP returns [{'type':'text','text':'<json-string>'}]
        first = (data['content'] or [{}])[0]
        text = first.get('text') or ''
        try:
            data = json.loads(text)
        except Exception:
            print(text); sys.exit(0)
    md = data.get('markdown') if isinstance(data, dict) else ''
    print(md or '')
except Exception:
    pass
")"

if [ -n "$MARKDOWN" ]; then
    printf '%s\n' "$MARKDOWN"
    printf '%s\n' "$SESSION_ID" >>"$SEEN_FILE"
    # Persist the exact injected block so it can be inspected later — the
    # log records only the fact of injection, this records the content
    # (observability: what did this session actually receive?). Fail-safe.
    INJECTED_DIR="$HOME/.atelier/logs/injected"
    mkdir -p "$INJECTED_DIR" 2>/dev/null || true
    {
        printf '\n<!-- bootstrap %s | session=%s | project=%s -->\n' \
            "$(date -u +%FT%TZ)" "$SESSION_ID" "$(basename "$WORKING_DIR")"
        printf '%s\n' "$MARKDOWN"
    } >>"$INJECTED_DIR/$SESSION_ID.md" 2>/dev/null || true
    log "injected session=$SESSION_ID project=$(basename "$WORKING_DIR")"
fi

exit 0
