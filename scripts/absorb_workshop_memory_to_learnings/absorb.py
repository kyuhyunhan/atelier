"""Absorb workshop per-product memory/ subtrees into learnings/by-project/.

After PR-7 the workshop lives at `<vault>/workshop/products/<n>/`. Each
product carries a `memory/` subdirectory with cross-cutting notes,
session logs, and other lesson-shaped writeups. Those are *learnings*,
not product operating memory — this migrator moves them into the
single learnings domain.

Layout transform:

    workshop/products/<n>/memory/<topic>/<file>.md
        │
        ▼
    learnings/accepted/by-topic/<topic>/<file>.md
    learnings/accepted/by-project/<n>/<file>.md  (mirror copy)

The migrator updates frontmatter on-the-fly:
- schema_version → 4
- status → accepted, ac_status → passed (these notes are pre-validated)
- target_topic ← memory subdirectory name (default 'general')
- target_project ← product name
- accepted_at ← now

Dry-run by default; refuses to overwrite existing destinations.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import uuid as _uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from runtime.index import parse as _parse
from runtime.service.learnings import store as _store
from runtime.util import config as _config


_SLUG_RX = re.compile(r"[^a-z0-9-]+")


def _slugify(value: str, *, fallback: str = "general") -> str:
    text = (value or fallback).strip().lower()
    text = _SLUG_RX.sub("-", text).strip("-")
    return text[:60] or fallback


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class Plan:
    src: Path
    dest: Path                 # flat notes/<YYYY-MM>/<name>
    project: str
    aspects: List[str]         # project-local categories (layer + also_in)
    new_fm: Dict


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


def _walk_memory(vault_root: Path,
                 *, workshop_source: Optional[Path]) -> List[Tuple[Path, str, str]]:
    """Yield (memory_md, project_name, topic) tuples.

    workshop_source is either:
    - vault_root/workshop/products    (post PR-7)
    - <builder space>/products        (pre PR-7, legacy)
    """
    if workshop_source is None or not workshop_source.exists():
        return []

    out: List[Tuple[Path, str, str]] = []
    for product_dir in sorted(workshop_source.iterdir()):
        if not product_dir.is_dir():
            continue
        mem_root = product_dir / "memory"
        if not mem_root.exists():
            continue
        for md in sorted(mem_root.rglob("*.md")):
            rel = md.relative_to(mem_root)
            # first path component is the topic, fall back to 'general'.
            topic = rel.parts[0] if len(rel.parts) > 1 else "general"
            out.append((md, product_dir.name, topic))
    return out


def _build_plan(srcs: List[Tuple[Path, str, str]],
                vault: Path) -> Tuple[List[Plan], List[str]]:
    plans: List[Plan] = []
    conflicts: List[str] = []

    for src, project, topic in srcs:
        project_slug = _slugify(project)
        topic_slug = _slugify(topic, fallback="general")
        fm, _body = _parse.split_frontmatter(src.read_text(encoding="utf-8"))

        # Corrected mapping (RFC 0001 §2.2). The workshop note's project-local
        # category is an ASPECT, not the global target_topic. Primary aspect =
        # the explicit `layer` if present, else the memory subdirectory; secondary
        # aspects = `also_in`. target_topic is left UNSET — there is no global
        # topic here, and flattening one into it is the exact bug being fixed.
        layer = fm.get("layer")
        primary = _slugify(layer) if isinstance(layer, str) and layer else topic_slug
        aspects: List[str] = []
        for a in [primary, *(_slugify(x) for x in _as_list(fm.get("also_in")))]:
            if a and a not in aspects:
                aspects.append(a)

        fm = dict(fm)
        fm["schema_version"] = 5
        # Idempotent identity — keep the original source-location namespace key
        # so re-running never duplicates an already-absorbed note.
        fm.setdefault("entry_id", str(_uuid.uuid5(
            _uuid.NAMESPACE_DNS,
            f"learnings:absorbed:{project_slug}/{topic_slug}/{src.name}",
        )))
        fm.setdefault("captured_at", _now_iso())
        fm.setdefault("agent_kind", "absorbed")
        fm.setdefault("hook", "manual")
        fm.setdefault("observation_kind", "project")
        fm["status"] = "accepted"
        fm["ac_status"] = "passed"
        fm["accepted_at"] = _now_iso()
        fm["target_project"] = project_slug
        fm["aspect"] = aspects
        fm.pop("target_topic", None)         # never flatten a layer into topic
        # `links` (typed {to, why}) and any `also_in` are preserved as-is.
        fm["ac_results"] = {"absorbed_from": "workshop/memory/"}

        dest = _store.flat_dest(vault, fm.get("captured_at"), src.name)
        if dest.exists():
            conflicts.append(f"already exists: {dest}")
            continue

        plans.append(Plan(src=src, dest=dest, project=project_slug,
                          aspects=aspects, new_fm=fm))
    return plans, conflicts


def _apply(plan: Plan) -> None:
    _, body = _parse.split_frontmatter(plan.src.read_text(encoding="utf-8"))
    serialized = yaml.safe_dump(plan.new_fm, sort_keys=False,
                                allow_unicode=True).rstrip()
    # One flat note (RFC 0001), written atomically. The by-project mirror is gone.
    plan.dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = plan.dest.parent / f".{plan.dest.name}.tmp"
    tmp.write_text(f"---\n{serialized}\n---\n{body}", encoding="utf-8")
    os.replace(tmp, plan.dest)
    # Source file is left in place — operator decides when to delete the
    # original memory/ subtree (likely after committing this absorption).


def absorb(*, apply: bool,
           workshop_source: Optional[Path] = None) -> int:
    cfg = _config.load()
    vault = _resolve_vault(cfg)

    if workshop_source is None:
        # Prefer post-PR-7 in-vault workshop; fall back to legacy builder space.
        in_vault = vault / "workshop" / "products"
        if in_vault.exists():
            workshop_source = in_vault
        else:
            try:
                builder_root = cfg.space_by_role("builder-territory").local
            except KeyError:
                builder_root = None
            workshop_source = (builder_root / "products") if builder_root else None

    if workshop_source is None or not workshop_source.exists():
        print("no workshop source found (neither vault/workshop nor "
              "legacy builder space). nothing to absorb.")
        return 0

    srcs = _walk_memory(vault, workshop_source=workshop_source)
    plans, conflicts = _build_plan(srcs, vault)

    print(f"vault:           {vault}")
    print(f"workshop source: {workshop_source}")
    print(f"memory files:    {len(srcs)}")
    print(f"plans:           {len(plans)}")
    if conflicts:
        print("CONFLICTS:")
        for c in conflicts:
            print(f"  ! {c}")

    by_aspect_counts: Counter[str] = Counter()
    for p in plans:
        for a in (p.aspects or ["(none)"]):
            by_aspect_counts[a] += 1
    if by_aspect_counts:
        print("by aspect (primary + secondary):")
        for t, n in sorted(by_aspect_counts.items()):
            print(f"  {n:4}  {t}")

    if not apply:
        print("\n(dry-run; pass --apply to copy files)")
        return 1 if conflicts else 0

    if conflicts:
        print("\nrefusing to apply due to conflicts.", file=sys.stderr)
        return 2

    for p in plans:
        _apply(p)
    print(f"\napplied {len(plans)} files. operator should review and "
          f"commit changes in {vault}, then delete the source memory/ "
          f"subtrees under {workshop_source}.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="absorb-workshop-memory-to-learnings",
        description="Migrate workshop per-product memory/ into "
                    "learnings/accepted/by-{topic,project}/.",
    )
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--dry-run", action="store_true", default=True)
    grp.add_argument("--apply", action="store_true")
    p.add_argument("--workshop-source",
                   help="override the workshop products root (default: "
                        "vault/workshop/products, fallback to builder space)")
    args = p.parse_args(argv)
    ws = Path(args.workshop_source).expanduser() if args.workshop_source else None
    return absorb(apply=args.apply, workshop_source=ws)


if __name__ == "__main__":
    sys.exit(main())
