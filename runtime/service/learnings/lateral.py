"""Lateral mutator (phase 5b, v1) — the dream cycle goes sideways.

The vertical dream cycle promotes learnings upward (candidate → accepted →
principle). The lateral mutator reorganizes the accepted corpus *in place* so
it stays findable as it grows. v1 ships the two jobs the manual passes proved
out, with their governance encoded:

- **plan_tags / apply_tags** — concept-tag learnings so each carries a
  distinctive probe. Suggestions are derived FROM the body (body-echo by
  construction); apply REJECTS any tag with no body echo (FTS indexes bodies,
  so a non-echoing tag is inert — the `concept-tagging` lesson as a gate);
  every apply is snapshot-wrapped and reports `newly_dark` (the omission
  guard: improving some learnings can silently bury others).
- **plan_merges** — flag near-duplicate groups by salient-term overlap.
  FLAG-ONLY: merging/retiring is high-blast-radius and stays human-gated,
  mirroring the dream cycle's cluster → synthesize → *human promote* split.

Division of labor (the dream-cycle pattern): the engine tees up
deterministically (no LLM anywhere here); the live agent supplies semantic
judgment (choosing/refining tags); the human gates destructive moves.
Writes are per-file and idempotent (a tagged learning is skipped, never
double-tagged); markdown stays the source of truth and `reindex` rebuilds
the projection after a write.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from . import cluster as _cluster
from . import surfacing as _surfacing

DEFAULT_SUGGESTIONS = 4
DEFAULT_MERGE_OVERLAP = 0.7

_TOKEN_RX = re.compile(r"\w+", re.UNICODE)
_TAG_SPLIT = re.compile(r"[\s\-_/]+")


def _vault_root() -> Path:
    from ...util import config as _config
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _body_tokens(path: Path) -> Set[str]:
    """All word tokens of the page body, lowercased — the echo universe.
    Wider than salient_terms (keeps short tokens like `kb`), because the echo
    gate asks "does FTS index this word at all?", not "is it salient?"."""
    from ...index import parse as _parse
    try:
        _, body = _parse.split_frontmatter(path.read_text(encoding="utf-8"))
    except Exception:               # pragma: no cover
        return set()
    return {t.lower() for t in _TOKEN_RX.findall(body)}


def _echoes(tag: str, body_tokens: Set[str]) -> bool:
    toks = [t for t in _TAG_SPLIT.split(tag.lower()) if t]
    return any(t in body_tokens for t in toks)


def _topic_tokens(topic: str, project: str) -> Set[str]:
    out: Set[str] = set()
    for s in (topic, project):
        out.update(t for t in _TAG_SPLIT.split((s or "").lower()) if t)
    return out


# ── plan: tags ───────────────────────────────────────────────────────────────


def plan_tags(*, suggest: int = DEFAULT_SUGGESTIONS) -> Dict[str, Any]:
    """Tee up the tagging work, deterministically. Returns:

    - `untagged` — learnings with no `touches`, each with up to `suggest`
      candidate tags: the learning's most *distinctive* salient terms (lowest
      corpus document-frequency first), excluding its own topic/project tokens
      (those are already in the probe and are exactly the coarse-bucket
      collision being broken).
    - `inert_tagged` — learnings whose existing tags have ZERO body echo
      (inert under FTS); they need re-tagging by hand.

    The live agent refines these suggestions; apply_tags() enforces the gates.
    """
    vault = _vault_root()
    learnings = _cluster.load_accepted(vault)
    df: Counter = Counter(t for l in learnings for t in set(l.terms))

    untagged: List[Dict[str, Any]] = []
    inert: List[Dict[str, Any]] = []
    for l in learnings:
        if not l.touches:
            skip = _topic_tokens(l.topic, l.project)
            # `suggestions` may legitimately be EMPTY (every salient term was a
            # topic/project token): the engine cannot help there — the live
            # agent must read the body and derive tags itself. The entry still
            # appears in `untagged` because it still needs tags (review Q2).
            ranked = sorted(
                (t for t in l.terms if t not in skip),
                key=lambda t: (df[t], t),          # rarest first, then stable
            )
            untagged.append({
                "entry_id": l.entry_id,
                "slug": l.slug,
                "path": str(l.path.relative_to(vault)),
                "topic": l.topic,
                "project": l.project,
                "suggestions": ranked[:suggest],
            })
        else:
            body = _body_tokens(l.path)
            if not any(_echoes(t, body) for t in l.touches):
                inert.append({
                    "entry_id": l.entry_id,
                    "slug": l.slug,
                    "path": str(l.path.relative_to(vault)),
                    "touches": list(l.touches),
                })
    return {
        "corpus": len(learnings),
        "untagged": untagged,
        "inert_tagged": inert,
        "params": {"suggest": suggest},
    }


# ── apply: tags ──────────────────────────────────────────────────────────────


def _insert_touches(path: Path, tags: List[str]) -> bool:
    """Textually insert a `touches:` block before the closing frontmatter
    fence. Minimal-diff by design (no YAML round-trip — the vault's files are
    user content; re-serializing would churn them cosmetically). Idempotent:
    refuses when a touches block already exists anywhere in the frontmatter
    (the guard scans up to the closing fence, never a fixed line window — a
    capped scan double-inserted on long frontmatter; review M1)."""
    if not tags:                    # never write a bare `touches:` header
        return False
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if not lines or not lines[0].startswith("---"):
        return False
    fence = next((i for i in range(1, len(lines))
                  if lines[i].rstrip() == "---"), None)
    if fence is None:
        return False                # pragma: no cover - malformed frontmatter
    if any(l.startswith("touches:") for l in lines[1:fence]):
        return False
    block = ["touches:\n"] + [f"- {t}\n" for t in tags]
    lines[fence:fence] = block
    path.write_text("".join(lines), encoding="utf-8")
    return True


def apply_tags(mapping: Dict[str, List[str]],
               *, probe_k: Optional[int] = None) -> Dict[str, Any]:
    """Apply `entry_id → tags` to canonicals (the by-project mirror was retired,
    RFC 0001), snapshot-wrapped:

    1. surfacing snapshot (before)
    2. per learning: drop tags with no body echo (`rejected`), insert the rest
       (skip files that already carry `touches` — idempotent)
    3. reindex (markdown → DB; concept edges rebuilt)
    4. surfacing snapshot (after) → diff — `newly_dark` is the omission guard
       and is part of the return contract; a caller MUST look at it.

    Result counters: `applied` (note written), `skipped` (already had touches),
    `fully_rejected` (every tag failed the echo gate — nothing written),
    `mirror_skipped` (retired with the by-project mirror, always 0; kept in the
    return contract), `unknown` (entry_id not in the corpus), `rejected`
    (per-entry tags dropped by the gate, regardless of counter).
    """
    from .. import api as _api
    kw = {"probe_k": probe_k} if probe_k is not None else {}

    vault = _vault_root()
    # Concurrency note: the MCP tool layer serializes callers via the CURATOR
    # writer lock, so no accept/apply can interleave between this snapshot and
    # load_accepted below. Direct in-process callers (scripts, tests) must not
    # run concurrent curator writes — same rule as every curator entry point.
    before = _surfacing.snapshot(**kw)

    learnings = {l.entry_id: l for l in _cluster.load_accepted(vault)}

    applied = skipped = fully_rejected = mirror_skipped = 0
    rejected: Dict[str, List[str]] = {}
    unknown: List[str] = []
    for eid, tags in mapping.items():
        l = learnings.get(eid)
        if l is None:
            unknown.append(eid)
            continue
        body = _body_tokens(l.path)
        good = [t for t in tags if _echoes(t, body)]
        bad = [t for t in tags if not _echoes(t, body)]
        if bad:
            rejected[eid] = bad
        if not good:
            # every tag failed the echo gate — make that loudly countable
            # rather than folding it into applied/skipped silence (review S1).
            fully_rejected += 1
            continue
        if _insert_touches(l.path, good):
            applied += 1                       # one flat note, no mirror (RFC 0001)
        else:
            skipped += 1

    if applied:
        _api.reindex(full=True)

    after = _surfacing.snapshot(**kw)
    diff = _surfacing.diff(before, after)
    return {
        "applied": applied,
        "skipped": skipped,
        "fully_rejected": fully_rejected,
        "mirror_skipped": mirror_skipped,
        "rejected": rejected,
        "unknown": unknown,
        "diff": diff,
    }


# ── plan: merges (flag-only) ─────────────────────────────────────────────────


def plan_merges(*, overlap: float = DEFAULT_MERGE_OVERLAP) -> Dict[str, Any]:
    """Flag groups of near-duplicate learnings (salient-term Jaccard ≥
    `overlap`). FLAG-ONLY by design: merging or retiring a learning is
    high-blast-radius (it changes what the vault remembers), so v1 reports
    candidates and a human decides — exactly the dream cycle's promote gate.
    """
    vault = _vault_root()
    learnings = _cluster.load_accepted(vault)

    # union-find over pairs above the overlap threshold
    parent: Dict[str, str] = {l.entry_id: l.entry_id for l in learnings}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # O(n²) pairwise comparison — acceptable to roughly ~2K learnings (today's
    # corpus is in the hundreds; the mutator runs in infrequent batches). Past
    # that, pre-filter pairs with an inverted term→learnings index so only
    # pairs sharing ≥1 salient term are compared (review S2).
    pair_overlap: Dict[frozenset, float] = {}
    for i, a in enumerate(learnings):
        for b in learnings[i + 1:]:
            j = _cluster.jaccard(a.terms, b.terms)
            if j >= overlap:
                pair_overlap[frozenset((a.entry_id, b.entry_id))] = j
                parent[find(a.entry_id)] = find(b.entry_id)

    groups_map: Dict[str, List[str]] = {}
    for l in learnings:
        groups_map.setdefault(find(l.entry_id), []).append(l.entry_id)

    by_id = {l.entry_id: l for l in learnings}
    groups: List[Dict[str, Any]] = []
    for members in groups_map.values():
        if len(members) < 2:
            continue
        members = sorted(members)
        pairs = [v for k, v in pair_overlap.items() if k <= set(members)]
        groups.append({
            "entry_ids": members,
            "slugs": [by_id[m].slug for m in members],
            "topics": sorted({by_id[m].topic for m in members}),
            "max_overlap": round(max(pairs), 3) if pairs else overlap,
        })
    groups.sort(key=lambda g: (-g["max_overlap"], g["entry_ids"][0]))
    return {
        "corpus": len(learnings),
        "groups": groups,
        "params": {"overlap": overlap},
        "note": "flag-only: merge/retire decisions are human-gated",
    }


# ── forgetting (RFC 0006 Pillar ④a — flag-only, mirrors plan_merges) ────────


def plan_forgets(*, probe_k: int = _surfacing.DEFAULT_PROBE_K) -> Dict[str, Any]:
    """Flag accepted learnings the surfacing audit reports DARK — unreachable by
    their own concept right now — as retraction CANDIDATES.

    FLAG-ONLY, same governance as `plan_merges`: this is the "does the pool ever
    shrink?" half of RFC 0006 4a. A learning going dark is not proof it should be
    forgotten (a probe-vocabulary mismatch can also cause it -- the exact failure
    mode P0.2b fixed once already), so retraction is a human decision via
    `review.retract(slug=...)`, never automatic here. `apply_tags` already proved
    this snapshot-diff discipline for the tagging job; this reuses the SAME
    `surfacing.audit` the omission gate (INV-4) is built on, so a learning this
    flags is provably unreachable by the identical measure the verifier trusts --
    not a second, drifting definition of "forgettable".

    `slug` on each candidate is the real filename stem (matching `plan_merges`'s
    contract) -- NOT `entry_id` duplicated under another key -- so a human's
    `review.retract(slug=...)` call gets an actual human-readable handle."""
    vault = _vault_root()
    aud = _surfacing.audit(probe_k=probe_k)
    by_id = {l.entry_id: l for l in _cluster.load_accepted(vault)}
    candidates = [
        {"entry_id": d["entry_id"],
         "slug": by_id[d["entry_id"]].slug if d["entry_id"] in by_id else d["entry_id"],
         "title": d["title"], "project": d["project"], "topic": d["topic"]}
        for d in aud["dark"]
    ]
    return {
        "total": aud["total"],
        "candidates": candidates,
        "candidate_count": len(candidates),
        "probe_k": probe_k,
        "note": ("flag-only: dark does not imply forget — a human reviews each "
                "candidate and calls review.retract(slug=...) if it should be "
                "removed from the accepted pool"),
    }
