"""Acceptance-criteria loader for the learnings domain.

`criteria.yaml` lives inside the vault at `learnings/criteria.yaml`. The
default content is shipped in the learnings overlay
(`schema/data/learnings.overlay.yaml`) under
`acceptance_criteria_template` and is materialized on first use by the
review tool (PR-20). The criteria file is *content*, not schema — the
user iterates on it freely.

This module exposes:

- `default_template()` — string body of the seed criteria file.
- `load(vault_root)` — parse the in-vault criteria.yaml, falling back to
  the template embedded in the overlay.
- `check(candidate_frontmatter, body, accepted_index)` — run the must /
  should / forbidden lists and return a structured result.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


_OVERLAY = (Path(__file__).resolve().parents[3] / "schema" / "data"
            / "learnings.overlay.yaml")


def default_template() -> str:
    data = yaml.safe_load(_OVERLAY.read_text())
    template = data.get("acceptance_criteria_template", "")
    if not template:  # pragma: no cover
        raise RuntimeError("learnings overlay missing acceptance_criteria_template")
    return template


@dataclass
class Criterion:
    id: str
    desc: str


@dataclass
class CriteriaSet:
    version: int = 1
    must: List[Criterion] = field(default_factory=list)
    should: List[Criterion] = field(default_factory=list)
    forbidden: List[Criterion] = field(default_factory=list)


def _parse(blob: Dict[str, Any]) -> CriteriaSet:
    def _criteria(key: str) -> List[Criterion]:
        return [Criterion(id=c["id"], desc=c.get("desc", ""))
                for c in (blob.get(key) or [])]
    return CriteriaSet(
        version=int(blob.get("version", 1)),
        must=_criteria("must"),
        should=_criteria("should"),
        forbidden=_criteria("forbidden"),
    )


def load(vault_root: Path) -> CriteriaSet:
    """Parse the in-vault criteria file, falling back to the overlay
    template when the user hasn't seeded one yet."""
    in_vault = vault_root / "learnings" / "criteria.yaml"
    blob: Dict[str, Any]
    if in_vault.exists():
        blob = yaml.safe_load(in_vault.read_text()) or {}
    else:
        blob = yaml.safe_load(default_template()) or {}
    return _parse(blob)


# ── Self-check (rule-based, conservative) ─────────────────────────────────


_PII_PATTERNS = (
    re.compile(r"\b(?:[A-Z0-9._%+-]+)@(?:[A-Z0-9.-]+)\.[A-Z]{2,}\b", re.I),
    re.compile(r"\b(?:sk|pk|ghp|xoxb|xoxp|AKIA)[A-Z0-9_-]{16,}\b"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                              # SSN-like
)

# Email-shaped tokens that are NOT PII — SSH git remotes (`git@github.com`),
# noreply addresses, and similar service identifiers. Stripped before the
# email pattern runs so a repo URL doesn't trip the (non-overridable)
# pii_leak gate.
_PII_FALSE_POSITIVE_RX = re.compile(
    r"\bgit@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"          # git@github.com, git@gitlab.com
    r"|\b[A-Za-z0-9._%+-]+@users\.noreply\.github\.com\b",
)

_META_PHRASES = (
    "claude did well", "claude was helpful", "great job",
    "nice work", "looks good",
)


def _check_has_why(body: str) -> bool:
    """Body must include a non-empty 'Why this matters' (or 'Why' alone)
    section."""
    m = re.search(r"^##+\s*Why(?:\s+this\s+matters)?\b(.*?)(?=^##|\Z)",
                  body, re.S | re.M | re.I)
    if not m:
        return False
    return bool(m.group(1).strip())


def _check_is_specific(fm: Dict[str, Any], body: str) -> bool:
    # Specific = has either project_hint or session_id or working_dir,
    # AND body is more than 40 characters of substance.
    if not (fm.get("project_hint") or fm.get("session_id") or fm.get("working_dir")):
        return False
    text = re.sub(r"\s+", " ", body).strip()
    return len(text) > 40


def _check_is_actionable(body: str) -> bool:
    """Looks for a "Rule"-shaped section or an imperative bullet."""
    if re.search(r"^##+\s*Applicable rule", body, re.I | re.M):
        return True
    # bullet line starting with imperative-ish verb
    return bool(re.search(r"^[-*]\s+(when|if|don[\'’]t|prefer|use|avoid)\b",
                         body, re.I | re.M))


def _check_tied_to_event(fm: Dict[str, Any]) -> bool:
    return bool(fm.get("session_id") or fm.get("working_dir"))


def _check_has_project_tag(fm: Dict[str, Any]) -> bool:
    return bool(fm.get("project_hint"))


def _check_novel(fm: Dict[str, Any], accepted_index: List[str]) -> bool:
    """Conservative novelty: dedupe by entry_id only. A real similarity
    check is deferred; here we just refuse re-accepting an entry_id
    already in accepted_index."""
    return fm.get("entry_id") not in accepted_index


def _check_retracted(fm: Dict[str, Any]) -> bool:
    return str(fm.get("ac_status", "")).lower() == "retracted"


def _check_pii_leak(body: str) -> bool:
    # Remove known non-PII email-shaped tokens (git SSH remotes, noreply
    # addresses) before scanning, so they don't false-positive.
    scrubbed = _PII_FALSE_POSITIVE_RX.sub(" ", body)
    return any(rx.search(scrubbed) for rx in _PII_PATTERNS)


def _check_pure_meta(body: str) -> bool:
    low = body.lower()
    if any(p in low for p in _META_PHRASES) and len(re.sub(r"\W+", "", body)) < 80:
        return True
    return False


_AUTO_CHECKS = {
    "has_why":        lambda fm, body, idx: _check_has_why(body),
    "is_specific":    lambda fm, body, idx: _check_is_specific(fm, body),
    "is_actionable":  lambda fm, body, idx: _check_is_actionable(body),
    "tied_to_event":  lambda fm, body, idx: _check_tied_to_event(fm),
    "has_project_tag": lambda fm, body, idx: _check_has_project_tag(fm),
    "novel":          lambda fm, body, idx: _check_novel(fm, idx),
    "retracted":      lambda fm, body, idx: _check_retracted(fm),
    "pii_leak":       lambda fm, body, idx: _check_pii_leak(body),
    "pure_meta":      lambda fm, body, idx: _check_pure_meta(body),
}


@dataclass
class CheckResult:
    must:      Dict[str, Optional[bool]] = field(default_factory=dict)
    should:    Dict[str, Optional[bool]] = field(default_factory=dict)
    forbidden: Dict[str, Optional[bool]] = field(default_factory=dict)

    def must_pass(self) -> bool:
        # must passes if every check is explicitly True. Unknown (None)
        # blocks acceptance — the reviewer must answer it manually.
        return all(v is True for v in self.must.values())

    def forbidden_clear(self) -> bool:
        # forbidden clear if every check is explicitly False (none triggered).
        return all(v is False for v in self.forbidden.values())


def check(fm: Dict[str, Any], body: str, *,
          accepted_index: Optional[List[str]] = None,
          criteria: Optional[CriteriaSet] = None,
          vault_root: Optional[Path] = None) -> CheckResult:
    if criteria is None:
        if vault_root is None:
            raise ValueError("check() needs either `criteria` or `vault_root`")
        criteria = load(vault_root)
    idx = accepted_index or []

    result = CheckResult()
    for c in criteria.must:
        fn = _AUTO_CHECKS.get(c.id)
        result.must[c.id] = fn(fm, body, idx) if fn else None
    for c in criteria.should:
        fn = _AUTO_CHECKS.get(c.id)
        result.should[c.id] = fn(fm, body, idx) if fn else None
    for c in criteria.forbidden:
        fn = _AUTO_CHECKS.get(c.id)
        result.forbidden[c.id] = fn(fm, body, idx) if fn else None
    return result
