# atelier — session entry note

This repo is the **public methodology layer** of a sovereign memory
system. It contains schemas, agent persona contracts, and runtime
tooling. Content (the actual user IP) lives in separate private repos.

## Hard rules

1. **No PII in repo *content*.** No user names, personal place names, or
   user-specific cultural keywords in schemas, code, docs, or commit
   message bodies. User-private voice and naming live in
   `~/.atelier/voices/*.md`, out of tree.
   - **Commit author identity is an explicit exception.** The maintainer
     identity for atelier is **`gorae <kyuhyunhaan@gmail.com>`** — the
     maintainer has deliberately opted in to having this email on the
     public commit history (it links the work to their GitHub
     contribution graph). The `gorae` name is the project/role identity;
     the email is the maintainer's personal address by choice. Do not
     "fix" this to a noreply address.
   - This applies to *commit message bodies*: do not narrate adopter-specific
     paths, repo names, or product names in commit messages of the public
     engine.
2. **Culture-neutral docs and agents.** Persona contracts describe
   responsibilities and a voice *contract* — never voice content.
   Cultural overlay (tone, language, domain keywords) is loaded at
   runtime from `~/.atelier/voices/`.
3. **Schema is data, not prose.** Schema rules live in
   `schema/data/*.yaml` and `schema/db/sql/*.sql`. Runtime is
   schema-driven; do not hard-code schema decisions in code.
4. **Markdown is truth; DB is projection.** SQLite at
   `~/.atelier/cache/atelier.db` is *derived* from content repos. Direct
   DB edits by any caller (human, LLM, script) are forbidden. All
   changes flow through markdown → `atelier reindex`.
5. **Single writer per space.** Librarian writes the wiki; builder
   writes the workshop. Other ops require explicit claims and may be
   `PROTECTED` (cost-bearing or invariant-breaking).
6. **Mobile is reserved, not built.** Schema fields, capture function,
   claim enums for mobile exist but remain inactive in `v0.1`.

## Source-of-truth pointers

| Need | Path |
|---|---|
| Release scope | `CHANGELOG.md` |
| Architecture overview | `docs/ARCHITECTURE.md` |
| Schema spec (human) | `docs/SCHEMA_V4.md` |
| Implementation history (archival) | `docs/_archive/IMPLEMENTATION_LOG.md` |
| Schema spec (data) | `schema/data/*.yaml` |
| DB schema | `schema/db/sql/*.sql` |
| Agent contracts | `agents/librarian.md`, `agents/builder.md` |
| User config template | `config/example.config.yaml` |

## Local config (out of tree)

Per-machine config lives at `~/.atelier/config.yaml`. Voice overlays at
`~/.atelier/voices/{librarian,builder}.md`. PII patterns for the
pre-commit guard at `~/.atelier/pii_patterns.txt`. Secrets at
`~/.atelier/secrets/.env`. None of these are tracked by atelier.

## Running setup

```bash
./scripts/setup
```

Installs the pre-commit PII guard into `.git/hooks/pre-commit` and
verifies that `~/.atelier/` exists.
