"""Capability claims + single-writer locks.

A *claim* is what the caller asserts they're authorized to do (e.g.
`librarian-write`). Read-side calls require no claim. Write-side calls
require both (a) the claim and (b) holding the *role lock* — an
asyncio.Lock keyed by writer role so only one writer mutates a subtree
at a time.

v0.1 had a single trusted client (local CLI) and only stubbed enforcement.
PR-2 adds real claim checks (callers without local-cli must carry the
claim) and the SpaceLockRegistry that PR-3+ MCP tool wrappers use to
serialize same-role writes.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator, Dict, Optional


class Claim(str, Enum):
    LIBRARIAN_WRITE  = "librarian-write"
    BUILDER_WRITE    = "builder-write"
    CAPTOR_WRITE     = "captor-write"          # learnings/candidates/ append
    CURATOR_WRITE    = "curator-write"         # learnings promote/archive
    PROMOTE_APPLY    = "promote-apply"
    MOBILE_CLAIM     = "mobile-claim"          # reserved, capture endpoint
    DOCTOR_REMEDIATE = "doctor-remediate"


# Writer-role identifiers used as lock keys. Aligned with Claim values
# but kept separate so the lock layer doesn't conflate "claim required"
# with "what subtree this writes to".
class WriterRole(str, Enum):
    LIBRARIAN = "librarian-write"
    BUILDER   = "builder-write"
    CAPTOR    = "captor-write"
    CURATOR   = "curator-write"


@dataclass
class CallContext:
    caller: str = "local-cli"
    claims: frozenset[Claim] = frozenset()


def local_cli_context() -> CallContext:
    """Default context for in-process CLI calls — full trust."""
    return CallContext(caller="local-cli", claims=frozenset(Claim))


def require(ctx: CallContext, claim: Claim) -> None:
    """Raise if `ctx` lacks `claim`. local-cli has every claim implicitly."""
    if ctx.caller == "local-cli":
        return
    if claim not in ctx.claims:
        raise PermissionError(f"caller {ctx.caller!r} lacks claim {claim.value!r}")


# ── Single-writer-per-role locks (used by MCP tool wrappers in PR-3+) ───────


class SpaceLockRegistry:
    """Lazy asyncio.Lock per WriterRole.

    One instance lives in the `atelier serve` process. Read-side tools
    do not touch it. Write-side tool wrappers do `async with reg.acquire(role)`
    around their api.py call so the same-role writers serialize.

    The registry is asyncio-bound: it must be constructed on the event
    loop that uses it (transports do this in PR-3/PR-4).
    """

    def __init__(self) -> None:
        self._locks: Dict[WriterRole, asyncio.Lock] = {}

    def _lock_for(self, role: WriterRole) -> asyncio.Lock:
        if role not in self._locks:
            self._locks[role] = asyncio.Lock()
        return self._locks[role]

    @asynccontextmanager
    async def acquire(self, role: WriterRole) -> AsyncIterator[None]:
        lock = self._lock_for(role)
        async with lock:
            yield

    def any_held(self) -> bool:
        """True if any writer-role lock is currently held. The vault
        auto-sync poller checks this to avoid committing mid-write."""
        return any(lock.locked() for lock in self._locks.values())


_REGISTRY: Optional[SpaceLockRegistry] = None


def registry() -> SpaceLockRegistry:
    """Module-level singleton. Reset with `reset_registry()` (tests)."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = SpaceLockRegistry()
    return _REGISTRY


def reset_registry() -> None:
    global _REGISTRY
    _REGISTRY = None
