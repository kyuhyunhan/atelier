"""Service API — the single funnel that CLI / MCP / HTTP all go through.

In v0.1 these are thin wrappers around runtime modules. v0.2 will add
real auth/claims enforcement here; callers don't change.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ..util import config, db
from . import auth, claims


# ── Read-side ────────────────────────────────────────────────────────────────

def reindex(space: Optional[str] = None, full: bool = False,
            token: Optional[str] = None) -> List[Dict[str, Any]]:
    ctx = auth.authenticate(token)
    from ..index import reindex as _reindex
    cfg = config.load()
    statses = (
        [_reindex.reindex_space(cfg, space, full=full)]
        if space else _reindex.reindex_all(cfg, full=full)
    )
    return [vars(s) for s in statses]


def reindex_path(path: str, *, embed: bool = False,
                 token: Optional[str] = None) -> Dict[str, Any]:
    """Change-feed entry (RFC 0006 ②): project a single file into the DB without
    a full reindex, so a read reflects an engine write immediately. `embed=False`
    (default) skips the vector pass for speed — the lexical projection goes fresh
    now; embeddings catch up on the next full reindex."""
    auth.authenticate(token)
    from ..index import reindex as _reindex
    cfg = config.load()
    gw = _reindex._resolve_gateway(cfg) if embed else None
    return vars(_reindex.reindex_path(cfg, Path(path), embed_gateway=gw))


def search(query: str, space: Optional[str] = None, limit: int = 20,
           fallback: bool = False, token: Optional[str] = None) -> List[Dict[str, Any]]:
    from ..search import fts
    conn = db.connect()
    try:
        hits = fts.search(conn, query, space=space, limit=limit)
        if not hits and fallback:
            hits = fts.search_like_fallback(conn, query, space=space, limit=limit)
        return [vars(h) for h in hits]
    finally:
        conn.close()


def lint(space: Optional[str] = None, rule_ids: Optional[List[str]] = None,
         apply_fixes: bool = False, token: Optional[str] = None) -> Dict[str, Any]:
    ctx = auth.authenticate(token)
    if apply_fixes:
        claims.require(ctx, claims.Claim.WIKI_WRITE)
    from ..lint import runner
    conn = db.connect()
    try:
        with conn:
            report = runner.run(conn, space=space, rule_ids=rule_ids,
                                apply_fixes=apply_fixes)
        return {
            "rules_run": report.rules_run,
            "findings": [vars(f) for f in report.findings],
            "by_severity": report.by_severity(),
            "fixes_applied": report.fixes_applied,
            "failed": report.failed(),
        }
    finally:
        conn.close()


def doctor(remediate: bool = False, max_usd: float = 0.0,
           token: Optional[str] = None) -> Dict[str, Any]:
    ctx = auth.authenticate(token)
    if remediate:
        claims.require(ctx, claims.Claim.DOCTOR_REMEDIATE)
    from ..doctor import diagnostics, remediate as rem
    cfg = config.load()
    diags = diagnostics.run_all(cfg)
    out: Dict[str, Any] = {"diagnoses": [vars(d) for d in diags]}
    if remediate:
        results = rem.remediate(cfg, diags, max_usd=max_usd)
        out["remediations"] = [vars(r) for r in results]
    return out


def sync(action: str, space: Optional[str] = None,
         message: Optional[str] = None,
         token: Optional[str] = None) -> Dict[str, Any]:
    from ..sync import orchestrator
    cfg = config.load()
    if action == "status":
        return {"status": [vars(s) for s in orchestrator.status(cfg, space=space)]}
    if action == "pull":
        orchestrator.pull(cfg, space=space)
        return {"pulled": True}
    if action == "push":
        orchestrator.push(cfg, space=space)
        return {"pushed": True}
    if action in ("commit", "commit-push"):
        msg = message or "chore(vault): sync [auto]"
        return orchestrator.commit_push(
            cfg, message=msg, space=space, push=(action == "commit-push"))
    raise ValueError(f"unknown sync action: {action}")


# ── Write-side ───────────────────────────────────────────────────────────────

def capture_text(text: str, source: str = "manual", title: Optional[str] = None,
                 domain: str = "inbox/undetermined", sensitivity: str = "private",
                 token: Optional[str] = None) -> Dict[str, Any]:
    ctx = auth.authenticate(token)
    from . import capture as _capture
    path = _capture.capture(text=text, source=source, title=title,
                            domain=domain, sensitivity=sensitivity, ctx=ctx)
    return {"path": str(path)}


def promote_propose(token: Optional[str] = None) -> Dict[str, Any]:
    from ..promote import propose
    return propose.propose_all()


def promote_apply(proposal: str, token: Optional[str] = None) -> Dict[str, Any]:
    ctx = auth.authenticate(token)
    claims.require(ctx, claims.Claim.PROMOTE_APPLY)
    from ..promote import apply as _apply
    return _apply.apply_proposal(Path(proposal))


def validate(paths: Optional[List[str]] = None,
             role: str = "librarian-territory",
             fail_fast: bool = False,
             token: Optional[str] = None) -> Dict[str, Any]:
    """Validate frontmatter against schema v4 (optionally on a subset)."""
    from ..lint import validate_v4
    cfg = config.load()
    if cfg.vault is not None:
        vault_root = cfg.vault.local
    else:
        vault_root = cfg.space_by_role(role).local
    if paths:
        targets = [Path(p) for p in paths]
    else:
        targets = sorted(vault_root.rglob("*.md"))
    findings = validate_v4.validate_paths(targets, vault_root=vault_root,
                                          fail_fast=fail_fast)
    return {
        "vault": str(vault_root),
        "scanned": len(targets),
        "findings": [vars(f) for f in findings],
        "failed": any(f.severity == "FAIL" for f in findings),
    }
