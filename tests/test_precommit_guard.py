"""RFC 0009 G1 — the pre-commit PII guard, proven live by execution.

The G1 bar (RFC 0009 §7) is deliberately not a pattern *count*: a guard is live
only if a seeded match actually BLOCKS a commit. These tests execute the real
hook script in a hermetic scratch repo — never against the user's
`~/.atelier/pii_patterns.txt` (the `ATELIER_PII_PATTERNS` override exists for
exactly this hermeticity).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "scripts" / "git-hooks" / "pre-commit"

pytestmark = pytest.mark.skipif(shutil.which("git") is None,
                                reason="probe needs git")


def _scratch_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


def _run_hook(repo: Path, patterns: Path | None) -> subprocess.CompletedProcess:
    # Hermetic env, mirroring metrics.seeded_probe_blocked (review [MUST]):
    # scrub inherited GIT_* (a dev running tests from inside a git hook must
    # not have this stage the wrong repo) and the size knob (an exported
    # ATELIER_MAX_STAGED_BYTES would spuriously fail test_clean_stage_passes);
    # isolate HOME so the user's real ~/.atelier can never leak in.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.pop("XDG_CONFIG_HOME", None)
    env.pop("ATELIER_MAX_STAGED_BYTES", None)
    env["HOME"] = str(repo.parent)
    if patterns is not None:
        env["ATELIER_PII_PATTERNS"] = str(patterns)
    return subprocess.run(["bash", str(HOOK)], cwd=repo, env=env,
                          capture_output=True, text=True, timeout=10)


def _stage(repo: Path, name: str, body: str) -> None:
    (repo / name).write_text(body, encoding="utf-8")
    subprocess.run(["git", "add", name], cwd=repo, check=True)


def test_seeded_match_blocks_the_commit(tmp_path: Path) -> None:
    """The liveness proof: a staged addition matching a pattern exits nonzero
    and names the pattern — not a count, an actual block."""
    repo = _scratch_repo(tmp_path)
    pat = tmp_path / "patterns.txt"
    pat.write_text("# fixture\nSEEDED-PII-XYZZY\n", encoding="utf-8")
    _stage(repo, "leak.md", "an innocuous line\nwith SEEDED-PII-XYZZY inside\n")
    res = _run_hook(repo, pat)
    assert res.returncode != 0
    assert "SEEDED-PII-XYZZY" in res.stderr        # the pattern is named


def test_clean_stage_passes(tmp_path: Path) -> None:
    """The guard must not block everything: a clean staged file exits 0 under
    the same active pattern file."""
    repo = _scratch_repo(tmp_path)
    pat = tmp_path / "patterns.txt"
    pat.write_text("SEEDED-PII-XYZZY\n", encoding="utf-8")
    _stage(repo, "ok.md", "nothing sensitive here\n")
    res = _run_hook(repo, pat)
    assert res.returncode == 0, res.stderr


def test_env_override_defaults_to_home_atelier(tmp_path: Path) -> None:
    """Without ATELIER_PII_PATTERNS the guard reads $HOME/.atelier/... — the
    production default. Proven by planting the pattern there (in the isolated
    HOME) and passing no override."""
    repo = _scratch_repo(tmp_path)
    at = tmp_path / ".atelier"
    at.mkdir()
    (at / "pii_patterns.txt").write_text("SEEDED-PII-XYZZY\n", encoding="utf-8")
    _stage(repo, "leak.md", "SEEDED-PII-XYZZY\n")
    res = _run_hook(repo, patterns=None)           # no override → $HOME path
    assert res.returncode != 0


def test_structural_large_file_layer_fires_without_patterns(tmp_path: Path) -> None:
    """Layer 1 (bulk-export guard) needs no pattern file at all — a fresh
    checkout is still protected against the multi-MB dump vector."""
    repo = _scratch_repo(tmp_path)
    _stage(repo, "dump.json", "x" * (600 * 1024))  # > 512 KB default
    res = _run_hook(repo, patterns=None)           # HOME isolated → no file
    assert res.returncode != 0
    assert "exceed" in res.stderr


def test_comment_only_pattern_file_scans_nothing_but_passes_clean(
        tmp_path: Path) -> None:
    """The RFC 0008 defect shape: a comments-only file blocks nothing. The
    guard must not error on it — liveness is the METRIC's job to expose."""
    repo = _scratch_repo(tmp_path)
    pat = tmp_path / "patterns.txt"
    pat.write_text("# only\n# comments\n", encoding="utf-8")
    _stage(repo, "leak.md", "SEEDED-PII-XYZZY\n")
    res = _run_hook(repo, pat)
    assert res.returncode == 0                     # nothing active → no block
