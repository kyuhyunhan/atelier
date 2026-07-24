"""RFC 0009 §6 — the `atelier goal-verify` CLI: exit 0 PASS / 1 FAIL / 2 abort.

The workflow's verify stage branches on these three exit codes, so they are the
contract with the harness, not a cosmetic. A hard abort (a broken pin, an unknown
metric key) must be code 2 — distinct from a FAIL — so the loop never retries an
untrustworthy run as if it were a missed target.
"""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from typing import Dict

from runtime import cli as _cli
from runtime.service import api as _api
from runtime.service.learnings import baseline as _baseline
from runtime.service.learnings import cluster as _cl
from runtime.service.learnings import freeze as _freeze
from runtime.service.learnings import vault_state as _vault_state


def _git(repo: Path, *a: str) -> str:
    return subprocess.run(["git", *a], cwd=str(repo), capture_output=True,
                          text=True, check=True).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"; r.mkdir()
    _git(r, "init", "-q"); _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "seed").write_text("s\n"); _git(r, "add", "seed"); _git(r, "commit", "-qm", "base")
    return r


def _claim(vault: Path, name: str) -> None:
    eid = str(uuid.uuid5(uuid.NAMESPACE_DNS, name))
    d = vault / "graph" / "atomic"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(
        f"---\nschema_version: 7\nentry_id: {eid}\nkind: claim\ndomain: knowledge\n"
        f"sensitivity: public\nsurfacing: query\ncreated_at: 2026-07-01T00:00:00+00:00\n"
        f"statement: s {name}\n---\n\nbody\n", encoding="utf-8")


def _setup(vault: Path, tmp_path: Path, intent):
    _claim(vault, "a")
    _api.reindex(space="gorae", full=True)
    before = _baseline.generate(vault=vault, captured_date="2026-07-24")
    before["_file_digests"] = _vault_state.file_digests(vault)
    bp = tmp_path / "before.json"; bp.write_text(json.dumps(before), encoding="utf-8")
    repo = _repo(tmp_path)
    contract = {"id": "G", "intent": intent, "envelope": {"mode": "default-deny"},
                "pins": {"before_sha256": _freeze.sha256_file(bp),
                         "captured_at_head": _git(repo, "rev-parse", "HEAD"),
                         "fixture_sha256": None}}
    rel = "docs/goals/G.json"; (repo / "docs/goals").mkdir(parents=True)
    (repo / rel).write_text(json.dumps(contract), encoding="utf-8")
    _git(repo, "add", rel); _git(repo, "commit", "-qm", "freeze")
    return repo, bp


def _run(repo: Path, bp: Path, vault: Path) -> int:
    return _cli.main(["goal-verify", "--contract", str(repo / "docs/goals/G.json"),
                      "--before", str(bp), "--repo", str(repo), "--vault", str(vault)])


def test_exit_0_on_pass(atelier_env: Dict, tmp_path: Path) -> None:
    vault = Path(_cl._vault_root())
    repo, bp = _setup(vault, tmp_path, intent=[])          # no-op contract
    assert _run(repo, bp, vault) == 0


def test_exit_1_on_fail(atelier_env: Dict, tmp_path: Path) -> None:
    vault = Path(_cl._vault_root())
    repo, bp = _setup(vault, tmp_path, intent=[])
    _claim(vault, "injected")                              # undeclared delta
    _api.reindex(space="gorae", full=True)
    assert _run(repo, bp, vault) == 1


def test_exit_2_on_hard_abort(atelier_env: Dict, tmp_path: Path) -> None:
    """A contract naming a metric no counter emits is a broken harness — code 2,
    not 1, so the loop does not retry it as a missed target."""
    vault = Path(_cl._vault_root())
    repo, bp = _setup(vault, tmp_path,
                      intent=[{"metric": "metrics.NONEXISTENT", "to": {"eq": 0}}])
    assert _run(repo, bp, vault) == 2
