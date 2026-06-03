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
# Shared logging helper — hooks source this to append to the unified
# ~/.atelier/logs/atelier.log in the same format as the engine. Without it the
# hooks degrade to a silent no-op for logging.
cp scripts/hooks/_log.sh ~/.atelier/bin/_log.sh
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

## Per-turn signal recall (PR-28, opt-in)

A third hook, `signal-recall.sh`, fires on every `UserPromptSubmit`
(not just the first) and pushes the top-K learnings *most relevant to
the current prompt* into the next turn's context.

Enable in `~/.atelier/config.yaml`:

```yaml
learnings:
  signal_detector:
    enabled: true
    relevance_threshold: null      # optional FTS score cutoff
    max_chars_per_turn: 1500       # advisory (engine-side cap)
    cache_ttl_seconds: 30
```

```bash
cp scripts/hooks/signal-recall.sh ~/.atelier/bin/signal-recall.sh
chmod +x ~/.atelier/bin/signal-recall.sh
```

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "matcher": "",
        "hooks": [
          { "type": "command",
            "command": "~/.atelier/bin/session-bootstrap.sh" },
          { "type": "command",
            "command": "~/.atelier/bin/signal-recall.sh" }
        ] }
    ]
  }
}
```

The hook keeps a 30-second cache on `hash(prompt)` and a per-session
"already-shown" set in `~/.atelier/cache/recall-seen/<session>.txt`, so
the same learning is never pushed twice in one session.

`signal-recall.sh` is independent of `session-bootstrap.sh` — you can
enable either one alone.

## What the hook captures

The hook adapter forwards the Claude Code stop/session-end payload (JSON)
to `atelier_learning_capture` along with the current working directory.
The engine writes a candidate to `gorae/learnings/candidates/<date>/`.
At review time (`atelier_learning_review_pending`) you decide which to
accept; everything else stays in the candidates pile until the
retention cutoff archives it.
