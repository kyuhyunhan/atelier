"""P4 on-disk migration (RFC 0001): accepted/by-topic → flat notes/<YYYY-MM>/.

Moves every accepted-learning markdown file from the legacy by-topic canonical
tree into the flat store sharded by immutable creation month, and bumps
schema_version 4→5. Classification is unaffected — it already lives in
frontmatter facets; only the *location* changes.

Properties:
- **Source = by-topic canonical only.** The by-project mirror is a duplicate; it
  is left in place and deleted wholesale in P7. (A by-project-only file would be
  reconcile drift — logged, not moved.)
- **Month from `captured_at`** (immutable), not accepted_at (mutable).
- **Stable identity:** the filename (the slug stem) is preserved, so a re-run is
  a no-op (skip if the destination already holds this entry_id).
- **Atomic:** write to a temp file in the destination dir, os.replace, then
  unlink the source — no half-files on power loss.
- **Idempotent + reversible via git** (the vault is a git repo); no inverse script.

Dry-run by default; pass --apply to move files.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from runtime.index import parse as _parse
from runtime.service.learnings import store as _store
from runtime.util import config as _config


def _resolve_vault(cfg: _config.Config) -> Path:
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


@dataclass
class Result:
    moved: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)      # already in flat store
    errors: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return {"moved": self.moved, "skipped": self.skipped,
                "errors": self.errors,
                "counts": {"moved": len(self.moved),
                           "skipped": len(self.skipped),
                           "errors": len(self.errors)}}


def _is_index(name: str) -> bool:
    stem = name[:-3] if name.endswith(".md") else name
    return stem in ("INDEX", "TAXONOMY")


def migrate(vault: Path, *, apply: bool = False) -> Dict[str, object]:
    """Move by-topic canonical files into notes/<YYYY-MM>/. Returns a report."""
    res = Result()
    by_topic = vault / "learnings" / "accepted" / "by-topic"
    if not by_topic.exists():
        return res.as_dict()

    for src in sorted(by_topic.rglob("*.md")):
        if _is_index(src.name):
            continue
        try:
            fm, body = _parse.split_frontmatter(src.read_text(encoding="utf-8"))
        except Exception as e:                       # pragma: no cover
            res.errors.append(f"{src}: parse error: {e}")
            continue
        if not isinstance(fm, dict):
            res.errors.append(f"{src}: no frontmatter")
            continue

        dest = _store.flat_dest(vault, fm.get("captured_at"), src.name)
        rel = src.relative_to(vault).as_posix()
        if dest.exists():
            # Idempotent ONLY when the destination holds the SAME record —
            # otherwise it is a genuine filename collision (two distinct
            # learnings share a name, e.g. README.md) and must be suffixed, not
            # skipped (skipping would strand the second one in the legacy tree).
            try:
                dfm, _ = _parse.split_frontmatter(dest.read_text(encoding="utf-8"))
            except Exception:
                dfm = {}
            if isinstance(dfm, dict) and dfm.get("entry_id") == fm.get("entry_id"):
                res.skipped.append(rel)
                continue
            stem, suffix = src.name[:-3], ".md"     # collision: find a free name
            n = 1
            while dest.exists():
                dest = dest.parent / f"{stem}-{n}{suffix}"
                n += 1

        fm = dict(fm)
        fm["schema_version"] = 5                      # v4 → v5 (RFC 0001)
        serialized = yaml.safe_dump(fm, sort_keys=False,
                                    allow_unicode=True).rstrip()
        new_text = f"---\n{serialized}\n---\n{body}"

        if apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.parent / f".{dest.name}.tmp"
            tmp.write_text(new_text, encoding="utf-8")
            os.replace(tmp, dest)                     # atomic on POSIX
            src.unlink()
        res.moved.append(f"{rel} → {dest.relative_to(vault).as_posix()}")

    return res.as_dict()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Flatten accepted learnings into notes/<YYYY-MM>/ (RFC 0001).")
    ap.add_argument("--apply", action="store_true",
                    help="actually move files (default: dry-run)")
    args = ap.parse_args(argv)

    cfg = _config.load()
    report = migrate(_resolve_vault(cfg), apply=args.apply)
    counts = report["counts"]
    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"[{mode}] moved={counts['moved']} skipped={counts['skipped']} "
          f"errors={counts['errors']}")
    for line in report["moved"][:5]:
        print(f"  {line}")
    if counts["moved"] > 5:
        print(f"  … and {counts['moved'] - 5} more")
    for e in report["errors"]:
        print(f"  ERROR {e}")
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
