"""Sync orchestrator: dispatches to adapters per-space based on config."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..util import config, logging as log
from .adapters import github, r2


def _git_targets(cfg: config.Config,
                 space: Optional[str] = None) -> List[Tuple[str, Path, Optional[str]]]:
    """Resolve (label, local, remote_type) targets, deduped by local path.

    In single-vault mode the config synthesizes two pseudo-spaces that both
    point at ``vault.local``; committing per-space would double-process the
    same directory. We therefore target the vault directly (once)."""
    if cfg.vault is not None:
        return [("vault", cfg.vault.local, cfg.vault.remote_type)]
    names = [space] if space else list(cfg.spaces)
    out: List[Tuple[str, Path, Optional[str]]] = []
    seen: set[Path] = set()
    for name in names:
        sp = cfg.space(name)
        if sp.local in seen:
            continue
        seen.add(sp.local)
        out.append((name, sp.local, sp.remote_type))
    return out


def status(cfg: config.Config, space: Optional[str] = None) -> List[github.GitStatus]:
    out: List[github.GitStatus] = []
    targets = [space] if space else list(cfg.spaces)
    for name in targets:
        sp = cfg.space(name)
        if not sp.local.exists() or not (sp.local / ".git").exists():
            log.warn("sync.skip", space=name, reason="no local git repo")
            continue
        out.append(github.status(name, sp.local))
    return out


def pull(cfg: config.Config, space: Optional[str] = None) -> None:
    targets = [space] if space else list(cfg.spaces)
    for name in targets:
        sp = cfg.space(name)
        if (sp.local / ".git").exists() and sp.remote_type == "github":
            out = github.pull(sp.local)
            log.info("sync.pull", space=name, out=out.strip())


def push(cfg: config.Config, space: Optional[str] = None) -> None:
    targets = [space] if space else list(cfg.spaces)
    for name in targets:
        sp = cfg.space(name)
        if (sp.local / ".git").exists() and sp.remote_type == "github":
            out = github.push(sp.local)
            log.info("sync.push", space=name, out=out.strip())


_NON_FF_MARKERS = ("non-fast-forward", "[rejected]", "fetch first",
                   "Updates were rejected")


def _is_non_fast_forward(text: str) -> bool:
    return any(m in text for m in _NON_FF_MARKERS)


def commit_push(cfg: config.Config, message: str, *,
                space: Optional[str] = None, push: bool = True,
                on_conflict: str = "surface",
                timeout: Optional[float] = github._DEFAULT_TIMEOUT,
                ) -> Dict[str, Any]:
    """Commit the vault (or a space) and optionally push, with safety gates.

    Never raises on a failed push — a flaky network or a diverged remote is
    surfaced via the return dict and logs, so callers (the auto-sync poller,
    a manual CLI) keep running. ``on_conflict`` is "surface" by default: on a
    non-fast-forward rejection we log and stop — we never auto pull/merge/force
    (that would mutate markdown out from under the DB projection)."""
    result: Dict[str, Any] = {"committed": False, "pushed": False}
    targets = _git_targets(cfg, space)

    for name, local, remote_type in targets:
        if not local.exists():
            log.warn("sync.skip", target=name, reason="local path missing")
            result["skipped"] = "no-git"
            continue
        if not github.is_repo_root(local):
            # distinguish "not a repo at all" from "a subdir of a repo"
            reason = "not-repo-root" if github._git_dir(local) else "no-git"
            log.warn("sync.skip", target=name, reason=reason)
            result["skipped"] = reason
            continue
        if github.in_merge_or_rebase(local) or github.lock_present(local):
            log.warn("sync.skip", target=name, reason="mid-merge/rebase or locked")
            result["skipped"] = "mid-merge-or-lock"
            continue

        try:
            sha = github.commit(local, message, timeout=timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            log.warn("sync.commit-failed", target=name, err=str(e))
            result["commit_error"] = str(e)
            continue

        if sha == "nothing to commit":
            log.info("sync.commit", target=name, result="nothing to commit")
            continue
        result["committed"] = True
        result["sha"] = sha
        log.info("sync.commit", target=name, sha=sha)

        if not push or remote_type != "github":
            continue
        try:
            out = github.push(local, timeout=timeout)
            result["pushed"] = True
            log.info("sync.push", target=name, out=out.strip())
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            detail = getattr(e, "output", "") or str(e)
            result["push_error"] = detail
            if isinstance(e, subprocess.CalledProcessError) and _is_non_fast_forward(detail):
                # remote diverged — surface, do not auto-reconcile.
                log.warn("sync.push-diverged", target=name,
                         hint="remote ahead; local commit kept, push deferred")
            else:
                log.warn("sync.push-failed", target=name, err=detail)

    return result
