"""A node census for the RFC 0006 verification baseline — counts of graph nodes
bucketed by their class-specific routing fields.

Hard rule #4 (*markdown is truth; the DB is a projection*): the census is a fast
aggregate over `pages.frontmatter`, exactly like `projection_counts`. It answers
"how is the graph composed?" — the denominators a lens (RFC 0006 ③) and a
consolidation pass (④a) reason about.

**Partitioned by `kind`, not flat.** The routing fields are class-specific:
`ac_status`/`surfacing` exist only on claims; `domain` is an enforced enum on
sources/entities (`in_scheme` on entities) but a free-string (`operational`) on
claims (RFC 0006 §5, `graph.overlay.yaml`). A flat "counts by
domain/kind/ac_status/surfacing" would produce mostly-null buckets and make the
parity assertion ill-defined. So the shape is `{kind: {field: {value: count}}}`.

Both paths — the DB projection and the filesystem fallback — feed the SAME tally
(`_tally`), so they can never disagree on *how* a node is counted; only the node
*population* differs (projection = last reindex; fallback = live disk). The
parity test locks those equal after a reindex, matching the discipline in
`projection_counts` / `tests/test_projection_counts.py`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ...index import parse as _parse
from ...util import db as _db

# Which frontmatter fields we tally, per node kind. Missing values bucket under
# `_ABSENT` so projection and filesystem agree even when a field is unset.
_FIELDS_BY_KIND: Dict[str, Tuple[str, ...]] = {
    "claim": ("domain", "ac_status", "surfacing"),
    "source": ("domain",),
    "entity": ("in_scheme",),
}
_ABSENT = "(absent)"


def _tally(rows: Iterable[Tuple[str, Dict[str, Any]]]) -> Dict[str, Dict[str, Dict[str, int]]]:
    """`(kind, frontmatter)` pairs → `{kind: {field: {value: count}}}`.

    The ONE place a node is turned into counts, shared by both data sources so
    they cannot drift. Values are stringified; an unset field counts as
    `(absent)` rather than being dropped."""
    out: Dict[str, Dict[str, Dict[str, int]]] = {}
    for kind, fm in rows:
        fields = _FIELDS_BY_KIND.get(kind)
        if fields is None:
            continue
        bucket = out.setdefault(kind, {f: {} for f in fields})
        for f in fields:
            raw = fm.get(f)
            value = _ABSENT if raw is None or raw == "" else str(raw)
            bucket[f][value] = bucket[f].get(value, 0) + 1
    return out


def _projection_rows() -> Optional[List[Tuple[str, Dict[str, Any]]]]:
    """`(kind, frontmatter)` from the projection, or None when it cannot answer
    (DB absent/empty/query error) so the caller falls back to disk. Mirrors
    `projection_counts._load_nodes`: one indexed query, JSON already parsed."""
    try:
        conn = _db.connect()
    except Exception:                            # pragma: no cover - defensive
        return None
    try:
        db_rows = _db.fetchall(
            conn,
            "SELECT page_type, frontmatter FROM pages "
            "WHERE page_type IN ('claim','source','entity')",
        )
    except Exception:                            # pragma: no cover - defensive
        return None
    finally:
        conn.close()
    if not db_rows:
        return None                              # cold/un-reindexed DB → fall back
    rows: List[Tuple[str, Dict[str, Any]]] = []
    for r in db_rows:
        try:
            fm = json.loads(r["frontmatter"])
        except Exception:                        # pragma: no cover - tolerant
            continue
        if isinstance(fm, dict):
            rows.append((r["page_type"], fm))
    return rows


def _fs_rows(vault: Path) -> List[Tuple[str, Dict[str, Any]]]:
    """`(kind, frontmatter)` read straight from disk — the fallback and the parity
    oracle. Buckets on the `kind` FIELD (the same discriminator `classify` turns
    into `page_type`), so a node is counted wherever its file physically lives
    (claims/entities share `graph/atomic/`; sources sit elsewhere). Parse failures
    are skipped, never fatal — a census must not crash on one bad file."""
    rows: List[Tuple[str, Dict[str, Any]]] = []
    if not vault.exists():
        return rows
    for p in sorted(vault.rglob("*.md")):
        if p.name == "INDEX.md" or p.name.upper() == "MEMORY.MD":
            continue
        try:
            fm, _ = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        except Exception:                        # pragma: no cover - tolerant
            continue
        if not isinstance(fm, dict):
            continue
        kind = fm.get("kind")
        if kind in _FIELDS_BY_KIND:
            rows.append((kind, fm))
    return rows


def census(vault: Optional[Path] = None) -> Dict[str, Dict[str, Dict[str, int]]]:
    """The node census, projection-first with a filesystem fallback.

    Warm DB → counts reflect the last `reindex`; cold DB → counts read live disk.
    Both go through `_tally`, so the two paths agree by construction on a
    reindexed vault (locked by the parity test)."""
    rows = _projection_rows()
    if rows is None:
        from . import cluster as _cl        # local import: _vault_root lives there
        rows = _fs_rows(vault if vault is not None else Path(_cl._vault_root()))
    return _tally(rows)
