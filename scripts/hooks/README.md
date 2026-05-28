# Claude Code hook templates

These are user-system installation templates — atelier does **not**
modify `~/.claude/settings.json` for you. Copy what you want and install
it manually.

## Aggressive learning capture (Stop + SessionEnd)

```bash
# 1) Make the adapter available on PATH.
mkdir -p ~/.atelier/bin
cp scripts/hooks/capture-learning.sh ~/.atelier/bin/capture-learning.sh
chmod +x ~/.atelier/bin/capture-learning.sh

# 2) Make sure atelier-mcp-call resolves on PATH (it's a console_script
#    installed by `pip install -e .`). Verify:
which atelier-mcp-call

# 3) Register the hooks in your Claude Code settings.
#    Add (or merge) the following into ~/.claude/settings.json:
```

```json
{
  "hooks": {
    "Stop": [
      { "matcher": "",
        "hooks": [
          { "type": "command",
            "command": "~/.atelier/bin/capture-learning.sh Stop" }
        ] }
    ],
    "SessionEnd": [
      { "matcher": "",
        "hooks": [
          { "type": "command",
            "command": "~/.atelier/bin/capture-learning.sh SessionEnd" }
        ] }
    ]
  }
}
```

```bash
# 4) Make sure ATELIER_MCP_HTTP_TOKEN is set in ~/.atelier/secrets/.env
#    and that `atelier serve --http` is running. The hook never blocks
#    your Claude session — failures are logged to ~/.atelier/logs/capture.log.
```

## Session-start context injection (PR-25)

A separate hook adapter, `session-bootstrap.sh`, runs on every
`UserPromptSubmit` event but only emits output on the *first* prompt of
each Claude session. It prints a markdown block on stdout — Claude Code
includes it as `additional_context` — containing the universal
principles (priority: always-inject) and the working-dir project's
learnings.

```bash
cp scripts/hooks/session-bootstrap.sh ~/.atelier/bin/session-bootstrap.sh
chmod +x ~/.atelier/bin/session-bootstrap.sh
```

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "matcher": "",
        "hooks": [
          { "type": "command",
            "command": "~/.atelier/bin/session-bootstrap.sh" }
        ] }
    ]
  }
}
```

Session-id dedup is kept in `~/.atelier/cache/seen-sessions.txt`. No
files in `~/.claude/` are modified by atelier — this is intentionally a
*loose-coupled* integration: removing the hook entry instantly reverts
Claude Code to its pre-atelier behavior.

## What the hook captures

The hook adapter forwards the Claude Code stop/session-end payload (JSON)
to `atelier_learning_capture` along with the current working directory.
The engine writes a candidate to `gorae/learnings/candidates/<date>/`.
At review time (`atelier_learning_review_pending`) you decide which to
accept; everything else stays in the candidates pile until the
retention cutoff archives it.
