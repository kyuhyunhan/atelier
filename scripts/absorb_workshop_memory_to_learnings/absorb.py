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
import re
import shutil
import sys
import uuid as _uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from runtime.index import parse as _parse
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
    dest_by_topic: Path
    dest_by_project: Path
    project: str
    topic: str
    new_fm: Dict


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

        fm = dict(fm)
        fm["schema_version"] = 4
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
        fm["target_topic"] = topic_slug
        fm["target_project"] = project_slug
        fm["ac_results"] = {"absorbed_from": "workshop/memory/"}

        dest_topic = (vault / "learnings" / "accepted" / "by-topic"
                      / topic_slug / src.name)
        dest_project = (vault / "learnings" / "accepted" / "by-project"
                        / project_slug / src.name)

        if dest_topic.exists():
            conflicts.append(f"already exists: {dest_topic}")
            continue
        if dest_project.exists():
            conflicts.append(f"already exists: {dest_project}")
            continue

        plans.append(Plan(
            src=src,
            dest_by_topic=dest_topic,
            dest_by_project=dest_project,
            project=project_slug,
            topic=topic_slug,
            new_fm=fm,
        ))
    return plans, conflicts


def _apply(plan: Plan) -> None:
    _, body = _parse.split_frontmatter(plan.src.read_text(encoding="utf-8"))
    serialized = yaml.safe_dump(plan.new_fm, sort_keys=False,
                                allow_unicode=True).rstrip()
    plan.dest_by_topic.parent.mkdir(parents=True, exist_ok=True)
    plan.dest_by_topic.write_text(f"---\n{serialized}\n---\n{body}",
                                  encoding="utf-8")
    plan.dest_by_project.parent.mkdir(parents=True, exist_ok=True)
    # Collision avoidance — two memory files in different topic subdirs
    # can share a filename (e.g. README.md) and would otherwise overwrite
    # in the by-project mirror.
    target = plan.dest_by_project
    n = 1
    while target.exists():
        stem = target.stem
        target = target.with_name(f"{stem}-{n}{target.suffix}")
        n += 1
    shutil.copy2(plan.dest_by_topic, target)
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

    by_topic_counts: Counter[str] = Counter()
    for p in plans:
        by_topic_counts[p.topic] += 1
    if by_topic_counts:
        print("by topic:")
        for t, n in sorted(by_topic_counts.items()):
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
