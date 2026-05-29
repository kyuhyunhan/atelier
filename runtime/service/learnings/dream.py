"""Dream-cycle orchestration — the two-phase handshake (PR-33).

The engine is domain-ignorant: it cannot perform synthesis (step ②).
It can, however, *tee up* the work deterministically and *finalize* the
cadence afterwards. So `atelier dream` is two phases the live agent
drives:

    plan()      → engine returns clusters + per-member previews + a
                  ready-to-fill synthesize call shape. The agent reads
                  this, generalizes each cluster, and calls
                  atelier_principle_synthesize(status="proposed", ...).
    complete()  → after the agent has drafted proposals, it calls this
                  to advance last_dream_at (PR-29), clearing the nudge.
                  Call ONLY on a clean pass — an interrupted pass must
                  skip complete() so the nudge re-fires.

plan() already filters clusters that are "already covered" by an
existing principle (proposed/accepted/archived) so the agent is never
handed a cluster it would only end up skipping.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ...index import parse as _parse
from ...util import config as _config
from . import cluster as _cluster
from . import principles as _principles


def _vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _member_preview(vault: Path, slug: str) -> Dict[str, Any]:
    """A compact preview of one accepted learning for the agent to read
    without opening the file."""
    # by-topic canonical copy; find by stem.
    root = vault / "learnings" / "accepted" / "by-topic"
    path: Optional[Path] = None
    if root.exists():
        for p in root.rglob(f"{slug}.md"):
            path = p
            break
    if path is None:
        return {"slug": slug, "title": slug, "rule": "", "path": None}
    try:
        fm, body = _parse.split_frontmatter(path.read_text(encoding="utf-8"))
    except Exception:                        # pragma: no cover
        return {"slug": slug, "title": slug, "rule": "", "path": str(path)}
    return {
        "slug": slug,
        "title": fm.get("title") or slug,
        "project": fm.get("target_project") or fm.get("project_hint"),
        "topic": fm.get("target_topic"),
        "rule": _rule_oneliner(body),
        "rel": str(path.relative_to(vault)) if path else None,
    }


def _rule_oneliner(body: str) -> str:
    import re
    m = re.search(r"^##+\s*(Rule|Applicable rule|Observation)\b", body,
                  re.M | re.I)
    if not m:
        # first non-empty line
        for line in body.splitlines():
            if line.strip():
                return line.strip()[:200]
        return ""
    for line in body[m.end():].lstrip().splitlines():
        s = line.strip()
        if s:
            return s[:200]
    return ""


def plan(*, min_shared_terms: int = 2,
         min_size: int = 2,
         min_projects: int = 2,
         overlap_threshold: float = 0.6,
         limit: int = 20) -> Dict[str, Any]:
    """Phase 1 — return clusters worth synthesizing, each with member
    previews and a ready-to-fill synthesize call. Clusters already
    covered by an existing principle are filtered out."""
    vault = _vault_root()
    clustered = _cluster.cluster(
        min_shared_terms=min_shared_terms, min_size=min_size,
        min_projects=min_projects, limit=limit * 2,
    )

    plans: List[Dict[str, Any]] = []
    skipped_covered = 0
    for c in clustered["clusters"]:
        covering = _principles.find_covering_principle(
            c["member_entry_ids"], overlap_threshold=overlap_threshold,
            vault=vault,
        )
        if covering is not None:
            skipped_covered += 1
            continue
        previews = [_member_preview(vault, s) for s in c["member_slugs"]]
        plans.append({
            "cluster_key": c["cluster_key"],
            "projects": c["projects"],
            "shared_terms": c["shared_terms"],
            "size": c["size"],
            "members": previews,
            # The agent fills title/rule/why, then calls this tool.
            "synthesize_call": {
                "tool": "atelier_principle_synthesize",
                "args": {
                    "source_slugs": c["member_slugs"],
                    "source_entry_ids": c["member_entry_ids"],
                    "cluster_key": c["cluster_key"],
                    "status": "proposed",
                    "title": "<fill: the principle in a few words>",
                    "rule": "<fill: the rule in one or two sentences>",
                    "why": "<fill: the recurring reason it holds>",
                    "coverage": "cross-project",
                    "priority": "on-relevant-prompt",
                },
            },
        })
        if len(plans) >= limit:
            break

    status = _cluster.dream_status()
    return {
        "vault": str(vault),
        "accepted_scanned": clustered["accepted_scanned"],
        "candidate_count": len(plans),
        "skipped_already_covered": skipped_covered,
        "clusters": plans,
        "cadence": status,
        "instructions": (
            "For each cluster: read members, decide if they generalize to "
            "one cross-project rule. If yes, call atelier_principle_synthesize "
            "with the cluster's source_slugs/source_entry_ids/cluster_key and "
            "your title/rule/why (status defaults to proposed). When done with "
            "the whole pass, call atelier_dream_complete. If you stop early, "
            "do NOT call complete — the nudge will re-fire next session."
        ),
    }


def _days_between(iso_a: Optional[str], iso_b: str) -> Optional[float]:
    """Whole-ish days between two ISO timestamps; None if `iso_a` absent
    or unparseable."""
    if not iso_a:
        return None
    from datetime import datetime
    try:
        a = datetime.fromisoformat(iso_a)
        b = datetime.fromisoformat(iso_b)
    except ValueError:
        return None
    return (b - a).total_seconds() / 86400.0


def nudge_info(*, now: str) -> Dict[str, Any]:
    """Single source of the dream-nudge decision, shared by the
    session_bootstrap model-context injection, the SessionStart
    systemMessage hook, and the statusline. Returns:

        {due, accepted_since, days_since, pending, short, long}

    Two independent triggers fire `due`:
    - accumulation: accepted_since >= nudge_after_accepted OR
      days_since >= nudge_after_days
    - pending review: proposed drafts exist (interrupted/unreviewed dream)
    """
    from . import cluster as _cluster
    from . import principles as _principles

    cfg = _config.load()
    dream_cfg = (cfg.raw.get("learnings") or {}).get("dream") or {}
    after_accepted = int(dream_cfg.get("nudge_after_accepted", 15))
    after_days = int(dream_cfg.get("nudge_after_days", 7))

    try:
        status = _cluster.dream_status()
    except Exception:                       # pragma: no cover
        status = {"accepted_since_last_dream": 0, "last_dream_at": None}
    since = int(status.get("accepted_since_last_dream", 0))
    last = status.get("last_dream_at")
    days = _days_between(last, now)

    try:
        pending = _principles.review_proposed(limit=500).get("count", 0)
    except Exception:                       # pragma: no cover
        pending = 0

    accumulation_due = (
        since >= after_accepted
        or (days is not None and days >= after_days)
    )
    due = accumulation_due or pending > 0

    # ── long form (model context + systemMessage) ──
    long = ""
    if due:
        bits: List[str] = []
        if pending > 0:
            bits.append(
                f"{pending} proposed principle(s) await review "
                f"(`atelier_principle_review_proposed`)"
            )
        if accumulation_due:
            when = f"{since} new learnings" if since else "enough time"
            if days is not None and days >= after_days and since < after_accepted:
                when = f"{int(days)} days"
            bits.append(
                f"{when} since the last dream — ask me to run a dream pass "
                f"(`atelier_learning_cluster` → synthesize proposals)"
            )
        long = "💡 **atelier dream** — " + "; ".join(bits) + "."

    # ── short form (statusline) ──
    short = ""
    if due:
        segs: List[str] = []
        if accumulation_due and since:
            segs.append(f"{since} to dream")
        elif accumulation_due:
            segs.append("dream due")
        if pending > 0:
            segs.append(f"{pending} to review")
        short = "💡 atelier: " + " · ".join(segs)

    return {
        "due": due,
        "accepted_since": since,
        "days_since": days,
        "pending": pending,
        "short": short,
        "long": long,
    }


def complete(*, when: str) -> Dict[str, Any]:
    """Phase 2 — advance the dream baseline after a clean pass. `when` is
    an ISO timestamp from the caller (engine keeps no clock for
    determinism)."""
    out = _cluster.mark_dream_complete(when=when)
    # Report how many proposals are now awaiting review.
    pending = _principles.review_proposed(limit=500).get("count", 0)
    out["proposed_awaiting_review"] = pending
    return out
