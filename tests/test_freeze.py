"""RFC 0009 §3.1 / §3.1.1 / §4.1 — the freeze guards.

These need a real git repo, so each test builds a throwaway one. The properties
under test are the integrity roots the delta axis rests on: the contract is read
from the committed blob (never the working tree), the round baseline's hash is
pinned, and `captured_at_head` must be EXACTLY the contract commit's first parent
— the tightening that removes the "some older ancestor" free variable §3.1.1
warns about.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from runtime.service.learnings import freeze as _f
from runtime.service.learnings.contract import ContractError


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
    # a base commit, so the contract commit has a first parent to pin to
    (repo / "seed").write_text("seed\n")
    _git(repo, "add", "seed")
    _git(repo, "commit", "-qm", "base")
    return repo


def _commit(repo: Path, relpath: str, data: dict, msg: str) -> str:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")
    _git(repo, "add", relpath)
    _git(repo, "commit", "-qm", msg)
    return _git(repo, "rev-parse", "HEAD")


def _contract(before_sha: str, head: str, *, fixture_sha=None) -> dict:
    return {"id": "G-test",
            "pins": {"before_sha256": before_sha, "captured_at_head": head,
                     "fixture_sha256": fixture_sha},
            "intent": [], "envelope": {"mode": "default-deny"}}


# ── reading the committed blob, not the working tree ─────────────────────────

def test_reads_the_committed_blob_on_a_clean_tree(tmp_path: Path):
    repo = _repo(tmp_path)
    _commit(repo, "docs/goals/g.json", {"intent": ["committed"]}, "add contract")
    got = _f.read_committed_contract(repo, repo / "docs/goals/g.json")
    assert got["intent"] == ["committed"]


def test_a_tampered_working_tree_raises_rather_than_reading_the_blob(
        tmp_path: Path):
    """A builder who commits a contract then edits the working tree does not get
    a silent blob read — the dirty check fires first. That is the stronger
    guarantee: it refuses to grade a run whose contract-on-disk no longer matches
    what was frozen, rather than quietly ignoring the edit."""
    repo = _repo(tmp_path)
    _commit(repo, "docs/goals/g.json", {"intent": ["committed"]}, "add contract")
    (repo / "docs/goals/g.json").write_text('{"intent": ["TAMPERED"]}')
    with pytest.raises(ContractError, match="uncommitted changes"):
        _f.read_committed_contract(repo, repo / "docs/goals/g.json")


def test_uncommitted_contract_raises(tmp_path: Path):
    repo = _repo(tmp_path)
    (repo / "g.json").write_text('{"intent": []}')   # never committed
    with pytest.raises(ContractError, match="not committed"):
        _f.read_committed_contract(repo, repo / "g.json")


def test_a_dirty_committed_contract_raises(tmp_path: Path):
    repo = _repo(tmp_path)
    _commit(repo, "g.json", {"intent": []}, "add")
    (repo / "g.json").write_text('{"intent": ["edited"]}')   # dirty working tree
    with pytest.raises(ContractError, match="uncommitted changes"):
        _f.contract_commit(repo, repo / "g.json")


# ── check_pins ───────────────────────────────────────────────────────────────

def test_pins_pass_when_every_root_matches(tmp_path: Path):
    repo = _repo(tmp_path)
    before = tmp_path / "before.json"
    before.write_text('{"metrics": {}}', encoding="utf-8")
    parent = _git(repo, "rev-parse", "HEAD")         # the base commit
    ct = _contract(_f.sha256_file(before), parent)
    _commit(repo, "g.json", ct, "add contract")      # its first parent == parent
    _f.check_pins(ct, repo=repo, contract_path=repo / "g.json",
                  before_path=before)                # no raise


def test_a_rewritten_round_baseline_raises(tmp_path: Path):
    repo = _repo(tmp_path)
    before = tmp_path / "before.json"
    before.write_text('{"metrics": {}}', encoding="utf-8")
    parent = _git(repo, "rev-parse", "HEAD")
    ct = _contract(_f.sha256_file(before), parent)
    _commit(repo, "g.json", ct, "add contract")
    before.write_text('{"metrics": {"tampered": 1}}', encoding="utf-8")  # rewrite
    with pytest.raises(ContractError, match="round baseline hash mismatch"):
        _f.check_pins(ct, repo=repo, contract_path=repo / "g.json",
                      before_path=before)


def test_captured_at_head_must_be_the_first_parent_not_any_ancestor(
        tmp_path: Path):
    """§3.1.1: 'some older ancestor' is exactly the free variable the tightening
    removes. A grandparent is an ancestor but not the first parent — reject it."""
    repo = _repo(tmp_path)
    grandparent = _git(repo, "rev-parse", "HEAD")
    # add one more commit so grandparent is a *strict* ancestor, not the parent
    _commit(repo, "filler", {"n": 1}, "filler")
    before = tmp_path / "before.json"
    before.write_text('{"metrics": {}}', encoding="utf-8")
    ct = _contract(_f.sha256_file(before), grandparent)   # pins the WRONG commit
    _commit(repo, "g.json", ct, "add contract")
    with pytest.raises(ContractError, match="first parent"):
        _f.check_pins(ct, repo=repo, contract_path=repo / "g.json",
                      before_path=before)


def test_a_rewritten_fixture_raises(tmp_path: Path):
    repo = _repo(tmp_path)
    before = tmp_path / "before.json"
    before.write_text('{"metrics": {}}', encoding="utf-8")
    fixture = tmp_path / "probes.json"
    fixture.write_text('{"probes": ["v1"]}', encoding="utf-8")
    parent = _git(repo, "rev-parse", "HEAD")
    ct = _contract(_f.sha256_file(before), parent,
                   fixture_sha=_f.sha256_file(fixture))
    _commit(repo, "g.json", ct, "add contract")
    fixture.write_text('{"probes": ["rewritten"]}', encoding="utf-8")
    with pytest.raises(ContractError, match="fixture hash mismatch"):
        _f.check_pins(ct, repo=repo, contract_path=repo / "g.json",
                      before_path=before, fixture_path=fixture)


def test_a_declared_fixture_that_is_absent_raises(tmp_path: Path):
    repo = _repo(tmp_path)
    before = tmp_path / "before.json"
    before.write_text('{"metrics": {}}', encoding="utf-8")
    parent = _git(repo, "rev-parse", "HEAD")
    ct = _contract(_f.sha256_file(before), parent, fixture_sha="deadbeef")
    _commit(repo, "g.json", ct, "add contract")
    with pytest.raises(ContractError, match="fixture is absent"):
        _f.check_pins(ct, repo=repo, contract_path=repo / "g.json",
                      before_path=before, fixture_path=None)


def test_missing_pins_block_raises(tmp_path: Path):
    repo = _repo(tmp_path)
    before = tmp_path / "before.json"
    before.write_text("{}", encoding="utf-8")
    with pytest.raises(ContractError, match="no `pins` block"):
        _f.check_pins({"intent": []}, repo=repo,
                      contract_path=repo / "g.json", before_path=before)
