"""Absorb Claude Code's per-project auto-memory into gorae/learnings/.

Claude Code maintains a per-project memory tree at:

    ~/.claude/projects/<encoded-cwd>/memory/
        ├── MEMORY.md           (index; user/agent appends here)
        └── <name>.md           (one file per memory; type-tagged)

The encoded-cwd folds the absolute working directory by replacing every
"/" with "-". `decode_cwd_dirname()` reverses this to recover the
original project path (and basename → `project_hint`).

Each memory file's frontmatter declares its semantic type. We map:

    type ∈ {feedback, reference} → atelier `accepted` (Claude has
                                    already curated these — they
                                    survive cross-session).
    type ∈ {user, project}       → atelier `candidate` (still worth a
                                    human review before promoting).

Deduplication is by sha256(body) recorded in
`<vault>/learnings/.absorbed-from-claude/<hash>.json`. Re-runs are no-ops
for hashes already seen; a file whose body changes upstream becomes a
new hash and lands as a fresh entry — the curator decides whether the
old one is now stale.

INVARIANT (CLAUDE.md hard rule #7): this is a *copy*, never a move.
Claude Code's memory under ~/.claude/projects/*/memory/** is the source
material and is strictly READ-ONLY to atelier — never edited, moved, or
deleted here. Even when a vault copy must later be purged (e.g. PII), the
source is left untouched; removing it is the user's decision, made in
that project's own context, not by atelier reaching outside its vault.
The dedup ledger prevents re-import without touching the source.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from ...index import parse as _parse
from ...util import config as _config


_CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
_DEDUP_DIRNAME = ".absorbed-from-claude"

_SLUG_RX = re.compile(r"[^a-z0-9-]+")


def _vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def decode_cwd_dirname(name: str) -> str:
    """Recover an absolute working directory from a Claude Code project
    directory name. Claude encodes `/` as `-`; multiple consecutive `-`
    typically come from `--` in the original path. We undo only the
    leading-`-` to "/" mapping and treat the rest as path components."""
    parts = name.lstrip("-").split("-")
    return "/" + "/".join(parts)


def derive_project(name: str) -> str:
    """Project slug = basename of the decoded path."""
    decoded = decode_cwd_dirname(name)
    base = Path(decoded).name
    if not base:
        return "unknown"
    return _slugify(base)


def _slugify(value: str, *, fallback: str = "x") -> str:
    text = (value or fallback).strip().lower()
    text = _SLUG_RX.sub("-", text).strip("-")
    return text[:64] or fallback


def _body_hash(body: str) -> str:
    normalized = re.sub(r"\s+", " ", body).strip().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class ClaudeMemory:
    src: Path
    project: str
    name: str
    description: Optional[str]
    type: str           # feedback | reference | user | project | unknown
    body: str
    body_sha: str


def _read_memory(path: Path) -> Optional[ClaudeMemory]:
    text = path.read_text(encoding="utf-8")
    fm, body = _parse.split_frontmatter(text)
    # Tolerate both top-level `type:` and nested `metadata.type:`.
    type_ = fm.get("type")
    if not type_ and isinstance(fm.get("metadata"), dict):
        type_ = fm["metadata"].get("type")
    if not isinstance(type_, str):
        type_ = "unknown"
    name = fm.get("name") or path.stem
    if not isinstance(name, str):
        name = path.stem
    desc = fm.get("description")
    if not isinstance(desc, (str, type(None))):
        desc = None
    project_dir = path.parents[1].name      # <encoded-cwd>
    return ClaudeMemory(
        src=path,
        project=derive_project(project_dir),
        name=name,
        description=desc,
        type=type_,
        body=body,
        body_sha=_body_hash(body),
    )


def _iter_memories(source_root: Path) -> Iterable[Path]:
    if not source_root.exists():
        return
    for project_dir in sorted(source_root.iterdir()):
        if not project_dir.is_dir():
            continue
        mem_dir = project_dir / "memory"
        if not mem_dir.exists():
            continue
        for p in sorted(mem_dir.glob("*.md")):
            if p.name.upper() == "MEMORY.MD":
                continue   # skip the index
            yield p


def _dedup_dir(vault: Path) -> Path:
    return vault / "learnings" / _DEDUP_DIRNAME


def _already_absorbed(vault: Path, body_sha: str) -> bool:
    return (_dedup_dir(vault) / f"{body_sha}.json").exists()


def _record_dedup(vault: Path, mem: ClaudeMemory, dest: Path) -> None:
    d = _dedup_dir(vault)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{mem.body_sha}.json").write_text(json.dumps({
        "source_path": str(mem.src),
        "absorbed_at": _now_iso(),
        "dest": str(dest),
        "project": mem.project,
        "type": mem.type,
    }, indent=2))


def _topic_from(mem: ClaudeMemory) -> str:
    """Best-effort topic. Prefer name (slugified) over fallback 'misc'."""
    return _slugify(mem.name, fallback="misc")


_AUTO_ACCEPT = {"feedback", "reference"}


def _build_frontmatter(mem: ClaudeMemory, *, status: str,
                       target_topic: Optional[str]) -> Dict[str, Any]:
    """v4 frontmatter common to both accepted and candidate."""
    entry_id = str(_uuid.uuid5(
        _uuid.NAMESPACE_DNS,
        f"learnings:claude:{mem.body_sha}",
    ))
    fm: Dict[str, Any] = {
        "schema_version": 4,
        "entry_id": entry_id,
        "captured_at": _now_iso(),
        "agent_kind": "claude-code",
        "hook": "manual",                        # absorbed offline; not a hook
        "status": status,
        "ac_status": "passed" if status == "accepted" else "pending",
        "observation_kind": _map_type(mem.type),
        "links": [],
        "ac_results": {"absorbed_from": "claude-code-memory"},
        "source": "claude-memory",
        "source_path": str(mem.src),
        "claude_memory_type": mem.type,
        "project_hint": mem.project,
    }
    if mem.description:
        fm["title"] = mem.name
        fm["description"] = mem.description
    if status == "accepted" and target_topic:
        fm["accepted_at"] = _now_iso()
        fm["target_topic"] = target_topic
        fm["target_project"] = mem.project
    return fm


def _map_type(claude_type: str) -> str:
    if claude_type == "feedback":
        return "feedback"
    if claude_type == "project":
        return "project"
    if claude_type == "reference":
        return "reference"
    if claude_type == "user":
        return "user"
    return "feedback"


def _write_md(path: Path, fm: Dict[str, Any], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    path.write_text(f"---\n{serialized}\n---\n{body}", encoding="utf-8")


def absorb(*, dry_run: bool = False,
           source_root: Optional[Path] = None,
           auto_accept_kinds: Optional[List[str]] = None) -> Dict[str, Any]:
    src_root = source_root or _CLAUDE_PROJECTS
    vault = _vault_root()
    accept_kinds = set(auto_accept_kinds or _AUTO_ACCEPT)

    absorbed_accepted: List[Dict[str, str]] = []
    absorbed_candidates: List[Dict[str, str]] = []
    deduped: List[str] = []
    skipped_other: List[str] = []

    for path in _iter_memories(src_root):
        try:
            mem = _read_memory(path)
        except Exception as exc:        # pragma: no cover - defensive
            skipped_other.append(f"{path}: parse-error {exc!r}")
            continue
        if mem is None:                 # pragma: no cover
            continue

        if _already_absorbed(vault, mem.body_sha):
            deduped.append(str(mem.src))
            continue

        is_accepted = mem.type in accept_kinds
        if is_accepted:
            topic = _topic_from(mem)
            base = f"claude-{mem.project}-{_slugify(mem.name)}-{mem.body_sha[:10]}.md"
            by_topic = (vault / "learnings" / "accepted" / "by-topic"
                        / topic / base)
            by_project = (vault / "learnings" / "accepted" / "by-project"
                          / mem.project / base)
            fm = _build_frontmatter(mem, status="accepted",
                                     target_topic=topic)
            if not dry_run:
                _write_md(by_topic, fm, mem.body)
                by_project.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(by_topic, by_project)
                _record_dedup(vault, mem, by_topic)
            absorbed_accepted.append({
                "src": str(mem.src),
                "by_topic": str(by_topic),
                "by_project": str(by_project),
                "type": mem.type,
                "project": mem.project,
            })
        else:
            day = datetime.now(timezone.utc).date().isoformat()
            base = (f"{datetime.now().strftime('%H%M')}-claude-"
                    f"{_slugify(mem.name)}-{mem.body_sha[:10]}.md")
            cand_path = (vault / "learnings" / "candidates" / day / base)
            fm = _build_frontmatter(mem, status="candidate", target_topic=None)
            if not dry_run:
                _write_md(cand_path, fm, mem.body)
                _record_dedup(vault, mem, cand_path)
            absorbed_candidates.append({
                "src": str(mem.src),
                "path": str(cand_path),
                "type": mem.type,
                "project": mem.project,
            })

    return {
        "vault": str(vault),
        "accepted": absorbed_accepted,
        "candidates": absorbed_candidates,
        "deduped": deduped,
        "skipped": skipped_other,
        "dry_run": dry_run,
    }
