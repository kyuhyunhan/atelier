# atelier

**Most memory systems remember everything automatically. atelier makes
memories earn their place.**

atelier turns markdown you own — notes, sources, captured lessons, kept in
your **private** git repos — into a queryable, self-linting memory for AI
agents. The engine is public and knows nothing about your content; everything
personal binds at runtime through `~/.atelier/config.yaml`.

**Claude Code-native, MCP-accessible.** The daily loop (session bootstrap,
signal recall, learning capture) is delivered through Claude Code hooks. Any
MCP client can query the vault; the full write–ask–tend contract currently
assumes Claude Code.

## Why it's different

Where other memory systems auto-extract and rank, atelier gives memory an
**editorial process**:

- **An acceptance gate** — a captured lesson must carry its *why*, or it is
  rejected. Memories start unproven and earn their status.
- **Budgeted surfacing tiers** — `query ⊂ proactive ⊂ always`. Recall serves
  at most three always-tier claims per turn, so the top tier is budgeted where
  it actually costs you context rather than by capping what you may store.
- **Provenance chains** — every claim derives from an immutable source node;
  "where did this memory come from" always has an answer.
- **Domain walls** — one graph, but a coding session structurally cannot see
  your diary. You classify a document once (its `domain`); which surfaces may
  read it follows from that, lint-enforced rather than ranking luck. (Agents
  select a wall by name — the `lens` parameter on the read tools: `dev`,
  `life`, `full`. You never have to.)

**Measured, on this repo's own eval harness** (methodology and limits in
[docs/BENCHMARKS.md](docs/BENCHMARKS.md)):

| Probe set | lexical-rrf | hybrid | Δ |
|---|---|---|---|
| paraphrase Recall@5 | 0.636 | **0.773** | +13.6 pp |
| self-probe Recall@5 | 1.000 | 0.990 | −1.0 pp |

Hybrid pays where wording diverges from storage; the numbers are
self-measured, not comparative — we make no "beats X" claims.

## Try it in five minutes

A synthetic demo vault ships with the repo — fictional people, invented
notes, every node kind and tier represented:

```bash
cp -r examples/vault-seed /tmp/seed-vault
# point a throwaway ~/.atelier/config.yaml at it, then:
atelier reindex --full
atelier search "WAL"        # retrieval
atelier nudges              # pending curation work
atelier doctor              # health checks
```

[`examples/vault-seed/README.md`](examples/vault-seed/README.md) is the tour.
The suite pins the seed against the live schema, so it cannot rot.

## Install

macOS/Linux, Python 3.11+, git. Your vault is any (private) git repo; start
empty or with the seed.

```bash
git clone https://github.com/<your-fork>/atelier ~/workspaces/atelier
cd ~/workspaces/atelier
python3 -m venv .venv && .venv/bin/pip install -e ".[serve,semantic]"
./scripts/setup                              # pre-commit guard + ~/.atelier check
cp config/example.config.yaml ~/.atelier/config.yaml
# fill every <REQUIRED> field — atelier refuses to start on placeholders
atelier setup && atelier reindex --full

# the long-running engine (MCP over HTTP, loopback + bearer):
echo "ATELIER_MCP_HTTP_TOKEN=$(openssl rand -hex 24)" >> ~/.atelier/secrets/.env
atelier serve --http
```

Register it once in `~/.claude/mcp.json`:

```json
{ "mcpServers": { "atelier": {
    "transport": "http", "url": "http://127.0.0.1:7322/mcp",
    "headers": { "Authorization": "Bearer ${ATELIER_MCP_HTTP_TOKEN}" } } } }
```

Full walkthrough: [`docs/ADOPTING.md`](docs/ADOPTING.md). Logs:
`~/.atelier/logs/atelier.log`.

## Daily use — three verbs

Everything else is machinery underneath these:

- **Write (쓴다)** — drop markdown in the vault. The autosync poller commits,
  pushes, and reindexes within ~60 s; you never run git for normal work.
- **Ask (묻는다)** — talk to Claude. Session start injects context; relevant
  lessons surface per prompt; deeper recall happens over MCP mid-task.
- **Tend (돌본다)** — answer the nudges when you feel like it: atomize new
  sources, promote proven lessons, let the dream pass distill principles.

## How it works

Three layers: **markdown is truth, the DB is a projection.**

```
your vault (private git)          ~/.atelier/cache (disposable)
raw/     immutable Sources   →    SQLite + FTS5 + vectors
graph/   Claims + Entities   →    rebuilt by `atelier reindex`
```

A Source lands in `raw/`; atomize mints content-addressed Claims and
Entities from it; reindex projects everything into a local SQLite cache;
recall serves it back ranked by
`gate(surfacing) × domain_prior × relevance × sensitivity`. Deleting the
cache loses nothing.

### The five words you'll actually meet

| Term | What it is | Who does it |
|---|---|---|
| **ingest** | putting a document into the vault's `raw/` as a file — that's all it is | you (or an agent on your behalf) |
| **Source** | the document once it's in `raw/`: an immutable original, never edited or summarized | (a state, not an action) |
| **autosync** | the background poller that notices changed files and commits + pushes + reindexes for you | the engine, automatic |
| **reindex** | re-projecting all markdown into the local SQLite cache that search and recall read | the engine, automatic |
| **atomize** | extracting atomic facts (Claims) and subjects (Entities) from a Source into `graph/` | an LLM extracts, you ratify |

The distinction that matters: after ingest + reindex a document is
**searchable** (like a file in a drive — found when asked). Only after
atomize does it become **memory**: split into facts that can earn their way
up the surfacing tiers and be recalled without being asked for by name.

Markdown is the truth, but **every number you see comes from the projection**
— counts, nudges, search. So a file you just wrote is invisible to them until
the next autosync tick (30–60 s), or until you run `atelier reindex` yourself.
If a count looks wrong, reproject before investigating.

### The ladder — three lanes, not one

The domain you pick decides how far a document can climb. `atelier nudges`
counts the gated edges (absorb / atomize / promote / dream), so the thing that
tells you where you are and the thing that teaches where you could go are the
same list:

```
every document:   ingest (or absorb) ──▶ Source ──atomize──▶ Claim
                                                              │
then, by domain:                                              ▼
  operational   Claim ──accept──▶ ──promote──▶ proactive ──dream──▶ always
                      (needs a why)
  knowledge     Claim ─────────────(no promote edge)──dream──▶ proactive/always
                      (born accepted)
  personal      Claim ────────────────── stops here, never pushed
```

- **operational** (session lessons) is the only lane with a promote edge, and
  `ac_status: passed` is the gate on it — not a side-trip, a precondition.
- **knowledge** claims are born accepted and answer when asked; they reach
  proactive/always only when a dream pass generalizes them.
- **personal** claims stop at Claim by construction (`private` is never pushed).

### What runs itself, and what waits for you

| Step | personal | knowledge / operational |
|---|---|---|
| commit + push (autosync) | automatic | automatic |
| reindex → searchable | automatic | automatic |
| **atomize → memory** | **manual, always** | **manual, always** |

Atomize is never automatic in any domain — it needs LLM judgment, costs
tokens, and touches your content, so a human pulls the trigger every time.
The `personal` domain adds one more layer on top of that manual gate: the
nudge *counts* its backlog but will not suggest running it — agents atomize
personal sources only when you explicitly direct it, and the derived claims
land `sensitivity: private` automatically (lint-enforced), which blocks them
from ever being pushed proactively. A diary entry cannot become ambient
context by accident; there is no code path for it.

Deep dives: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (system
contract), [`docs/rfc/`](docs/rfc/) (design history, 0001–0009, every item
shipped or formally dispositioned).

## What the engine must never know

Enforced by config validation, lint, CI, and review — not convention:

- No user paths, repo names, or cultural keywords in the engine; personal
  voice lives out-of-tree in `~/.atelier/voices/`.
- No silent defaults for content locations — placeholder config refuses to
  start.
- No writes outside the configured vault; sources it ingests from are
  read-only.

## Status

`v0.2`, single-vault model, actively developed by one maintainer.
[`CHANGELOG.md`](CHANGELOG.md) carries the release history;
[`CONTRIBUTING.md`](CONTRIBUTING.md) states this repo's (unusual) rules
before your first PR; [`SECURITY.md`](SECURITY.md) scopes what is
security-relevant on a local-first engine.

## License

MIT. See [`LICENSE`](LICENSE).
