"""Vault auto-sync — a background poller that commits + pushes the vault.

This is a *background subsystem* (not a transport): it runs on the
supervisor's event loop and observes the vault's git working tree on a
fixed interval. It is source-agnostic — it catches both atelier-mediated
writes and direct edits, because it watches the tree's *state*, not who
wrote it.

Design (see docs/ARCHITECTURE.md):
  - poll every `interval_seconds`; between ticks the loop sleeps on an
    interruptible wait so shutdown is immediate.
  - commit only when the tree is dirty AND *quiescent* — the porcelain
    status is unchanged across two consecutive ticks (`require_stable`).
    This coalesces a burst of writes into one commit without a watcher.
  - never commit while a writer-role lock is held (mid tool write) or the
    repo is mid merge/rebase/locked (those gates live in commit_push).
  - blocking git runs in a worker thread (`to_thread`) so the loop — which
    also serves MCP transports — never stalls.

The per-tick decision is isolated in the pure `_decide()` function so it
can be unit-tested without async, threads, real git, or real time.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional, Tuple

from ..structure import resolver as _structure
from ..sync import orchestrator
from ..sync.adapters import github
from ..util import logging as log
from . import claims, server


# ── pure decision core (no async / no IO) ────────────────────────────────────


def _decide(status: str, prev: Optional[str], *,
            require_stable: bool, lock_busy: bool) -> Tuple[bool, Optional[str]]:
    """Decide whether to commit this tick. Returns (should_commit, new_prev).

    - clean tree            → no commit, reset memory
    - a writer lock is held → defer (a tool is mid-write); keep memory
    - require_stable and the dirty set changed since last tick → wait one
      more tick (record the new fingerprint)
    - otherwise (dirty + settled) → commit
    """
    if not status:
        return (False, None)
    if lock_busy:
        return (False, prev)
    if require_stable and status != prev:
        return (False, status)
    return (True, None)


def _message(prefix: str, status: str) -> str:
    """Conventional, AI-co-author-free commit message:
    `<prefix> sync N change(s) [auto]` + body listing changed paths."""
    lines = [ln for ln in status.splitlines() if ln.strip()]
    subject = f"{prefix} sync {len(lines)} change(s) [auto]"
    paths = [ln[3:] for ln in lines]            # porcelain: "XY path"
    body = "\n".join(paths)
    return f"{subject}\n\n{body}" if body else subject


# ── the poll loop (async, dependency-injected for tests) ─────────────────────


SleepFn = Callable[[float], Awaitable[bool]]    # returns True to proceed, False to stop


async def _poll_loop(sup: server.Supervisor, *,
                     status_fn: Callable[[], str],
                     commit_fn: Callable[[str], object],
                     lock_busy_fn: Callable[[], bool],
                     sleep_fn: SleepFn,
                     require_stable: bool,
                     interval_seconds: float,
                     message_prefix: str,
                     reindex_fn: Optional[Callable[[str], object]] = None,
                     ) -> None:
    prev: Optional[str] = None
    while not sup.shutdown.is_set():
        proceed = await sleep_fn(interval_seconds)
        if not proceed:
            break
        try:
            status = await asyncio.to_thread(status_fn)
        except Exception as e:                  # status probe must never crash the loop
            log.warn("vault-autosync.status-failed", err=str(e))
            continue
        should, prev = _decide(status, prev,
                               require_stable=require_stable,
                               lock_busy=lock_busy_fn())
        if not should:
            continue
        try:
            await asyncio.to_thread(commit_fn, _message(message_prefix, status))
        except Exception as e:                  # commit_push already catches push;
            log.warn("vault-autosync.commit-failed", err=str(e))
            continue                            # no commit → nothing to reindex

        # RFC 0005 §7.2 — reindex piggyback. We only get here AFTER a successful
        # commit, which only fires when the tree was quiescent (require_stable:
        # status unchanged across two ticks) and no writer lock was held. So the
        # reindex runs on a settled tree, never mid-write — the quiescence gate
        # is the commit gate. Reindex is deterministic + idempotent (content_hash
        # dedups), so re-running over already-indexed files is a no-op; this is
        # what structurally removes the manual-reindex drift class (D2). The
        # changed-file set is `status` (the same porcelain the commit consumed).
        if reindex_fn is not None:
            try:
                await asyncio.to_thread(reindex_fn, status)
            except Exception as e:              # a reindex hiccup must not crash the loop
                log.warn("vault-autosync.reindex-failed", err=str(e))


# ── default interruptible sleep ──────────────────────────────────────────────


async def _interruptible_sleep(sup: server.Supervisor, interval: float) -> bool:
    """Sleep up to `interval`s, returning True if it elapsed, False if shutdown
    was signalled meanwhile (so the loop exits promptly)."""
    try:
        await asyncio.wait_for(sup.shutdown.wait(), timeout=interval)
        return False                            # shutdown won the race
    except asyncio.TimeoutError:
        return True                             # interval elapsed normally


# ── supervisor entrypoint ────────────────────────────────────────────────────


async def run(sup: server.Supervisor) -> None:
    """Background task. Self-gates on config; idles if disabled or the vault
    is not a git repo root (graceful degradation, same as the sync path)."""
    ac = sup.cfg.auto_sync
    vault = sup.cfg.vault

    if not ac.enabled:
        log.info("vault-autosync.disabled", reason="not enabled")
        await sup.shutdown.wait()
        return
    if vault is None:
        log.info("vault-autosync.disabled", reason="no vault configured")
        await sup.shutdown.wait()
        return
    local = vault.local
    if not (local / ".git").exists() or not github.is_repo_root(local):
        log.warn("vault-autosync.disabled", reason="vault is not a git repo root",
                 vault=str(local))
        await sup.shutdown.wait()
        return

    log.info("vault-autosync.ready", vault=str(local),
             interval=ac.interval_seconds, push=ac.push)

    await _poll_loop(
        sup,
        status_fn=lambda: github.dirty_porcelain(local),
        commit_fn=lambda msg: orchestrator.commit_push(
            sup.cfg, message=msg, push=ac.push, on_conflict=ac.on_conflict,
            # Human/machine commit separation: raw/ (content_root, from the
            # structure resolver — hard rule #3) lands as its own "journal:"
            # commit; the engine tree keeps message_prefix. Off → legacy add -A.
            split_human_tree=(_structure.content_root()
                              if ac.split_human_commits else None),
            split_prefixes=("journal:", ac.message_prefix)),
        lock_busy_fn=lambda: claims.registry().any_held(),
        sleep_fn=lambda interval: _interruptible_sleep(sup, interval),
        require_stable=ac.require_stable,
        interval_seconds=ac.interval_seconds,
        message_prefix=ac.message_prefix,
        reindex_fn=(_reindex_changed if ac.reindex_on_commit else None),
    )


def _reindex_changed(_status: str) -> None:
    """RFC 0005 §7.2 piggyback — reindex changed files after an autosync commit.

    Runs an INCREMENTAL reindex across the canonical spaces: crawl already
    skips files whose `content_hash` is unchanged, so this re-indexes exactly the
    just-committed (changed) files and is a no-op for everything else —
    deterministic and idempotent. `_status` (the committed porcelain) is accepted
    for parity/logging; the incremental crawl is the changed-set selector. The
    embed pass self-gates (ATELIER_EMBED / gateway reachability), so this is
    cheap when no provider is configured."""
    from ..index import reindex as _reindex
    from ..util import config as _config
    cfg = _config.load()
    stats = _reindex.reindex_all(cfg, full=False)
    changed = sum(s.pages_changed for s in stats)
    log.info("vault-autosync.reindexed", pages_changed=changed,
             spaces=len(stats))


# Auto-register on import (cli's `serve` imports this module).
server.register_background(run)
