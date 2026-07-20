"""Unified nudge surface — one shape, one surfacing point (RFC 0005 §7).

The three GATED, human-invoked edges of the atomic-graph lifecycle each have
their own status/nudge probe today, but in inconsistent shapes and surfaced
inconsistently (only DREAM reaches SessionStart):

    atomize  (L1 Source → L2 Claims/Entities)   — atomize.nudge_info()
    promote  (query → proactive, behind accept) — promote.propose.eligible_count()
    dream    (proactive → always + synthesis)   — dream.nudge_info(now=…)

This module normalizes all three to a single frozen `Nudge` shape so callers —
the MCP tool `atelier_nudges`, the SessionStart hook, a statusline — consume ONE
abstraction instead of three bespoke dicts.

Each wrapper is TOLERANT: a failing probe yields a not-due `Nudge`, never an
exception (matching the dream/atomize posture, so a single broken edge can never
suppress the others or crash session start).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Nudge:
    """One normalized nudge across every gated edge.

    - kind:  the edge ('atomize' | 'promote' | 'dream').
    - due:   whether this edge wants the human's attention now.
    - count: the salient number for the edge (un-atomized sources / eligible
             claims / accepted-since-or-pending) — 0 when not due / unknown.
    - short: a compact one-line form (statusline).
    - long:  the full user-facing message (systemMessage / model context).
    """
    kind: str
    due: bool
    count: int
    short: str
    long: str


def _safe(kind: str) -> Nudge:
    """A not-due Nudge for an edge whose probe failed — never crash the surface."""
    return Nudge(kind=kind, due=False, count=0, short="", long="")


def _atomize_nudge() -> Nudge:
    try:
        from .learnings import atomize as _atomize
        info = _atomize.nudge_info()
        return Nudge(
            kind="atomize",
            due=bool(info.get("due")),
            count=int(info.get("count") or 0),
            short=str(info.get("short") or ""),
            long=str(info.get("long") or ""),
        )
    except Exception:                            # pragma: no cover - tolerance
        return _safe("atomize")


def _dream_nudge(*, now: str) -> Nudge:
    try:
        from .learnings import dream as _dream
        info = _dream.nudge_info(now=now)
        # The salient number: accepted-since when accumulation drove the nudge,
        # otherwise the pending-review count (an interrupted/unreviewed dream).
        count = int(info.get("proactive_since") or 0) or int(info.get("pending") or 0)
        return Nudge(
            kind="dream",
            due=bool(info.get("due")),
            count=count,
            short=str(info.get("short") or ""),
            long=str(info.get("long") or ""),
        )
    except Exception:                            # pragma: no cover - tolerance
        return _safe("dream")


def _promote_nudge() -> Nudge:
    """Promote has no native nudge surface — synthesize one from the eligible
    count (claims that passed acceptance and await query→proactive). Due when
    ≥1 claim is eligible; the action is `atelier-consolidate` (which runs the
    promote → dream → reindex pass)."""
    try:
        from ..promote import propose as _propose
        count = int(_propose.eligible_count())
        due = count >= 1
        long = ""
        short = ""
        if due:
            noun = "claim" if count == 1 else "claims"
            long = (
                f"⬆️ **atelier promote** — {count} accepted {noun} on query-only "
                f"awaiting promotion to proactive. Ask me to run "
                f"`atelier-consolidate` to promote them behind the acceptance gate."
            )
            short = f"⬆️ atelier: {count} to promote"
        return Nudge(kind="promote", due=due, count=count,
                     short=short, long=long)
    except Exception:                            # pragma: no cover - tolerance
        return _safe("promote")


def all_nudges(*, now: str) -> List[Nudge]:
    """Every edge normalized to `Nudge`, in lifecycle order
    (atomize → promote → dream). Each is independently tolerant."""
    return [
        _atomize_nudge(),
        _promote_nudge(),
        _dream_nudge(now=now),
    ]


def due_nudges(*, now: str) -> List[Nudge]:
    """Only the nudges currently wanting attention."""
    return [n for n in all_nudges(now=now) if n.due]
