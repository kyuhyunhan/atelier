"""Absorb the builder-territory workshop into the librarian-territory vault.

Moves `products/`, `notes/`, and `logs/` from the configured builder
space into a `workshop/` subtree under the librarian space (the new
single vault). `products/*/profile.local.yaml` files are extracted to
`~/.atelier/profiles/<product>.yaml` (out of vault). The `memory/`
subtree under each product is left alone in PR-7 — PR-21 absorbs it
into `learnings/by-project/<product>/`.

The script is **dry-run by default**. It refuses to write to dirty git
trees and refuses to overwrite existing files (conflict detection). The
operator commits the resulting changes manually in the librarian repo.

Usage:
    python -m scripts.absorb_workshop.absorb [--dry-run | --apply]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from runtime.util import config as _config


@dataclass
class Plan:
    """A single source → dest move. dest's parent is guaranteed to exist."""
    src: Path
    dest: Path
    kind: str          # "move-tree" | "copy-file" | "extract-profile" | "consolidate-log"


def _git_porcelain(path: Path) -> str:
    if not (path / ".git").exists():
        return ""
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            check=True, capture_output=True, text=True,
        )
        return out.stdout
    except subprocess.CalledProcessError as e:  # pragma: no cover
        return e.stderr or "(git status failed)"


def _build_plan(builder_root: Path, vault_root: Path,
                profiles_dir: Path) -> Tuple[List[Plan], List[str]]:
    plans: List[Plan] = []
    conflicts: List[str] = []

    workshop_root = vault_root / "workshop"
    products_src = builder_root / "products"
    products_dst = workshop_root / "products"

    if products_src.exists():
        for product_dir in sorted(products_src.iterdir()):
            if not product_dir.is_dir():
                continue
            dest = products_dst / product_dir.name
            if dest.exists():
                conflicts.append(f"already exists: {dest}")
            else:
                plans.append(Plan(product_dir, dest, "move-tree"))
            profile = product_dir / "profile.local.yaml"
            if profile.exists():
                plans.append(Plan(
                    profile, profiles_dir / f"{product_dir.name}.yaml",
                    "extract-profile",
                ))

    notes_src = builder_root / "notes"
    if notes_src.exists():
        dest = workshop_root / "notes"
        if dest.exists():
            conflicts.append(f"already exists: {dest}")
        else:
            plans.append(Plan(notes_src, dest, "move-tree"))

    logs_src = builder_root / "logs"
    if logs_src.exists():
        plans.append(Plan(logs_src, workshop_root / "log.md", "consolidate-log"))

    return plans, conflicts


def _apply_plan(plan: Plan) -> None:
    plan.dest.parent.mkdir(parents=True, exist_ok=True)
    if plan.kind == "move-tree":
        shutil.copytree(plan.src, plan.dest)
    elif plan.kind == "extract-profile":
        shutil.copy2(plan.src, plan.dest)
    elif plan.kind == "consolidate-log":
        lines: List[str] = []
        for f in sorted(plan.src.rglob("*.md")):
            rel = f.relative_to(plan.src)
            lines.append(f"\n## {rel}\n")
            lines.append(f.read_text(encoding="utf-8"))
        plan.dest.write_text("# workshop log (consolidated from atelier-workshop/logs/)\n"
                             + "".join(lines), encoding="utf-8")
    else:  # pragma: no cover
        raise RuntimeError(f"unknown plan kind: {plan.kind}")


def absorb(*, apply: bool, profiles_dir: Optional[Path] = None) -> int:
    cfg = _config.load()
    librarian = cfg.space_by_role("librarian-territory")
    builder = cfg.space_by_role("builder-territory")
    vault_root = librarian.local
    builder_root = builder.local

    if librarian.local == builder.local:
        print(f"librarian and builder already share a root ({vault_root}); "
              "nothing to absorb.")
        return 0

    if not builder_root.exists():
        print(f"builder root does not exist: {builder_root}")
        return 2

    if apply:
        for tag, root in (("librarian", vault_root), ("builder", builder_root)):
            porcelain = _git_porcelain(root)
            if porcelain:
                print(f"ERROR: {tag} ({root}) has uncommitted changes:\n{porcelain}",
                      file=sys.stderr)
                return 2

    profiles_dir = profiles_dir or (Path.home() / ".atelier" / "profiles")
    plans, conflicts = _build_plan(builder_root, vault_root, profiles_dir)

    print(f"librarian vault: {vault_root}")
    print(f"builder root:    {builder_root}")
    print(f"profiles dir:    {profiles_dir}")
    print()

    if conflicts:
        print("CONFLICTS (refusing to overwrite):")
        for c in conflicts:
            print(f"  ! {c}")
        print()

    if plans:
        print(f"would do {len(plans)} actions:")
        for p in plans:
            print(f"  [{p.kind}]  {p.src}  →  {p.dest}")
    else:
        print("nothing to do.")

    if not apply:
        print("\n(dry-run; pass --apply to copy files)")
        return 1 if conflicts else 0

    if conflicts:
        print("\nrefusing to apply due to conflicts.", file=sys.stderr)
        return 2

    profiles_dir.mkdir(parents=True, exist_ok=True)
    for plan in plans:
        _apply_plan(plan)

    print(f"\napplied {len(plans)} actions. operator must commit changes "
          f"in {vault_root} (and optionally archive {builder_root}).")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="absorb-workshop",
        description="Absorb the builder-territory workshop into the librarian vault.",
    )
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--dry-run", action="store_true", default=True)
    grp.add_argument("--apply", action="store_true")
    p.add_argument("--profiles-dir",
                   help="override the destination for profile.local.yaml files "
                        "(default: ~/.atelier/profiles/)")
    args = p.parse_args(argv)
    profiles_dir = Path(args.profiles_dir).expanduser() if args.profiles_dir else None
    return absorb(apply=args.apply, profiles_dir=profiles_dir)


if __name__ == "__main__":
    sys.exit(main())
