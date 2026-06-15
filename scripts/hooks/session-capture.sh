#!/usr/bin/env bash
# session-capture.sh — Claude Code SessionEnd / PreCompact hook adapter (RFC 0004).
#
# Installed to: ~/.atelier/bin/session-capture.sh
# Registered in: ~/.claude/settings.json under hooks.SessionEnd and hooks.PreCompact
#
# Lands a learning CANDIDATE at the session boundary WITHOUT requiring a `why`
# (require_why=false). The engine builds the observation from the transcript tail
# (atelier_learning_capture → _extract_transcript_tail); the no-substance gate
# still drops empty/trivial sessions. Quality is judged later by the phase-2
# curation pass — this hook only stops durable material from being lost.
#
# Supersedes the DEPRECATED capture-learning.sh, which fired with require_why=true
# and was therefore rejected by the empty-why gate. Always exits 0 — a failed
# capture must never block the user's Claude Code session.

set -u  # NOT set -e: swallow errors deliberately.
export PATH="$HOME/.atelier/bin:$PATH"

# Defensive: ensure ATELIER_MCP_HTTP_TOKEN is present even if Claude Code was
# launched without sourcing ~/.zshrc.
[ -r "$HOME/.atelier/secrets/.env" ] && \
  { set -a; . "$HOME/.atelier/secrets/.env"; set +a; }

HOOK_KIND="${1:-SessionEnd}"

command -v atelier-mcp-call >/dev/null 2>&1 || exit 0

# Read the payload once so we can both extract cwd AND re-pipe it to the CLI.
PAYLOAD="$(cat 2>/dev/null || true)"

# Prefer the session's real cwd from the payload, falling back to $PWD — same as
# signal-recall.sh / session-bootstrap.sh. The hook runner's own working dir is
# not guaranteed to be the user's project root, so $PWD alone is unreliable.
WORKING_DIR="$(printf '%s' "$PAYLOAD" | python3 -c "
import json, sys
try:
    print(json.loads(sys.stdin.read() or '{}').get('cwd') or '')
except Exception:
    pass
" 2>/dev/null)"
[ -z "$WORKING_DIR" ] && WORKING_DIR="$PWD"

# Land a candidate with no why (require_why=false); curation judges quality later.
# --payload-from-stdin forwards transcript_path/session_id; mcp_call whitelists
# capture fields and drops Claude Code envelope keys.
printf '%s' "$PAYLOAD" | atelier-mcp-call atelier_learning_capture \
    --working_dir "$WORKING_DIR" \
    --hook "$HOOK_KIND" \
    --require_why false \
    --payload-from-stdin \
    >/dev/null 2>&1 || true

exit 0
