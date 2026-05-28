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
python3 -m venv .venv && .venv/bin/pip install -e .
./scripts/setup
# create ~/.atelier/{cache,voices,secrets} and copy config:
cp config/example.config.yaml ~/.atelier/config.yaml
# edit ~/.atelier/config.yaml — fill every <REQUIRED> field before continuing
atelier setup
atelier reindex --full
```

See [`docs/ADOPTING.md`](docs/ADOPTING.md) for a longer walkthrough,
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the system contract, and
[`CHANGELOG.md`](CHANGELOG.md) for release scope.

## Status

`v0.1.0`. First public release.

Known v0.2 work, surfaced as a backlog by the engine-contract audit:

- Role-based dispatch in `runtime/index/classify.py` and
  `runtime/index/linker.py` (currently still hardcoded against literal space
  names; helpers exist via `cfg.space_by_role()`).
- Schema overlays declare `spaces: [...]` literals instead of `roles: [...]`.
- L2 (hallucination) automated lint, vector search, real auth/claims
  enforcement, mobile capture endpoint — see `CHANGELOG.md`.

## License

MIT. See [`LICENSE`](LICENSE).
