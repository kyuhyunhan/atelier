"""PR-18: criteria.yaml parsing + self-check for learnings candidates."""
from __future__ import annotations

from pathlib import Path
from typing import Dict

import pytest
import yaml

from runtime.service.learnings import criteria as _crit


def test_default_template_parses() -> None:
    template = _crit.default_template()
    blob = yaml.safe_load(template)
    assert blob["version"] == 1
    assert any(c["id"] == "has_why" for c in blob["must"])


def test_load_falls_back_to_template(tmp_path: Path) -> None:
    cs = _crit.load(tmp_path)  # no learnings/criteria.yaml present
    must_ids = {c.id for c in cs.must}
    assert {"has_why", "is_specific", "is_actionable"} <= must_ids


def test_load_uses_in_vault_when_present(tmp_path: Path) -> None:
    (tmp_path / "learnings").mkdir()
    (tmp_path / "learnings" / "criteria.yaml").write_text(yaml.safe_dump({
        "version": 2,
        "must": [{"id": "has_why", "desc": "x"}],
        "should": [],
        "forbidden": [],
    }))
    cs = _crit.load(tmp_path)
    assert cs.version == 2
    assert len(cs.must) == 1


# ── self-check (auto-evaluable rules) ─────────────────────────────────────


_GOOD_BODY = (
    "## Observation\n"
    "Calling `_h_search` without a fallback returned [] for tilde queries.\n\n"
    "## Why this matters\n"
    "fts5 ignores tilde tokens; UI returned 'no results' silently.\n\n"
    "## Applicable rule\n"
    "- When user query contains punctuation, enable fallback search.\n"
)


def _sample_fm() -> Dict:
    return {
        "schema_version": 4,
        "entry_id": "11111111-1111-5111-8111-111111111111",
        "captured_at": "2026-05-28T13:00:00+09:00",
        "agent_kind": "claude-code",
        "hook": "Stop",
        "status": "candidate",
        "ac_status": "pending",
        "observation_kind": "feedback",
        "session_id": "abc",
        "working_dir": "/Users/me/workspaces/lexio",
        "project_hint": "lexio",
    }


def test_good_candidate_passes_must(tmp_path: Path) -> None:
    res = _crit.check(_sample_fm(), _GOOD_BODY, accepted_index=[],
                      vault_root=tmp_path)
    assert res.must_pass()
    assert res.forbidden_clear()


def test_missing_why_section_fails_must(tmp_path: Path) -> None:
    no_why = _GOOD_BODY.replace("## Why this matters\n"
                                "fts5 ignores tilde tokens; "
                                "UI returned 'no results' silently.\n",
                                "## Why this matters\n\n")
    res = _crit.check(_sample_fm(), no_why, accepted_index=[],
                      vault_root=tmp_path)
    assert not res.must_pass()


def test_pii_leak_triggers_forbidden(tmp_path: Path) -> None:
    body = _GOOD_BODY + "\nContact: foo@example.com for keys.\n"
    res = _crit.check(_sample_fm(), body, accepted_index=[],
                      vault_root=tmp_path)
    assert not res.forbidden_clear()


def test_duplicate_entry_id_rejected_as_not_novel(tmp_path: Path) -> None:
    fm = _sample_fm()
    res = _crit.check(fm, _GOOD_BODY,
                      accepted_index=[fm["entry_id"]],
                      vault_root=tmp_path)
    # novel is a `should`, not a `must` — but its result must be False.
    assert res.should.get("novel") is False


def test_pii_check_ignores_git_ssh_and_noreply() -> None:
    from runtime.service.learnings.criteria import _check_pii_leak
    assert _check_pii_leak("Repo: git@github.com:kyuhyunhan/tas.git") is False
    assert _check_pii_leak("gorae@users.noreply.github.com") is False
    assert _check_pii_leak("email me at admin@example.com") is True
