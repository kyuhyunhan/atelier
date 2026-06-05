# Builder — Role Contract

The Builder is the steward of the **builder-territory** space: a working
repository of products, build notes, logs, and product-specific decisions.

> **Note on naming.** Throughout this contract the example space name `workshop`
> appears for illustration. In a real installation, `workshop` is whatever name
> the user gives to their builder-territory space in `~/.atelier/config.yaml`
> — atelier itself is agnostic to that name and resolves it via `role:`.

This document is the **culture-neutral contract**. It is *not* a runtime
agent — the engine never loads or executes it. The only runtime teeth behind
"Builder" are the single-writer locks in `runtime/service/claims.py` (the
`builder-write` role): they enforce who may write the builder-territory
regardless of this file. Everything below is a **role contract** for whoever
fills the role — a human, a Claude session, or a user-authored skill.

The user-private voice overlay at `~/.atelier/voices/builder.md` is a
**reserved convention**: its presence is checked by `atelier doctor` (D3),
but the engine does not auto-load it. Adopting the voice is the caller's
choice, not an automatic behavior.

---

## Identity

- **Role**: Product builder and shipping-oriented thinker.
- **Territory**: the space configured with `role: builder-territory` in
  `~/.atelier/config.yaml`. The engine resolves this at runtime; no
  specific path is wired into the contract.
- **Authority**: Single writer of all of the builder-territory space.
- **Posture**: TDD-native, declarative, ships small reversible increments.
  Prefers to log decisions in-product (ADRs, retros) rather than scattering
  them across notes.

---

## Invariants

The Builder MUST:

1. **One product = one subdirectory.** `workshop/products/{name}/` is the unit
   of containment. README.md is the product's contract; all other pages live
   beneath it.
2. **Decision records are first-class.** Non-trivial choices land in
   `products/{name}/adr/NNNN-{slug}.md`. Retros land in
   `products/{name}/retro/`.
3. **Every product has a status.** `active | paused | archived | shipped`.
   Status changes are logged in `wiki/log.md`-style append to the product's
   own log.
4. **Cross-reference, don't duplicate.** When a product depends on knowledge
   that already lives in gorae wiki, cite via `[[gorae:...]]` — never copy
   the knowledge into workshop.
5. **Date everything.** `created`, `updated` on every page. `date` on log
   entries.
6. **Respect single-writer.** The Builder does not write to `gorae/**`.
   Cross-space promotion goes through `atelier promote propose`.

The Builder MUST NOT:

- Create products without a README.md.
- Leave a product without a status.
- Mix unrelated products in a single subdirectory.
- Write personal/diary content (that belongs in gorae raw).

---

## Inputs

- `workshop/**/*.md` — read-write working set.
- `gorae/**/*.md` — read-only knowledge reference via cross-space links.
- `~/.atelier/cache/atelier.db` — derived index (read-only from agent's view).
- `~/.atelier/voices/builder.md` — voice overlay.

---

## Outputs

| Output | Path | Frequency |
|---|---|---|
| Product README | `products/{name}/README.md` | Once per product |
| Spec | `products/{name}/spec/{slug}.md` | Per spec |
| ADR | `products/{name}/adr/NNNN-{slug}.md` | Per decision |
| Retro | `products/{name}/retro/{date}.md` | Per retro |
| Build log | `logs/{YYYY-MM-DD}.md` | Per work session |
| Standalone note | `notes/{slug}.md` | As needed |

---

## Operations

The Builder implements 4 operations:

| Operation | Trigger | Output |
|---|---|---|
| **new-product** | `atelier new-product <name>` | scaffolds `products/{name}/` with README, status=active |
| **log** | "오늘 한 일 기록해줘" / "log what I did today" | `logs/{date}.md` append |
| **adr** | "이 결정을 기록해줘" / "record this decision" | `products/{name}/adr/` page |
| **retro** | "이 제품 회고해줘" / "retro this product" | `products/{name}/retro/{date}.md` |

All conclude with: `atelier reindex --space workshop` → `git commit`.

---

## Tools (CLI surface)

```
atelier new-product <name>
atelier reindex --space workshop [--incremental | --full]
atelier search "<query>" --space workshop [--mode keyword|graph]
atelier links <slug> [--inbound | --outbound]
atelier list --type {product|note|log} --space workshop
atelier lint --space workshop
atelier sync pull|push|status --space workshop
atelier promote propose  # offers a workshop→wiki promotion proposal
```

For writes, the Builder uses normal file I/O on `workshop/**`. Direct DB
writes are forbidden.

---

## Promotion to wiki

When a workshop artifact (typically a retro or ADR) yields knowledge that
belongs in the personal wiki, the Builder does NOT copy it. Instead:

1. `atelier promote propose` — generates a proposal document at
   `~/.atelier/cache/promotions/{ts}-{slug}.md` describing what would move
   to which gorae wiki page type.
2. User reviews and edits the proposal.
3. `atelier promote apply` — Librarian (not Builder) writes the wiki page.
4. The original workshop page gains a `[[gorae:wiki/...]]` backlink.

This preserves single-writer-per-space.

---

## Cross-space behavior

- The Builder **reads** `gorae/**` to ground product decisions in user knowledge.
- The Builder **does not write** to gorae.
- When citing gorae content in a product page, prefer linking the most stable
  page type (theme > entity > synthesis > source). Digests are too volatile
  to link from product specs.

---

## Voice overlay

Persona tone, language mix, and code style preferences load from
`~/.atelier/voices/builder.md`. If missing, the Builder falls back to neutral
English with no language bias.
