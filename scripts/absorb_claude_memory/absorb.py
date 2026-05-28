"""CLI wrapper around runtime.service.learnings.absorb_claude.

Usage:
    python -m scripts.absorb_claude_memory.absorb [--dry-run | --apply] \\
        [--source-root ~/.claude/projects] \\
        [--auto-accept feedback,reference]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from runtime.service.learnings import absorb_claude as _ac


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="absorb-claude-memory")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--dry-run", action="store_true", default=True)
    grp.add_argument("--apply", action="store_true")
    p.add_argument("--source-root",
                   help="override ~/.claude/projects (rarely needed)")
    p.add_argument("--auto-accept",
                   default="feedback,reference",
                   help="comma-separated claude-memory types to auto-accept "
                        "(default: feedback,reference)")
    args = p.parse_args(argv)

    src = Path(args.source_root).expanduser() if args.source_root else None
    kinds = [k.strip() for k in args.auto_accept.split(",") if k.strip()]

    result = _ac.absorb(
        dry_run=not args.apply,
        source_root=src,
        auto_accept_kinds=kinds,
    )

    print(f"vault:              {result['vault']}")
    print(f"accepted:           {len(result['accepted'])}")
    print(f"candidates:         {len(result['candidates'])}")
    print(f"deduped (skipped):  {len(result['deduped'])}")
    if result["skipped"]:
        print(f"errors:             {len(result['skipped'])}")
        for s in result["skipped"][:5]:
            print(f"  ! {s}")
    if not args.apply:
        print("\n(dry-run; pass --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
