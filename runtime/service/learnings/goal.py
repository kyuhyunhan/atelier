"""RFC 0009 §6 / §8.1 — the goal orchestrator: wire the three layers together.

`contract.py` scores INTENT and ENVELOPE; `freeze.py` proves the contract and the
before-picture are trustworthy; `verify.py` holds the global invariants. This
module is where they meet, and where a `supersedes` release is actually *applied*
(the other modules only shape-check it).

Two entry points, split on purpose:

- `verify_contract(contract, before, after)` — **pure**: evaluate + apply the
  invariants, honouring supersession. No git, no vault, no clock. This is what the
  §8.1 two-sided gate exercises end-to-end with an injected delta.
- `verify_contract_run(...)` — the operational wrapper: read the committed
  contract, check the pins, generate the after-state, then call the pure core.

The invariant clauses are DATA (`schema/data/invariants.yaml`, §3.3), decomposed
so a `supersedes` entry can release exactly one clause — INV-4 guards two
quantities, and releasing it wholesale for a fall in `visible` would silently stop
gating `dark_count`. INV-1 (no node kind vanished) stays whole and unreleasable:
a goal never legitimately reduces a node *kind* (its counters live in `metrics`).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from . import contract as _contract
from . import vault_state as _vault_state
from .contract import ContractError

_INVARIANTS_YAML = (Path(__file__).resolve().parents[3]
                    / "schema" / "data" / "invariants.yaml")


# ── invariant clauses (schema-driven, §3.3) ─────────────────────────────────

def _clauses() -> List[Dict[str, Any]]:
    data = yaml.safe_load(_INVARIANTS_YAML.read_text(encoding="utf-8"))
    return list(data.get("clauses") or [])


def _released(direction: str) -> str:
    """A `supersedes` direction releases the clause forbidding the opposite."""
    return "fall" if direction == "may-fall" else "rise"


def _superseded_clause_ids(contract: Dict[str, Any]) -> set:
    """The clause ids a validated `supersedes` block releases. Matching is on
    (metric, forbidden-direction); the shape/INTENT-pairing checks already ran in
    `contract.validate_supersedes`."""
    entries = _contract.validate_supersedes(contract)
    by_metric_dir = {(c["metric"], c["forbids"]): c["id"] for c in _clauses()}
    out = set()
    for e in entries:
        key = (e["metric"], _released(e["direction"]))
        cid = by_metric_dir.get(key)
        if cid is None:
            raise ContractError(
                f"supersedes names {e['metric']}/{e['direction']}, which is not "
                "a known invariant clause")
        out.add(cid)
    return out


def apply_invariants(before: Dict[str, Any], after: Dict[str, Any],
                     superseded: set) -> List[Dict[str, Any]]:
    """Run each schema invariant clause not in `superseded`. Returns per-clause
    results; a missing metric on either side is a RAISE (an invariant that cannot
    be measured is a broken harness, not a satisfied one), except that a clause
    whose metric is absent from BOTH baselines is skipped as not-applicable."""
    results: List[Dict[str, Any]] = []
    for clause in _clauses():
        cid = clause["id"]
        if cid in superseded:
            results.append({"layer": "invariant", "id": cid, "ok": True,
                            "superseded": True, "detail": "released by supersedes"})
            continue
        metric, forbids = clause["metric"], clause["forbids"]
        bv = _contract._leaf(before, metric)
        av = _contract._leaf(after, metric)
        if bv is _contract._MISSING and av is _contract._MISSING:
            continue                                 # metric not in this baseline
        if bv is _contract._MISSING or av is _contract._MISSING:
            raise ContractError(
                f"invariant {cid} names {metric!r}, absent from "
                f"{'before' if bv is _contract._MISSING else 'after'}")
        ok = av >= bv if forbids == "fall" else av <= bv
        arrow = "fell" if forbids == "fall" else "rose"
        results.append({"layer": "invariant", "id": cid, "ok": ok,
                        "superseded": False,
                        "detail": f"{bv} → {av}" if ok else f"{metric} {arrow} {bv} → {av}"})
    return results


# ── INV-1: node kinds did not vanish (whole, unreleasable) ───────────────────

def _check_no_data_loss(before: Dict, after: Dict) -> Dict[str, Any]:
    """INV-1, reused from `verify.py` so there is one definition. Stays whole:
    §3.3 keeps it as 'graph nodes did not vanish', and a goal never legitimately
    reduces a node kind."""
    from . import verify as _verify
    ok, detail = _verify._check_no_data_loss(before, after)
    return {"layer": "invariant", "id": "INV-1/no_data_loss", "ok": ok,
            "superseded": False, "detail": detail}


# ── the pure core ────────────────────────────────────────────────────────────

def _inject_changed_paths(before: Dict[str, Any], after: Dict[str, Any]) -> None:
    """Compute `vault.changed_paths.count` from the per-file digest maps and
    write it into `after`, so a fingerprint waiver's bound can resolve it (§3.5).

    The per-file maps live under `_file_digests` (round-baseline only, `_`-prefixed
    so they are not namespace leaves). Absent → nothing to inject; the fingerprint
    is then guarded by plain equality alone.
    """
    b_dig = before.get("_file_digests")
    a_dig = after.get("_file_digests")
    if not isinstance(b_dig, dict) or not isinstance(a_dig, dict):
        return
    changed = _vault_state.changed_paths(b_dig, a_dig)
    vault_block = dict(after.get("vault") or {})
    vault_block["changed_paths"] = {"count": len(changed)}
    after["vault"] = vault_block


def verify_contract(contract: Dict[str, Any], before: Dict[str, Any],
                    after: Dict[str, Any]) -> Dict[str, Any]:
    """Score a contract's three layers against a (before, after) pair. Pure.

    Raises `ContractError` (a hard abort, §6) for any untrustworthy-harness
    condition; returns `{passed, intent, envelope, invariants}` otherwise. INTENT
    or ENVELOPE failing, or an un-superseded invariant clause failing, makes
    `passed` False — a FAIL the fixer may address.
    """
    _inject_changed_paths(before, after)
    scored = _contract.evaluate(contract, before, after)

    superseded = _superseded_clause_ids(contract)
    invariants = [_check_no_data_loss(before, after)]
    invariants += apply_invariants(before, after, superseded)

    passed = scored["passed"] and all(c["ok"] for c in invariants)
    return {"passed": passed, "intent": scored["intent"],
            "envelope": scored["envelope"], "invariants": invariants}


# ── the operational wrapper ──────────────────────────────────────────────────

def verify_contract_run(contract_path: Path, before_path: Path, *, repo: Path,
                        vault: Optional[Path] = None,
                        fixture_path: Optional[Path] = None,
                        captured_date: Optional[str] = None) -> Dict[str, Any]:
    """The full path: read the committed contract, check the pins, generate the
    after-state, then score. This is where git and the vault enter; the pure core
    above is what the tests hammer.
    """
    from . import baseline as _baseline
    from . import freeze as _freeze

    contract = _freeze.read_committed_contract(repo, contract_path)
    _freeze.check_pins(contract, repo=repo, contract_path=contract_path,
                       before_path=before_path, fixture_path=fixture_path)

    import json
    before = json.loads(Path(before_path).read_text(encoding="utf-8"))
    after = _baseline.generate(vault=vault,
                               captured_date=captured_date
                               or before.get("captured_date"))
    # The after-state's per-file digests, so a fingerprint waiver can be scored.
    after["_file_digests"] = _vault_state.file_digests(vault)

    report = verify_contract(contract, before, after)
    report["contract_id"] = contract.get("id")
    return report
