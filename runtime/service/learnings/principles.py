"""Cross-project principles — the developer-ethos tier of learnings.

A *principle* generalizes recurring per-project learnings into a
universal rule that the developer adopts across all projects. Principles
live at `<vault>/learnings/principles/<slug>.md` and carry an `evidence`
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
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from ...index import parse as _parse
from ...util import config as _config


_SLUG_RX = re.compile(r"[^a-z0-9-]+")


def _vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _principles_dir(vault: Optional[Path] = None) -> Path:
    return (vault or _vault_root()) / "learnings" / "principles"


def _archived_dir(vault: Optional[Path] = None) -> Path:
    return (vault or _vault_root()) / "learnings" / "archived"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _slugify(value: str, *, fallback: str = "principle") -> str:
    text = (value or fallback).strip().lower()
    text = _SLUG_RX.sub("-", text).strip("-")
    return text[:80] or fallback


def _entry_id(slug: str) -> str:
    return str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"learnings:principle:{slug}"))


def _serialize(fm: Dict[str, Any], body: str) -> str:
    serialized = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    return f"---\n{serialized}\n---\n{body}"


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
        ) -> Dict[str, Any]:
    if coverage not in ("cross-project", "single-project", "single-topic"):
        raise ValueError(f"unknown coverage: {coverage!r}")
    if priority not in ("always-inject", "on-relevant-prompt", "manual-only"):
        raise ValueError(f"unknown priority: {priority!r}")

    vault = _vault_root()
    chosen_slug = slug or _slugify(title, fallback="principle")
    target = _principles_dir(vault) / f"{chosen_slug}.md"
    if target.exists():
        raise FileExistsError(str(target))

    links = _evidence_to_links(evidence or [])
    fm: Dict[str, Any] = {
        "schema_version": 4,
        "entry_id": _entry_id(chosen_slug),
        "title": title,
        "status": "accepted",
        "ac_status": "passed",
        "coverage": coverage,
        "priority": priority,
        "accepted_at": _now_iso(),
        "evidence": links,
        "observation_kind": "feedback",
        "source": "manual",
        "links": [],
        "ac_results": {"origin": "principles.add"},
    }
    if target_topic:
        fm["target_topic"] = _slugify(target_topic)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        _serialize(fm, _render_body(rule, why, links, notes)),
        encoding="utf-8",
    )
    _append_log(vault, f"- {fm['accepted_at']}  principle-add  {chosen_slug}  "
                       f"priority={priority}")

    from . import indexes as _indexes
    _indexes.safe_regen_principles()

    return {"slug": chosen_slug, "path": str(target), "evidence": links}


# ── synthesize from existing learnings ─────────────────────────────────────


def _resolve_source(vault: Path, slug_or_path: str) -> Optional[Path]:
    """Find an accepted learning by relative path or by file stem."""
    accepted_root = vault / "learnings" / "accepted" / "by-topic"
    if not accepted_root.exists():
        return None
    needle = slug_or_path.removesuffix(".md")
    # Direct path under accepted/by-topic
    candidate = accepted_root / slug_or_path
    if candidate.exists():
        return candidate
    for p in accepted_root.rglob("*.md"):
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
               ) -> Dict[str, Any]:
    """Draft a principle from multiple accepted-learning sources.

    Resolves each `source_slugs[i]` to its file under
    `accepted/by-topic/` and assembles an Evidence list. Body fields
    (rule, why) are filled if provided, otherwise left as scaffolds for
    the caller to edit.
    """
    vault = _vault_root()
    if not source_slugs:
        raise ValueError("synthesize requires at least one source slug")

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
    )
    out["evidence_resolved"] = resolved
    out["fields_to_fill"] = [k for k in ("rule", "why")
                              if not (rule if k == "rule" else why)]
    return out


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
             coverage: Optional[str] = None) -> List[Dict[str, Any]]:
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
        out.append({
            "slug": p.stem,
            "title": fm.get("title") or p.stem,
            "coverage": fm.get("coverage"),
            "priority": fm.get("priority"),
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

    dest.write_text(_serialize(fm, body), encoding="utf-8")
    target.unlink()

    _append_log(vault, f"- {_now_iso()}  principle-archive  {target.stem}  "
                       f"reason={reason!r}")

    from . import indexes as _indexes
    _indexes.safe_regen_principles()

    return {"path": str(dest), "slug": target.stem}


# ── log ───────────────────────────────────────────────────────────────────


def _append_log(vault: Path, line: str) -> None:
    log = vault / "learnings" / "log.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")
