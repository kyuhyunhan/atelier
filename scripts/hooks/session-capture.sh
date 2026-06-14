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

# Claude Code pipes the hook payload (JSON: session_id, transcript_path, cwd, …)
# on stdin. --payload-from-stdin forwards it; mcp_call whitelists capture fields
# and --require_why false lets the candidate land.
atelier-mcp-call atelier_learning_capture \
    --working_dir "$PWD" \
    --hook "$HOOK_KIND" \
    --require_why false \
    --payload-from-stdin \
    >/dev/null 2>&1 || true

exit 0
