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
- **Budgeted surfacing tiers** — `query ⊂ proactive ⊂ always`, and the
  always-tier is hard-capped (12 slots against 4,486 claims today). Nothing
  floods your context by default.
- **Provenance chains** — every claim derives from an immutable source node;
  "where did this memory come from" always has an answer.
- **Lens walls** — one graph, but a coding session structurally cannot see
  your diary (`dev` / `life` / `full` lenses + a `private` sensitivity gate
  that is lint-enforced, not ranking luck).

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
