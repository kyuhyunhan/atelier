#!/usr/bin/env bash
# signal-recall.sh — Claude Code UserPromptSubmit hook (opt-in, every turn).
#
# Installed to: ~/.atelier/bin/signal-recall.sh
# Registered in: ~/.claude/settings.json under hooks.UserPromptSubmit
#
# On every UserPromptSubmit, asks the running atelier engine for the
# top-K learnings relevant to the current prompt (FTS5 + project boost)
# and emits a compact markdown block on stdout. Claude Code includes
# this block as additional_context for the upcoming turn.
#
# Disabled unless `learnings.signal_detector.enabled: true` in
# ~/.atelier/config.yaml.
#
# Cache: 30s on hash(prompt) to suppress repeated lookups (retries,
# re-renders).
# Per-session dedup: avoid pushing the same slug twice in one session.
#
# Always exits 0 — must never block the user's flow.

set -u

LOG="$HOME/.atelier/logs/recall.log"
CACHE_ROOT="$HOME/.atelier/cache/recall"
SEEN_ROOT="$HOME/.atelier/cache/recall-seen"
CONFIG="$HOME/.atelier/config.yaml"
mkdir -p "$(dirname "$LOG")" "$CACHE_ROOT" "$SEEN_ROOT" 2>/dev/null || true

log() { printf '%s  %s\n' "$(date -u +%FT%TZ)" "$*" >>"$LOG" 2>/dev/null || true; }

PAYLOAD="$(cat 2>/dev/null || true)"

# Bail out fast if signal detector is disabled. We read the YAML
# minimally via grep — sufficient for the boolean flag.
ENABLED="$(grep -E '^[[:space:]]*signal_detector:' -A1 "$CONFIG" 2>/dev/null \
            | grep -E 'enabled:[[:space:]]*true' | head -1)"
if [ -z "$ENABLED" ]; then
    exit 0
fi

extract() {
    printf '%s' "$PAYLOAD" | python3 -c "
import json, sys
try:
    blob = json.loads(sys.stdin.read() or '{}')
    print(blob.get('$1') or '')
except Exception:
    pass
"
}

PROMPT="$(extract prompt)"
SESSION_ID="$(extract session_id)"
WORKING_DIR="$(extract cwd)"
[ -z "$WORKING_DIR" ] && WORKING_DIR="$PWD"

if [ -z "$PROMPT" ]; then
    exit 0
fi
if ! command -v atelier-mcp-call >/dev/null 2>&1; then
    exit 0
fi

# Time-based cache (30s) on hash(prompt).
PROMPT_HASH="$(printf '%s' "$PROMPT" | shasum -a 256 | awk '{print $1}')"
CACHE_FILE="$CACHE_ROOT/$PROMPT_HASH.json"
NOW="$(date +%s)"
if [ -f "$CACHE_FILE" ]; then
    AGE=$(( NOW - $(stat -f %m "$CACHE_FILE" 2>/dev/null \
                    || stat -c %Y "$CACHE_FILE" 2>/dev/null \
                    || echo "$NOW") ))
    if [ "$AGE" -lt 30 ]; then
        RAW="$(cat "$CACHE_FILE" 2>/dev/null || true)"
    fi
fi
if [ -z "${RAW:-}" ]; then
    PARAMS="$(python3 -c "
import json, sys
print(json.dumps({
    'query':       sys.argv[1],
    'project':     sys.argv[2] or None,
    'top_k':       5,
    'max_chars':   1500,
}))
" "$PROMPT" "$(basename "$WORKING_DIR")")"
    RAW="$(atelier-mcp-call atelier_recall --json "$PARAMS" --strict \
            2>/dev/null || true)"
    if [ -n "$RAW" ]; then
        printf '%s' "$RAW" >"$CACHE_FILE"
    fi
fi

if [ -z "$RAW" ]; then
    exit 0
fi

# Per-session dedup: filter out items we've already pushed this session.
SEEN_FILE="$SEEN_ROOT/$SESSION_ID.txt"
touch "$SEEN_FILE" 2>/dev/null || true

OUTPUT="$(printf '%s' "$RAW" | python3 -c "
import json, sys, os
raw = sys.stdin.read()
seen_path = sys.argv[1] if len(sys.argv) > 1 else ''
try:
    data = json.loads(raw or '{}')
    if 'result' in data:  data = data['result']
    if 'content' in data:
        text = (data['content'] or [{}])[0].get('text') or ''
        try:    data = json.loads(text)
        except: print(text); sys.exit(0)
    items = data.get('items') or []
    md_raw = data.get('markdown') or ''
    seen = set()
    if seen_path and os.path.exists(seen_path):
        seen = {ln.strip() for ln in open(seen_path) if ln.strip()}
    fresh = [it for it in items if it.get('slug') not in seen]
    if not fresh:
        sys.exit(0)
    fresh_slugs = [it.get('slug') for it in fresh if it.get('slug')]
    if seen_path:
        with open(seen_path, 'a') as f:
            for s in fresh_slugs:
                f.write(s + '\n')
    # Rebuild a minimal markdown block from the fresh items only.
    lines = ['## atelier — relevant memory', '']
    for it in fresh:
        kind = (it.get('page_type') or '').replace('learning_', '')
        title = it.get('title') or it.get('slug') or ''
        proj = it.get('project') or '-'
        topic = it.get('topic') or '-'
        snip = it.get('snippet') or ''
        lines.append(f'- **[{kind}] {title}** ({proj}/{topic}): {snip}')
    print('\n'.join(lines))
except Exception:
    pass
" "$SEEN_FILE")"

if [ -n "$OUTPUT" ]; then
    printf '%s\n' "$OUTPUT"
    log "recall session=$SESSION_ID project=$(basename "$WORKING_DIR") items=fresh"
fi

exit 0
