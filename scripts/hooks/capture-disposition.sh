#!/usr/bin/env bash
# capture-disposition.sh — Claude Code SessionStart hook adapter.
#
# Installed to: ~/.atelier/bin/capture-disposition.sh
# Registered in: ~/.claude/settings.json under hooks.SessionStart
#
# Plants a CAPTURE DISPOSITION into the model's context once per session
# (additionalContext is model-only, invisible to the user). This is the
# correct realization of "let the agent fill the why": a bash hook cannot
# judge what was learned or why it matters, so instead of firing a blind
# empty capture (the old capture-learning.sh, which now gets rejected by
# the substance gate), we instruct the live agent to capture durable
# lessons *itself*, with a real why, as they arise during work.
#
# Always exits 0; never blocks session start.

set -u
export PATH="$HOME/.atelier/bin:$PATH"

read -r -d '' DISPOSITION <<'TXT'
atelier learnings — capture disposition for this session:
When you hit a DURABLE, reusable lesson during work — a behavior rule, a
non-obvious technical fact, or a mistake and its fix that will matter in
a future session or another project — record it by calling
atelier_learning_capture with:
  - observation: the specific thing (1–3 sentences),
  - why: why it matters / what it prevents (REQUIRED — empty-why captures
    are rejected),
  - rule (optional): the "when X, do Y" takeaway,
  - project_hint: the project it belongs to (or "cross-cutting").
Capture sparingly and only substance. SKIP: transient status ("phase 1
done", "tests passing now"), trivia, and anything you cannot give a real
why for. You — not the hook — fill the why; that is the whole point.
TXT

# SessionStart additionalContext (model-only).
python3 -c "
import json, sys
print(json.dumps({
  'hookSpecificOutput': {
    'hookEventName': 'SessionStart',
    'additionalContext': sys.argv[1],
  }
}))
" "$DISPOSITION"

exit 0
