"""Capability claims. Placeholder enforcement in v0.1.

A claim is what the caller asserts they're authorized to do. v0.1 has a
single trusted client (the local CLI), so claims are recorded but not
enforced. v0.2 will gate operations by claim.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Claim(str, Enum):
    LIBRARIAN_WRITE = "librarian-write"
    BUILDER_WRITE   = "builder-write"
    PROMOTE_APPLY   = "promote-apply"
    MOBILE_CLAIM    = "mobile-claim"     # reserved, capture endpoint
    DOCTOR_REMEDIATE = "doctor-remediate"


@dataclass
class CallContext:
    caller: str = "local-cli"
    claims: frozenset[Claim] = frozenset()  # claims granted to this caller


def local_cli_context() -> CallContext:
    """Default context for in-process CLI calls — full trust."""
    return CallContext(caller="local-cli", claims=frozenset(Claim))


def require(ctx: CallContext, claim: Claim) -> None:
    """Raise if `ctx` lacks `claim`. v0.1: no-op for local-cli, logs otherwise."""
    if ctx.caller == "local-cli":
        return
    if claim not in ctx.claims:
        raise PermissionError(f"caller {ctx.caller!r} lacks claim {claim.value!r}")
