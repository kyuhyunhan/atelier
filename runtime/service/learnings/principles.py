"""Cross-project principles — the developer-ethos tier of learnings.

RFC 0005 §7.1 — a *principle* is NOT a separate content type in a separate
directory. It is one **v7 Claim** at the top of the surfacing ladder:
`domain:operational`, `surfacing:always` (always-inject) — the cross-project
generalization that earns the T0 budget. The legacy `learnings/principles/` and
`learnings/archived/` FILE lifecycle is retired; the principle tier collapses to
CLAIM FIELDS:

    surfacing:  always  (always-inject) | proactive (on-relevant-prompt|manual-only)
    ac_status:  passed  (accepted)      | pending (proposed)   | retracted (archived)

The `priority` / `coverage` markers are kept as flat fields on the claim so the
session bootstrap (`always-inject`) and the principle MCP surface keep working
unchanged — but they no longer pick a directory; they are facets on the node.

Two creation paths, both born-as-claim (written via `claims_io`):

1. **manual** — `add()` mints a principle claim directly with body provided by
   the caller (the user, or Claude synthesizing in a conversation).

2. **synthesize** — `synthesize()` reads several accepted operational claims,
   builds a draft principle claim with Evidence backlinks + `derived_from` links,
   and an empty Rule / Why scaffold for the caller to fill in.

`priority: always-inject` principles are picked up by the session bootstrap
(PR-25) at every session start (via `list_all`, a tier query over claims).

No file is ever moved: accept / approve / archive / reject are FIELD transitions
in place (entry_id preserved, content_hash re-derived) via `claims_io`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ...index import parse as _parse
from ...util import config as _config
from . import claims_io as _claims
from . import store as _store


_SLUG_RX = re.compile(r"[^a-z0-9-]+")

# A principle claim is an operational claim that carries this marker field — the
# ethos tier. It is what distinguishes a "principle" from an ordinary captured
# operational claim in the same flat claim store (RFC 0005 §7.1).
_PRINCIPLE_FIELD = "principle_tier"

# priority → surfacing tier. always-inject is the T0 ethos (surfacing:always);
# the lower priorities live at proactive (surfaced on relevance, not every turn).
_PRIORITY_TO_SURFACING = {
    "always-inject": _claims.TIER_ALWAYS,
    "on-relevant-prompt": _claims.TIER_PROACTIVE,
    "manual-only": _claims.TIER_PROACTIVE,
}


def _vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _slugify(value: str, *, fallback: str = "principle") -> str:
    text = (value or fallback).strip().lower()
    text = _SLUG_RX.sub("-", text).strip("-")
    return text[:80] or fallback


def _evidence_to_links(evidence: Iterable[str]) -> List[str]:
    """Normalize evidence entries (de-duplicate, preserve order)."""
    out: List[str] = []
    for raw in evidence or []:
        e = str(raw).strip().strip("[]")
        if e:
            out.append(e)
    return list(dict.fromkeys(out))


def _render_body(rule: Optional[str], why: Optional[str],
                  evidence_links: List[str],
                  notes: Optional[str] = None) -> str:
    rule_s = (rule or "").strip()
    why_s = (why or "").strip()
    lines: List[str] = []
    lines.append("## Rule")
    lines.append(rule_s if rule_s else "(fill in: the principle in one or two sentences)")
    lines.append("")
    lines.append("## Why")
    lines.append(why_s if why_s else "(fill in: the recurring reason this principle holds)")
    if notes and notes.strip():
        lines.append("")
        lines.append("## Notes")
        lines.append(notes.strip())
    if evidence_links:
        lines.append("")
        lines.append("## Evidence")
        for link in evidence_links:
            lines.append(f"- [[{link}]]")
    return "\n".join(lines) + "\n"


# ── claim enumeration (the principle tier is a query over claims) ─────────────


def _is_principle(fm: Dict[str, Any]) -> bool:
    """A principle claim carries the ethos marker field. (Back-compat: a v7
    claim that lacks the marker but still carries the legacy `coverage`/
    `priority` shape from an earlier migration also counts.)"""
    if not _claims.is_claim(fm):
        return False
    if fm.get(_PRINCIPLE_FIELD):
        return True
    return bool(fm.get("coverage") and fm.get("priority"))


def _iter_principle_claims(vault: Path) -> Iterable[Tuple[Path, Dict[str, Any], str]]:
    for p in _claims.iter_claim_files(vault):
        got = _claims.read_claim(p)
        if got is None:
            continue
        fm, body = got
        if _is_principle(fm):
            yield p, fm, body


def _status_of(fm: Dict[str, Any]) -> str:
    """Map the claim's ac_status field back to the legacy status vocabulary the
    MCP surface and bootstrap speak (proposed | accepted | archived)."""
    ac = str(fm.get("ac_status") or "")
    if ac == "passed":
        return "accepted"
    if ac in ("failed", "retracted"):
        return "archived"
    return "proposed"


def _find(vault: Path, slug_or_id: str) -> Tuple[Path, Dict[str, Any], str]:
    """Locate a principle claim by its `principle_slug` facet, then by entry_id
    or file stem (the claims_io fallbacks). The MCP surface speaks the slug a
    caller saw in a listing — that is the `principle_slug` field, not the
    content-addressed file stem — so it is tried first."""
    needle = str(slug_or_id).removesuffix(".md")
    for p, fm, body in _iter_principle_claims(vault):
        if fm.get("principle_slug") == needle:
            return p, fm, body
    found = _claims.find_claim_by_slug_or_id(slug_or_id, vault)
    if found is None:
        raise FileNotFoundError(f"no principle claim matches {slug_or_id!r}")
    return found


# ── manual add ─────────────────────────────────────────────────────────────


def add(*, title: str, rule: str, why: str,
        evidence: Optional[List[str]] = None,
        coverage: str = "cross-project",
        priority: str = "on-relevant-prompt",
        target_topic: Optional[str] = None,
        notes: Optional[str] = None,
        slug: Optional[str] = None,
        status: str = "accepted",
        source_entry_ids: Optional[List[str]] = None,
        cluster_key: Optional[str] = None,
        ) -> Dict[str, Any]:
    """Mint a principle as a v7 operational Claim (RFC 0005 §7.1).

    `status=accepted` → `ac_status:passed`; `status=proposed` (the dream draft
    path) → `ac_status:pending`. `priority` maps to the `surfacing` tier
    (always-inject → always; else proactive) and is kept as a flat field so the
    bootstrap / MCP filters keep working. NO legacy principles/ file is written.
    """
    if coverage not in ("cross-project", "single-project", "single-topic"):
        raise ValueError(f"unknown coverage: {coverage!r}")
    if priority not in ("always-inject", "on-relevant-prompt", "manual-only"):
        raise ValueError(f"unknown priority: {priority!r}")
    if status not in ("proposed", "accepted"):
        raise ValueError(f"add() status must be proposed|accepted, got {status!r}")

    vault = _vault_root()
    chosen_slug = slug or _slugify(title, fallback="principle")

    # A principle's statement is its title (the durable assertion handle). The
    # claim id is content-addressed on (statement | derived_from), so a caller
    # who passes an explicit `slug` and re-adds the same title collides — mirror
    # the old FileExistsError contract on that explicit-slug path.
    statement = " ".join((title or chosen_slug).split())[:400]
    links = _evidence_to_links(evidence or [])
    now = _now_iso()
    surfacing = _PRIORITY_TO_SURFACING[priority]
    ac_status = "passed" if status == "accepted" else "pending"

    # Refuse a same-slug collision (legacy add() contract). We detect it by an
    # existing principle claim whose stable slug-stem matches `slug`.
    if slug:
        for p, _fm, _b in _iter_principle_claims(vault):
            if _fm.get("principle_slug") == chosen_slug:
                raise FileExistsError(str(p))

    extra: Dict[str, Any] = {
        _PRINCIPLE_FIELD: True,
        "principle_slug": chosen_slug,
        "title": title,
        "coverage": coverage,
        "priority": priority,
        "evidence": links,
        "ac_results": {"origin": f"principles.add:{status}"},
    }
    if status == "accepted":
        extra["accepted_at"] = now
    else:
        extra["proposed_at"] = now
    if source_entry_ids:
        extra["source_entry_ids"] = list(dict.fromkeys(source_entry_ids))
    if cluster_key:
        extra["cluster_key"] = cluster_key
    if target_topic:
        extra["target_topic"] = _slugify(target_topic)

    # derived_from: the source claims when given (the synthesis evidence), else
    # a thin manual session Source so the PROV chain is never empty. We always
    # mint a thin Source as the id-discriminating anchor; when source claims are
    # given, `derived_from` is overridden (via extra) to point at them.
    src = _claims.mint_session_source(
        statement=statement, hook="principle-add",
        agent_kind="curator", vault=vault,
    )
    source_eid = src["entry_id"]
    if source_entry_ids:
        extra["derived_from"] = list(dict.fromkeys(source_entry_ids))

    claim = _claims.write_operational_claim(
        statement=statement, source_entry_id=source_eid,
        body=_render_body(rule, why, links, notes),
        generated_by="dream" if status == "proposed" else "promote",
        attributed_to="curator", agent_kind="curator",
        hook="principle-add", observation_kind="feedback",
        why_status="present" if (why or "").strip() else "missing",
        surfacing=surfacing, ac_status=ac_status,
        extra=extra, vault=vault,
    )

    _append_log(vault, f"- {now}  principle-{status}  {chosen_slug}  "
                       f"priority={priority}")

    return {"slug": chosen_slug, "path": claim["path"],
            "entry_id": claim["entry_id"],
            "status": status, "evidence": links}


# ── synthesize from existing accepted claims ───────────────────────────────


def _resolve_source(vault: Path, slug_or_path: str) -> Optional[Path]:
    """Find an accepted learning (now a v7 claim, RFC 0005 §7.1) by entry_id,
    relative path, or file stem. Falls back to the legacy notes/ store on disk."""
    needle = slug_or_path.removesuffix(".md")
    by_id = _claims.find_claim_by_entry_id(needle, vault)
    if by_id is not None:
        return by_id[0]
    # Direct relative path under either accepted root (legacy notes/).
    for root in _store.accepted_roots(vault):
        candidate = root / slug_or_path
        if candidate.exists():
            return candidate
    for p in _store.iter_accepted_files(vault):
        if p.stem == needle:
            return p
    # Stem match against the claim store.
    for p in _claims.iter_claim_files(vault):
        if p.stem == needle:
            return p
    return None


def synthesize(*, source_slugs: List[str],
               title: Optional[str] = None,
               rule: Optional[str] = None,
               why: Optional[str] = None,
               coverage: str = "cross-project",
               priority: str = "on-relevant-prompt",
               notes: Optional[str] = None,
               slug: Optional[str] = None,
               status: str = "proposed",
               source_entry_ids: Optional[List[str]] = None,
               cluster_key: Optional[str] = None,
               skip_if_covered: bool = True,
               overlap_threshold: float = 0.6,
               ) -> Dict[str, Any]:
    """Draft a principle CLAIM from multiple accepted-claim sources (RFC 0005
    §7.1). Default `status="proposed"` → `ac_status:pending` (the dream-cycle
    draft; not injected until a curator approves). Resolves each `source_slugs[i]`
    to its claim node and assembles an Evidence list of vault-relative paths.

    Idempotent: when `skip_if_covered` and `source_entry_ids` are given, the
    draft is skipped if an existing principle claim already covers >=
    `overlap_threshold` of these ids.
    """
    vault = _vault_root()
    if not source_slugs:
        raise ValueError("synthesize requires at least one source slug")

    if skip_if_covered and source_entry_ids:
        covering = find_covering_principle(
            source_entry_ids, overlap_threshold=overlap_threshold, vault=vault)
        if covering is not None:
            return {"skipped": True, "reason": "already-covered",
                    "covered_by": covering}

    resolved: List[str] = []      # vault-relative paths
    resolved_ids: List[str] = []  # source claim entry_ids (for derived_from)
    missing: List[str] = []
    for s in source_slugs:
        p = _resolve_source(vault, s)
        if p is None:
            missing.append(s)
            continue
        try:
            rel = p.relative_to(vault).as_posix()
        except ValueError:
            rel = s
        resolved.append(rel)
        got = _claims.read_claim(p)
        if got is not None and got[0].get("entry_id"):
            resolved_ids.append(str(got[0]["entry_id"]))
    if missing:
        raise FileNotFoundError(
            f"could not resolve evidence sources: {missing}"
        )

    inferred_title = title or _suggest_title(vault, resolved)
    out = add(
        title=inferred_title,
        rule=rule or "",
        why=why or "",
        evidence=resolved,
        coverage=coverage,
        priority=priority,
        notes=notes,
        slug=slug,
        status=status,
        source_entry_ids=source_entry_ids or (resolved_ids or None),
        cluster_key=cluster_key,
    )
    out["skipped"] = False
    out["evidence_resolved"] = resolved
    out["fields_to_fill"] = [k for k in ("rule", "why")
                              if not (rule if k == "rule" else why)]
    return out


# ── idempotent dedup ────────────────────────────────────────────────────────


def find_covering_principle(member_entry_ids: List[str], *,
                            overlap_threshold: float = 0.6,
                            vault: Optional[Path] = None
                            ) -> Optional[Dict[str, Any]]:
    """Return the first existing principle claim (proposed/accepted/archived)
    whose `source_entry_ids` cover >= `overlap_threshold` of `member_entry_ids`,
    else None. Archived ones are checked too, so a rejected cluster is never
    re-proposed (resilience rule #3)."""
    vault = vault or _vault_root()
    target = set(member_entry_ids)
    if not target:
        return None
    for p, fm, _body in _iter_principle_claims(vault):
        existing = set(fm.get("source_entry_ids") or [])
        if not existing:
            continue
        overlap = len(target & existing) / len(target)
        if overlap >= overlap_threshold:
            return {
                "slug": fm.get("principle_slug") or p.stem,
                "status": _status_of(fm),
                "overlap": round(overlap, 3),
                "path": str(p),
            }
    return None


def _suggest_title(vault: Path, resolved_rel_paths: List[str]) -> str:
    """Borrow the first source's title/statement when the caller gives none."""
    if not resolved_rel_paths:
        return "principle"
    first = vault / resolved_rel_paths[0]
    if first.exists():
        try:
            fm, _ = _parse.split_frontmatter(first.read_text(encoding="utf-8"))
            t = fm.get("title") or fm.get("statement") or fm.get("name")
            if isinstance(t, str) and t.strip():
                return f"principle: {t.strip()}"
        except Exception:    # pragma: no cover
            pass
    return f"principle: {Path(resolved_rel_paths[0]).stem}"


# ── list ─────────────────────────────────────────────────────────────────────


@dataclass
class PrincipleSummary:
    slug: str
    title: str
    coverage: str
    priority: str
    evidence_count: int
    path: str


def list_all(*, priority: Optional[str] = None,
             coverage: Optional[str] = None,
             status: Optional[str] = None) -> List[Dict[str, Any]]:
    """List principle claims. Filter by priority / coverage / status (the legacy
    status vocabulary mapped from ac_status). `status='accepted'` (passed) is what
    session_bootstrap passes so a `proposed` (pending) draft is never injected."""
    vault = _vault_root()
    out: List[Dict[str, Any]] = []
    # An archived principle (retracted) is excluded from the default listing,
    # mirroring the old "archived files live elsewhere" behavior.
    for p, fm, _body in _iter_principle_claims(vault):
        st = _status_of(fm)
        if status is None and st == "archived":
            continue
        if priority and fm.get("priority") != priority:
            continue
        if coverage and fm.get("coverage") != coverage:
            continue
        if status and st != status:
            continue
        out.append({
            "slug": fm.get("principle_slug") or p.stem,
            "title": fm.get("title") or fm.get("statement") or p.stem,
            "coverage": fm.get("coverage"),
            "priority": fm.get("priority"),
            "status": st,
            "evidence": list(fm.get("evidence") or []),
            "path": str(p),
        })
    return out


# ── archive (FIELD transition: ac_status → retracted) ─────────────────────────


def archive(*, slug: str, reason: str) -> Dict[str, Any]:
    """Archive a principle: `ac_status → retracted` IN PLACE (RFC 0005 §7.1 — a
    field transition, not a directory move). entry_id preserved, file unmoved."""
    vault = _vault_root()
    path, fm, body = _find(vault, slug)
    new_fm = _claims.set_ac_status(path, fm, body, new_status="retracted",
                                   archive_reason=reason)
    chosen_slug = fm.get("principle_slug") or path.stem
    _append_log(vault, f"- {_now_iso()}  principle-archive  {chosen_slug}  "
                       f"reason={reason!r}")
    return {"path": str(path), "slug": chosen_slug,
            "entry_id": new_fm.get("entry_id"), "status": "archived"}


# ── proposed review / approve / reject (dream cycle ③) ──────────────────────


def _rule_one_liner(body: str) -> str:
    m = re.search(r"^##+\s*Rule\b", body, re.M | re.I)
    if not m:
        return ""
    for line in body[m.end():].lstrip().splitlines():
        s = line.strip()
        if s:
            return s
    return ""


def review_proposed(*, limit: int = 50) -> Dict[str, Any]:
    """List principle claims awaiting promotion (ac_status:pending) with their
    evidence and a one-line Rule preview, for a fast batch review."""
    vault = _vault_root()
    items: List[Dict[str, Any]] = []
    for p, fm, body in _iter_principle_claims(vault):
        if _status_of(fm) != "proposed":
            continue
        items.append({
            "slug": fm.get("principle_slug") or p.stem,
            "title": fm.get("title") or fm.get("statement") or p.stem,
            "coverage": fm.get("coverage"),
            "priority": fm.get("priority"),
            "rule": _rule_one_liner(body),
            "evidence": list(fm.get("evidence") or []),
            "source_entry_ids": list(fm.get("source_entry_ids") or []),
            "cluster_key": fm.get("cluster_key"),
            "proposed_at": fm.get("proposed_at"),
            "path": str(p),
        })
        if len(items) >= limit:
            break
    return {"count": len(items), "items": items, "vault": str(vault)}


def approve(*, slug: str,
            priority: Optional[str] = None,
            coverage: Optional[str] = None) -> Dict[str, Any]:
    """Promote a proposed principle to accepted: `ac_status pending → passed` IN
    PLACE (RFC 0005 §7.1). Optionally override priority / coverage at approval
    time (the common edit is priority=always-inject, which also raises the
    `surfacing` tier to `always`)."""
    vault = _vault_root()
    path, fm, body = _find(vault, slug)
    if _status_of(fm) != "proposed":
        raise ValueError(
            f"{slug}: only proposed principles can be approved "
            f"(status={_status_of(fm)!r})"
        )
    if priority is not None and priority not in (
            "always-inject", "on-relevant-prompt", "manual-only"):
        raise ValueError(f"unknown priority: {priority!r}")
    if coverage is not None and coverage not in (
            "cross-project", "single-project", "single-topic"):
        raise ValueError(f"unknown coverage: {coverage!r}")

    now = _now_iso()
    # 1) acceptance gate: ac_status pending → passed (entry_id preserved).
    new_fm = _claims.set_ac_status(path, fm, body, new_status="passed")
    # 2) record principle facet edits + the accepted stamp on the same node.
    new_fm = dict(new_fm)
    new_fm["accepted_at"] = now
    new_fm.pop("proposed_at", None)
    if priority is not None:
        new_fm["priority"] = priority
    if coverage is not None:
        new_fm["coverage"] = coverage
    _rewrite(path, new_fm, body)
    # 3) surfacing tier follows priority (always-inject → always).
    eff_priority = new_fm.get("priority") or "on-relevant-prompt"
    target_tier = _PRIORITY_TO_SURFACING.get(eff_priority, _claims.TIER_PROACTIVE)
    got = _claims.read_claim(path)
    if got is not None and _claims.surfacing_of(got[0]) != target_tier:
        _claims.set_surfacing(path, got[0], got[1],
                              new_tier=target_tier, generated_by="promote")

    chosen_slug = new_fm.get("principle_slug") or path.stem
    _append_log(vault, f"- {now}  principle-approve  {chosen_slug}  "
                       f"priority={new_fm.get('priority')}")
    return {"slug": chosen_slug, "path": str(path),
            "status": "accepted", "priority": new_fm.get("priority")}


def reject(*, slug: str, reason: str = "rejected") -> Dict[str, Any]:
    """Reject a proposed principle → archived (`ac_status → retracted`). The
    dedup check in synthesize() consults retracted principle claims, so a rejected
    cluster is never re-proposed by a later dream pass."""
    vault = _vault_root()
    path, fm, _body = _find(vault, slug)
    if _status_of(fm) != "proposed":
        raise ValueError(
            f"{slug}: only proposed principles can be rejected "
            f"(status={_status_of(fm)!r}); use archive() for accepted ones"
        )
    out = archive(slug=slug, reason=f"rejected: {reason}")
    out["status"] = "archived"
    return out


# ── helpers ────────────────────────────────────────────────────────────────


def _rewrite(path: Path, fm: Dict[str, Any], body: str) -> None:
    """Re-emit a principle claim with re-derived content_hash (facet update)."""
    import yaml
    fm = dict(fm)
    fm.pop("content_hash", None)
    fm.pop("_prev_surfacing", None)
    fm["content_hash"] = _claims._content_hash(fm)
    serialized = yaml.safe_dump(fm, sort_keys=True, allow_unicode=True,
                                default_flow_style=False)
    path.write_text(f"---\n{serialized}---\n\n{body.strip()}\n", encoding="utf-8")


def _append_log(vault: Path, line: str) -> None:
    log = _store.learning_root(vault) / "log.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")
