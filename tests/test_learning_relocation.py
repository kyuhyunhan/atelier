"""RFC 0003 P6 — relocate learnings/ → provenance/learning/.

Phase E1 is dual-path: the engine must classify and resolve BOTH the legacy
top-level `learnings/` tree and the new `provenance/learning/` tree, so the
vault `git mv` (V1) flips reads+writes atomically with no dangling. These tests
pin that equivalence BEFORE the vault moves.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from runtime.index.classify import classify
from runtime.service.learnings import store


# (subpath, expected page_type) — one per learnings overlay rule that has a
# working glob. NOTE: `archived/<flat>.md` is intentionally absent: the overlay
# pattern `learnings/archived/**/*.md` requires an intermediate dir, so flat
# archived files match neither form and fall to `unknown` (pre-existing bug,
# tracked separately — not introduced by P6). Equivalence for it is covered by
# `test_both_path_forms_are_equivalent` below.
_CASES = [
    ("candidates/2026-06/x.md", "learning_candidate"),
    ("notes/2026-06/x.md", "learning_accepted"),
    ("principles/commit-discipline.md", "learning_principle"),
    ("principles/INDEX.md", "learnings_index"),
    ("log.md", "learnings_log"),
]

# Every subpath that should classify the SAME under both prefixes, regardless of
# what that type is — the actual E1 contract (the rename must not change type).
_EQUIV_SUBS = [s for s, _ in _CASES] + ["archived/flat.md", "archived/2026/x.md"]


@pytest.mark.parametrize("sub,ptype", _CASES)
def test_both_path_forms_classify_to_expected_type(sub: str, ptype: str) -> None:
    """Every working learnings overlay rule matches the new provenance/learning/
    prefix exactly as the legacy learnings/ prefix — including the
    INDEX-before-glob specificity ordering, and outranking the generic
    `provenance/**/*.md` raw_source catch-all."""
    assert classify("gorae", f"learnings/{sub}", {}) == ptype
    assert classify("gorae", f"provenance/learning/{sub}", {}) == ptype


@pytest.mark.parametrize("sub", _EQUIV_SUBS)
def test_both_path_forms_are_equivalent(sub: str) -> None:
    """The relocation must be type-preserving: legacy and relocated forms
    classify identically, even where the resulting type is `unknown`."""
    assert classify("gorae", f"learnings/{sub}", {}) == \
        classify("gorae", f"provenance/learning/{sub}", {})


def test_learning_root_resolves_to_live_tree(tmp_path: Path) -> None:
    """learning_root resolves to whichever tree is live, so a single git mv
    flips the engine. Transition default is the LEGACY tree (E1 is a pure no-op);
    it switches to provenance/learning/ only once that tree exists on disk."""
    vault = tmp_path

    # neither exists → legacy default (E1 no-op: fresh vaults stay on learnings/)
    assert store.learning_root(vault) == vault / "learnings"

    # only legacy exists → legacy (the current gorae state)
    (vault / "learnings").mkdir()
    assert store.learning_root(vault) == vault / "learnings"

    # new exists → new wins, even if legacy lingers mid-migration
    (vault / "provenance" / "learning").mkdir(parents=True)
    assert store.learning_root(vault) == vault / "provenance" / "learning"


def test_notes_root_derives_from_learning_root(tmp_path: Path) -> None:
    """The flat-store path is defined relative to learning_root, not a second
    hard-coded literal."""
    (tmp_path / "provenance" / "learning").mkdir(parents=True)
    assert store.notes_root(tmp_path) == tmp_path / "provenance" / "learning" / "notes"
