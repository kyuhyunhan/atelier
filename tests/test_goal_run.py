"""RFC 0009 §8.1 — the two-sided gate, end-to-end.

This is the test the whole program is built to pass: an unchanged vault PASSes,
and a REAL delta injected into the vault and measured end-to-end FAILs. The
failing side must run through the actual census→metric→fingerprint path, not a
synthetic after-dict — a counter hard-wired to a constant passes both sides of a
dict-only test, which is exactly the vacuous PASS RFC 0009 exists to prevent.

So this exercises `verify_contract_run`: it reads a committed contract, checks the
pins against a real git repo, generates the after-state from the live (temp)
vault, and scores. The delta is a minted claim — the same write path a real run
uses.
"""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from typing import Dict

import pytest

from runtime.service import api as _api
from runtime.service.learnings import baseline as _baseline
from runtime.service.learnings import cluster as _cl
from runtime.service.learnings import freeze as _freeze
from runtime.service.learnings import goal as _goal
from runtime.service.learnings import vault_state as _vault_state


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=str(repo),
                       capture_output=True, text=True, check=True)
    return (r.stdout or "").strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "seed").write_text("seed\n")
    _git(repo, "add", "seed")
    _git(repo, "commit", "-qm", "base")
    return repo


def _write_claim(vault: Path, name: str, *, surfacing: str = "query",
                 sensitivity: str = "public", ac_status: str = "") -> None:
    eid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"claim-{name}"))
    d = vault / "graph" / "atomic"
    d.mkdir(parents=True, exist_ok=True)
    ac = f"ac_status: {ac_status}\n" if ac_status else ""
    (d / f"{name}.md").write_text(
        f"---\nschema_version: 7\nentry_id: {eid}\nkind: claim\n"
        f"domain: knowledge\nsensitivity: {sensitivity}\nsurfacing: {surfacing}\n"
        f"{ac}created_at: 2026-07-01T00:00:00+00:00\n"
        f"statement: statement of {name}\n---\n\nbody\n", encoding="utf-8")


def _freeze_round_baseline(vault: Path, tmp_path: Path) -> Path:
    """The round baseline (before.json), carrying the per-file digest map so a
    fingerprint waiver could be scored — it lives outside the repo, like the real
    one under ~/.atelier/cache."""
    _api.reindex(space="gorae", full=True)
    before = _baseline.generate(vault=vault, captured_date="2026-07-24")
    before["_file_digests"] = _vault_state.file_digests(vault)
    p = tmp_path / "before.json"
    p.write_text(json.dumps(before), encoding="utf-8")
    return p


def _commit_contract(repo: Path, contract: dict, before_path: Path) -> Path:
    """Pin and commit the contract so its first parent is the current HEAD."""
    contract["pins"] = {"before_sha256": _freeze.sha256_file(before_path),
                        "captured_at_head": _git(repo, "rev-parse", "HEAD"),
                        "fixture_sha256": None}
    rel = "docs/goals/g.json"
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(contract), encoding="utf-8")
    _git(repo, "add", rel)
    _git(repo, "commit", "-qm", "freeze contract")
    return p


# ── the two sides ─────────────────────────────────────────────────────────────

def test_unchanged_vault_passes(atelier_env: Dict, tmp_path: Path) -> None:
    vault = Path(_cl._vault_root())
    _write_claim(vault, "a")
    before_path = _freeze_round_baseline(vault, tmp_path)
    repo = _repo(tmp_path)
    contract_path = _commit_contract(
        repo, {"id": "G-noop", "intent": [], "envelope": {"mode": "default-deny"}},
        before_path)

    # nothing changed between the round baseline and now
    report = _goal.verify_contract_run(contract_path, before_path, repo=repo,
                                       vault=vault, captured_date="2026-07-24")
    assert report["passed"] is True


def test_a_real_injected_delta_fails_end_to_end(atelier_env: Dict,
                                                tmp_path: Path) -> None:
    """The load-bearing case. A minted claim moves `promote_eligible` AND the
    vault fingerprint; an empty contract declares nothing, so default-deny must
    catch both — measured through the real census/fingerprint path, not a dict."""
    vault = Path(_cl._vault_root())
    _write_claim(vault, "a")
    before_path = _freeze_round_baseline(vault, tmp_path)
    repo = _repo(tmp_path)
    contract_path = _commit_contract(
        repo, {"id": "G-noop", "intent": [], "envelope": {"mode": "default-deny"}},
        before_path)

    # inject the delta: a new eligible claim, then reindex so the projection sees it
    _write_claim(vault, "injected")
    _api.reindex(space="gorae", full=True)

    report = _goal.verify_contract_run(contract_path, before_path, repo=repo,
                                       vault=vault, captured_date="2026-07-24")
    assert report["passed"] is False
    moved = [c for c in report["envelope"] if not c["ok"]]
    metrics_moved = {c["metric"] for c in moved}
    # both the counter and the fingerprint moved, and nothing waived them
    assert "metrics.promote_eligible.total" in metrics_moved
    assert "vault.content_fingerprint" in metrics_moved


def test_a_declared_reduction_passes_end_to_end(atelier_env: Dict,
                                                tmp_path: Path) -> None:
    """The positive control: when the change IS declared (and the vault edit
    waived), the same path PASSes — so the FAIL above is the delta, not the
    harness refusing everything.

    The reduction narrows one claim out of eligibility by making it `private`.
    That is the shape a real predicate change produces at the metric layer:
    `promote_eligible` drops, but the census counters (`domain`/`ac_status`/
    `surfacing` — sensitivity is untracked) do not move, and no node vanishes so
    INV-1 is silent. The one file edited moves the fingerprint, which the waiver
    bounds by changed-path count."""
    vault = Path(_cl._vault_root())
    _write_claim(vault, "keep")
    _write_claim(vault, "doomed")
    before_path = _freeze_round_baseline(vault, tmp_path)
    repo = _repo(tmp_path)

    # both leaves of promote_eligible move (total AND by_domain.knowledge), so a
    # complete contract declares both — the RFC's own G2 example has exactly two
    # promote_eligible clauses. The envelope catching an undeclared second leaf
    # is default-deny doing its job, not a bug.
    contract = {"id": "G-narrow",
                "intent": [{"metric": "metrics.promote_eligible.total",
                            "to": {"delta": -1}},
                           {"metric": "metrics.promote_eligible.by_domain.knowledge",
                            "to": {"delta": -1}}],
                "envelope": {"mode": "default-deny",
                             "waivers": [{"release": "vault.content_fingerprint",
                                          "bound": {"metric": "vault.changed_paths.count",
                                                    "to": {"max": 3}},
                                          "reason": "one claim narrowed to private"}]}}
    contract_path = _commit_contract(repo, contract, before_path)

    # enact the declared reduction: narrow one claim out of eligibility
    doomed = vault / "graph" / "atomic" / "doomed.md"
    doomed.write_text(doomed.read_text(encoding="utf-8").replace(
        "sensitivity: public", "sensitivity: private"), encoding="utf-8")
    _api.reindex(space="gorae", full=True)

    report = _goal.verify_contract_run(contract_path, before_path, repo=repo,
                                       vault=vault, captured_date="2026-07-24")
    assert report["passed"] is True, [c for c in report["envelope"] if not c["ok"]] \
        + [c for c in report["invariants"] if not c["ok"]]
