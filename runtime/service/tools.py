"""MCP tool registry — single funnel for stdio + HTTP transports.

Each ToolDef carries the public name, a description (for MCP), the async
handler, and optional claim + lock-role for write tools. The handler is
the function FastMCP introspects for input schema, so handlers MUST have
fully typed parameters and a return type. Claim and lock enforcement
happen inside the handler body via `_guard()`, NOT via a decorator
(decorators with `**kwargs` erase the FastMCP schema).

Session resolution
------------------
Handlers read the current Session through a contextvar. Transport
adapters set this contextvar before each tool dispatch:

- mcp_stdio.py: sets `local_cli_session()` once per process (stdio
  callers are subprocesses launched by the user themselves; trusted).
- mcp_http.py: sets a bearer-authenticated Session per request.

The contextvar default is `local_cli_session()` so unit tests can call
handlers directly without setup.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..util import config as _config
from . import api as _api
from . import auth as _auth
from . import claims as _claims


# ── Session contextvar (transport adapters set this) ───────────────────────


_current: contextvars.ContextVar[_auth.Session] = contextvars.ContextVar(
    "atelier.current_session", default=_auth.local_cli_session()
)


def set_session(s: _auth.Session) -> contextvars.Token:
    return _current.set(s)


def current_session() -> _auth.Session:
    return _current.get()


# ── ToolDef + registry ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    handler: Callable[..., Awaitable[Dict[str, Any]]]
    claim: Optional[_claims.Claim] = None
    lock_role: Optional[_claims.WriterRole] = None


_REGISTRY: Dict[str, ToolDef] = {}


def register(t: ToolDef) -> None:
    _REGISTRY[t.name] = t


def iter_tools() -> List[ToolDef]:
    return list(_REGISTRY.values())


def get(name: str) -> ToolDef:
    return _REGISTRY[name]


async def invoke(name: str, **params: Any) -> Dict[str, Any]:
    """Direct dispatch path (used by tests + MCP transports).

    Runs claim + lock guards then awaits the handler. The active
    Session is resolved from the contextvar.
    """
    t = get(name)
    sess = current_session()
    if t.claim is not None:
        _claims.require(sess.to_call_context(), t.claim)
    if t.lock_role is not None:
        async with _claims.registry().acquire(t.lock_role):
            return await t.handler(**params)
    return await t.handler(**params)


# ── Read-side handlers ─────────────────────────────────────────────────────


async def _h_search(query: str, space: Optional[str] = None,
                    limit: int = 20, fallback: bool = False) -> Dict[str, Any]:
    """Full-text search over indexed pages. Returns ranked hits."""
    return {"hits": _api.search(query, space=space, limit=limit, fallback=fallback)}


async def _h_links(slug: str, direction: str = "both") -> Dict[str, Any]:
    """Inbound and/or outbound `[[wikilinks]]` for a page."""
    from ..search import graph
    from ..util import db
    conn = db.connect_shared()
    inbound = list(graph.inbound(conn, slug)) if direction in ("inbound", "both") else []
    outbound = list(graph.outbound(conn, slug)) if direction in ("outbound", "both") else []
    return {"slug": slug, "inbound": inbound, "outbound": outbound}


async def _h_list_pages(space: Optional[str] = None,
                        page_type: Optional[str] = None) -> Dict[str, Any]:
    """List indexed pages, optionally filtered by space or page_type."""
    from ..util import db
    conn = db.connect_shared()
    sql = "SELECT slug, page_type, space FROM pages WHERE 1=1"
    params: List[Any] = []
    if page_type:
        sql += " AND page_type=?"
        params.append(page_type)
    if space:
        sql += " AND space=?"
        params.append(space)
    sql += " ORDER BY space, slug"
    rows = [dict(r) for r in conn.execute(sql, params)]
    return {"pages": rows}


async def _h_lint(space: Optional[str] = None,
                  rule_ids: Optional[List[str]] = None,
                  apply_fixes: bool = False) -> Dict[str, Any]:
    """Run lint rules (L1/L3/L5/L6). With apply_fixes=true requires
    librarian-write claim and lock."""
    if apply_fixes:
        sess = current_session()
        _claims.require(sess.to_call_context(), _claims.Claim.LIBRARIAN_WRITE)
        async with _claims.registry().acquire(_claims.WriterRole.LIBRARIAN):
            return _api.lint(space=space, rule_ids=rule_ids, apply_fixes=True)
    return _api.lint(space=space, rule_ids=rule_ids, apply_fixes=False)


async def _h_doctor(remediate: bool = False, max_usd: float = 0.0) -> Dict[str, Any]:
    """Drift diagnostics. With remediate=true requires the
    doctor-remediate claim."""
    if remediate:
        sess = current_session()
        _claims.require(sess.to_call_context(), _claims.Claim.DOCTOR_REMEDIATE)
    return _api.doctor(remediate=remediate, max_usd=max_usd)


async def _h_sync(action: str, space: Optional[str] = None) -> Dict[str, Any]:
    """Git status / pull / push for one or all spaces."""
    return _api.sync(action, space=space)


# ── Write-side handlers ────────────────────────────────────────────────────


async def _h_reindex(space: Optional[str] = None, full: bool = False) -> Dict[str, Any]:
    """Rebuild the SQLite projection of markdown content."""
    return {"results": _api.reindex(space=space, full=full)}


async def _h_capture(text: str, source: str = "manual",
                     title: Optional[str] = None) -> Dict[str, Any]:
    """Append a short note to raw/personal/inbox/."""
    return _api.capture_text(text, source=source, title=title)


async def _h_promote_propose() -> Dict[str, Any]:
    """Scan workshop for promote-worthy notes; emit a proposal document."""
    return _api.promote_propose()


async def _h_promote_apply(proposal: str) -> Dict[str, Any]:
    """Apply a proposal — Librarian writes the wiki page + backlink."""
    return _api.promote_apply(proposal)


async def _h_new_product(name: str) -> Dict[str, Any]:
    """Scaffold a new product in the builder territory."""
    cfg = _config.load()
    builder_space = cfg.space_by_role("builder-territory").local
    product_dir = builder_space / "products" / name
    if product_dir.exists():
        raise FileExistsError(f"product already exists: {product_dir}")
    product_dir.mkdir(parents=True)
    from datetime import datetime, timezone
    import uuid as _uuid
    now = datetime.now(timezone.utc).date().isoformat()
    eid = _uuid.uuid5(_uuid.NAMESPACE_DNS, f"workshop:products/{name}")
    (product_dir / "README.md").write_text(
        f"---\nschema_version: 4\nentry_id: {eid}\ntitle: {name}\n"
        f"type: product\nstatus: active\nsensitivity: private\n"
        f"created: {now}\nupdated: {now}\nsummary: \"\"\n---\n\n"
        f"# {name}\n\n(product description)\n",
        encoding="utf-8",
    )
    return {"path": str(product_dir / "README.md")}


# ── Registration (executed on import) ──────────────────────────────────────


def _register_v01_tools() -> None:
    # Read tools — no claim, no lock.
    register(ToolDef("atelier_search",
                     "Full-text search over indexed pages.",
                     _h_search))
    register(ToolDef("atelier_links",
                     "Inbound/outbound wikilinks for a page slug.",
                     _h_links))
    register(ToolDef("atelier_list_pages",
                     "List indexed pages, optionally filtered.",
                     _h_list_pages))
    register(ToolDef("atelier_lint",
                     "Run lint rules; optionally apply fixes "
                     "(requires librarian-write).",
                     _h_lint))
    register(ToolDef("atelier_doctor",
                     "Drift diagnostics; optionally remediate.",
                     _h_doctor))
    register(ToolDef("atelier_sync",
                     "Git status / pull / push.",
                     _h_sync))
    register(ToolDef("atelier_promote_propose",
                     "Scan workshop for promote-worthy notes.",
                     _h_promote_propose))

    # Write tools — claim + role lock.
    register(ToolDef("atelier_reindex",
                     "Rebuild SQLite projection from markdown.",
                     _h_reindex,
                     claim=_claims.Claim.LIBRARIAN_WRITE,
                     lock_role=_claims.WriterRole.LIBRARIAN))
    register(ToolDef("atelier_capture",
                     "Append a short note to the librarian inbox.",
                     _h_capture,
                     claim=_claims.Claim.MOBILE_CLAIM,
                     lock_role=_claims.WriterRole.LIBRARIAN))
    register(ToolDef("atelier_promote_apply",
                     "Apply a promotion proposal — writes wiki/.",
                     _h_promote_apply,
                     claim=_claims.Claim.PROMOTE_APPLY,
                     lock_role=_claims.WriterRole.LIBRARIAN))
    register(ToolDef("atelier_new_product",
                     "Scaffold a new product in workshop/products/.",
                     _h_new_product,
                     claim=_claims.Claim.BUILDER_WRITE,
                     lock_role=_claims.WriterRole.BUILDER))


_register_v01_tools()


# ── Shared helper for transport adapters ───────────────────────────────────


def add_to_fastmcp(app: Any) -> None:
    """Register every ToolDef as a FastMCP tool. Used by mcp_stdio /
    mcp_http so both transports advertise the identical surface.

    The wrapper preserves the handler signature (FastMCP introspects it
    for the JSON schema) and routes through invoke() to run claim + lock
    guards.
    """
    import functools
    import inspect

    for tdef in iter_tools():
        sig = inspect.signature(tdef.handler)

        def _make(td: ToolDef, s: inspect.Signature):
            @functools.wraps(td.handler)
            async def wrapper(*args: Any, **kwargs: Any) -> Dict[str, Any]:
                bound = s.bind(*args, **kwargs)
                return await invoke(td.name, **bound.arguments)
            wrapper.__doc__ = td.description
            return wrapper

        app.add_tool(_make(tdef, sig),
                     name=tdef.name,
                     description=tdef.description)
