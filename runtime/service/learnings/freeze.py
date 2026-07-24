"""RFC 0009 §3.1 / §3.1.1 / §4.1 — the freeze guards.

`contract.py` decides whether the *right* change happened. These guards decide
whether the contract and the before-picture can be *trusted* in the first place —
the integrity roots the whole delta axis rests on.

The threat is self-grading: a builder who cannot hit a bound could widen it, or
re-measure the "before" after the change and diff against itself. RFC 0006's
baseline guard was the first line here, and review established it was weaker than
it read (`verify.py` docstring):

- `atelier verify --allow-uncommitted` is a *public* flag, not a test affordance;
- `git status` proves a file *clean*, not *old*, so committing a widened bound
  defeats it.

So a contract is pinned by **content and ancestry**, not cleanliness, and every
integrity root lives *inside the contract* — the run's only git-pinned artifact
(§3.1.1). A manifest under `~/.atelier/cache/` would be as writable as the thing
it attests to.

What these guards do NOT claim: to prove stage ordering. `captured_at_head` is a
value the graded party writes, and git ancestry orders commits, not the work
behind them (§3.1.1). The orchestrator is trusted to sequence Snapshot → Contract
→ Implement; these guards detect *tampering with an artifact between stages*.
Tightening `captured_at_head` to the contract commit's first parent removes the
one free variable — "some older ancestor" the author could pick — so a mismatch
is mechanical rather than a judgement.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .contract import ContractError


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


# ── git plumbing (thin; the only impure part) ───────────────────────────────

def _git(repo: Path, *args: str) -> Tuple[int, str]:
    r = subprocess.run(["git", *args], cwd=str(repo),
                       capture_output=True, text=True)
    return r.returncode, (r.stdout or "").strip()


def _contract_relpath(repo: Path, contract_path: Path) -> str:
    try:
        return str(Path(contract_path).resolve().relative_to(repo.resolve()))
    except ValueError:
        raise ContractError(
            f"contract {contract_path} is not inside the repo {repo}")


def contract_commit(repo: Path, contract_path: Path) -> str:
    """The commit that last modified the contract file. Raises if the file is
    untracked or has never been committed — an uncommitted contract cannot be
    frozen, and `--allow-uncommitted` is not honoured here (§3.1)."""
    rel = _contract_relpath(repo, contract_path)
    code, out = _git(repo, "log", "-1", "--format=%H", "--", rel)
    if code != 0 or not out:
        raise ContractError(
            f"contract {rel} is not committed (no git history); "
            "a contract must be frozen before implementation (§3.1)")
    # A tracked file with uncommitted edits: the working tree no longer matches
    # the blob the verifier will read. Fail closed rather than grade a draft.
    code, dirty = _git(repo, "status", "--porcelain", "--", rel)
    if code == 0 and dirty:
        raise ContractError(f"contract {rel} has uncommitted changes")
    return out


def first_parent(repo: Path, commit: str) -> Optional[str]:
    code, out = _git(repo, "rev-parse", "--verify", f"{commit}^")
    return out if code == 0 and out else None


def read_committed_contract(repo: Path, contract_path: Path) -> Dict[str, Any]:
    """Read the contract from the committed blob via `git show`, NEVER the
    working tree (§3.1 step 1). The working tree is what the builder can edit;
    the blob is what was frozen."""
    rel = _contract_relpath(repo, contract_path)
    commit = contract_commit(repo, contract_path)
    code, out = _git(repo, "show", f"{commit}:{rel}")
    if code != 0:
        raise ContractError(f"cannot read committed contract {rel} at {commit}")
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise ContractError(f"committed contract {rel} is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise ContractError(f"committed contract {rel} is not an object")
    return data


# ── the guard ───────────────────────────────────────────────────────────────

def check_pins(contract: Dict[str, Any], *, repo: Path, contract_path: Path,
               before_path: Path, fixture_path: Optional[Path] = None) -> None:
    """Fail closed unless every integrity root matches. Raises `ContractError`
    on the first failure — a broken pin is a hard abort (§6), never a FAIL.

    Checks, in order:
    1. the round baseline's content hash equals `pins.before_sha256`;
    2. `pins.captured_at_head` is EXACTLY the contract commit's first parent
       (not merely some ancestor — §3.1.1);
    3. if the goal uses a fixture, its content hash equals `pins.fixture_sha256`.
    """
    pins = contract.get("pins")
    if not isinstance(pins, dict):
        raise ContractError("contract has no `pins` block (§3.1.1)")

    # 1. round baseline hash — the artifact that actually decides the delta
    want_before = pins.get("before_sha256")
    if not want_before:
        raise ContractError("pins.before_sha256 is missing (§4.1)")
    try:
        got_before = sha256_file(before_path)
    except OSError as e:
        # A missing round baseline is an untrustworthy-harness condition, not a
        # FAIL — it must surface as the typed hard-abort, never a raw
        # FileNotFoundError the CLI would report as exit 1 and the loop retry.
        raise ContractError(f"round baseline unreadable: {e}")
    if got_before != want_before:
        raise ContractError(
            f"round baseline hash mismatch: pinned {want_before[:12]}…, "
            f"measured {got_before[:12]}… — the before-picture was rewritten")

    # 2. captured_at_head == contract commit's first parent
    want_head = pins.get("captured_at_head")
    if not want_head:
        raise ContractError("pins.captured_at_head is missing (§3.1.1)")
    commit = contract_commit(repo, contract_path)
    parent = first_parent(repo, commit)
    if parent is None:
        raise ContractError(
            f"contract commit {commit[:12]}… has no parent to pin against")
    # Canonicalize the pinned value through git rather than prefix-matching a
    # raw string: `startswith` would accept `want_head="2a"`. Resolving it to a
    # full sha and comparing exactly rejects a truncated or ambiguous pin, and
    # still accepts a legitimately-abbreviated one that names the same commit.
    code, resolved = _git(repo, "rev-parse", "--verify", f"{want_head}^{{commit}}")
    if code != 0 or resolved != parent:
        raise ContractError(
            f"captured_at_head {want_head} is not the contract commit's "
            f"first parent {parent[:12]}… (§3.1.1) — snapshot did not precede "
            "the contract, or an older commit was substituted")

    # 3. fixture hash, only when the goal declares one
    want_fixture = pins.get("fixture_sha256")
    if want_fixture:
        if fixture_path is None or not Path(fixture_path).is_file():
            raise ContractError(
                "pins.fixture_sha256 is set but the fixture is absent")
        got_fixture = sha256_file(fixture_path)
        if got_fixture != want_fixture:
            raise ContractError(
                f"fixture hash mismatch: pinned {want_fixture[:12]}…, "
                f"measured {got_fixture[:12]}… — the probe was rewritten mid-run")
