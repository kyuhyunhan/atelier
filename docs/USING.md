# Using atelier — the three verbs

atelier's CLI has ~19 subcommands. **You need three verbs.** Everything
else is engine surface: it runs itself, or it's for maintainers.

This is the daily-use contract. If a task isn't covered by a verb below,
the system is supposed to do it for you — and if it isn't doing it,
that's a bug to report, not a chore to learn.

---

## 1. Write (쓴다)

Put markdown in the vault. That's the whole verb.

- Drop new material into `raw/inbox/` (or directly into `raw/knowledge/`
  / `raw/personal/` when you already know where it belongs).
- Use Obsidian, your editor, or ask Claude — anything that writes a
  `.md` file into the vault.
- **No commands.** While the engine is up, autosync commits, pushes,
  and reindexes your change within about a minute of the file going
  quiet (it waits for two 30-second polls of stability before
  committing, so the window is 30–60s). Requires
  `vault.auto_commit.enabled: true` in `~/.atelier/config.yaml` —
  opt-in, per machine.

You never run `git`, `atelier sync`, or `atelier reindex` for normal
writing. If you edited a huge batch (more than 50 files), the engine defers
embeddings and tells you so in the log; a manual `atelier reindex`
catches vectors up — that's the only writing-adjacent command you'll
ever be nudged toward.

## 2. Ask (묻는다)

Talk to Claude. Memory arrives on its own.

- Session start injects vault context (bootstrap hook).
- Relevant learnings surface per prompt (signal recall, if enabled).
- Claude reaches the engine over MCP for deeper recall mid-task.

Manual form, when you want to look something up yourself:

```bash
atelier search "query"
```

## 3. Tend (돌본다)

React to what the system asks of you. Don't go looking for chores.

- **Nudges** appear at session start (atomize / promote / dream cycles
  that are due). Act on them or dismiss them; they re-surface on their
  cadence.
- **Health**, when something feels off:

  ```bash
  atelier doctor
  ```

That's the entire tending surface. Weekly maintenance rituals are gone;
the engine polls, commits, and reindexes on its own.

---

## The contract behind the verbs

| You do | The engine does |
|---|---|
| write markdown | commit, push, reindex (30–60s, autosync) |
| talk to Claude | inject context, recall, capture learnings |
| answer nudges | schedule them, dedup them, track cadence |
| nothing | stay alive (session-anchored daemon: every Claude Code session start runs `atelier daemon ensure`) |

The engine's liveness is anchored to your Claude Code sessions: starting
any session guarantees `atelier serve` is running, with your macOS file
permissions intact (no launchd, no Full Disk Access dialogs). If no
session ever starts after a reboot, sync just waits — files are safe on
disk; git lags until your next session.

## Escape hatches (not daily verbs)

| Situation | Command |
|---|---|
| is the engine alive? | `atelier daemon status` |
| stop the engine | `atelier daemon stop` |
| bulk edit deferred embeddings | `atelier reindex` |
| something looks broken | `atelier doctor`, then `~/.atelier/logs/atelier.log` |

Everything else in `atelier --help` (`serve`, `snapshot`, `baseline`,
`verify`, `lint`, `capture`, `promote`, `dream`, …) is engine or
maintainer surface — invoked by hooks, the daemon, or someone working
*on* atelier rather than *with* it. You can ignore it all.
