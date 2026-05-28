# absorb-workshop

One-shot migrator that folds the builder-territory workshop into the
librarian-territory vault. Use this once when collapsing the two-repo
model into a single vault.

## What it moves

| Source (builder root)                       | Destination                              |
|---------------------------------------------|------------------------------------------|
| `products/<name>/`                          | `<vault>/workshop/products/<name>/`      |
| `products/<n>/profile.local.yaml`           | `~/.atelier/profiles/<n>.yaml`           |
| `notes/`                                    | `<vault>/workshop/notes/`                |
| `logs/`                                     | `<vault>/workshop/log.md` (consolidated) |

The `memory/` subtree under each product is **left alone** by this
script — PR-21 (`absorb_workshop_memory_to_learnings`) absorbs those
into `learnings/by-project/<n>/`.

## Safety

- Dry-run is the default. `--apply` must be explicit.
- Refuses to overwrite an existing destination — conflicts are listed,
  not auto-resolved.
- Refuses to apply if either git tree is dirty.
- Never commits. Operator reviews diffs in the librarian repo and
  commits there.

## Usage

```bash
python -m scripts.absorb_workshop.absorb              # dry-run
python -m scripts.absorb_workshop.absorb --apply      # actually copy

# Override the profiles destination (rarely needed):
python -m scripts.absorb_workshop.absorb --apply --profiles-dir ~/.config/atelier/profiles
```

After applying:

1. Inspect the librarian repo's diff (`git status`, `git diff`).
2. Commit the new `workshop/` subtree.
3. Run PR-21's memory-to-learnings absorber if desired.
4. Archive the old builder repo (`ARCHIVED.md` commit + GitHub `archived: true`).
