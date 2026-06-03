"""atelier CLI — thin argparse dispatcher.

All commands route through runtime.service.api. The service layer is the
single funnel for CLI, MCP (v0.2), and HTTPS (v0.2) surfaces.
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from .service import api
from .util import logging as log


def _cmd_setup(args: argparse.Namespace) -> int:
    from .util import config, db
    cfg = config.load()
    conn = db.connect()
    try:
        schema_version = db.get_meta(conn, "schema_version")
    finally:
        conn.close()
    log.info("setup.ok", db=str(config.DB_PATH), schema_version=schema_version)
    for name, sp in cfg.spaces.items():
        marker = "✓" if sp.local.exists() else "✗ (missing)"
        print(f"  space: {name:10}  local={sp.local}  {marker}")
    return 0


def _cmd_reindex(args: argparse.Namespace) -> int:
    statses = api.reindex(space=args.space, full=args.full)
    for s in statses:
        print(
            f"[{s['space']}] pages_changed={s['pages_changed']} "
            f"chunks={s['chunks_written']} links={s['links_written']} "
            f"entities={s['entities_upserted']}"
        )
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    hits = api.search(args.query, space=args.space, limit=args.limit,
                      fallback=args.fallback)
    if not hits:
        print("(no results)")
        return 0
    for h in hits:
        title = h.get("title") or "(untitled)"
        print(f"{h['space']:8} {h['page_type']:14} {h['slug']}")
        print(f"   {title}")
        if h.get("snippet"):
            print(f"   {h['snippet']}")
        if args.explain:
            print(f"   rank={h['rank']:.3f}")
        print()
    return 0


def _cmd_links(args: argparse.Namespace) -> int:
    from .search import graph
    from .util import db
    conn = db.connect()
    try:
        if args.outbound:
            for s in graph.outbound(conn, args.slug):
                print(s)
        elif args.inbound:
            for s in graph.inbound(conn, args.slug):
                print(s)
        else:
            print("== inbound ==")
            for s in graph.inbound(conn, args.slug):
                print(f"  {s}")
            print("== outbound ==")
            for s in graph.outbound(conn, args.slug):
                print(f"  {s}")
    finally:
        conn.close()
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    from .util import db
    conn = db.connect()
    try:
        sql = "SELECT slug, page_type, space FROM pages WHERE 1=1"
        params: list = []
        if args.type:
            sql += " AND page_type=?"; params.append(args.type)
        if args.space:
            sql += " AND space=?"; params.append(args.space)
        sql += " ORDER BY space, slug"
        for r in conn.execute(sql, params):
            print(f"{r['space']:8} {r['page_type']:14} {r['slug']}")
    finally:
        conn.close()
    return 0


def _cmd_lint(args: argparse.Namespace) -> int:
    rules = args.rule.split(",") if args.rule else None
    out = api.lint(space=args.space, rule_ids=rules, apply_fixes=args.fix)
    print(f"Rules run: {', '.join(out['rules_run']) or '(none)'}")
    print(f"Findings: {len(out['findings'])}  ({out['by_severity']})")
    if args.fix:
        print(f"Fixes applied: {out['fixes_applied']}")
    if args.show:
        for f in out["findings"][:args.show]:
            slug = f["page_slug"] or "-"
            print(f"  [{f['severity']}] {f['rule_id']} {slug}: {f['message']}")
    return 1 if out["failed"] else 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    out = api.doctor(remediate=args.remediate, max_usd=args.max_usd)
    failed = False
    for d in out["diagnoses"]:
        marker = {"OK": "✓", "WARN": "!", "FAIL": "✗"}.get(d["severity"], "?")
        print(f"  {marker} [{d['severity']:4}] {d['id']} {d['name']}: {d['message']}")
        if d["severity"] == "FAIL":
            failed = True
    for r in out.get("remediations", []):
        print(f"  → {r['diagnosis_id']} {r['action']}: "
              f"{'ok' if r['success'] else 'failed'} (${r['cost_usd']:.4f})")
    return 1 if failed else 0


def _cmd_sync(args: argparse.Namespace) -> int:
    out = api.sync(args.action, space=args.space,
                   message=getattr(args, "message", None))
    if args.action == "status":
        for st in out["status"]:
            print(f"  {st['space']:10} clean={st['clean']} ahead={st['ahead']} "
                  f"behind={st['behind']} unstaged={len(st['unstaged'])} "
                  f"untracked={len(st['untracked'])}")
    elif args.action in ("commit", "commit-push"):
        if out.get("skipped"):
            print(f"  skipped: {out['skipped']}")
        elif out.get("committed"):
            if out.get("pushed"):
                tail = " pushed"
            elif out.get("push_error"):
                tail = " push-failed (surfaced; local commit kept)"
            else:
                tail = ""
            print(f"  committed {out.get('sha', '')[:9]}{tail}")
        else:
            print("  nothing to commit")
    return 0


def _cmd_capture(args: argparse.Namespace) -> int:
    out = api.capture_text(args.text, source=args.source, title=args.title)
    print(out["path"])
    return 0


def _cmd_new_product(args: argparse.Namespace) -> int:
    from .service import api as _api  # capture endpoint reused
    from .util import config
    cfg = config.load()
    builder_space = cfg.space_by_role("builder-territory").local
    product_dir = builder_space / "products" / args.name
    if product_dir.exists():
        log.error("product already exists", path=str(product_dir))
        return 1
    product_dir.mkdir(parents=True)
    from datetime import datetime, timezone
    import uuid as _uuid
    now = datetime.now(timezone.utc).date().isoformat()
    eid = _uuid.uuid5(_uuid.NAMESPACE_DNS, f"workshop:products/{args.name}")
    (product_dir / "README.md").write_text(
        f"---\n"
        f"schema_version: 4\n"
        f"entry_id: {eid}\n"
        f"title: {args.name}\n"
        f"type: product\n"
        f"status: active\n"
        f"sensitivity: private\n"
        f"created: {now}\n"
        f"updated: {now}\n"
        f"summary: \"\"\n"
        f"---\n\n"
        f"# {args.name}\n\n"
        f"(product description)\n",
        encoding="utf-8",
    )
    print(f"Created {product_dir}/README.md")
    return 0


def _cmd_dream(args: argparse.Namespace) -> int:
    """Dream cycle convenience. `atelier dream` prints the plan (clusters
    worth synthesizing); the actual generalization is done by an agent
    calling atelier_principle_synthesize. `atelier dream --complete`
    advances the cadence after a finished pass."""
    from .service.learnings import dream as _dr
    import json as _json
    if args.status:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        info = _dr.nudge_info(now=now)
        if args.json:
            print(_json.dumps(info, ensure_ascii=False))
        else:
            # one compact line for the statusline (empty when nothing due)
            print(info["short"])
        return 0
    if args.complete:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        out = _dr.complete(when=now)
        print(f"dream complete @ {out['last_dream_at']}  "
              f"(proposed awaiting review: {out['proposed_awaiting_review']})")
        return 0
    plan = _dr.plan(min_projects=args.min_projects, limit=args.limit)
    if args.json:
        print(_json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    print(f"accepted scanned: {plan['accepted_scanned']}")
    print(f"clusters to synthesize: {plan['candidate_count']}  "
          f"(skipped already-covered: {plan['skipped_already_covered']})")
    for c in plan["clusters"]:
        print(f"\n● [{c['size']}n {len(c['projects'])}proj] "
              f"{c['projects']}  terms={c['shared_terms'][:5]}")
        for m in c["members"][:6]:
            print(f"    - {m['title']}  ({m.get('project') or '-'})")
        if len(c["members"]) > 6:
            print(f"    … +{len(c['members']) - 6} more")
    print("\nNext: an agent reads these and calls atelier_principle_synthesize "
          "per cluster, then `atelier dream --complete`.")
    return 0


def _cmd_inject_preview(args: argparse.Namespace) -> int:
    """Print exactly what atelier would inject for a session whose working
    directory is `--cwd`: the session-start bootstrap block, and (with
    --query) the per-turn recall block. Read-only — resolves the project
    and renders the same markdown the hooks emit, without a session or any
    side effects. Use it to see what a given client actually receives."""
    import os
    from .service.learnings import bootstrap as _bs
    from .service.learnings import recall as _rc
    from .service.learnings import project as _proj

    cwd = args.cwd or os.getcwd()
    res = _proj.resolve_project(cwd)
    print(f"# project={res.slug!r}  source={res.source}  known={res.known}")
    print(f"# cwd={cwd}")

    boot = _bs.bootstrap(working_dir=cwd, max_chars=args.max_chars)
    print("\n===== session-start bootstrap =====\n")
    print(boot["markdown"])

    if args.query:
        rc = _rc.recall(query=args.query, project=res.slug,
                        max_chars=args.max_chars)
        print(f"\n===== per-turn recall (query: {args.query!r}) =====\n")
        print(rc["markdown"] or "_(no recall hits)_")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    """Long-running asyncio supervisor. Each --<transport> flag opts in;
    with no flags the process idles (useful for smoke-testing the
    supervisor itself)."""
    from .service import server
    # Configure the file sink BEFORE importing transports: in stdio mode no
    # handler may touch stdout (JSON-RPC frames), and uvicorn/mcp library logs
    # are bridged into the same atelier.log.
    log.configure(stdio=args.stdio, bridge_libraries=True)
    if args.stdio:
        from .service import mcp_stdio  # noqa: F401  (registers on import)
    if args.http:
        from .service import mcp_http   # noqa: F401  (PR-4)
    # Background subsystem: self-gates on config vault.auto_commit.enabled.
    from .service import vault_autosync  # noqa: F401  (registers on import)
    return server.run()


def _cmd_promote(args: argparse.Namespace) -> int:
    if args.action == "propose":
        out = api.promote_propose()
        print(f"Proposal written: {out.get('path', '(no proposal)')}")
    elif args.action == "apply":
        out = api.promote_apply(args.proposal)
        print(f"Applied: {out.get('applied', False)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="atelier")
    p.add_argument("--verbose", "-v", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup");                  s.set_defaults(func=_cmd_setup)

    s = sub.add_parser("reindex")
    s.add_argument("--space"); s.add_argument("--full", action="store_true")
    s.add_argument("--incremental", action="store_true", default=True)
    s.set_defaults(func=_cmd_reindex)

    s = sub.add_parser("search")
    s.add_argument("query"); s.add_argument("--space"); s.add_argument("--limit", type=int, default=20)
    s.add_argument("--explain", action="store_true"); s.add_argument("--fallback", action="store_true")
    s.set_defaults(func=_cmd_search)

    s = sub.add_parser("links")
    s.add_argument("slug")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--inbound", action="store_true"); g.add_argument("--outbound", action="store_true")
    s.set_defaults(func=_cmd_links)

    s = sub.add_parser("list")
    s.add_argument("--type"); s.add_argument("--space")
    s.set_defaults(func=_cmd_list)

    s = sub.add_parser("lint")
    s.add_argument("--space"); s.add_argument("--rule"); s.add_argument("--fix", action="store_true")
    s.add_argument("--show", type=int, default=20)
    s.set_defaults(func=_cmd_lint)

    s = sub.add_parser("doctor")
    s.add_argument("--remediate", action="store_true")
    s.add_argument("--max-usd", type=float, default=0.0)
    s.set_defaults(func=_cmd_doctor)

    s = sub.add_parser("sync")
    s.add_argument("action",
                   choices=["status", "pull", "push", "commit", "commit-push"])
    s.add_argument("--space")
    s.add_argument("--message", help="commit subject (commit/commit-push only)")
    s.set_defaults(func=_cmd_sync)

    s = sub.add_parser("capture")
    s.add_argument("--text", required=True); s.add_argument("--source", default="manual")
    s.add_argument("--title")
    s.set_defaults(func=_cmd_capture)

    s = sub.add_parser("new-product")
    s.add_argument("name")
    s.set_defaults(func=_cmd_new_product)

    s = sub.add_parser("dream",
                       help="dream cycle: print clusters to synthesize, or "
                            "--complete to advance the cadence")
    s.add_argument("--complete", action="store_true",
                   help="mark the dream pass complete (advance last_dream_at)")
    s.add_argument("--status", action="store_true",
                   help="print a compact one-line dream status (for statusline / "
                        "SessionStart hook); empty when nothing is due")
    s.add_argument("--min-projects", type=int, default=2)
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--json", action="store_true",
                   help="emit the full machine-readable plan")
    s.set_defaults(func=_cmd_dream)

    s = sub.add_parser("inject-preview",
                       help="preview the context atelier would inject for a "
                            "working dir (bootstrap + optional recall)")
    s.add_argument("--cwd", help="working directory to resolve a project for "
                                 "(default: current directory)")
    s.add_argument("--query", help="also preview per-turn recall for this prompt")
    s.add_argument("--max-chars", type=int, default=6000)
    s.set_defaults(func=_cmd_inject_preview)

    s = sub.add_parser("serve",
                       help="run the long-running engine (MCP stdio + HTTP)")
    s.add_argument("--stdio", action="store_true",
                   help="enable MCP stdio transport (for Claude Code subprocess)")
    s.add_argument("--http", action="store_true",
                   help="enable MCP HTTP transport (localhost, bearer-authenticated)")
    s.set_defaults(func=_cmd_serve)

    s = sub.add_parser("promote")
    s.add_argument("action", choices=["propose", "apply"])
    s.add_argument("--proposal", help="path to a proposal file (for apply)")
    s.set_defaults(func=_cmd_promote)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    log.configure(level="debug" if args.verbose else None)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        log.warn("interrupted")
        return 130
    except Exception as e:
        log.error(type(e).__name__, detail=str(e))
        if args.verbose:
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
