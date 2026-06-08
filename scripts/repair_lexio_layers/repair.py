"""P6 — repair the records damaged by the old workshop absorb (RFC 0001 §2.2).

The old absorb flattened each note's project-local category into the GLOBAL
`target_topic` and dropped `also_in`. This repairs those records IN PLACE:

    target_topic (a project-local layer)  →  aspect[0]   (primary)
    also_in (recovered from the live workshop note)  →  aspect[1:]  (secondary)
    target_topic                                     →  removed

Damage signal is `agent_kind == "absorbed"` (the P0 census signal). The repair is
keyed on the record itself (no id churn) and idempotent: a record already carrying
`aspect` and no `target_topic` is skipped.

`also_in` lives ONLY in the live workshop note, so this MUST run before the
workshop is frozen/deleted (P8). The workshop source is matched by filename under
`workshop/products/<target_project>/memory/`.

Read-only by default; pass --apply to rewrite frontmatter.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from runtime.index import parse as _parse
from runtime.service.learnings import store as _store
from runtime.util import config as _config


_SLUG_RX = re.compile(r"[^a-z0-9-]+")
ABSORB_SIGNAL = "absorbed"


def _slugify(value: str) -> str:
    return _SLUG_RX.sub("-", (value or "").strip().lower()).strip("-")


def _as_list(value) -> List[str]:
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, (str, int))]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _resolve_vault(cfg: _config.Config) -> Path:
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _workshop_index(vault: Path) -> Dict[str, Dict]:
    """filename → {layer, also_in} from the live workshop memory notes, the only
    surviving source of `also_in` for the damaged records."""
    idx: Dict[str, Dict] = {}
    root = vault / "workshop" / "products"
    if not root.exists():
        return idx
    for p in root.rglob("memory/**/*.md"):
        try:
            fm, _ = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(fm, dict):
            idx[p.name] = {"layer": fm.get("layer"),
                           "also_in": _as_list(fm.get("also_in"))}
    return idx


def _repair_fm(fm: Dict, ws: Optional[Dict]) -> Optional[Dict]:
    """Return a repaired frontmatter dict, or None if nothing to do."""
    if fm.get("agent_kind") != ABSORB_SIGNAL:
        return None
    # Idempotent: already repaired (has aspect, no flattened topic).
    if fm.get("aspect") and not fm.get("target_topic"):
        return None

    layer = fm.get("layer") or (ws or {}).get("layer")
    also_in = _as_list(fm.get("also_in")) or (ws or {}).get("also_in") or []
    primary = _slugify(layer) if isinstance(layer, str) and layer \
        else _slugify(fm.get("target_topic") or "")

    aspects: List[str] = []
    for a in [primary, *(_slugify(x) for x in also_in)]:
        if a and a not in aspects:
            aspects.append(a)

    new = dict(fm)
    if aspects:
        new["aspect"] = aspects
    new.pop("target_topic", None)            # project-local value now in aspect
    return new


def repair(vault: Path, *, apply: bool = False) -> Dict[str, object]:
    ws_index = _workshop_index(vault)
    repaired: List[str] = []
    skipped = 0
    recovered_also_in = 0

    for p in _store.iter_accepted_files(vault):
        try:
            fm, body = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(fm, dict):
            continue
        new_fm = _repair_fm(fm, ws_index.get(p.name))
        if new_fm is None:
            if fm.get("agent_kind") == ABSORB_SIGNAL:
                skipped += 1               # already repaired
            continue
        if not _as_list(fm.get("also_in")) and ws_index.get(p.name, {}).get("also_in"):
            recovered_also_in += 1
        if apply:
            serialized = yaml.safe_dump(new_fm, sort_keys=False,
                                        allow_unicode=True).rstrip()
            tmp = p.parent / f".{p.name}.tmp"
            tmp.write_text(f"---\n{serialized}\n---\n{body}", encoding="utf-8")
            tmp.replace(p)
        repaired.append(f"{p.name}: aspect={new_fm.get('aspect')}")

    return {"repaired": len(repaired), "already_ok": skipped,
            "recovered_also_in": recovered_also_in,
            "workshop_notes_indexed": len(ws_index),
            "samples": repaired[:8]}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Repair workshop-absorb damaged learnings (RFC 0001 P6).")
    ap.add_argument("--apply", action="store_true",
                    help="rewrite frontmatter (default: dry-run)")
    args = ap.parse_args(argv)
    cfg = _config.load()
    rep = repair(_resolve_vault(cfg), apply=args.apply)
    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"[{mode}] repaired={rep['repaired']} already_ok={rep['already_ok']} "
          f"recovered_also_in={rep['recovered_also_in']} "
          f"(workshop notes indexed={rep['workshop_notes_indexed']})")
    for s in rep["samples"]:
        print(f"  {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
