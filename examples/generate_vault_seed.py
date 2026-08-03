#!/usr/bin/env python3
"""Regenerate examples/vault-seed/ deterministically.

The seed's entry_ids are the engine's OWN content-addressed templates
(runtime.structure.resolver.entry_id), so this script is thin: it authors the
synthetic content and lets the engine mint every id. Idempotent — rerunning on
an unchanged script yields byte-identical files. The suite pins conformance
(tests/test_vault_seed.py), so editing seed content here and rerunning is the
only supported way to change the seed."""
import hashlib
import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from runtime.service.learnings.claims_io import _content_hash  # noqa: E402
from runtime.structure import resolver as R  # noqa: E402


def _body_hash(body: str) -> str:
    """Source content_hash: the engine hashes the BODY (cf. youtube.py ingest)."""
    return "sha256:" + hashlib.sha256(body.strip().encode("utf-8")).hexdigest()

ROOT = pathlib.Path(__file__).resolve().parent / "vault-seed"
C = "2026-07-01T09:00:00Z"


def page(rel, fm, body):
    p = ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True)
                 + "---\n\n" + body.strip() + "\n", encoding="utf-8")


def src(rel, title, dom, sens, attributed, body, created=C):
    ch = _body_hash(body)
    eid = R.entry_id("source", created_at=created, discriminator=ch)
    page(rel, {"entry_id": eid, "schema_version": 7, "kind": "source",
               "created_at": created, "content_hash": ch, "title": title,
               "sensitivity": sens, "domain": dom, "attributed_to": attributed},
         body)
    return eid


def ent(slug, label, typ, scheme, sens, body):
    eid = R.entry_id("entity", type=typ, pref_label=label)
    fm = {"entry_id": eid, "schema_version": 7, "kind": "entity",
          "created_at": C, "sensitivity": sens,
          "pref_label": label, "type": typ, "in_scheme": scheme,
          # basename is seed-ent-<slug>, so bare [[pref_label]] wikilinks in
          # claim bodies resolve through the alias index, not the basename
          "aliases": [label]}
    fm["content_hash"] = _content_hash(fm)
    page(f"graph/atomic/seed-ent-{slug}.md", fm, body)
    return eid


def clm(name, statement, dom, surf, sens, derived, about, attributed,
        generated, extra=None, body=""):
    eid = R.entry_id("claim", statement=statement,
                     derived_from="|".join(sorted(derived)))
    fm = {"entry_id": eid, "schema_version": 7, "kind": "claim", "created_at": C,
          "statement": statement,
          "is_about": about, "derived_from": derived,
          "attributed_to": attributed, "generated_by": generated,
          "surfacing": surf, "domain": dom, "sensitivity": sens}
    if extra:
        fm.update(extra)
    fm["content_hash"] = _content_hash(fm)
    page(f"graph/atomic/seed-clm-{name}.md", fm, body or statement)
    return eid


def main() -> None:
    SQLITE = src("raw/knowledge/sqlite-wal-notes.md", "Notes: SQLite WAL mode",
        "knowledge", "public", "홍길동", """# Notes: SQLite WAL mode

Write-Ahead Logging appends changes to a `-wal` sidecar instead of rewriting
pages in place. Readers keep reading the main file while a writer appends, so
readers never block writers. A **checkpoint** folds the WAL back into the main
database file.

One operational consequence: backing up a WAL-mode database by copying only the
`.db` file silently loses every un-checkpointed transaction — copy the `-wal`
and `-shm` sidecars too, or run `VACUUM INTO`.""")

    src("raw/knowledge/soil-basics.md", "Notes: soil for container gardening",
        "knowledge", "public", "홍길동", """# Notes: soil for container gardening

Container soil is not garden dirt: it needs far more drainage. A common mix is
roughly equal parts compost, coco coir, and perlite. Overwatering kills more
container plants than underwatering — the symptom (drooping) looks identical,
so check the soil before reaching for the watering can.

*(No Claim derives from this Source yet — it is one of the two notes that keep
the `atomize` nudge alive in this seed. Atomize it and watch the count drop.)*""")

    DIARY = src("raw/personal/diary/2026/06/21.md", "Diary 2026-06-21",
        "personal", "private", "홍길동", """Rained all afternoon. Repotted the
balcony tomatoes with Gildong — the new soil mix drains much better. Note to
self: stop watering on a schedule and start checking the soil first.""",
        created="2026-06-21T21:30:00Z")

    src("raw/inbox/2026-07-01-clip-to-triage.md", "Capture: drip irrigation clip",
        "inbox", "public", "홍길동", """A quick capture that has not been triaged
yet. `atelier nudges` counts **Source nodes** (files with `kind: source`) that
no Claim `derived_from` — this one and the soil note are the two keeping the
atomize nudge due in this seed.""")

    SESSION = src("raw/operational/2026-07-01-gardening-session.md",
        "Session: balcony gardening retro", "operational", "public", "claude-code",
        """The thin session Source the two minted learnings below derive from —
mint never leaves a Claim without provenance (PROV-O `wasDerivedFrom`).""")

    E_SQLITE = ent("sqlite", "SQLite", "Tool", ["knowledge"], "public",
        "The embedded database this seed's knowledge notes are about.")
    E_GILDONG = ent("gildong", "홍길동", "Person", ["personal"], "private",
        "A fictional person for the demo — the diary's gardening companion.")

    clm("wal-readers",
        "In SQLite WAL mode readers never block writers: writes append to the -wal sidecar while readers keep using the main file.",
        "knowledge", "query", "public", [SQLITE], [E_SQLITE], "홍길동", "atomize",
        body="In [[SQLite]] WAL mode readers never block writers: writes append to the `-wal` sidecar while readers keep using the main file.")

    C_BACKUP = clm("wal-backup",
        "Copying only the .db file of a WAL-mode SQLite database silently loses un-checkpointed transactions — copy the sidecars or use VACUUM INTO.",
        "knowledge", "proactive", "public", [SQLITE], [E_SQLITE], "홍길동", "atomize",
        body="Copying only the `.db` file of a WAL-mode [[SQLite]] database silently loses un-checkpointed transactions — copy the sidecars or use `VACUUM INTO`.")

    clm("backup-principle",
        "Verify a backup by restoring it, not by checking that the copy exists.",
        "operational", "always", "public", [C_BACKUP], [], "atelier-dream", "dream",
        {"links": [{"to": C_BACKUP, "rel": "refines", "why": "generalized by dream"}]},
        body="Verify a backup by restoring it, not by checking that the copy exists.\n\nGeneralized from the WAL-sidecar backup claim this refines.")

    clm("tomatoes",
        "The balcony tomatoes were repotted into a faster-draining mix on 2026-06-21 with Gildong.",
        "personal", "query", "private", [DIARY], [E_GILDONG], "홍길동", "atomize",
        body="The balcony tomatoes were repotted into a faster-draining mix on 2026-06-21 with [[홍길동]].")

    clm("learning-passed",
        "Check the soil before watering: schedule-based watering was killing the container plants.",
        "operational", "proactive", "public", [SESSION], [], "claude-code", "mint",
        {"ac_status": "passed"},
        body="""Check the soil before watering.

**Why:** overwatering and underwatering droop identically, so the schedule was
optimizing the wrong signal.
**How to apply:** finger-test the top 3 cm of soil; water only if dry.""")

    clm("learning-pending",
        "Label seed trays immediately — two unlabeled trays became indistinguishable within a week.",
        "operational", "query", "public", [SESSION], [], "claude-code", "mint",
        {"ac_status": "pending"},
        body="""Label seed trays at sowing time.

**Why:** memory of which tray is which decays faster than germination takes.""")

    page("workshop/products/demo-widget/README.md",
         {"schema_version": 4,
          "entry_id": R.entry_id("product", name="demo-widget"),
          "sensitivity": "public", "created_at": C},
         """# demo-widget

A placeholder product so the builder territory is not empty. Product working
memory (decisions, TODO state) lives beside the code it describes.""")

    print("seed regenerated:", sum(1 for _ in ROOT.rglob("*.md")), "content pages")


if __name__ == "__main__":
    main()
