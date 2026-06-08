"""Deterministic clustering of accepted learnings — step ① of the dream cycle.

The engine is domain-ignorant: it cannot decide *what a group of learnings
means*. It can, however, mechanically group learnings that share salient
vocabulary AND span multiple projects — exactly the candidates worth
generalizing into a cross-project principle. The semantic generalization
(step ②) is left to the live agent.

Algorithm (fully deterministic — same vault → same clusters):

1. Load every accepted learning from the flat notes/ store.
2. Extract a salient term-set per learning from its body + title
   (lowercased word tokens, stopword-filtered, length≥4).
3. Build clusters by single-link agglomeration on term-overlap:
   two learnings link if they share ≥ `min_shared_terms` salient terms.
4. Keep only clusters that (a) have ≥ `min_size` members and
   (b) span ≥ `min_projects` distinct projects.
5. Each cluster reports its member slugs, the projects it spans, and the
   terms its members share — enough for the agent to draft a principle
   and for idempotent dedup downstream.

No LLM, no randomness, no network.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ...util import config as _config
from ...util import db as _db


# Minimal stopword set — we only need to kill the highest-frequency noise
# so shared-term overlap reflects real topical similarity.
_STOPWORDS = {
    "this", "that", "with", "from", "have", "they", "their", "them",
    "when", "what", "which", "while", "would", "could", "should", "into",
    "than", "then", "your", "yours", "about", "above", "after", "again",
    "because", "before", "being", "between", "both", "does", "doing",
    "down", "each", "more", "most", "only", "other", "over", "same",
    "some", "such", "very", "were", "will", "wont", "dont", "didnt",
    "must", "must", "also", "make", "made", "uses", "used", "using",
    "like", "just", "not", "but", "and", "the", "for", "are", "was",
    "via", "per", "out", "use", "see", "one", "two", "its",
}

_WORD_RX = re.compile(r"[a-z][a-z0-9_-]{3,}", re.IGNORECASE)

# Structural scaffold words from the learning markdown template
# (## Observation / ## Why this matters / ## Applicable rule /
# ## Source excerpt). These appear in *every* candidate body, so they
# create false cross-learning overlap and must be excluded from the
# salient term-set.
_SCAFFOLD_WORDS = {
    "observation", "matters", "applicable", "rule", "source", "excerpt",
    "session", "tail", "fill", "sentences", "recurring", "reason", "holds",
}


def _vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


@dataclass
class Learning:
    """One accepted learning loaded from the flat notes/ markdown.
    Public: shared by the dream cycle (cluster) and the lateral mutator."""
    slug: str
    project: str
    topic: str
    entry_id: str
    terms: Set[str]
    touches: List[str]
    path: Path


@dataclass
class Cluster:
    cluster_key: str               # stable hash of sorted member entry_ids
    member_slugs: List[str]
    member_entry_ids: List[str]
    projects: List[str]
    shared_terms: List[str]
    size: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cluster_key": self.cluster_key,
            "member_slugs": self.member_slugs,
            "member_entry_ids": self.member_entry_ids,
            "projects": self.projects,
            "shared_terms": self.shared_terms,
            "size": self.size,
        }


def _strip_markdown_headers(text: str) -> str:
    """Drop lines that are markdown headers (the learning template's
    scaffold) so only substantive prose contributes terms."""
    return "\n".join(
        ln for ln in text.splitlines() if not ln.lstrip().startswith("#")
    )


def salient_terms(text: str) -> Set[str]:
    out: Set[str] = set()
    for m in _WORD_RX.finditer(_strip_markdown_headers(text).lower()):
        w = m.group(0)
        if w in _STOPWORDS or w in _SCAFFOLD_WORDS:
            continue
        out.add(w)
    return out


def load_accepted(vault: Path) -> List[Learning]:
    """Read accepted learnings from the flat notes/ store (RFC 0001).

    Markdown is the source of truth; the dream cycle runs infrequently
    (batch), so we read the filesystem directly rather than the DB
    projection — this avoids missing learnings that were accepted since
    the last `reindex`. Public: shared by cluster() and the lateral mutator.
    Navigational views (INDEX/TAXONOMY) are excluded via recall's shared
    noise predicate — they are projections, not learnings.
    """
    from ...index import parse as _parse
    from . import recall as _recall
    from . import store as _store
    learnings: List[Learning] = []
    for p in _store.iter_accepted_files(vault):
        if _recall.is_noise(p.name):
            continue
        try:
            fm, body = _parse.split_frontmatter(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        terms = salient_terms((fm.get("title") or "") + " " + body)
        raw_touches = fm.get("touches")
        touches = [t for t in raw_touches if isinstance(t, str)] \
            if isinstance(raw_touches, list) else []
        learnings.append(Learning(
            slug=p.stem,
            project=str(fm.get("target_project") or fm.get("project_hint") or ""),
            topic=str(fm.get("target_topic") or ""),
            entry_id=str(fm.get("entry_id") or p.stem),
            terms=terms,
            touches=touches,
            path=p,
        ))
    return learnings


def _cluster_key(entry_ids: List[str]) -> str:
    import hashlib
    joined = "|".join(sorted(entry_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def cluster(*, min_shared_terms: int = 2,
            min_size: int = 2,
            min_projects: int = 2,
            max_doc_frequency: float = 0.6,
            dedup_jaccard: float = 0.7,
            limit: int = 50) -> Dict[str, Any]:
    """Group accepted learnings into cross-project clusters by *term
    anchoring* (not single-link agglomeration, which chains into one
    blob at scale).

    For each salient term that (a) is not too common across the corpus
    (`max_doc_frequency`) and (b) anchors learnings spanning
    ≥`min_projects` projects with ≥`min_size` members, emit a cluster of
    the learnings containing that term. Near-duplicate clusters (member
    Jaccard ≥ `dedup_jaccard`) are merged so overlapping seed terms don't
    produce redundant clusters.

    Deterministic: identical vault contents always yield identical
    clusters (sorted members, content-hash keys, stable seed ordering).
    """
    vault = _vault_root()
    items = load_accepted(vault)
    n = len(items)

    # term → indices of learnings containing it
    term_to_idx: Dict[str, Set[int]] = defaultdict(set)
    for i, it in enumerate(items):
        for t in it.terms:
            term_to_idx[t].add(i)

    # Document-frequency cap filters generic terms in a *large* corpus.
    # Never drop below min_size, else small corpora exclude every shared
    # term (a term in 2/2 docs would look "too common").
    df_cap = max(min_size, int(n * max_doc_frequency))

    # Seed terms: not too common, span enough projects + members.
    seeds: List[Tuple[str, frozenset]] = []
    for term, idxs in term_to_idx.items():
        if len(idxs) < min_size or len(idxs) > df_cap:
            continue
        projects = {items[i].project for i in idxs if items[i].project}
        if len(projects) < min_projects:
            continue
        seeds.append((term, frozenset(idxs)))

    # Deterministic seed order: widest project spread, then most members,
    # then term alphabetically.
    def _spread(idxs: frozenset) -> int:
        return len({items[i].project for i in idxs if items[i].project})
    seeds.sort(key=lambda s: (-_spread(s[1]), -len(s[1]), s[0]))

    clusters: List[Cluster] = []
    emitted_member_sets: List[Set[str]] = []   # by entry_id, for dedup

    for term, idxs in seeds:
        members = sorted(idxs)
        entry_ids = sorted(items[m].entry_id for m in members)
        member_id_set = set(entry_ids)

        # Skip if highly overlapping with an already-emitted cluster.
        if any(jaccard(member_id_set, prev) >= dedup_jaccard
               for prev in emitted_member_sets):
            continue

        # Terms common to a majority of members (informative, not strict
        # full-intersection which collapses on big clusters). Always
        # includes the seed.
        freq: Dict[str, int] = defaultdict(int)
        for m in members:
            for t in items[m].terms:
                freq[t] += 1
        threshold = max(2, (len(members) + 1) // 2)
        common = sorted(
            (t for t, c in freq.items() if c >= threshold),
            key=lambda t: (-freq[t], t),
        )
        if term not in common:
            common.insert(0, term)
        if len(common) < min_shared_terms:
            continue

        projects = sorted({items[m].project for m in members if items[m].project})
        clusters.append(Cluster(
            cluster_key=_cluster_key(entry_ids),
            member_slugs=sorted(items[m].slug for m in members),
            member_entry_ids=entry_ids,
            projects=projects,
            shared_terms=common[:12],
            size=len(members),
        ))
        emitted_member_sets.append(member_id_set)

    clusters.sort(key=lambda c: (-len(c.projects), -c.size, c.cluster_key))
    clusters = clusters[:limit]

    return {
        "vault": str(vault),
        "accepted_scanned": n,
        "cluster_count": len(clusters),
        "clusters": [c.to_dict() for c in clusters],
        "params": {
            "min_shared_terms": min_shared_terms,
            "min_size": min_size,
            "min_projects": min_projects,
            "max_doc_frequency": max_doc_frequency,
            "dedup_jaccard": dedup_jaccard,
        },
    }


# ── dream-cadence tracking (meta table) ─────────────────────────────────────

_META_LAST_DREAM = "last_dream_at"
_META_DREAM_BASELINE = "dream_accepted_baseline"   # accepted count at last dream


def _count_accepted(vault: Path) -> int:
    """Count canonical accepted learnings on disk (markdown is truth).

    The DB projection can lag behind accepts that happened since the last
    reindex, so the cadence counter reads the filesystem directly.
    """
    from . import store as _store
    from . import recall as _recall
    # Share recall's noise predicate (INDEX/TAXONOMY/README) with load_accepted
    # so the cadence count matches the actually-loadable set.
    return sum(1 for p in _store.iter_accepted_files(vault)
               if not _recall.is_noise(p.name))


def dream_status() -> Dict[str, Any]:
    """Return cadence info for the nudge: last dream time + how many
    accepted learnings have appeared since."""
    vault = _vault_root()
    last: Optional[str] = None
    baseline_raw: Optional[str] = None
    conn = _db.connect()
    try:
        last = _db.get_meta(conn, _META_LAST_DREAM)
        baseline_raw = _db.get_meta(conn, _META_DREAM_BASELINE)
    except Exception:
        # meta table absent (DB not yet migrated) → treat as never-dreamed.
        pass
    finally:
        conn.close()
    total = _count_accepted(vault)
    baseline = int(baseline_raw) if (baseline_raw or "").isdigit() else 0
    return {
        "last_dream_at": last,
        "accepted_total": total,
        "accepted_since_last_dream": max(0, total - baseline),
    }


def mark_dream_complete(*, when: str) -> Dict[str, Any]:
    """Advance the dream baseline. Call ONLY on a clean, complete pass —
    an interrupted pass must leave these unchanged so the nudge re-fires.
    `when` is an ISO timestamp supplied by the caller (engine has no clock
    of its own in tests)."""
    vault = _vault_root()
    total = _count_accepted(vault)
    conn = _db.connect()
    try:
        _db.set_meta(conn, _META_LAST_DREAM, when)
        _db.set_meta(conn, _META_DREAM_BASELINE, str(total))
        conn.commit()
    finally:
        conn.close()
    return {"last_dream_at": when, "accepted_total": total}
