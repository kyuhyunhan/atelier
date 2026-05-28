"""Sync orchestrator: dispatches to adapters per-space based on config."""
from __future__ import annotations

from typing import List, Optional

from ..util import config, logging as log
from .adapters import github, r2


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
