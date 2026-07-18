# Adopting atelier

atelier is a methodology layer for a sovereign personal memory system: a
two-steward agent runtime (Librarian + Builder) that operates over your own
content repositories with a derived SQLite index. Markdown is the truth; the
DB is a projection.

If you want to run atelier on your own content, this document is the recipe.
Once it's running, day-to-day use is three verbs — see `docs/USING.md`.

---

## What you need

- Two content repositories (or directories) you own:
  - **gorae-like space** for personal/knowledge content (raw + wiki)
  - **workshop-like space** for products and build notes
- Python 3.11+
- git
- (Optional) Cloudflare R2 for embedded assets — v0.1 has the slot but a stub

---

## Install

```bash
git clone https://github.com/<your-username>/atelier ~/workspaces/atelier
cd ~/workspaces/atelier
python3 -m venv .venv
.venv/bin/pip install -e .
./scripts/setup            # installs pre-commit PII hook, verifies ~/.atelier/
```

The setup script tells you to create `~/.atelier/`:

```bash
mkdir -p ~/.atelier/{cache,voices,secrets}
cp config/example.config.yaml ~/.atelier/config.yaml
touch ~/.atelier/pii_patterns.txt
touch ~/.atelier/voices/{librarian,builder}.md
```

Edit `~/.atelier/config.yaml` to point at your two spaces. The example file
is annotated.

---

## Day-one workflow

```bash
atelier setup                                 # verify config + apply DB schema
atelier reindex --space gorae --full          # index your provenance/ + graph/
atelier reindex --space workshop --full       # index your products
atelier doctor                                # confirm all six checks green
atelier lint --space gorae --show 20          # see what needs cleanup
```

If `atelier doctor` shows anything other than six ✓, fix that before doing
anything else. Most issues at this stage are config typos or missing voice
overlays.

---

## Concepts to internalize

- **Three layers**: methodology (this repo, public) / content (your private
  repos) / local cache (`~/.atelier/`, gitignored). See `docs/ARCHITECTURE.md`.
- **Two stewards**: Librarian (writes wiki, reads raw) and Builder (writes
  workshop). See `agents/{librarian,builder}.md`.
- **Single-writer per space** is the integrity invariant. Don't break it.
- **Schema is data**: see `schema/data/*.yaml`. Adjust if your content needs
  different page types; the runtime reads these at startup.

---

## Customizing

| Thing to customize | Where |
|---|---|
| Page types | `schema/data/{gorae,workshop,learnings}.overlay.yaml` |
| Lint rules | `schema/data/lint.yaml` (severity, automation, queries) |
| Voice (per-user) | `~/.atelier/voices/{librarian,builder}.md` (out of tree) |
| PII guard regexes | `~/.atelier/pii_patterns.txt` (out of tree) |
| Storage backends | `runtime/sync/adapters/` (add your own) |

---

## What atelier is NOT

- It is not an inference engine. It does not call LLMs in v0.1 except where
  you (the agent runtime) call them. doctor's `--max-usd N` flag is a placeholder.
- It is not a web app. There is no server in v0.1 — service-shape exists for
  v0.2's MCP/HTTPS surfaces.
- It is not a turnkey "personal AI". It is the substrate one would build on.

---

## v0.1 limitations (read before reporting an issue)

See `docs/PLAN_v0.1.md` § I for the canonical out-of-scope list. The big ones:

- No mobile capture (scaffolding only).
- No vector search (FTS5 keyword only).
- No automated L2 (hallucination) lint.
- No real trust-boundary enforcement.

These are all v0.2/v0.3 targets, not bugs.
