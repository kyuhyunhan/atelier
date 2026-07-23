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
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple  # noqa: F401

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


def _decode_naive(name: str) -> str:
    """Every `-` treated as a path separator — the decoding to fall back on
    when the real directory no longer exists on this machine."""
    return "/" + "/".join(name.lstrip("-").split("-"))


def _decode_by_filesystem(name: str, *, root: Path = Path("/")) -> Optional[str]:
    """Resolve the encoding's ambiguity by probing the real filesystem.

    Claude Code encodes a working directory by replacing `/` with `-`, but a
    directory name may itself CONTAIN `-` (`identity-hub`, `app-frontend`,
    `fe-shared`). The encoding is therefore NOT injective: in
    `…-inheaden-identity-hub`, the `-` before `hub` is indistinguishable from a
    separator by string inspection alone. No parser can recover the truth from
    the string — but the filesystem can, because only one of the candidate
    splits names a directory that actually exists.

    We walk the token list depth-first, at each step trying the LONGEST
    remaining join first (so `identity-hub` is preferred over `identity/hub`),
    and accept the first full consumption whose path exists. Returns None when
    no candidate resolves (deleted project, another machine) — the caller then
    falls back to `_decode_naive`."""
    tokens = name.lstrip("-").split("-")
    if not tokens:
        return None

    def walk(base: Path, i: int) -> Optional[Path]:
        if i == len(tokens):
            return base
        # longest-first: consume as many tokens as possible into one component
        for j in range(len(tokens), i, -1):
            cand = base / "-".join(tokens[i:j])
            try:
                if not cand.is_dir():
                    continue
            except OSError:                     # pragma: no cover - defensive
                continue
            found = walk(cand, j)
            if found is not None:
                return found
        return None

    hit = walk(root, 0)
    return str(hit) if hit is not None else None


def decode_cwd_dirname(name: str) -> str:
    """Recover an absolute working directory from a Claude Code project
    directory name.

    The encoding (`/` → `-`) is lossy, so this consults the real filesystem to
    disambiguate (see `_decode_by_filesystem`) and only falls back to treating
    every `-` as a separator when nothing resolves."""
    return _decode_by_filesystem(name) or _decode_naive(name)


def _decode_verified(name: str) -> Tuple[str, bool]:
    """(path, verified) — `verified` is False when the path had to be guessed
    because nothing on this filesystem matched."""
    hit = _decode_by_filesystem(name)
    return (hit, True) if hit else (_decode_naive(name), False)


@lru_cache(maxsize=256)
def derive_project(name: str) -> str:
    """The project slug for an absorbed memory.

    Routed through `project.resolve_project` — the SINGLE accessor every other
    path (capture, bootstrap, recall) already shares. absorb previously derived
    its own slug by basename, so an absorbed claim was written under one key
    (`hub`) while the live session looked it up under another
    (`inheaden-identity-hub`) and the project boost could never match — exactly
    the divergence `project.py` exists to prevent. Deriving it here would
    reintroduce the split, so we decode to a real path and delegate.

    `need_known=False`: we want only the slug, and the `known` probe scans the
    whole vault for a project with no learnings yet. Memoized because a batch
    absorbs many memories per project directory (17 dirs / 61 files today), and
    the answer is a pure function of the encoded name for one run.

    An UNVERIFIED decode (the project directory is gone, or we are on another
    machine) skips the resolver: its config-map layer matches by path PREFIX,
    so a guessed path like `…/app/fe` (really the deleted `app-fe`) would map
    onto a *different live* project and contaminate that project's recall
    boost. A wrong-but-orphan key is strictly safer than a wrong-but-real one,
    so an unverified decode falls back to the plain basename."""
    decoded, verified = _decode_verified(name)
    if verified:
        try:
            from . import project as _project
            slug = _project.resolve_project(decoded, need_known=False).slug
            if slug:
                return slug
        except Exception:                       # pragma: no cover - defensive
            pass
    base = Path(decoded).name
    return _slugify(base) if base else "unknown"


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


def _read_memory(path: Path, *, with_project: bool = True) -> Optional[ClaudeMemory]:
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
    # `with_project=False` for probes that only need the body hash (the
    # session-start nudge count): project resolution touches config, the
    # filesystem and — before this was split out — the vault, per file.
    return ClaudeMemory(
        src=path,
        project=derive_project(project_dir) if with_project else "",
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


def memory_key(source_path: str) -> str:
    """The MACHINE-INDEPENDENT identity of an upstream memory file:
    `<encoded-project-dir>/<filename>` (RFC 0008 §4).

    The ledger is git-tracked precisely so dedup stays consistent across
    machines, so the path index must not be keyed on an absolute
    `/Users/<someone>/.claude/...` path — that would never match elsewhere and
    supersession would silently never fire there."""
    p = Path(source_path)
    try:
        return f"{p.parents[1].name}/{p.name}"
    except IndexError:                          # pragma: no cover - odd path
        return p.name


def _migrate_ledger(data: Dict[str, Any]) -> Dict[str, Any]:
    """Bring a ledger to the RFC 0008 §4 indexed shape, in memory.

    Legacy shape is a FLAT `{<body_sha>: {...}}`; the indexed shape nests it
    under `by_sha` and adds `by_path` mapping each memory's machine-independent
    key to its latest sha. The migration is derived from the entries' own
    `source_path`, so it is lossless and one-time; entries without one simply
    do not get a path index (they can never supersede — forward-only, the same
    posture RFC 0007 took with the legacy anchor)."""
    if "by_sha" in data and isinstance(data.get("by_sha"), dict):
        data.setdefault("by_path", {})
        return data
    by_sha = {k: v for k, v in data.items() if isinstance(v, dict)}
    # A path may carry SEVERAL shas (the memory was absorbed, edited, absorbed
    # again before M2 existed). The index must name the LATEST, so pick by
    # `absorbed_at` — NOT by iteration order: `_save_ledger` writes with
    # sort_keys=True, so the file is sha-lexicographic and "last one wins"
    # would crown an arbitrary (in practice, older) entry. Getting this wrong
    # is not cosmetic: the RFC's claim_id backfill would then arm supersession
    # against a stale entry and retract the wrong claim.
    best: Dict[str, Tuple[str, str]] = {}       # key -> (absorbed_at, sha)
    for sha, entry in by_sha.items():
        sp = entry.get("source_path")
        if not (isinstance(sp, str) and sp):
            continue
        key = memory_key(sp)
        stamp = str(entry.get("absorbed_at") or "")
        prior = best.get(key)
        if prior is None or stamp > prior[0]:
            best[key] = (stamp, sha)
    return {"by_sha": by_sha,
            "by_path": {k: sha for k, (_stamp, sha) in best.items()}}


def _load_ledger(vault: Path) -> Dict[str, Any]:
    """The dedup ledger in the indexed `{by_sha, by_path}` shape; empty on an
    absent file (the normal cold start), migrating a legacy flat ledger on
    read. A ledger that EXISTS but is unreadable/non-dict is treated as empty
    too so a manual absorb never crashes — but that is WARNED, not swallowed:
    proceeding blind would re-import every memory as a duplicate and then
    overwrite the (git-tracked) ledger with only the new entries. The warning
    tells the operator to restore it from git instead of re-running."""
    p = _ledger_path(vault)
    if not p.exists():
        return {"by_sha": {}, "by_path": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        _log.warn("absorb.ledger-unreadable", path=str(p), error=repr(exc),
                  hint="restore from git; re-running absorb would re-import "
                       "duplicates and overwrite the ledger")
        return {"by_sha": {}, "by_path": {}}
    if not isinstance(data, dict):
        _log.warn("absorb.ledger-not-dict", path=str(p),
                  hint="restore from git; re-running absorb would re-import "
                       "duplicates and overwrite the ledger")
        return {"by_sha": {}, "by_path": {}}
    return _migrate_ledger(data)


def _save_ledger(vault: Path, ledger: Dict[str, Any]) -> None:
    _ledger_path(vault).write_text(
        json.dumps(ledger, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )


def _ledger_entry(mem: ClaudeMemory, *, statement: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "source_path": str(mem.src),
        "absorbed_at": _now_iso(),
        "project": mem.project,
        "type": mem.type,
    }
    if statement:
        # Kept so a later revision can locate this absorb's claim file in O(1)
        # (the filename is f(slug(statement), entry_id)) without an
        # O(vault) scan.
        out["statement"] = statement
    return out


def _is_absorbed(ledger: Dict[str, Any], body_sha: str) -> bool:
    """The ONE membership test against the dedup ledger (RFC 0008 §7): both
    `absorb` and `unabsorbed_count` go through this accessor, so the `by_sha`
    nesting changed one function, not every call site."""
    return body_sha in (ledger.get("by_sha") or {})


def _previous_absorb(ledger: Dict[str, Any],
                     source_path: str) -> Optional[Dict[str, Any]]:
    """The ledger entry for the LAST absorb of this memory file, or None when
    the path is new. Resolving `by_path` → `by_sha` is what turns "same file,
    new hash" into the deterministic fact 'this memory was revised'."""
    key = memory_key(source_path)
    prev_sha = (ledger.get("by_path") or {}).get(key)
    if not prev_sha:
        return None
    entry = (ledger.get("by_sha") or {}).get(prev_sha)
    return entry if isinstance(entry, dict) else None


def _claim_owners(source_root: Path) -> Dict[str, List[str]]:
    """claim_id → the memory keys that currently mint to it, computed from the
    LIVE upstream corpus rather than the ledger.

    The ledger cannot answer this: a pre-M2 entry records no `claim_id`, and
    with every legacy entry in that state a ledger-only guard would either
    never fire (retracting claims another memory still owns) or always fire
    (never retracting anything). The upstream files are authoritative and we
    are walking them anyway — `claim_id` is a pure function of the memory's
    description, so ownership is exact, current, and vintage-independent."""
    owners: Dict[str, List[str]] = {}
    for path in _iter_memories(source_root):
        try:
            mem = _read_memory(path, with_project=False)
        except Exception:                       # pragma: no cover - defensive
            continue
        if mem is None:                         # pragma: no cover
            continue
        cid = _claims.operational_claim_id_for(_statement_of(mem))
        owners.setdefault(cid, []).append(memory_key(str(mem.src)))
    return owners


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
    one sha256 per file. `MEMORY.md` indexes are skipped, as in absorb.

    Runs at EVERY session start, so it reads memories with
    `with_project=False` — the count needs only the body hash, and project
    resolution is comparatively expensive."""
    src_root = source_root if source_root is not None else _CLAUDE_PROJECTS
    vault = vault if vault is not None else _vault_root()
    ledger = _load_ledger(vault)
    count = 0
    for path in _iter_memories(src_root):
        try:
            mem = _read_memory(path, with_project=False)
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


def _retract_superseded(owners: Dict[str, List[str]], old_claim_id: str,
                        this_key: str, *, old_statement: str,
                        new_claim_id: str, vault: Path) -> bool:
    """Retract the claim a revision superseded (RFC 0008 §4 step 3).

    GUARDED: if another memory file still mints to the same claim (two
    memories sharing one `description` collapse onto one content-addressed
    claim), the claim is still OWNED by that live path — the new claim links
    to it, but retracting would destroy a claim that is not stale. Returns
    True when a retraction actually happened.

    Retraction goes through `ac_status: retracted` (`set_ac_status`), the same
    field every other retraction uses, so the claim leaves promote eligibility
    structurally rather than by a bespoke flag."""
    others = [k for k in owners.get(old_claim_id, []) if k != this_key]
    if others:
        _log.warn("absorb.supersede-retract-skipped", claim=old_claim_id,
                  owned_by=others,
                  hint="another memory still resolves to this claim; linked "
                       "the new revision without retracting")
        return False
    path = _claims.claim_path_for(old_statement, old_claim_id, vault=vault)
    if not path:
        return False
    p = Path(path)
    try:
        fm, body = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
    except Exception:                           # pragma: no cover - defensive
        return False
    if not isinstance(fm, dict) or fm.get("ac_status") == "retracted":
        return False
    _claims.set_ac_status(
        p, fm, body, new_status="retracted",
        archive_reason=(f"{_SUPERSEDE_REASON_PREFIX}{new_claim_id} "
                        f"(upstream memory revised)"))
    return True


_SUPERSEDE_REASON_PREFIX = "superseded by "


def _unretract_if_superseded(claim_path: str) -> bool:
    """Reverse a retraction THIS mechanism authored, and only that one.

    A description can come back (A→B→A). The claim for A still exists — the
    re-mint guard correctly refuses to rewrite it — but supersession retracted
    it when B took over, so without this the live memory would own nothing but
    retracted claims. A curator's retraction carries a different (or no)
    `archive_reason` and is never touched: the vault's judgement outranks the
    mechanism's bookkeeping."""
    p = Path(claim_path)
    try:
        fm, body = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
    except Exception:                           # pragma: no cover - defensive
        return False
    if not isinstance(fm, dict) or fm.get("ac_status") != "retracted":
        return False
    if not str(fm.get("archive_reason") or "").startswith(
            _SUPERSEDE_REASON_PREFIX):
        return False                            # a human retracted this
    new_fm = dict(fm)
    new_fm["ac_status"] = "pending"             # back for review, not passed
    for k in ("archived_at", "archive_reason"):
        new_fm.pop(k, None)
    new_fm["unretracted_at"] = _now_iso()
    new_fm.pop("content_hash", None)
    new_fm["content_hash"] = _claims._content_hash(new_fm)
    p.write_text(_claims._emit(new_fm, body), encoding="utf-8")
    _log.warn("absorb.supersede-reverted", claim=claim_path,
              hint="this description returned upstream; the supersession "
                   "retraction was reversed (ac_status back to pending)")
    return True


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
    # The memo is per-process, but the daemon is long-lived: a `project_map`
    # edit or a directory rename between runs must be picked up. absorb is
    # manual and rare, so paying cold resolution once per run is free.
    derive_project.cache_clear()

    # One read of the dedup ledger up front; mutate in memory, persist once at
    # the end (absorb is manual + single-writer, so read-modify-write is safe).
    ledger = _load_ledger(vault)
    ledger_dirty = False
    # claim_id -> memory keys, from the LIVE corpus (see _claim_owners):
    # the shared-description guard cannot be answered from the ledger,
    # whose legacy entries carry no claim_id.
    owners = _claim_owners(src_root)

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

        is_accepted = mem.type in accept_kinds
        ac_status = "passed" if is_accepted else "pending"
        statement = _statement_of(mem)
        now = _now_iso()

        if _is_absorbed(ledger, mem.body_sha):
            # Dedup is by BODY hash (frontmatter excluded), so a
            # description-only edit — the memory keeps its content but is
            # re-titled — lands here with an unchanged hash. That still moves
            # the claim: the statement IS the description, and the claim id is
            # f(statement, …). Left alone it would strand the old claim exactly
            # as an un-superseded body edit would. Detect it by comparing the
            # id this statement resolves to against the one the ledger
            # recorded; identical (the overwhelmingly common case) costs one
            # uuid5 and falls through to the normal no-op.
            prev = _previous_absorb(ledger, str(mem.src))
            prev_claim_id = (prev or {}).get("claim_id")
            if not (prev_claim_id
                    and prev_claim_id != _claims.operational_claim_id_for(
                        statement)):
                deduped.append(str(mem.src))
                continue
            _log.warn("absorb.description-only-revision",
                      source_path=str(mem.src),
                      hint="body unchanged but the description moved, so the "
                           "claim id moved too; superseding")

        # RFC 0008 M4 — safety at the absorb boundary, demote-never-block:
        # a `user` memory describes who the user IS → private by default; a
        # PII pattern hit demotes to private + flags for later curation. The
        # promote gate requires public, so a demoted claim can never be
        # proactively pushed.
        pii_flagged = bool(pii_rx) and (_pii_hit(mem.body, pii_rx)
                                        or _pii_hit(statement, pii_rx))
        sensitivity = ("private" if (mem.type == "user" or pii_flagged)
                       else "public")

        # RFC 0008 §4 — supersession, decided BEFORE any write. A first-seen
        # hash on a KNOWN path means this memory was revised. Which kind of
        # revision it is depends on the STATEMENT, not the body: the claim id
        # is f(statement, source_id) and the Source id is f(statement) alone,
        # so a body-only edit resolves to the very same nodes.
        prev = _previous_absorb(ledger, str(mem.src))
        prev_claim_id = (prev or {}).get("claim_id")
        # The superseded claim's own statement — needed to locate its file in
        # O(1) (the writer's filename is f(slug(statement), id)). Recorded
        # since M2; absent on a legacy entry, which therefore cannot supersede.
        prev_statement = (prev or {}).get("statement") or ""
        new_claim_id = _claims.operational_claim_id_for(statement)
        # A body-only revision keeps the id; a description change moves it.
        body_only_revision = bool(prev) and prev_claim_id == new_claim_id
        supersedes = (prev_claim_id
                      if (prev and prev_claim_id and prev_statement
                          and not body_only_revision)
                      else None)

        # RFC 0007: born-as-Source + deterministic mint. Each absorbed memory
        # lands as its OWN content-addressed operational Source (carrying its
        # ~/.claude provenance — source_path / claude_memory_type / body_sha) in
        # raw/operational/, from which a no-LLM 1:1 mint derives the Claim
        # (generated_by: mint). The body-hash ledger (below) still guards
        # re-import, so a mint is reached only for a first-seen memory.
        claim_existed = False
        refreshed = superseded = False
        if dry_run:
            dest_repr = f"<claim:{statement[:40]}>"
        elif body_only_revision:
            # §4 step 2 — the Claim is NOT written at all: its statement is
            # unchanged and its lifecycle fields (surfacing, ac_status,
            # accepted_at, links) must survive. Only the Source's body is
            # refreshed so the vault mirror tracks the upstream revision.
            out = _claims.refresh_operational_source_body(
                statement=statement, body=mem.body, body_sha=mem.body_sha,
                revised_at=now, vault=vault)
            if out is None:
                # The Source is gone (deleted out of band). Refusing here would
                # advance the ledger while storing the revision nowhere, so
                # re-create it rather than silently lose the body.
                _log.warn("absorb.source-missing-recreated",
                          source_path=str(mem.src),
                          hint="the operational Source for this memory was "
                               "absent; re-created it from the revision")
                _claims.write_operational_source(
                    statement=statement, body=mem.body,
                    attributed_to="absorbed", agent_kind="absorbed",
                    hook="manual", captured_at=now, sensitivity=sensitivity,
                    source_extra={"source_type": "claude-memory",
                                  "source_path": str(mem.src),
                                  "claude_memory_type": mem.type,
                                  "body_sha": mem.body_sha},
                    vault=vault)
                refreshed = True
            else:
                refreshed = bool(out.get("changed"))
            dest_repr = _claims.claim_path_for(statement, new_claim_id,
                                              vault=vault) or ""
            entry = _ledger_entry(mem, statement=statement)
            entry["claim_id"] = new_claim_id
            entry["revision_of"] = (prev or {}).get("absorbed_at")
            ledger["by_sha"][mem.body_sha] = entry
            ledger["by_path"][memory_key(str(mem.src))] = mem.body_sha
            ledger_dirty = True
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
                    # §4 step 3: the `refines` edge rides the NEW claim (where
                    # links belong), not the retract call on the old one.
                    **({"links": [{"to": supersedes, "rel": "refines",
                                   "why": "supersedes the previously absorbed "
                                          "revision of this memory"}]}
                       if supersedes else {}),
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
            claim_existed = bool(claim.get("existed"))
            if claim_existed and supersedes:
                # A→B→A: this description was used before, superseded away,
                # and is now back. The claim still exists but WE retracted it
                # on the way out — un-retract it, or the memory ends up with
                # every one of its claims retracted while it is live. Only a
                # supersession-authored retraction is reversed (identified by
                # its archive_reason); a curator's retraction is never undone.
                _unretract_if_superseded(dest_repr)
            if claim_existed and not supersedes:
                # Not a known revision (no prior absorb of this path), yet the
                # claim already exists — a DIFFERENT memory minted the same
                # statement. Its body is not stored anywhere; say so.
                _log.warn("absorb.revision-dropped", source_path=str(mem.src),
                          claim=claim["path"],
                          hint="another memory already minted this exact "
                               "statement, so the claim exists and is not "
                               "rewritten; this memory's body is not stored")
            if supersedes:
                superseded = _retract_superseded(
                    owners, supersedes, memory_key(str(mem.src)),
                    old_statement=prev_statement,
                    new_claim_id=new_claim_id, vault=vault)
            entry = _ledger_entry(mem, statement=statement)
            entry["claim_id"] = new_claim_id
            if supersedes:
                entry["supersedes"] = supersedes
            ledger["by_sha"][mem.body_sha] = entry
            ledger["by_path"][memory_key(str(mem.src))] = mem.body_sha
            ledger_dirty = True

        # sensitivity + pii_flag ride the record so a dry-run previews which
        # memories would land private/flagged before any write happens.
        # The revision fields report which §4 branch this memory took:
        # `body_refreshed` (statement unchanged — Source body updated, claim
        # untouched), `superseded` (description changed — old claim retracted),
        # or `revision_dropped` (a DIFFERENT memory already owns this exact
        # statement, so this body is stored nowhere).
        record = {"src": str(mem.src), "path": str(dest_repr),
                  "type": mem.type, "project": mem.project,
                  "sensitivity": sensitivity,
                  **({"pii_flag": True} if pii_flagged else {}),
                  **({"body_refreshed": True} if refreshed else {}),
                  **({"supersedes": supersedes} if supersedes else {}),
                  **({"superseded": True} if superseded else {}),
                  **({"revision_dropped": True}
                     if (claim_existed and not supersedes) else {})}
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
