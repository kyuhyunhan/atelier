"""Absorb Claude Code's per-project auto-memory into gorae/learnings/.

Claude Code maintains a per-project memory tree at:

    ~/.claude/projects/<encoded-cwd>/memory/
        ├── MEMORY.md           (index; user/agent appends here)
        └── <name>.md           (one file per memory; type-tagged)

The encoded-cwd folds the absolute working directory by replacing every
"/" with "-". `decode_cwd_dirname()` reverses this to recover the
original project path (and basename → `project_hint`).

Each absorbed memory is BORN AS A v7 CLAIM (RFC 0005 §7.1 —
`generated_by: absorbed`), derived_from the single shared operational-capture
Source (RFC 0005 P10 — one canonical L1 node, not a per-memory session stub).
There is no candidate FILE step; the lifecycle is the `ac_status` field:

    type ∈ {feedback, reference} → ac_status `passed` (Claude already curated
                                    these — they survive cross-session).
    type ∈ {user, project}       → ac_status `pending` (still worth a human
                                    review before promoting).

Deduplication is by sha256(body) recorded in a single vault-level ledger
`<vault>/.absorbed-from-claude.json` (a JSON object keyed by body hash). It is
git-tracked so dedup is consistent across machines, but lives at the vault root
— NOT inside a content lane (`raw/`/`graph/`) — because it is engine ingestion
metadata, not a node the crawler should index. Re-runs are no-ops for hashes
already seen; a memory whose body changes upstream becomes a new hash and lands
as a fresh entry — the curator decides whether the old one is now stale.

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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ...index import parse as _parse
from ...util import config as _config
from ...util import logging as _log
from . import claims_io as _claims


_CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
_LEDGER_FILENAME = ".absorbed-from-claude.json"

# RFC 0008 M4: the SAME pattern vocabulary as the pre-commit PII guard
# (scripts/git-hooks/pre-commit) — one file, two enforcement points. Absent
# file → the pass is a no-op, matching the guard's behavior.
_PII_PATTERNS_PATH = Path.home() / ".atelier" / "pii_patterns.txt"

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


def _ledger_path(vault: Path) -> Path:
    """The single vault-level dedup ledger (git-tracked, NOT a content lane —
    see module docstring). A JSON object keyed by body sha256."""
    return vault / _LEDGER_FILENAME


def _load_ledger(vault: Path) -> Dict[str, Any]:
    """The dedup ledger as a dict; empty on an absent file (the normal cold
    start). A ledger that EXISTS but is unreadable/non-dict is treated as empty
    too so a manual absorb never crashes — but that is WARNED, not swallowed:
    proceeding blind would re-import every memory as a duplicate and then
    overwrite the (git-tracked) ledger with only the new entries. The warning
    tells the operator to restore it from git instead of re-running."""
    p = _ledger_path(vault)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        _log.warn("absorb.ledger-unreadable", path=str(p), error=repr(exc),
                  hint="restore from git; re-running absorb would re-import "
                       "duplicates and overwrite the ledger")
        return {}
    if not isinstance(data, dict):
        _log.warn("absorb.ledger-not-dict", path=str(p),
                  hint="restore from git; re-running absorb would re-import "
                       "duplicates and overwrite the ledger")
        return {}
    return data


def _save_ledger(vault: Path, ledger: Dict[str, Any]) -> None:
    _ledger_path(vault).write_text(
        json.dumps(ledger, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )


def _ledger_entry(mem: ClaudeMemory) -> Dict[str, Any]:
    return {
        "source_path": str(mem.src),
        "absorbed_at": _now_iso(),
        "project": mem.project,
        "type": mem.type,
    }


def _is_absorbed(ledger: Dict[str, Any], body_sha: str) -> bool:
    """The ONE membership test against the dedup ledger (RFC 0008 §7): both
    `absorb` and `unabsorbed_count` go through this accessor, so M2's `by_sha`
    nesting will change one function, not every call site."""
    return body_sha in ledger


def _pii_patterns(path: Optional[Path] = None) -> List[re.Pattern]:
    """Compiled PII patterns, one regex per line (blank / `#` lines skipped).
    Missing file → empty list (the pass is a no-op, same trust model as the
    pre-commit guard).

    A line this pass cannot honor is skipped but WARNED, never silent — a
    silently dropped pattern is a hole in the safety net the operator believes
    is closed. Two cases: an uncompilable regex, and a POSIX character class
    (`[[:alpha:]]` etc.) — grep -E (the guard) matches those, but Python `re`
    compiles them as a literal character set that matches nothing, so honoring
    the line silently would be a no-op divergence from the guard. The shared
    vocabulary is the ERE ∩ Python-re subset (literals and standard classes
    like `\\w`, `[A-Za-z]`)."""
    p = path if path is not None else _PII_PATTERNS_PATH
    if not p.exists():
        return []
    out: List[re.Pattern] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "[[:" in line:
            _log.warn("absorb.pii-pattern-posix-class", pattern=line,
                      hint="POSIX classes match in the pre-commit guard but "
                           "not in Python re; rewrite with [A-Za-z] / \\w "
                           "style classes")
            continue
        try:
            out.append(re.compile(line))
        except re.error as exc:
            _log.warn("absorb.pii-pattern-invalid", pattern=line,
                      error=repr(exc))
            continue
    return out


def _pii_hit(text: str, patterns: List[re.Pattern]) -> bool:
    return any(rx.search(text) for rx in patterns)


# ── RFC 0008 M1 — the absorb nudge (unabsorbed backlog) ──────────────────────


def unabsorbed_count(*, source_root: Optional[Path] = None,
                     vault: Optional[Path] = None) -> int:
    """Number of Claude Code memories whose body sha is not in the ledger
    (RFC 0008 §3). Deterministic, read-only, LLM-free: one directory walk +
    one sha256 per file. `MEMORY.md` indexes are skipped, as in absorb."""
    src_root = source_root if source_root is not None else _CLAUDE_PROJECTS
    vault = vault if vault is not None else _vault_root()
    ledger = _load_ledger(vault)
    count = 0
    for path in _iter_memories(src_root):
        try:
            mem = _read_memory(path)
        except Exception:                       # pragma: no cover - defensive
            continue
        if mem is None:                         # pragma: no cover
            continue
        if not _is_absorbed(ledger, mem.body_sha):
            count += 1
    return count


def nudge_info(*, now: Optional[str] = None,
               source_root: Optional[Path] = None,
               vault: Optional[Path] = None) -> Dict[str, Any]:
    """Single source of the absorb-nudge decision, shaped like
    atomize/dream.nudge_info(): {due, count, short, long}.

    `due` fires when the backlog reaches `learnings.absorb.nudge_after_memories`
    (default 1). Human-pulled, never cron (RFC 0008 §3 posture): absorb is the
    only ingest that reads outside the vault, and it auto-passes
    feedback/reference claims — unattended runs would accrue unreviewed
    `passed` claims. `now` is accepted for signature parity; the nudge is
    count-driven."""
    cfg = _config.load()
    absorb_cfg = (cfg.raw.get("learnings") or {}).get("absorb") or {}
    after = int(absorb_cfg.get("nudge_after_memories", 1))

    try:
        count = unabsorbed_count(source_root=source_root, vault=vault)
    except Exception:                           # pragma: no cover
        count = 0

    due = count >= after
    long = ""
    short = ""
    if due:
        noun = "memory" if count == 1 else "memories"
        long = (
            f"📥 **atelier absorb** — {count} Claude Code {noun} not yet "
            f"absorbed into the vault. Ask me to run "
            f"`atelier_absorb_claude_memory` to pull them in (copy-only — "
            f"the `~/.claude` source is never touched)."
        )
        short = f"📥 atelier: {count} to absorb"
    return {"due": due, "count": count, "short": short, "long": long}


_AUTO_ACCEPT = {"feedback", "reference"}


def _map_type(claude_type: str) -> str:
    if claude_type in ("feedback", "project", "reference", "user"):
        return claude_type
    return "feedback"


def _statement_of(mem: ClaudeMemory) -> str:
    """The absorbed memory's claim `statement` — prefer its description (the
    curated one-liner), else its title/name, whitespace-collapsed."""
    text = (mem.description or "").strip() or (mem.name or "").strip() or "(absorbed memory)"
    return " ".join(text.split())[:400]


def absorb(*, dry_run: bool = False,
           source_root: Optional[Path] = None,
           auto_accept_kinds: Optional[List[str]] = None,
           pii_patterns_path: Optional[Path] = None) -> Dict[str, Any]:
    src_root = source_root or _CLAUDE_PROJECTS
    vault = _vault_root()
    accept_kinds = set(auto_accept_kinds or _AUTO_ACCEPT)
    pii_rx = _pii_patterns(pii_patterns_path)

    # One read of the dedup ledger up front; mutate in memory, persist once at
    # the end (absorb is manual + single-writer, so read-modify-write is safe).
    ledger = _load_ledger(vault)
    ledger_dirty = False

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

        if _is_absorbed(ledger, mem.body_sha):
            deduped.append(str(mem.src))
            continue

        is_accepted = mem.type in accept_kinds
        ac_status = "passed" if is_accepted else "pending"
        statement = _statement_of(mem)
        now = _now_iso()

        # RFC 0008 M4 — safety at the absorb boundary, demote-never-block:
        # a `user` memory describes who the user IS → private by default; a
        # PII pattern hit demotes to private + flags for later curation. The
        # promote gate requires public, so a demoted claim can never be
        # proactively pushed.
        pii_flagged = bool(pii_rx) and (_pii_hit(mem.body, pii_rx)
                                        or _pii_hit(statement, pii_rx))
        sensitivity = ("private" if (mem.type == "user" or pii_flagged)
                       else "public")

        # RFC 0007: born-as-Source + deterministic mint. Each absorbed memory
        # lands as its OWN content-addressed operational Source (carrying its
        # ~/.claude provenance — source_path / claude_memory_type / body_sha) in
        # raw/operational/, from which a no-LLM 1:1 mint derives the Claim
        # (generated_by: mint). The body-hash ledger (below) still guards
        # re-import, so a mint is reached only for a first-seen memory.
        if dry_run:
            dest_repr = f"<claim:{statement[:40]}>"
        else:
            is_about = []
            if mem.project:
                is_about.append(_claims._resolve_entity_id(
                    mem.project, sensitivity="public",
                    in_scheme="operational", vault=vault))
            minted = _claims.mint_operational_claim(
                statement=statement, body=mem.body,
                observation_kind=_map_type(mem.type),
                why_status="present", project=mem.project or None,
                is_about=is_about, attributed_to="absorbed",
                agent_kind="absorbed", hook="manual",
                sensitivity=sensitivity,
                ac_status=ac_status, captured_at=now,
                extra={
                    "source": "claude-memory",
                    "source_path": str(mem.src),
                    "claude_memory_type": mem.type,
                    "ac_results": {"absorbed_from": "claude-code-memory"},
                    **({"pii_flag": True} if pii_flagged else {}),
                    **({"accepted_at": now} if is_accepted else {}),
                    **({"title": mem.name, "description": mem.description}
                       if mem.description else {}),
                },
                source_extra={
                    "source_type": "claude-memory",
                    "source_path": str(mem.src),
                    "claude_memory_type": mem.type,
                    "body_sha": mem.body_sha,
                    **({"pii_flag": True} if pii_flagged else {}),
                },
                vault=vault,
            )
            claim = minted["claim"]
            dest_repr = claim["path"]
            ledger[mem.body_sha] = _ledger_entry(mem)
            ledger_dirty = True

        # sensitivity + pii_flag ride the record so a dry-run previews which
        # memories would land private/flagged before any write happens.
        record = {"src": str(mem.src), "path": str(dest_repr),
                  "type": mem.type, "project": mem.project,
                  "sensitivity": sensitivity,
                  **({"pii_flag": True} if pii_flagged else {})}
        (absorbed_accepted if is_accepted else absorbed_candidates).append(record)

    if not dry_run and ledger_dirty:
        _save_ledger(vault, ledger)

    return {
        "vault": str(vault),
        "accepted": absorbed_accepted,
        "candidates": absorbed_candidates,
        "deduped": deduped,
        "skipped": skipped_other,
        "dry_run": dry_run,
    }
