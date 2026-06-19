"""Entity-stub backfill (RFC 0003 P3) — connect the orphan island, deterministically.

Learnings already emit `link_type='concept'` edges from their `touches`/`target_topic`
(reindex._concept_targets), but those edges DANGLE: ~99% have no entity page to resolve
to, so relational retrieval is dead for learnings. This tool materializes the missing
nodes: for every distinct learning concept not already resolvable to an entity, it
creates a stub `graph/entities/{slug}.md` carrying the concept string as an `alias`.
After a reindex, the existing concept edges bind via the alias index (reindex._resolve's
`_norm(concept)` fallback) — with ZERO changes to any learning file.

DETERMINISM is the contract:
- The concept set is the indexed `learning_facets` (kind touches/topic) — already
  deduped + lowercased, the same values reindex resolves against.
- "Already resolvable" mirrors reindex's resolution EXACTLY (`_norm` of an entity's
  basename or any of its aliases), so we never create a stub a real entity already covers.
- create-if-missing: running twice creates nothing new (idempotent file set). The
  one-shot LLM alias-merge (GP3b) is a SEPARATE, reviewed step — this tool is pure.

This is a tool, NOT part of reindex: reindex stays a read-only projection (it must
never author markdown). The backfill's committed output (the stub files) is the artifact.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from ...index import reindex as _reindex   # reuse _norm so resolution can't drift
from ...structure import resolver as _structure


def _slugify(concept: str) -> str:
    """concept string → entity-file basename. Lowercase, separators→hyphen, keep
    word chars (incl. CJK). Deterministic and stable."""
    s = concept.strip().lower()
    s = re.sub(r"[\s_/]+", "-", s)
    s = re.sub(r"[^\w\-]", "", s, flags=re.UNICODE)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "concept"


@dataclass(frozen=True)
class StubPlan:
    concept: str          # the original (normalized) concept string
    slug: str             # entity basename, slugify(concept)
    rel_path: str         # vault-relative path, graph/entities/{slug}.md


def _resolvable_norms(conn: sqlite3.Connection) -> set:
    """The set of `_norm(...)` keys an existing entity already answers — its
    basename plus every alias. Mirrors reindex._build_alias_index's key set so a
    concept this tool would 'connect' is never one a real entity already covers."""
    import json
    keys: set = set()
    for r in conn.execute("SELECT canonical_slug, aliases FROM entities"):
        base = r["canonical_slug"].split("/")[-1].rsplit(".", 1)[0]
        keys.add(_reindex._norm(base))
        try:
            for a in json.loads(r["aliases"] or "[]"):
                if isinstance(a, str) and a.strip():
                    keys.add(_reindex._norm(a))
        except (TypeError, ValueError):
            pass
    return keys


def plan_stubs(conn: sqlite3.Connection) -> List[StubPlan]:
    """Distinct learning concepts (touches+topic) not already resolvable → one
    StubPlan each, ordered for determinism. Concepts that collapse to the same
    slug yield one plan (first concept string wins as the alias seed)."""
    resolvable = _resolvable_norms(conn)
    concepts = [r["value"] for r in conn.execute(
        "SELECT DISTINCT value FROM learning_facets "
        "WHERE kind IN ('touches','topic') AND value <> '' ORDER BY value")]
    plans: List[StubPlan] = []
    seen_slug: set = set()
    for c in concepts:
        if _reindex._norm(c) in resolvable:
            continue
        slug = _slugify(c)
        if slug in seen_slug:
            continue
        seen_slug.add(slug)
        plans.append(StubPlan(concept=c, slug=slug,
                              rel_path=f"{_structure.home('graph_entity')}/{slug}.md"))
    return plans


def _stub_markdown(concept: str, created: str) -> str:
    fm = {
        "title": concept,
        "type": "entity",
        "category": "concept",
        "subtype": "concept",            # RFC 0003 entity subtype
        "first_mention": None,
        "source_count": 0,
        "created": created,
        "updated": created,
        "schema_version": 5,
        "provenance": "knowledge",
        "sensitivity": "public",
        "aliases": [concept],            # binds the dangling concept edge via _norm
        "stub": True,                    # marks an auto-created, un-curated node
    }
    body = (f"# {concept}\n\n"
            "_Auto-created stub to anchor concept links (RFC 0003 P3). "
            "Expand when curated._\n")
    return "---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + "---\n" + body


def backfill(conn: sqlite3.Connection, *, vault: Path, created: str,
             plans: Optional[List[StubPlan]] = None) -> Dict[str, object]:
    """Create a stub file per plan, if missing. Returns stats + the created paths.

    `created` is injected (caller passes a date) so the tool itself is free of
    wall-clock — two runs on the same vault produce the identical file set, and a
    file that already exists is left byte-untouched (create-if-missing)."""
    plans = plan_stubs(conn) if plans is None else plans
    created_paths: List[str] = []
    skipped = 0
    for p in plans:
        path = vault / p.rel_path
        if path.exists():
            skipped += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_stub_markdown(p.concept, created), encoding="utf-8")
        created_paths.append(p.rel_path)
    return {"planned": len(plans), "created": len(created_paths),
            "skipped": skipped, "paths": created_paths}
