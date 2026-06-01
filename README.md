# atelier

> **atelier is the engine.**
> A public, distributable methodology layer for a sovereign personal memory
> system. atelier knows nothing about *your* content: no user-specific paths,
> repo names, topics, or identifiers live in this repo. All binding to your
> world is supplied at runtime via `~/.atelier/config.yaml`.

---

## What it is

`atelier` defines schemas, agent persona contracts, and the runtime that turns
markdown content — held in your own private repositories — into a queryable,
synthesizable, self-linting brain. It is a public substrate; the content layer
that runs on top of it is private to each adopter.

## Architecture

Three layers, two stewards.

**Layers**

- **Layer 1 — `atelier`** (this repo): the engine. Schemas, agent contracts,
  runtime, sync adapters, lint rules. Culture-neutral, distributable, content-agnostic.
- **Layer 2 — content** (private per-user): markdown corpora in your own private
  GitHub repos (or any git host) + asset stores. Owned by you; never enters
  this repo.
- **Layer 3 — local**: working copies + a derived SQLite index + per-host
  config under `~/.atelier/`. Ephemeral; regenerable from Layer 2 at any time.

**Stewards**

- **Librarian** — owns the wiki integration layer (digests, sources, entities,
  themes, syntheses) in whichever space you configure with
  `role: librarian-territory`.
- **Builder** — owns the workshop (per-product working memory) in whichever
  space you configure with `role: builder-territory`.

A single SQLite database at `~/.atelier/cache/atelier.db` projects the markdown
content for fast queries; it is derived, gitignored, and rebuildable from
source at any time.

## Engine contract — what atelier MUST NOT know

A strict invariant enforced by config validation and code review:

- **No user-specific paths** as defaults (no `~/Documents/yourthing/`).
- **No user-specific repo names** in source or commit metadata.
- **No domain/cultural keywords** baked into the runtime (those live in
  `~/.atelier/voices/{librarian,builder}.md` — out of tree).
- **No fallbacks for missing config.** atelier refuses to start if
  `~/.atelier/config.yaml` contains placeholders. Adoption requires
  *deliberate* configuration.

This is what makes atelier *public-safe* despite operating on private content:
the engine is severed from the content by design, not by convention.

## Quick start

```bash
git clone https://github.com/<your-username>/atelier ~/workspaces/atelier
cd ~/workspaces/atelier
python3 -m venv .venv && .venv/bin/pip install -e ".[serve]"
./scripts/setup
# create ~/.atelier/{cache,voices,secrets} and copy config:
cp config/example.config.yaml ~/.atelier/config.yaml
# edit ~/.atelier/config.yaml — fill every <REQUIRED> field before continuing
atelier setup
atelier reindex --full

# Start the long-running engine. Claude Code in any directory connects
# to this process over MCP (HTTP, loopback + bearer):
echo "ATELIER_MCP_HTTP_TOKEN=$(openssl rand -hex 24)" >> ~/.atelier/secrets/.env
atelier serve --http
```

Then register atelier as an MCP server in `~/.claude/mcp.json`:

```json
{
  "mcpServers": {
    "atelier": {
      "transport": "http",
      "url": "http://127.0.0.1:7322/mcp",
      "headers": { "Authorization": "Bearer ${ATELIER_MCP_HTTP_TOKEN}" }
    }
  }
}
```

Now `claude` in any project directory can call `atelier_search`,
`atelier_youtube`, `atelier_learning_capture`, etc. against your one
gorae vault.

See [`docs/ADOPTING.md`](docs/ADOPTING.md) for a longer walkthrough,
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the system contract, and
[`CHANGELOG.md`](CHANGELOG.md) for release scope.

## Status

`v0.2.4`. Engine + single-vault model + learnings domain.

### What v0.2 added

- **Single-vault rename regression fix + unification** (v0.2.4) —
  classification is now schema-overlay-driven and space-identity-independent;
  lint/promote are space-agnostic; doctor D2 no longer false-reports phantom
  drift; cross-domain wikilinks resolve to one canonical entity; new **D7**
  learnings-mirror reconcile (`atelier_learning_reconcile`).

- **`atelier serve`** long-running engine; **MCP stdio + HTTP** transports
  with bearer-auth + asyncio role locks.
- **Single-vault model** — `vault:` + `subtrees:` config blocks; the
  legacy two-space (`librarian-territory` + `builder-territory`) model
  is collapsed into one repo. Workshop content + per-product memory
  absorbed by one-shot migration scripts.
- **Learnings domain** — `gorae/learnings/{candidates,accepted,principles}/`
  with agent-driven capture (a SessionStart disposition + a substance
  gate keep captures substantive), acceptance-criteria-gated
  promotion, cross-project **principles**, session-start injection,
  per-turn signal recall, Claude-Code-memory absorption, and a
  **usage-coupled dream cycle** for automated principle synthesis. See
  *Learnings domain & dream cycle* in
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design
  rationale (trigger model, interruption resilience).
- **Capability ports** — `atelier_{validate, fix_pending, index_regen,
  prepare_commit, clip_image, new_doc, youtube}` replace the proto-engine
  scripts inside gorae. See `scripts/gorae_cleanup/CHECKLIST.md` to
  remove them.

### Backlog deferred to v0.3+

- LLM facets reclass on prepare_commit; OpenAI STT path on YouTube
- Discord transport (out of scope by user decision)
- OAuth for MCP HTTP (currently static bearer + loopback only)
- launchd / autostart (foreground-only by user decision)
- Full R2 sync adapter; automatic AC scoring on learnings; vector /
  hybrid search; L2 hallucination lint

## License

MIT. See [`LICENSE`](LICENSE).
