#!/usr/bin/env bash
# DEPRECATED (PR-37): blind hook captures have no 'why' and are now
# rejected by the capture substance gate. Use capture-disposition.sh
# (SessionStart) instead — it instructs the live agent to capture
# durable lessons itself, with a real why. This file is kept only for
# historical reference; do NOT register it on Stop/SessionEnd.
# capture-learning.sh — Claude Code Stop / SessionEnd hook adapter.
#
# Installed to: ~/.atelier/bin/capture-learning.sh
# Registered in: ~/.claude/settings.json under hooks.Stop and hooks.SessionEnd
#
# Claude Code pipes the hook payload (JSON) on stdin; we forward it to
# the running atelier MCP server via atelier-mcp-call. By design this
# script ALWAYS exits 0 — a failed capture must never block the user's
# Claude Code session.

set -u  # NOT set -e: we want to swallow errors deliberately.

HOOK_KIND="${1:-manual}"

if ! command -v atelier-mcp-call >/dev/null 2>&1; then
    # atelier not installed in PATH; nothing we can do silently.
    echo "atelier-mcp-call not in PATH — skipping learning capture" \
        >>"$HOME/.atelier/logs/capture.log" 2>/dev/null || true
    exit 0
fi

# Forward stdin JSON to atelier; ignore non-zero return so we never
# break the host shell.
atelier-mcp-call atelier_learning_capture \
    --working_dir "$PWD" \
    --hook "$HOOK_KIND" \
    --payload-from-stdin \
    >/dev/null 2>&1 || true

exit 0
