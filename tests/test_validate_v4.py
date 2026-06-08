"""PR-8: schema v4 frontmatter validator."""
from __future__ import annotations

import asyncio
import uuid as _uuid
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

from runtime.lint import validate_v4
from runtime.service import api as _api


def _write(path: Path, fm: Dict, body: str = "body\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    path.write_text(f"---\n{serialized}\n---\n{body}", encoding="utf-8")


def _uid() -> str:
    return str(_uuid.uuid4())


def test_valid_learning_candidate_passes(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    fm = {
        "schema_version": 4,
        "entry_id": _uid(),
        "captured_at": "2026-05-28T13:00:00+09:00",
        "agent_kind": "claude-code",
        "hook": "Stop",
        "status": "candidate",
        "ac_status": "pending",
        "observation_kind": "feedback",
    }
    p = vault / "learnings" / "candidates" / "2026-05-28" / "1300-x.md"
    _write(p, fm)
    findings = validate_v4.validate_paths([p], vault_root=vault)
    assert findings == []


def test_v5_accepted_with_facets_and_no_topic_passes(atelier_env: Dict) -> None:
    """RFC 0001 / P1: an accepted learning may be schema_version 5, carry
    `aspect[]` + typed `links`, and omit `target_topic` (now optional)."""
    vault = atelier_env["gorae"]
    fm = {
        "schema_version": 5,
        "entry_id": _uid(),
        "captured_at": "2026-05-28T13:00:00+09:00",
        "accepted_at": "2026-05-29T13:00:00+09:00",
        "agent_kind": "claude-code",
        "status": "accepted",
        "ac_status": "passed",
        "observation_kind": "project",
        "target_project": "lexio",
        "aspect": ["client", "cross-cutting"],          # many-valued, free-form
        "links": [{"to": "20260513T1700", "why": "extends the policy"}],
        # NOTE: no target_topic — legal under v5.
    }
    # Must live under notes/ so the learning_accepted overlay actually matches
    # and its field_specs are exercised (not the path-unmatched minimal check).
    p = vault / "learnings" / "notes" / "2026-05" / "n.md"
    _write(p, fm)
    findings = validate_v4.validate_paths([p], vault_root=vault)
    assert findings == [], [f.message for f in findings]


def test_v4_accepted_in_notes_still_valid(atelier_env: Dict) -> None:
    """Backward-compat: a v4 accepted record (with target_topic) in the flat
    notes/ store remains valid (schema_version accepts {4, 5})."""
    vault = atelier_env["gorae"]
    fm = {
        "schema_version": 4,
        "entry_id": _uid(),
        "captured_at": "2026-05-28T13:00:00+09:00",
        "accepted_at": "2026-05-29T13:00:00+09:00",
        "agent_kind": "claude-code",
        "status": "accepted",
        "ac_status": "passed",
        "observation_kind": "project",
        "target_topic": "rendering",
    }
    p = vault / "learnings" / "notes" / "2026-05" / "n.md"
    _write(p, fm)
    findings = validate_v4.validate_paths([p], vault_root=vault)
    assert findings == [], [f.message for f in findings]


def test_accepted_missing_required_field_fails(atelier_env: Dict) -> None:
    """Proves the learning_accepted overlay actually matches notes/ paths: a
    missing required field (accepted_at) must FAIL validation, not slip through
    the path-unmatched minimal check."""
    vault = atelier_env["gorae"]
    fm = {
        "schema_version": 5,
        "entry_id": _uid(),
        "captured_at": "2026-05-28T13:00:00+09:00",
        # accepted_at intentionally omitted
        "agent_kind": "claude-code",
        "status": "accepted",
        "ac_status": "passed",
        "observation_kind": "project",
    }
    p = vault / "learnings" / "notes" / "2026-05" / "bad.md"
    _write(p, fm)
    findings = validate_v4.validate_paths([p], vault_root=vault)
    msgs = " ".join(f.message for f in findings)
    assert "accepted_at" in msgs, msgs


def test_missing_required_field_fails(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    fm = {
        "schema_version": 4,
        "entry_id": _uid(),
        "status": "candidate",
        # missing captured_at, agent_kind, hook, ac_status, observation_kind
    }
    p = vault / "learnings" / "candidates" / "2026-05-28" / "1301-bad.md"
    _write(p, fm)
    findings = validate_v4.validate_paths([p], vault_root=vault)
    msgs = " ".join(f.message for f in findings)
    assert "missing required field: captured_at" in msgs
    assert "missing required field: agent_kind" in msgs


def test_wrong_schema_version_fails(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    fm = {"schema_version": 3, "entry_id": _uid()}
    p = vault / "raw" / "old.md"
    _write(p, fm)
    findings = validate_v4.validate_paths([p], vault_root=vault)
    assert any("schema_version" in f.message for f in findings)


def test_const_mismatch_fails(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    fm = {
        "schema_version": 4,
        "entry_id": _uid(),
        "captured_at": "2026-05-28T13:00:00+09:00",
        "agent_kind": "claude-code",
        "hook": "Stop",
        "status": "WRONG",          # const: candidate
        "ac_status": "pending",
        "observation_kind": "feedback",
    }
    p = vault / "learnings" / "candidates" / "2026-05-28" / "1302-cs.md"
    _write(p, fm)
    findings = validate_v4.validate_paths([p], vault_root=vault)
    assert any("status" in f.message and "must equal" in f.message
               for f in findings)


def test_duplicate_entry_id_corpus_check(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    eid = _uid()
    common = {
        "schema_version": 4,
        "entry_id": eid,
        "captured_at": "2026-05-28T13:00:00+09:00",
        "agent_kind": "claude-code",
        "hook": "Stop",
        "status": "candidate",
        "ac_status": "pending",
        "observation_kind": "feedback",
    }
    p1 = vault / "learnings" / "candidates" / "2026-05-28" / "1303-a.md"
    p2 = vault / "learnings" / "candidates" / "2026-05-28" / "1304-b.md"
    _write(p1, common)
    _write(p2, common)
    findings = validate_v4.validate_paths([p1, p2], vault_root=vault)
    assert any(f.rule_id == "V1" for f in findings)


def test_api_validate_returns_summary(atelier_env: Dict) -> None:
    vault = atelier_env["gorae"]
    fm = {"schema_version": 3, "entry_id": _uid()}
    _write(vault / "raw" / "stale.md", fm)
    out = _api.validate(role="librarian-territory")
    assert out["failed"] is True
    assert out["scanned"] >= 1


def test_mcp_dispatch_validate(atelier_env: Dict) -> None:
    from runtime.service import tools as _tools
    vault = atelier_env["gorae"]
    fm = {
        "schema_version": 4,
        "entry_id": _uid(),
        "captured_at": "2026-05-28T13:00:00+09:00",
        "agent_kind": "claude-code",
        "hook": "Stop",
        "status": "candidate",
        "ac_status": "pending",
        "observation_kind": "feedback",
    }
    _write(vault / "learnings" / "candidates" / "2026-05-28" / "1305-x.md", fm)
    async def go() -> Dict:
        return await _tools.invoke("atelier_validate")
    out = asyncio.run(go())
    assert out["failed"] is False
