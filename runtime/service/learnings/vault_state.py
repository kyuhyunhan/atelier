"""RFC 0009 §5.7 — the vault content fingerprint.

The ENVELOPE primitive. The metric counters answer "how much is eligible?"; the
fingerprint answers the harder half of "and nothing else moved" — did any vault
*file* change that no INTENT clause accounts for. Without it, a goal could hit
its metric bound while silently rewriting unrelated claims, and the envelope
would never see it.

Two products from one walk:

- `content_fingerprint` — a single aggregate hash over `(relpath, sha256(body))`
  for every vault markdown file. It goes in the baseline; the envelope checks it
  for strict equality, and a vault-mutating goal *releases* it through a waiver
  (§3.5) rather than trying to bound a hash string.
- `file_digests` — the per-file `{relpath: sha256}` map. It is NOT committed
  (7k entries would bloat the frozen anchor); it lives in the round baseline, and
  the orchestrator diffs two of them to compute `changed_paths` for a waiver's
  bound. "Repaired 12 links" is `len(changed) == 12`, distinguishable from
  "rewrote 400 files" only because the per-file map exists.

Content only, never `mtime` (§5.7): the verify stage runs `reindex`, which
rewrites `INDEX.md` and can bump `mtime` on files whose bytes never changed. The
derived files themselves (`INDEX.md`, `MEMORY.md`) are excluded, exactly as
`census._fs_rows` excludes them, so a reindex cannot move the fingerprint.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

# Derived files a reindex rewrites — excluded so the fingerprint tracks authored
# content, not the projection's own output. MEMORY.md is matched case-insensitively
# to stay in parity with `census._fs_rows` (`p.name.upper() == "MEMORY.MD"`); a
# `memory.md` excluded there but hashed here would move the fingerprint whenever
# reindex rewrote it.
_DERIVED_EXACT = {"INDEX.md"}


def _is_derived(name: str) -> bool:
    return name in _DERIVED_EXACT or name.upper() == "MEMORY.MD"


def _vault_root(vault: Optional[Path]) -> Path:
    if vault is not None:
        return Path(vault)
    from . import cluster as _cl
    return Path(_cl._vault_root())


def file_digests(vault: Optional[Path] = None) -> Dict[str, str]:
    """`{relpath: sha256(body)}` for every non-derived vault markdown file.

    Sorted-relpath order at the caller's discretion; the aggregate below imposes
    a stable order so two runs over the same content agree byte-for-byte."""
    root = _vault_root(vault)
    out: Dict[str, str] = {}
    if not root.exists():
        return out
    for p in root.rglob("*.md"):
        if _is_derived(p.name):
            continue
        try:
            body = p.read_bytes()
        except OSError:
            continue
        rel = str(p.relative_to(root))
        out[rel] = hashlib.sha256(body).hexdigest()
    return out


def content_fingerprint(vault: Optional[Path] = None,
                        digests: Optional[Dict[str, str]] = None) -> str:
    """One aggregate hash over the per-file digests, in sorted-relpath order so
    it is deterministic. Pass `digests` to avoid re-walking when the caller
    already has them."""
    d = digests if digests is not None else file_digests(vault)
    h = hashlib.sha256()
    for rel in sorted(d):
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(d[rel].encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def vault_block(vault: Optional[Path] = None) -> Dict[str, Any]:
    """The baseline's `vault` block: the aggregate hash only. The per-file map is
    a round-baseline artifact (see module docstring), not part of the committed
    anchor."""
    return {"content_fingerprint": content_fingerprint(vault)}


def changed_paths(before: Dict[str, str], after: Dict[str, str]) -> List[str]:
    """The relpaths whose content differs between two per-file digest maps —
    added, removed, or modified. This is the delta a fingerprint waiver bounds
    (§3.5); it cannot live in a single snapshot, so the orchestrator computes it
    from the round baseline's `file_digests` and the after-state's."""
    keys = set(before) | set(after)
    return sorted(k for k in keys if before.get(k) != after.get(k))
