# gorae cleanup — operator checklist (PR-13 / PR-15 / PR-17)

After atelier v0.2 lands, every proto-engine capability now lives in
atelier. The gorae content repo no longer needs its `scripts/` Python
modules, the `gorae` bash dispatcher beyond its Next.js dev helpers, or
the v3 schema artifacts.

Follow this checklist after you have:

1. Run `python -m scripts.migrate_schema_v3_to_v4.migrate --apply`
   and committed the resulting diff in the gorae repo.
2. Run `python -m scripts.absorb_workshop.absorb --apply` and
   committed the new `workshop/` subtree in the gorae repo.
3. Run `python -m scripts.absorb_workshop_memory_to_learnings.absorb
   --apply` and committed the new `learnings/accepted/` subtrees.
4. Verified `atelier validate` reports zero failures.

## A) Remove proto-engine Python (PR-8/9/10/11/12/13/14/15/16)

Each `gorae <cmd>` is now an atelier MCP tool. Delete the corresponding
file in the gorae repo:

| gorae file                                  | atelier tool                |
|---------------------------------------------|-----------------------------|
| `scripts/validate_metadata.py`              | `atelier_validate`          |
| `scripts/fix_pending_entries.py`            | `atelier_fix_pending`       |
| `scripts/update_wiki_index.py`              | `atelier_index_regen`       |
| `scripts/prepare.py`                        | `atelier_prepare_commit`    |
| `scripts/pre_commit_update.py`              | (folded into prepare_commit; LLM facets reclass deferred) |
| `scripts/clip_images.py`                    | `atelier_clip_image`        |
| `scripts/r2_upload.py`                      | (folded into clip + runtime.sync.adapters.r2) |
| `scripts/sync_r2_local.py`                  | `atelier_sync`              |
| `scripts/create_document.py`                | `atelier_new_doc`           |
| `scripts/delete_document.py`                | `atelier_delete_doc` (v0.1 path)  |
| `scripts/wiki_lint.py`                      | `atelier_lint`              |
| `scripts/ingest_youtube.py`                 | `atelier_youtube`           |
| `scripts/utils.py`                          | (deleted last — no callers) |

```bash
cd ~/Documents/gorae

# DELETE in one go:
rm -f scripts/{validate_metadata,fix_pending_entries,update_wiki_index, \
              prepare,pre_commit_update,clip_images,r2_upload,sync_r2_local, \
              create_document,delete_document,wiki_lint,ingest_youtube,utils}.py
rm -rf scripts/__pycache__

git status      # review
git add -u scripts/
git commit -m "drop proto-engine scripts — atelier owns these now"
```

After commit, verify:

```bash
find scripts -name '*.py' | wc -l       # expect: 0
```

## B) Trim the `gorae` bash dispatcher (PR-17)

The bash file currently routes both content-coupled (Next.js dev) and
engine commands (validate / youtube / etc.). Keep only the dev helpers:

```bash
# Edit `gorae` so the case statement contains only:
#   dev|dev:blog|dev:admin|dev:logs
# Remove every other subcommand block (new, validate, lint, reindex,
# prepare, sync-assets, fix-pending, clip-images, youtube, delete).
```

## C) Update `.claude/skills/` markdown (PR-17)

Every `gorae <cmd>` mention in `.claude/skills/**/*.md` becomes an
`atelier_*` MCP tool name:

```bash
grep -rn "gorae validate\|gorae lint\|gorae reindex\|gorae youtube\| \
          gorae clip-images\|gorae fix-pending\|gorae prepare\| \
          gorae sync-assets\|gorae new\|gorae delete" .claude/skills/
```

For each hit, rewrite to call the matching atelier MCP tool. Examples:

- `gorae youtube <url>` → `atelier_youtube(url="<url>")`
- `gorae validate` → `atelier_validate()`
- `gorae prepare --dry-run` → `atelier_prepare_commit(dry_run=true)`

## D) Replace `.git/hooks/pre-commit` (PR-11)

```bash
cat > .git/hooks/pre-commit <<'SH'
#!/usr/bin/env bash
# Routes through atelier's prepare_commit. Atelier serve must be running.
atelier-mcp-call atelier_prepare_commit \
    --json "$(git diff --cached --name-only -z | python3 -c \
        'import sys,json,os;
paths=[p for p in sys.stdin.read().split(chr(0)) if p.endswith(\".md\")];
print(json.dumps({\"paths\": paths, \"dry_run\": False}))')" >/dev/null
SH
chmod +x .git/hooks/pre-commit
```

## E) Archive `atelier-workshop` (after PR-7 + PR-21 apply)

```bash
cd ~/workspaces/atelier-workshop
cat > ARCHIVED.md <<EOF
This repo's content has been absorbed into the gorae vault as of
$(date -u +%Y-%m-%d).

- products/, notes/, logs/  → gorae/workshop/
- products/*/memory/        → gorae/learnings/by-project/<n>/
- products/*/profile.local.yaml → ~/.atelier/profiles/<n>.yaml

This repo is now read-only.
EOF
git add ARCHIVED.md
git commit -m "archive: absorbed into gorae"
gh repo edit --archived           # or set on github.com/<user>/<repo>/settings
```

Then in `~/.atelier/config.yaml` remove the legacy `spaces:` block and
use the `vault:` + `subtrees:` blocks (see `config/example.config.yaml`).

## F) Smoke checks

```bash
# 1) atelier serve runs.
atelier serve --http &
sleep 2

# 2) Claude Code attaches and a search works.
# (run `claude` in any directory; ask "use atelier_search to find 'foo'")

# 3) PII guard still passes on atelier itself.
cd ~/workspaces/atelier && pytest -q

# 4) Validate the migrated vault has zero failures.
atelier validate
```

You're done. The proto-engine is fully absorbed.
