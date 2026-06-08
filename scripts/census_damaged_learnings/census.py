"""P0 census — the authoritative set of learnings damaged by the workshop absorb.

RFC 0001 §2.2: the workshop→learnings absorb mapped each note's project-local
memory subdirectory onto the GLOBAL `target_topic`, collapsing every project's
`cross-cutting` (etc.) into one meaningless global bucket. P6 (repair) must move
that flattened value back into `aspect[]` and recover `also_in`. This census is
the input P6 consumes — it must be produced BEFORE anything mutates, so a
legitimately-global `target_topic` can be told apart from a flattened layer.

The damage signal is `agent_kind == "absorbed"` — EVERY absorbed record got its
`target_topic` from a project-local source, so this is authoritative. A naive
"target_topic is one of the four canonical lexio layers" heuristic undercounts
(observed live: 85 vs the true 100 absorbed), because lexio's memory had more
subdirectories than the four canonical layers. We therefore key on the absorb
provenance and use the canonical-layer-token test only to grade CONFIDENCE.

Read-only. Scans the by-topic canonical tree (never the by-project mirror, to
avoid double-counting). Emits JSON.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

from runtime.index import parse as _parse
from runtime.util import config as _config


# The four canonical lexio layers (memory/TAXONOMY.md). A flattened
# `target_topic` matching one of these is high-confidence damage.
CANONICAL_LAYER_TOKENS = frozenset({"client", "server", "cross-cutting", "product"})

ABSORB_SIGNAL = "absorbed"   # frontmatter agent_kind written by the absorb script


def _resolve_vault(cfg: _config.Config) -> Path:
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _as_list(value) -> List[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _record(path: Path, vault: Path, fm: Dict) -> Dict:
    """Build one census record with the data P6 needs to repair in place."""
    target_topic = fm.get("target_topic")
    layer = fm.get("layer")                      # lexio dialect: primary aspect
    also_in = _as_list(fm.get("also_in"))        # lexio dialect: secondary aspects
    # Primary aspect to restore: the explicit `layer` if the note still carries
    # it, else the value that was flattened into `target_topic`.
    recoverable_primary = layer if isinstance(layer, str) and layer else target_topic
    topic_is_layer_token = target_topic in CANONICAL_LAYER_TOKENS
    return {
        "entry_id": fm.get("entry_id"),
        "path": str(path.relative_to(vault)),
        "target_project": fm.get("target_project") or fm.get("project_hint"),
        "target_topic": target_topic,
        "layer": layer,
        "also_in": also_in,
        "recoverable_primary_aspect": recoverable_primary,
        "recoverable_secondary_aspects": also_in,
        # high  → target_topic is a canonical layer OR an explicit layer field
        #          survives: the flattening is unambiguous.
        # review → absorbed but target_topic is not a known layer token and no
        #          layer field: a human must confirm it is not a real global topic.
        "confidence": "high" if (topic_is_layer_token or isinstance(layer, str))
                      else "review",
    }


def census(vault: Path) -> Dict:
    """Scan the accepted by-topic canonical tree; return the damaged-record set."""
    accepted = vault / "learnings" / "accepted" / "by-topic"
    records: List[Dict] = []
    scanned = 0
    if accepted.exists():
        for md in sorted(accepted.rglob("*.md")):
            try:
                fm, _ = _parse.split_frontmatter(md.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(fm, dict):
                continue
            scanned += 1
            if fm.get("agent_kind") == ABSORB_SIGNAL:
                records.append(_record(md, vault, fm))

    by_aspect = Counter(r["recoverable_primary_aspect"] for r in records)
    return {
        "generated_from": str(vault),
        "signal": f"agent_kind == {ABSORB_SIGNAL!r}",
        "scanned": scanned,
        "damaged": len(records),
        "summary": {
            "high_confidence": sum(1 for r in records if r["confidence"] == "high"),
            "needs_review": sum(1 for r in records if r["confidence"] == "review"),
            "with_layer_field": sum(1 for r in records if r["layer"]),
            "with_also_in": sum(1 for r in records if r["also_in"]),
            "by_recoverable_primary_aspect": dict(by_aspect.most_common()),
        },
        "records": records,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Census workshop-absorb damaged learnings.")
    ap.add_argument("--out", type=Path, default=None,
                    help="write JSON here (default: stdout)")
    ap.add_argument("--summary-only", action="store_true",
                    help="print only the summary block, not per-record rows")
    args = ap.parse_args(argv)

    cfg = _config.load()
    report = census(_resolve_vault(cfg))
    if args.summary_only:
        report = {k: report[k] for k in
                  ("generated_from", "signal", "scanned", "damaged", "summary")}
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {report.get('damaged', '?')} damaged records → {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
