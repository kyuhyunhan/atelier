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
    wiki-write claim and lock."""
    if apply_fixes:
        sess = current_session()
        _claims.require(sess.to_call_context(), _claims.Claim.WIKI_WRITE)
        async with _claims.registry().acquire(_claims.WriterRole.WIKI):
            return _api.lint(space=space, rule_ids=rule_ids, apply_fixes=True)
    return _api.lint(space=space, rule_ids=rule_ids, apply_fixes=False)


async def _h_doctor(remediate: bool = False, max_usd: float = 0.0) -> Dict[str, Any]:
    """Drift diagnostics. With remediate=true requires the
    doctor-remediate claim."""
    if remediate:
        sess = current_session()
        _claims.require(sess.to_call_context(), _claims.Claim.DOCTOR_REMEDIATE)
    return _api.doctor(remediate=remediate, max_usd=max_usd)


async def _h_sync(action: str, space: Optional[str] = None,
                  message: Optional[str] = None) -> Dict[str, Any]:
    """Git sync for the vault. action: status | pull | push | commit | commit-push.
    `commit`/`commit-push` stage+commit (and push) only if the tree is dirty and
    safe (repo toplevel, not mid-merge/locked); a failed push is surfaced, never
    raised. `message` overrides the commit subject."""
    return _api.sync(action, space=space, message=message)


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


async def _h_fix_pending(dry_run: bool = False,
                          role: str = "librarian-territory") -> Dict[str, Any]:
    """Resolve every `entry_id: PENDING` to a stable UUID5."""
    from .jobs import pending as _jp
    return _jp.fix_pending(dry_run=dry_run, role=role)


async def _h_index_regen(role: str = "librarian-territory",
                          dry_run: bool = False) -> Dict[str, Any]:
    """Regenerate wiki/index.md from current wiki/* contents."""
    from .jobs import index_regen as _jir
    return _jir.regen(role=role, dry_run=dry_run)


async def _h_clip_image(url: str,
                         role: str = "librarian-territory",
                         subdir: str = "gorae-resources") -> Dict[str, Any]:
    """Fetch a remote image into the vault and (when configured) return a CDN URL."""
    from .jobs import clip as _jc
    return _jc.clip_image(url=url, role=role, subdir=subdir)


async def _h_new_doc(template: str, name: str,
                      role: str = "librarian-territory",
                      fields: Optional[Dict[str, Any]] = None
                      ) -> Dict[str, Any]:
    """Scaffold a new document under raw/, workshop/products, workshop/notes,
    or learnings/candidates/."""
    from .jobs import new_doc as _jnd
    return _jnd.new_doc(template=template, name=name, role=role,
                         fields=fields)


async def _h_prepare_commit(paths: Optional[List[str]] = None,
                             dry_run: bool = False
                             ) -> Dict[str, Any]:
    """Recalculate word_count / embedded_assets / edited_at for
    pre-commit hygiene. LLM facets reclassification is deferred."""
    from .jobs import prepare as _jp
    return _jp.prepare_commit(paths=paths, dry_run=dry_run)


async def _h_youtube(url: str, role: str = "librarian-territory",
                      lang: Optional[str] = None,
                      force_stt: bool = False,
                      staging_subdir: str = "_new"
                      ) -> Dict[str, Any]:
    """Ingest a YouTube URL into raw/knowledge/<staging_subdir>/. When
    captions are unavailable and STT is not configured, returns
    status=needs-stt for operator follow-up."""
    from .jobs import youtube as _jy
    return _jy.youtube_ingest(url=url, role=role, lang=lang,
                              force_stt=force_stt,
                              staging_subdir=staging_subdir)


async def _h_validate(paths: Optional[List[str]] = None,
                      role: str = "librarian-territory",
                      fail_fast: bool = False) -> Dict[str, Any]:
    """Validate frontmatter against schema v4. Read-only."""
    return _api.validate(paths=paths, role=role, fail_fast=fail_fast)


async def _h_learning_capture(observation: str = "",
                              why: Optional[str] = None,
                              rule: Optional[str] = None,
                              excerpt: Optional[str] = None,
                              working_dir: Optional[str] = None,
                              project_hint: Optional[str] = None,
                              session_id: Optional[str] = None,
                              transcript_path: Optional[str] = None,
                              agent_kind: str = "claude-code",
                              hook: str = "manual",
                              observation_kind: str = "feedback",
                              require_why: bool = True
                              ) -> Dict[str, Any]:
    """Append a candidate learning to learnings/candidates/.

    A substance gate rejects content-free captures (empty/stub
    observation + no why → `no-substance`; and, with require_why=True,
    an empty why → `empty-why`). "Why this matters" is an LLM judgement a
    bash hook cannot supply, so the agent must fill it. Promotion-time
    acceptance criteria still apply later. Returns {skipped, reason} when
    the gate rejects.
    """
    from .learnings import capture as _cap
    sess = current_session()
    # Use the real observation; otherwise try a transcript tail. Do NOT
    # fabricate a "(hook=...) session_id=..." stub — the gate would (and
    # should) reject that as no-substance anyway.
    obs = observation or _extract_transcript_tail(transcript_path) or ""
    return _cap.capture(
        observation=obs,
        why=why, rule=rule,
        excerpt=excerpt or (transcript_path or None),
        working_dir=working_dir or sess.working_dir,
        project_hint=project_hint,
        session_id=session_id or sess.session_id,
        agent_kind=agent_kind or sess.agent_kind,
        hook=hook,
        observation_kind=observation_kind,
        require_why=require_why,
    )


def _extract_transcript_tail(path: Optional[str], *, max_msgs: int = 3,
                              max_chars: int = 600) -> Optional[str]:
    """Best-effort summary of the last few user/assistant turns from a
    Claude Code transcript JSONL file. Returns None on any error."""
    if not path:
        return None
    try:
        import json as _json
        from pathlib import Path as _Path
        p = _Path(path).expanduser()
        if not p.exists():
            return None
        msgs: List[tuple[str, str]] = []
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                m = _json.loads(line)
            except Exception:
                continue
            role = m.get("role") or m.get("type") or ""
            content = m.get("content") or m.get("text") or ""
            if isinstance(content, list):
                content = " ".join(
                    (c.get("text") or "") for c in content
                    if isinstance(c, dict)
                )
            if role in ("user", "assistant") and isinstance(content, str) \
                    and content.strip():
                msgs.append((role, content.strip()))
        if not msgs:
            return None
        chunks = [f"{role}: {body[:max_chars]}"
                  for role, body in msgs[-max_msgs:]]
        return "Session tail:\n" + "\n".join(f"- {c}" for c in chunks)
    except Exception:                        # pragma: no cover
        return None


async def _h_learning_review_pending(limit: int = 20,
                                     project: Optional[str] = None,
                                     since: Optional[str] = None
                                     ) -> Dict[str, Any]:
    """List learning candidates with self-checked AC results."""
    from .learnings import review as _rev
    return _rev.review_pending(limit=limit, project=project, since=since)


async def _h_learning_accept(candidate_slug: str,
                             target_topic: str,
                             target_project: Optional[str] = None,
                             links: Optional[List[str]] = None,
                             override_unknown: bool = False,
                             override_must: bool = False
                             ) -> Dict[str, Any]:
    """Promote a candidate to learnings/accepted/. Refuses on must-fail
    unless override_must (a reviewed curator decision); forbidden
    criteria (pii/pure-meta) are never overridable."""
    from .learnings import review as _rev
    return _rev.accept(
        candidate_slug=candidate_slug,
        target_topic=target_topic,
        target_project=target_project,
        links=links,
        override_unknown=override_unknown,
        override_must=override_must,
    )


async def _h_learning_archive(candidate_slug: str, reason: str) -> Dict[str, Any]:
    """Move a candidate to learnings/archived/."""
    from .learnings import review as _rev
    return _rev.archive(candidate_slug=candidate_slug, reason=reason)


async def _h_learning_retract(slug: str, reason: str = "retracted"
                              ) -> Dict[str, Any]:
    """Retract a candidate or an accepted learning into archived/."""
    from .learnings import review as _rev
    return _rev.retract(slug=slug, reason=reason)


async def _h_learning_search(query: str = "",
                             status: str = "accepted",
                             project: Optional[str] = None,
                             topic: Optional[str] = None,
                             limit: int = 20) -> Dict[str, Any]:
    """Search the learnings domain (accepted by default)."""
    from .learnings import search as _ls
    return _ls.search(query=query, status=status, project=project,
                      topic=topic, limit=limit)


async def _h_learning_relink(slug: str, links: List[str],
                             mode: str = "replace") -> Dict[str, Any]:
    """Set or merge the wiki backlinks on an accepted learning."""
    from .learnings import search as _ls
    return _ls.relink(slug=slug, links=links, mode=mode)


async def _h_principle_add(title: str, rule: str, why: str,
                            evidence: Optional[List[str]] = None,
                            coverage: str = "cross-project",
                            priority: str = "on-relevant-prompt",
                            target_topic: Optional[str] = None,
                            notes: Optional[str] = None,
                            slug: Optional[str] = None,
                            ) -> Dict[str, Any]:
    """Add a principle directly. Caller supplies rule and why."""
    from .learnings import principles as _pr
    return _pr.add(title=title, rule=rule, why=why, evidence=evidence,
                    coverage=coverage, priority=priority,
                    target_topic=target_topic, notes=notes, slug=slug)


async def _h_principle_synthesize(source_slugs: List[str],
                                    title: Optional[str] = None,
                                    rule: Optional[str] = None,
                                    why: Optional[str] = None,
                                    coverage: str = "cross-project",
                                    priority: str = "on-relevant-prompt",
                                    notes: Optional[str] = None,
                                    slug: Optional[str] = None,
                                    ) -> Dict[str, Any]:
    """Draft a principle from several accepted learnings. Body
    sections are scaffolded; caller may pass rule/why to fill them."""
    from .learnings import principles as _pr
    return _pr.synthesize(source_slugs=source_slugs, title=title,
                           rule=rule, why=why, coverage=coverage,
                           priority=priority, notes=notes, slug=slug)


async def _h_principle_list(priority: Optional[str] = None,
                              coverage: Optional[str] = None
                              ) -> Dict[str, Any]:
    """List current principles, optionally filtered by priority / coverage."""
    from .learnings import principles as _pr
    items = _pr.list_all(priority=priority, coverage=coverage)
    return {"count": len(items), "items": items}


async def _h_principle_archive(slug: str, reason: str) -> Dict[str, Any]:
    """Move a principle to learnings/archived/ with ac_status=retracted."""
    from .learnings import principles as _pr
    return _pr.archive(slug=slug, reason=reason)


async def _h_principle_review_proposed(limit: int = 50) -> Dict[str, Any]:
    """Dream cycle ③ — list proposed principles awaiting promotion, with
    rule preview + evidence, for a fast batch review."""
    from .learnings import principles as _pr
    return _pr.review_proposed(limit=limit)


async def _h_principle_approve(slug: str,
                                priority: Optional[str] = None,
                                coverage: Optional[str] = None
                                ) -> Dict[str, Any]:
    """Promote a proposed principle to accepted (optionally set priority,
    e.g. always-inject)."""
    from .learnings import principles as _pr
    return _pr.approve(slug=slug, priority=priority, coverage=coverage)


async def _h_principle_reject(slug: str, reason: str = "rejected"
                               ) -> Dict[str, Any]:
    """Reject a proposed principle → archived (never re-proposed)."""
    from .learnings import principles as _pr
    return _pr.reject(slug=slug, reason=reason)


async def _h_recall(query: str,
                     project: Optional[str] = None,
                     top_k: int = 5,
                     max_chars: int = 1500,
                     include_candidates: bool = False,
                     relevance_threshold: Optional[float] = None,
                     ) -> Dict[str, Any]:
    """Per-turn signal-detector retrieval over the learnings domain."""
    from .learnings import recall as _rc
    sess = current_session()
    if project is None and sess.working_dir:
        # Route through the shared accessor so recall's project key matches
        # the one capture wrote and bootstrap injects (learning `1446`).
        from .learnings import project as _proj
        project = _proj.resolve_project(sess.working_dir).slug
    return _rc.recall(query=query, project=project, top_k=top_k,
                       max_chars=max_chars,
                       include_candidates=include_candidates,
                       relevance_threshold=relevance_threshold)


async def _h_surfacing_audit(probe_k: int = 10) -> Dict[str, Any]:
    """Read-only retrieval observability: which accepted learnings can no longer
    be found by their *own* concept (gone dark). The instrument that makes
    silent omission visible — a content diff cannot show what stopped surfacing."""
    from .learnings import surfacing as _sf
    return _sf.audit(probe_k=probe_k)


async def _h_lateral_plan(suggest: int = 4,
                          overlap: float = 0.7) -> Dict[str, Any]:
    """Lateral mutator tee-up (read-only, deterministic): untagged learnings
    with body-derived tag suggestions, inert existing tags, and flag-only
    near-duplicate groups. The live agent refines; apply enforces the gates.
    Returns {"tags": plan_tags() result, "merges": plan_merges() result}."""
    from .learnings import lateral as _lat
    return {"tags": _lat.plan_tags(suggest=suggest),
            "merges": _lat.plan_merges(overlap=overlap)}


async def _h_lateral_apply(mapping: Dict[str, List[str]]) -> Dict[str, Any]:
    """Lateral mutator apply — snapshot-wrapped concept tagging of canonicals
    + mirrors with the body-echo gate (non-echoing tags rejected). Returns the
    surfacing diff; the caller MUST check `diff.newly_dark`."""
    from .learnings import lateral as _lat
    return _lat.apply_tags(mapping)


async def _h_learning_cluster(min_shared_terms: int = 3,
                               min_size: int = 2,
                               min_projects: int = 2,
                               limit: int = 50) -> Dict[str, Any]:
    """Dream cycle step ① — deterministic clustering of accepted learnings
    by shared salient terms + cross-project spread. Read-only; the agent
    generalizes each cluster into a principle (step ②)."""
    from .learnings import cluster as _cl
    return _cl.cluster(min_shared_terms=min_shared_terms, min_size=min_size,
                        min_projects=min_projects, limit=limit)


async def _h_dream_status() -> Dict[str, Any]:
    """Cadence info for the dream nudge: last dream time + accepted
    learnings accrued since."""
    from .learnings import cluster as _cl
    return _cl.dream_status()


async def _h_dream_plan(min_shared_terms: int = 2,
                         min_size: int = 2,
                         min_projects: int = 2,
                         overlap_threshold: float = 0.6,
                         limit: int = 20) -> Dict[str, Any]:
    """Dream cycle phase 1 — return clusters worth synthesizing, each with
    member previews + a ready-to-fill synthesize call. Already-covered
    clusters are filtered out. The agent generalizes each and calls
    atelier_principle_synthesize; then atelier_dream_complete."""
    from .learnings import dream as _dr
    return _dr.plan(min_shared_terms=min_shared_terms, min_size=min_size,
                    min_projects=min_projects,
                    overlap_threshold=overlap_threshold, limit=limit)


async def _h_dream_complete() -> Dict[str, Any]:
    """Dream cycle phase 2 — advance last_dream_at after a clean pass
    (clears the nudge). Call ONLY when the whole pass finished; an
    interrupted pass must skip this so the nudge re-fires."""
    from datetime import datetime, timezone
    from .learnings import dream as _dr
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    return _dr.complete(when=now)


async def _h_session_bootstrap(working_dir: Optional[str] = None,
                                max_chars: int = 6000
                                ) -> Dict[str, Any]:
    """Return a markdown block intended for the *first turn* of a
    Claude Code session: always-inject principles + this project's learnings."""
    from .learnings import bootstrap as _bs
    sess = current_session()
    return _bs.bootstrap(
        working_dir=working_dir or sess.working_dir,
        max_chars=max_chars,
    )  # `now` defaults to wall-clock inside bootstrap()


async def _h_absorb_claude_memory(dry_run: bool = False,
                                   source_root: Optional[str] = None,
                                   auto_accept_kinds: Optional[List[str]] = None
                                   ) -> Dict[str, Any]:
    """Walk ~/.claude/projects/*/memory/*.md and import into
    learnings/{accepted,candidates}/. Dedup by content hash."""
    from pathlib import Path as _Path
    from .learnings import absorb_claude as _ac
    sr = _Path(source_root).expanduser() if source_root else None
    return _ac.absorb(dry_run=dry_run, source_root=sr,
                       auto_accept_kinds=auto_accept_kinds)


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
                     "(requires wiki-write).",
                     _h_lint))
    register(ToolDef("atelier_doctor",
                     "Drift diagnostics; optionally remediate.",
                     _h_doctor))
    register(ToolDef("atelier_sync",
                     "Git status / pull / push / commit / commit-push for the vault.",
                     _h_sync))
    register(ToolDef("atelier_promote_propose",
                     "Scan workshop for promote-worthy notes.",
                     _h_promote_propose))
    register(ToolDef("atelier_validate",
                     "Validate frontmatter against schema v4. "
                     "Reports missing required fields, type mismatches, "
                     "and duplicate entry_ids.",
                     _h_validate))
    register(ToolDef("atelier_fix_pending",
                     "Resolve every `entry_id: PENDING` to a stable UUID5.",
                     _h_fix_pending,
                     claim=_claims.Claim.WIKI_WRITE,
                     lock_role=_claims.WriterRole.WIKI))
    register(ToolDef("atelier_index_regen",
                     "Regenerate wiki/index.md from current wiki contents.",
                     _h_index_regen,
                     claim=_claims.Claim.WIKI_WRITE,
                     lock_role=_claims.WriterRole.WIKI))
    register(ToolDef("atelier_clip_image",
                     "Fetch a remote image into the vault and return its "
                     "local + (when configured) CDN URL.",
                     _h_clip_image,
                     claim=_claims.Claim.WIKI_WRITE,
                     lock_role=_claims.WriterRole.WIKI))
    register(ToolDef("atelier_new_doc",
                     "Scaffold a new document. template ∈ "
                     "{product, raw, note, learning}.",
                     _h_new_doc,
                     claim=_claims.Claim.WIKI_WRITE,
                     lock_role=_claims.WriterRole.WIKI))
    register(ToolDef("atelier_prepare_commit",
                     "Pre-commit hygiene: recalculate word_count, "
                     "embedded_assets, edited_at. LLM facets reclass "
                     "is deferred.",
                     _h_prepare_commit,
                     claim=_claims.Claim.WIKI_WRITE,
                     lock_role=_claims.WriterRole.WIKI))
    register(ToolDef("atelier_youtube",
                     "Ingest a YouTube URL into raw/knowledge/. Falls "
                     "back to status=needs-stt when neither captions "
                     "nor OpenAI STT are available.",
                     _h_youtube,
                     claim=_claims.Claim.WIKI_WRITE,
                     lock_role=_claims.WriterRole.WIKI))

    # Write tools — claim + role lock.
    register(ToolDef("atelier_reindex",
                     "Rebuild SQLite projection from markdown.",
                     _h_reindex,
                     claim=_claims.Claim.WIKI_WRITE,
                     lock_role=_claims.WriterRole.WIKI))
    register(ToolDef("atelier_capture",
                     "Append a short note to the librarian inbox.",
                     _h_capture,
                     claim=_claims.Claim.MOBILE_CLAIM,
                     lock_role=_claims.WriterRole.WIKI))
    register(ToolDef("atelier_promote_apply",
                     "Apply a promotion proposal — writes wiki/.",
                     _h_promote_apply,
                     claim=_claims.Claim.PROMOTE_APPLY,
                     lock_role=_claims.WriterRole.WIKI))
    register(ToolDef("atelier_new_product",
                     "Scaffold a new product in workshop/products/.",
                     _h_new_product,
                     claim=_claims.Claim.LEARNINGS_WRITE,
                     lock_role=_claims.WriterRole.LEARNINGS))
    register(ToolDef(
        "atelier_learning_capture",
        "Append a candidate learning to learnings/candidates/. "
        "Permissive — acceptance criteria are checked at promotion time.",
        _h_learning_capture,
        claim=_claims.Claim.CAPTOR_WRITE,
        lock_role=_claims.WriterRole.CAPTOR,
    ))
    register(ToolDef(
        "atelier_learning_review_pending",
        "List candidate learnings with acceptance-criteria self-check results.",
        _h_learning_review_pending,
    ))
    register(ToolDef(
        "atelier_learning_accept",
        "Promote a candidate to learnings/accepted/. Refuses unless every "
        "must-criterion passes (override_unknown=True to bypass unknown).",
        _h_learning_accept,
        claim=_claims.Claim.CURATOR_WRITE,
        lock_role=_claims.WriterRole.CURATOR,
    ))
    register(ToolDef(
        "atelier_learning_archive",
        "Move a candidate to learnings/archived/ with an archive_reason.",
        _h_learning_archive,
        claim=_claims.Claim.CURATOR_WRITE,
        lock_role=_claims.WriterRole.CURATOR,
    ))
    register(ToolDef(
        "atelier_learning_retract",
        "Retract a candidate or accepted learning into learnings/archived/.",
        _h_learning_retract,
        claim=_claims.Claim.CURATOR_WRITE,
        lock_role=_claims.WriterRole.CURATOR,
    ))
    register(ToolDef(
        "atelier_learning_search",
        "Search the learnings domain (status=accepted by default; "
        "filter by project / topic).",
        _h_learning_search,
    ))
    register(ToolDef(
        "atelier_learning_relink",
        "Replace or merge wiki backlinks on an accepted learning.",
        _h_learning_relink,
        claim=_claims.Claim.CURATOR_WRITE,
        lock_role=_claims.WriterRole.CURATOR,
    ))
    register(ToolDef(
        "atelier_absorb_claude_memory",
        "Import Claude Code's per-project auto-memory into "
        "learnings/{accepted,candidates}/. Dedupes by content hash; "
        "re-runs are safe.",
        _h_absorb_claude_memory,
        claim=_claims.Claim.CURATOR_WRITE,
        lock_role=_claims.WriterRole.CURATOR,
    ))
    register(ToolDef(
        "atelier_principle_add",
        "Add a cross-project principle (developer ethos) with rule + why "
        "+ optional evidence backlinks. priority=always-inject is auto-injected "
        "on every session start.",
        _h_principle_add,
        claim=_claims.Claim.CURATOR_WRITE,
        lock_role=_claims.WriterRole.CURATOR,
    ))
    register(ToolDef(
        "atelier_principle_synthesize",
        "Draft a principle from several accepted learnings; rule/why may "
        "be left empty for the caller to fill in.",
        _h_principle_synthesize,
        claim=_claims.Claim.CURATOR_WRITE,
        lock_role=_claims.WriterRole.CURATOR,
    ))
    register(ToolDef(
        "atelier_principle_list",
        "List current principles, optionally filtered by priority/coverage.",
        _h_principle_list,
    ))
    register(ToolDef(
        "atelier_principle_archive",
        "Retire a principle into learnings/archived/.",
        _h_principle_archive,
        claim=_claims.Claim.CURATOR_WRITE,
        lock_role=_claims.WriterRole.CURATOR,
    ))
    register(ToolDef(
        "atelier_principle_review_proposed",
        "Dream cycle ③ — list proposed principle drafts awaiting "
        "promotion (rule preview + evidence).",
        _h_principle_review_proposed,
    ))
    register(ToolDef(
        "atelier_principle_approve",
        "Promote a proposed principle to accepted; optionally set "
        "priority (e.g. always-inject) / coverage.",
        _h_principle_approve,
        claim=_claims.Claim.CURATOR_WRITE,
        lock_role=_claims.WriterRole.CURATOR,
    ))
    register(ToolDef(
        "atelier_principle_reject",
        "Reject a proposed principle into archived/ (never re-proposed).",
        _h_principle_reject,
        claim=_claims.Claim.CURATOR_WRITE,
        lock_role=_claims.WriterRole.CURATOR,
    ))
    register(ToolDef(
        "atelier_session_bootstrap",
        "Return a markdown block for first-turn context injection: "
        "always-inject principles + the working-dir project's learnings. "
        "Intended for a UserPromptSubmit hook with session_id-based dedup.",
        _h_session_bootstrap,
    ))
    register(ToolDef(
        "atelier_recall",
        "Per-turn signal-detector retrieval over the learnings domain. "
        "Returns top-K learnings ranked by hybrid retrieval (lexical BM25 + "
        "semantic vectors, fused by reciprocal rank), with a project-match boost.",
        _h_recall,
    ))
    register(ToolDef(
        "atelier_learning_surfacing_audit",
        "Retrieval observability — list accepted learnings that can no longer "
        "be found by their own concept (gone 'dark'). Read-only; makes silent "
        "omission visible before a self-reorganization pass.",
        _h_surfacing_audit,
    ))
    register(ToolDef(
        "atelier_lateral_plan",
        "Lateral mutator ① (read-only) — tee up concept-tagging work: "
        "untagged learnings with body-derived suggestions, inert tags, and "
        "flag-only near-duplicate groups. The agent refines; apply gates.",
        _h_lateral_plan,
    ))
    register(ToolDef(
        "atelier_lateral_apply",
        "Lateral mutator ② — apply entry_id→tags to canonicals + mirrors, "
        "snapshot-wrapped with the body-echo gate; returns the surfacing "
        "diff (check newly_dark). Merges stay human-gated (no tool).",
        _h_lateral_apply,
        claim=_claims.Claim.CURATOR_WRITE,
        lock_role=_claims.WriterRole.CURATOR,
    ))
    register(ToolDef(
        "atelier_learning_cluster",
        "Dream cycle ① — deterministically group accepted learnings by "
        "shared terms + cross-project spread (≥2 projects). The agent "
        "then generalizes each cluster into a principle.",
        _h_learning_cluster,
    ))
    register(ToolDef(
        "atelier_dream_status",
        "Dream cadence: last_dream_at + accepted learnings accrued since. "
        "Drives the session-start nudge.",
        _h_dream_status,
    ))
    register(ToolDef(
        "atelier_dream_plan",
        "Dream phase 1 — clusters worth synthesizing (member previews + "
        "ready-to-fill synthesize calls; already-covered clusters filtered).",
        _h_dream_plan,
    ))
    register(ToolDef(
        "atelier_dream_complete",
        "Dream phase 2 — advance last_dream_at after a clean pass "
        "(clears the nudge). Skip on an interrupted pass.",
        _h_dream_complete,
        claim=_claims.Claim.CURATOR_WRITE,
        lock_role=_claims.WriterRole.CURATOR,
    ))


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
