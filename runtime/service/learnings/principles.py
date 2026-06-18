"""Cross-project principles — the developer-ethos tier of learnings.

A *principle* generalizes recurring per-project learnings into a
universal rule that the developer adopts across all projects. Principles
live at `<vault>/provenance/learning/principles/<slug>.md` and carry an `evidence`
array linking back to the by-project learnings that ground them.

Two creation paths:

1. **manual** — `add()` writes a principle directly with body provided
   by the caller (the user, or Claude synthesizing in a conversation).

2. **synthesize** — `synthesize()` reads several existing accepted
   learnings, builds a draft principle with Evidence backlinks, and an
   empty Rule / Why scaffold for the caller to fill in. The caller may
   pass `rule`/`why` to fill them inline.

`priority: always-inject` principles are picked up by the session
bootstrap (PR-25) at every session start.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from ...index import parse as _parse
from ...structure import resolver as _structure
from ...util import config as _config
from . import store as _store


_SLUG_RX = re.compile(r"[^a-z0-9-]+")


def _vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _principles_dir(vault: Optional[Path] = None) -> Path:
    return _store.learning_root(vault or _vault_root()) / "principles"


def _archived_dir(vault: Optional[Path] = None) -> Path:
    return _store.learning_root(vault or _vault_root()) / "archived"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _slugify(value: str, *, fallback: str = "principle") -> str:
    text = (value or fallback).strip().lower()
    text = _SLUG_RX.sub("-", text).strip("-")
    return text[:80] or fallback


def _entry_id(slug: str) -> str:
    return _structure.entry_id("principle", slug=slug)


def _serialize(fm: Dict[str, Any], body: str) -> str:
    serialized = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    return f"---\n{serialized}\n---\n{body}"


def _atomic_write(path: Path, text: str) -> None:
    """Write `text` to `path` atomically: write a sibling .tmp then
    os.rename (atomic on POSIX). A power loss mid-write leaves only the
    .tmp — never a half-written target. (Dream-cycle resilience rule #2.)"""
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _evidence_to_links(evidence: Iterable[str]) -> List[str]:
    """Normalize evidence entries into wiki-style backlinks."""
    out: List[str] = []
    for raw in evidence or []:
        e = str(raw).strip().strip("[]")
        if not e:
            continue
        if e.startswith("learnings/") or "/" in e:
            out.append(e)
        else:
            out.append(e)
    # de-duplicate, preserve order
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
    if coverage not in ("cross-project", "single-project", "single-topic"):
        raise ValueError(f"unknown coverage: {coverage!r}")
    if priority not in ("always-inject", "on-relevant-prompt", "manual-only"):
        raise ValueError(f"unknown priority: {priority!r}")
    if status not in ("proposed", "accepted"):
        raise ValueError(f"add() status must be proposed|accepted, got {status!r}")

    vault = _vault_root()
    chosen_slug = slug or _slugify(title, fallback="principle")
    target = _principles_dir(vault) / f"{chosen_slug}.md"
    if target.exists():
        raise FileExistsError(str(target))

    links = _evidence_to_links(evidence or [])
    now = _now_iso()
    fm: Dict[str, Any] = {
        "schema_version": 4,
        "entry_id": _entry_id(chosen_slug),
        "title": title,
        "status": status,
        "ac_status": "passed" if status == "accepted" else "pending",
        "coverage": coverage,
        "priority": priority,
        "evidence": links,
        "observation_kind": "feedback",
        "source": "dream" if status == "proposed" else "manual",
        "links": [],
        "ac_results": {"origin": f"principles.add:{status}"},
    }
    if status == "accepted":
        fm["accepted_at"] = now
    else:
        fm["proposed_at"] = now
    if source_entry_ids:
        fm["source_entry_ids"] = list(dict.fromkeys(source_entry_ids))
    if cluster_key:
        fm["cluster_key"] = cluster_key
    if target_topic:
        fm["target_topic"] = _slugify(target_topic)

    _atomic_write(target, _serialize(fm, _render_body(rule, why, links, notes)))
    _append_log(vault, f"- {now}  principle-{status}  {chosen_slug}  "
                       f"priority={priority}")

    from . import indexes as _indexes
    _indexes.safe_regen_principles()

    return {"slug": chosen_slug, "path": str(target),
            "status": status, "evidence": links}


# ── synthesize from existing learnings ─────────────────────────────────────


def _resolve_source(vault: Path, slug_or_path: str) -> Optional[Path]:
    """Find an accepted learning by relative path or by file stem (RFC 0001:
    the flat notes/ store, plus the legacy by-topic tree during migration)."""
    from . import store as _store
    needle = slug_or_path.removesuffix(".md")
    # Direct relative path under either accepted root.
    for root in _store.accepted_roots(vault):
        candidate = root / slug_or_path
        if candidate.exists():
            return candidate
    for p in _store.iter_accepted_files(vault):
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
    """Draft a principle from multiple accepted-learning sources.

    Default `status="proposed"` — this is the dream-cycle drafting path;
    drafts are NOT injected until a curator promotes them. Resolves each
    `source_slugs[i]` to its file under `accepted/by-topic/` and assembles
    an Evidence list. `rule`/`why` are filled if provided, else scaffolded.

    When `skip_if_covered` (default) and `source_entry_ids` are given, the
    draft is skipped if an existing principle (proposed OR accepted OR
    archived) already covers >= `overlap_threshold` of these entry_ids —
    so re-runs are idempotent and a rejected cluster is never re-proposed.
    """
    vault = _vault_root()
    if not source_slugs:
        raise ValueError("synthesize requires at least one source slug")

    # Idempotent dedup — check before doing any work.
    if skip_if_covered and source_entry_ids:
        covering = find_covering_principle(
            source_entry_ids, overlap_threshold=overlap_threshold, vault=vault)
        if covering is not None:
            return {"skipped": True, "reason": "already-covered",
                    "covered_by": covering}

    resolved: List[str] = []      # vault-relative paths
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
        source_entry_ids=source_entry_ids,
        cluster_key=cluster_key,
    )
    out["skipped"] = False
    out["evidence_resolved"] = resolved
    out["fields_to_fill"] = [k for k in ("rule", "why")
                              if not (rule if k == "rule" else why)]
    return out


# ── idempotent dedup ────────────────────────────────────────────────────────


def _principle_files(vault: Path) -> Iterable[Path]:
    """All principle-origin files: live principles/ + archived/ ones that
    carry a cluster_key or source_entry_ids (i.e. were principles)."""
    pdir = _principles_dir(vault)
    if pdir.exists():
        for p in pdir.glob("*.md"):
            if p.name != "INDEX.md":
                yield p
    adir = _archived_dir(vault)
    if adir.exists():
        for p in adir.glob("*.md"):
            yield p


def find_covering_principle(member_entry_ids: List[str], *,
                            overlap_threshold: float = 0.6,
                            vault: Optional[Path] = None
                            ) -> Optional[Dict[str, Any]]:
    """Return the first existing principle (proposed/accepted/archived)
    whose `source_entry_ids` cover >= `overlap_threshold` of
    `member_entry_ids`, else None.

    Checking archived too means a cluster the user already *rejected* is
    not re-proposed on the next dream pass (resilience rule #3).
    """
    vault = vault or _vault_root()
    target = set(member_entry_ids)
    if not target:
        return None
    for p in _principle_files(vault):
        try:
            fm, _ = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        existing = set(fm.get("source_entry_ids") or [])
        if not existing:
            # Older principles without source tracking: fall back to
            # exact cluster_key match if available.
            continue
        overlap = len(target & existing) / len(target)
        if overlap >= overlap_threshold:
            return {
                "slug": p.stem,
                "status": fm.get("status"),
                "overlap": round(overlap, 3),
                "path": str(p),
            }
    return None


def _suggest_title(vault: Path, resolved_rel_paths: List[str]) -> str:
    """Borrow the first source's title (or stem) when caller hasn't provided one."""
    if not resolved_rel_paths:
        return "principle"
    first = vault / resolved_rel_paths[0]
    if first.exists():
        try:
            fm, _ = _parse.split_frontmatter(first.read_text(encoding="utf-8"))
            t = fm.get("title") or fm.get("name")
            if isinstance(t, str) and t.strip():
                return f"principle: {t.strip()}"
        except Exception:    # pragma: no cover
            pass
    return f"principle: {Path(resolved_rel_paths[0]).stem}"


# ── list / archive ─────────────────────────────────────────────────────────


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
    """List principles in principles/. Filter by priority / coverage /
    status. NOTE: session_bootstrap passes status='accepted' so that
    `proposed` drafts are never injected before promotion."""
    vault = _vault_root()
    root = _principles_dir(vault)
    if not root.exists():
        return []
    out: List[Dict[str, Any]] = []
    for p in sorted(root.glob("*.md")):
        if p.name == "INDEX.md":
            continue
        try:
            fm, _body = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        except Exception:           # pragma: no cover
            continue
        if priority and fm.get("priority") != priority:
            continue
        if coverage and fm.get("coverage") != coverage:
            continue
        if status and fm.get("status") != status:
            continue
        out.append({
            "slug": p.stem,
            "title": fm.get("title") or p.stem,
            "coverage": fm.get("coverage"),
            "priority": fm.get("priority"),
            "status": fm.get("status"),
            "evidence": list(fm.get("evidence") or []),
            "path": str(p),
        })
    return out


def archive(*, slug: str, reason: str) -> Dict[str, Any]:
    vault = _vault_root()
    target = _principles_dir(vault) / f"{slug.removesuffix('.md')}.md"
    if not target.exists():
        raise FileNotFoundError(str(target))

    fm, body = _parse.split_frontmatter(target.read_text(encoding="utf-8"))
    fm = dict(fm)
    fm["status"] = "archived"
    fm["ac_status"] = "retracted"
    fm["archived_at"] = _now_iso()
    fm["archive_reason"] = reason

    dest_dir = _archived_dir(vault)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / target.name

    _atomic_write(dest, _serialize(fm, body))
    target.unlink()

    _append_log(vault, f"- {_now_iso()}  principle-archive  {target.stem}  "
                       f"reason={reason!r}")

    from . import indexes as _indexes
    _indexes.safe_regen_principles()

    return {"path": str(dest), "slug": target.stem}


# ── proposed review / approve / reject (dream cycle ③) ──────────────────────


def _rule_one_liner(body: str) -> str:
    import re as _re
    m = _re.search(r"^##+\s*Rule\b", body, _re.M | _re.I)
    if not m:
        return ""
    for line in body[m.end():].lstrip().splitlines():
        s = line.strip()
        if s:
            return s
    return ""


def review_proposed(*, limit: int = 50) -> Dict[str, Any]:
    """List principles awaiting promotion (status == proposed) with their
    evidence and a one-line Rule preview, for a fast batch review."""
    vault = _vault_root()
    root = _principles_dir(vault)
    items: List[Dict[str, Any]] = []
    if root.exists():
        for p in sorted(root.glob("*.md")):
            if p.name == "INDEX.md":
                continue
            try:
                fm, body = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
            except Exception:            # pragma: no cover
                continue
            if fm.get("status") != "proposed":
                continue
            items.append({
                "slug": p.stem,
                "title": fm.get("title") or p.stem,
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
    """Promote a proposed principle to accepted. Optionally override
    priority / coverage at approval time (the most common edit is
    setting priority=always-inject)."""
    vault = _vault_root()
    target = _principles_dir(vault) / f"{slug.removesuffix('.md')}.md"
    if not target.exists():
        raise FileNotFoundError(str(target))
    fm, body = _parse.split_frontmatter(target.read_text(encoding="utf-8"))
    if fm.get("status") != "proposed":
        raise ValueError(
            f"{slug}: only proposed principles can be approved "
            f"(status={fm.get('status')!r})"
        )
    if priority is not None and priority not in (
            "always-inject", "on-relevant-prompt", "manual-only"):
        raise ValueError(f"unknown priority: {priority!r}")
    if coverage is not None and coverage not in (
            "cross-project", "single-project", "single-topic"):
        raise ValueError(f"unknown coverage: {coverage!r}")

    fm = dict(fm)
    now = _now_iso()
    fm["status"] = "accepted"
    fm["ac_status"] = "passed"
    fm["accepted_at"] = now
    fm.pop("proposed_at", None)
    if priority is not None:
        fm["priority"] = priority
    if coverage is not None:
        fm["coverage"] = coverage

    _atomic_write(target, _serialize(fm, body))
    _append_log(vault, f"- {now}  principle-approve  {target.stem}  "
                       f"priority={fm.get('priority')}")

    from . import indexes as _indexes
    _indexes.safe_regen_principles()

    return {"slug": target.stem, "path": str(target),
            "status": "accepted", "priority": fm.get("priority")}


def reject(*, slug: str, reason: str = "rejected") -> Dict[str, Any]:
    """Reject a proposed principle → archived. The dedup check in
    synthesize() consults archived, so a rejected cluster is never
    re-proposed by a later dream pass."""
    vault = _vault_root()
    target = _principles_dir(vault) / f"{slug.removesuffix('.md')}.md"
    if not target.exists():
        raise FileNotFoundError(str(target))
    fm, _body = _parse.split_frontmatter(target.read_text(encoding="utf-8"))
    if fm.get("status") != "proposed":
        raise ValueError(
            f"{slug}: only proposed principles can be rejected "
            f"(status={fm.get('status')!r}); use archive() for accepted ones"
        )
    # archive() handles the move + frontmatter + dedup-preserving fields.
    out = archive(slug=slug, reason=f"rejected: {reason}")
    out["status"] = "archived"
    return out


# ── log ───────────────────────────────────────────────────────────────────


def _append_log(vault: Path, line: str) -> None:
    log = _store.learning_root(vault) / "log.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")
