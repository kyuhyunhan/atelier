"""Auto-generated indexes for the learnings tier — RETIRED (RFC 0005 §7.1).

In the v7 field model a learning is one Claim whose tier is the `surfacing`
field, not a directory. The old generated INDEX.md files (candidates/principles
folder listings) described directories that no longer exist, so the generators
are retired.

`regen_principles()` is kept as a NO-OP so the existing principle-lifecycle call
sites (`principles.safe_regen_principles()`) stay valid without writing into any
legacy `learnings/principles/` tree. "principles" — high-`surfacing` operational
claims — are discovered by a facet/tier query over the claim store
(`recall` / `claims_io.iter_claim_files`), not by a generated folder index.
"""
from __future__ import annotations

from typing import Dict, Optional


def regen_principles(vault: Optional[object] = None) -> Dict[str, object]:
    """Retired. The principles "index" is now a tier query over claims, not a
    generated INDEX.md in a legacy directory. Kept as a no-op for call-site
    compatibility (returns the same shape as before, `written: False`)."""
    return {"written": False, "count": 0, "reason": "retired (RFC 0005 §7.1)"}


def safe_regen_principles() -> None:
    """No-op (retired). See module docstring."""
    return None
