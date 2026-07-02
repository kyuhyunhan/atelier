"""Dream-cycle orchestration on CLAIM FIELDS (RFC 0005 §7.1).

`dream` is no longer a directory mover. It is the T0-budget curator (§6):

  - **distill** `proactive → always`: elevate the highest-value proactive claims
    into the small, hard-capped `always` (T0) budget — a FIELD transition in
    place (`claims_io.set_surfacing`), never a file move.
  - **synthesize** new Claims: cross-claim generalizations that the agent writes,
    `generated_by: dream`, `derived_from` the source claims and linked to them by
    `refines`/`supports` (RFC 0005 §4.3). The synthesized generalization is born
    `surfacing: always` (it is what earns the T0 budget).

The engine stays OFF the generate path (RFC 0003 / §11): it tees the work up
deterministically (cluster proactive claims, prepare a ready-to-fill call shape)
and finalizes the cadence; the live agent supplies the synthesis *text*:

    plan()      → engine returns clusters of proactive claims + per-member
                  previews + a ready-to-fill synthesize call. The agent reads
                  this, generalizes each cluster, and calls dream.synthesize(...).
    synthesize()→ engine writes ONE new always-claim from the agent's
                  statement/why, linked refines/supports to the source claims.
    distill()   → elevate named proactive claims to always (T0), capped.
    complete()  → after a clean pass, advance the cadence baseline (clears the
                  nudge). Skip on an interrupted pass so the nudge re-fires.

plan() filters clusters already covered by an existing synthesized claim, so the
agent is never handed a cluster it would only re-synthesize.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ...util import config as _config
from . import claims_io as _claims
from . import cluster as _cluster
from . import recall_v7 as _rv


def _vault_root() -> Path:
    cfg = _config.load()
    if cfg.vault is not None:
        return cfg.vault.local
    return cfg.space_by_role("librarian-territory").local


def _member_preview(vault: Path, entry_id: str) -> Dict[str, Any]:
    """A compact preview of one proactive claim for the agent to read without
    opening the file."""
    found = _claims.find_claim_by_entry_id(entry_id, vault)
    if found is None:
        return {"entry_id": entry_id, "statement": "", "domain": "",
                "project": "", "rel": None}
    _path, fm, _body = found
    return {
        "entry_id": entry_id,
        "statement": str(fm.get("statement") or "")[:240],
        "domain": fm.get("domain") or "",
        "project": fm.get("project") or "",
        "rel": str(_path.relative_to(vault)) if _path else None,
    }


# ── coverage: a cluster already synthesized? ──────────────────────────────────


def _synthesized_link_targets(vault: Path) -> List[set]:
    """Every existing dream-synthesized claim's set of linked source claim ids.
    Used to skip a cluster a prior pass already generalized (idempotent re-runs).
    """
    out: List[set] = []
    for p in _claims.iter_claim_files(vault):
        got = _claims.read_claim(p)
        if got is None:
            continue
        fm, _ = got
        if fm.get("generated_by") != "dream":
            continue
        targets = set()
        for ln in fm.get("links") or []:
            if isinstance(ln, dict) and ln.get("to"):
                targets.add(str(ln["to"]))
            elif isinstance(ln, str):
                targets.add(ln)
        # also count its derived_from (the source-claim provenance)
        df = fm.get("derived_from")
        if isinstance(df, list):
            targets.update(str(x) for x in df)
        if targets:
            out.append(targets)
    return out


def _is_covered(member_ids: List[str], covered_sets: List[set],
                overlap_threshold: float) -> bool:
    target = set(member_ids)
    if not target:
        return False
    for existing in covered_sets:
        overlap = len(target & existing) / len(target)
        if overlap >= overlap_threshold:
            return True
    return False


# ── plan ──────────────────────────────────────────────────────────────────────


def plan(*, min_shared_terms: int = 2,
         min_size: int = 2,
         min_projects: int = 1,
         overlap_threshold: float = 0.6,
         limit: int = 20) -> Dict[str, Any]:
    """Phase 1 — cluster proactive claims into generalizable groups, each with
    member previews + a ready-to-fill `dream.synthesize` call. Clusters already
    covered by an existing synthesized claim are filtered out (RFC 0005 §7.1)."""
    vault = _vault_root()
    clustered = _cluster.cluster_claims(
        min_shared_terms=min_shared_terms, min_size=min_size,
        min_projects=min_projects, limit=limit * 2,
    )

    covered_sets = _synthesized_link_targets(vault)
    plans: List[Dict[str, Any]] = []
    skipped_covered = 0
    for c in clustered["clusters"]:
        member_ids = c["member_entry_ids"]
        if _is_covered(member_ids, covered_sets, overlap_threshold):
            skipped_covered += 1
            continue
        previews = [_member_preview(vault, e) for e in member_ids]
        plans.append({
            "cluster_key": c["cluster_key"],
            "domains": c["projects"],          # claim clusters group by domain/project
            "shared_terms": c["shared_terms"],
            "size": c["size"],
            "members": previews,
            # The agent fills statement/why, then calls this tool. The engine
            # writes the node — the agent never writes the vault directly.
            "synthesize_call": {
                "tool": "atelier_dream_synthesize",
                "args": {
                    "source_claim_ids": member_ids,
                    "cluster_key": c["cluster_key"],
                    "statement": "<fill: the generalization in one assertion>",
                    "why": "<fill: the recurring reason it holds>",
                    "rel": "refines",
                },
            },
        })
        if len(plans) >= limit:
            break

    status = _cluster.dream_status()
    return {
        "vault": str(vault),
        "proactive_scanned": clustered.get("proactive_scanned", 0),
        "candidate_count": len(plans),
        "skipped_already_covered": skipped_covered,
        "clusters": plans,
        "cadence": status,
        "instructions": (
            "For each cluster: read members, decide if they generalize to one "
            "cross-claim assertion. If yes, call atelier_dream_synthesize with "
            "the cluster's source_claim_ids and your statement/why (it writes a "
            "new surfacing:always claim linked refines/supports to the sources). "
            "Optionally call atelier_dream_distill to elevate strong proactive "
            "claims into the T0 budget. When the whole pass is done, call "
            "atelier_dream_complete. If you stop early, do NOT call complete — "
            "the nudge will re-fire next session."
        ),
    }


# ── synthesize (agent text → engine-written always-claim) ─────────────────────


def synthesize(*, source_claim_ids: List[str],
               statement: str,
               why: str = "",
               rel: str = "refines",
               is_about: Optional[List[str]] = None,
               domain: str = "operational",
               sensitivity: str = "public",
               project: Optional[str] = None,
               cluster_key: Optional[str] = None,
               overlap_threshold: float = 0.6,
               skip_if_covered: bool = True,
               ) -> Dict[str, Any]:
    """Write ONE new synthesized always-claim generalizing `source_claim_ids`
    (RFC 0005 §7.1). The agent supplies `statement`/`why`; the engine writes the
    node (`generated_by: dream`, `surfacing: always`, linked `rel` to each source,
    `derived_from` the sources' own upstream sources). Idempotent: a cluster
    already covered by a prior synthesized claim is skipped."""
    vault = _vault_root()
    if not source_claim_ids:
        raise ValueError("synthesize requires source_claim_ids")
    if not (statement or "").strip():
        raise ValueError("synthesize requires a statement")

    if skip_if_covered:
        covered_sets = _synthesized_link_targets(vault)
        if _is_covered(source_claim_ids, covered_sets, overlap_threshold):
            return {"skipped": True, "reason": "already-covered",
                    "source_claim_ids": source_claim_ids}

    # Collect the source claims' OWN upstream sources for the PROV chain, plus
    # any is_about entities to carry onto the generalization when the caller did
    # not pass them.
    upstream: List[str] = []
    inferred_about: List[str] = []
    for cid in source_claim_ids:
        found = _claims.find_claim_by_entry_id(cid, vault)
        if found is None:
            continue
        _p, fm, _b = found
        df = fm.get("derived_from")
        if isinstance(df, list):
            upstream.extend(str(x) for x in df)
        ab = fm.get("is_about")
        if isinstance(ab, list):
            inferred_about.extend(str(x) for x in ab)

    out = _claims.write_synthesized_claim(
        statement=statement,
        source_claim_ids=source_claim_ids,
        source_entry_ids_for_id=upstream or None,
        is_about=is_about if is_about is not None else list(dict.fromkeys(inferred_about)),
        rel=rel,
        why=why,
        domain=domain,
        sensitivity=sensitivity,
        surfacing=_claims.TIER_ALWAYS,
        project=project,
        vault=vault,
    )
    out["skipped"] = False
    if cluster_key:
        out["cluster_key"] = cluster_key
    return out


# ── distill (proactive → always, T0 budget) ──────────────────────────────────


def distill(*, claim_ids: List[str]) -> Dict[str, Any]:
    """Elevate named proactive claims to `always` (T0) — a field transition in
    place (RFC 0005 §7.1). The T0 budget is hard-capped at recall (recall_v7.
    T0_CAP); distilling more than fits simply means the recall ranker keeps the
    most relevant. Only claims currently at `proactive` are elevated; anything
    else is skipped (idempotent)."""
    vault = _vault_root()
    elevated: List[str] = []
    skipped: List[Dict[str, str]] = []
    for cid in claim_ids:
        found = _claims.find_claim_by_entry_id(cid, vault)
        if found is None:
            skipped.append({"entry_id": cid, "reason": "not-found"})
            continue
        path, fm, body = found
        if _claims.surfacing_of(fm) != _claims.TIER_PROACTIVE:
            skipped.append({"entry_id": cid, "reason": "not-proactive"})
            continue
        _claims.set_surfacing(path, fm, body, new_tier=_claims.TIER_ALWAYS,
                              generated_by="dream")
        elevated.append(cid)
    return {"elevated": elevated, "skipped": skipped,
            "t0_cap": _rv.T0_CAP}


# ── nudge (cadence) ───────────────────────────────────────────────────────────


def _days_between(iso_a: Optional[str], iso_b: str) -> Optional[float]:
    """Whole-ish days between two ISO timestamps; None if `iso_a` absent or
    unparseable."""
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
    session_bootstrap model-context injection, the SessionStart systemMessage
    hook, and the statusline. Returns:

        {due, accepted_since, days_since, pending, short, long}

    Two independent triggers fire `due`:
    - accumulation: accepted_since >= nudge_after_accepted OR
      days_since >= nudge_after_days
    - pending review: proposed drafts exist (interrupted/unreviewed dream)

    The cadence (count + time) is shared infrastructure unchanged by the §7.1
    field-transition rework; the pending-review trigger counts proposed principle
    *claims* (ac_status:pending) — the principle file pipeline is retired (P7), so
    `review_proposed` is now a tier/field query over claims, not a dir scan."""
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
        pending = _principles.proposed_count()
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
                f"(`atelier_dream_plan` → synthesize + distill)"
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


# ── complete ──────────────────────────────────────────────────────────────────


def complete(*, when: str) -> Dict[str, Any]:
    """Phase 2 — advance the dream baseline after a clean pass. `when` is an ISO
    timestamp from the caller (engine keeps no clock for determinism)."""
    from . import principles as _principles
    out = _cluster.mark_dream_complete(when=when)
    # Report any still-pending proposed principle claims (ac_status:pending) so
    # the CLI summary line stays informative.
    try:
        out["proposed_awaiting_review"] = _principles.review_proposed(
            limit=500).get("count", 0)
    except Exception:                       # pragma: no cover
        out["proposed_awaiting_review"] = 0
    return out
