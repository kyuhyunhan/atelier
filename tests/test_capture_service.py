"""RFC 0005 P5.2 — generic capture lands in the `inbox` intake domain with an
explicit `domain` FIELD, not decreed personal-by-channel.

These cover runtime/service/capture.py (the manual/mobile inbox capture), which
is distinct from the operational learning capture in
runtime/service/learnings/capture.py.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

from runtime.index.parse import split_frontmatter
from runtime.service import capture as _cap


def _read_fm(path: Path) -> dict:
    fm, _body = split_frontmatter(path.read_text(encoding="utf-8"))
    return fm


def test_capture_lands_in_inbox_domain_not_personal(atelier_env: Dict) -> None:
    path = _cap.capture(text="a stray thought", source="web-clipper")
    assert path.exists()
    # First-class `inbox` intake sibling of personal/knowledge (RFC 0005 §3) —
    # NOT raw/personal/inbox. A capture is never personal-by-channel.
    assert "raw/inbox/" in str(path)
    assert "raw/personal/" not in str(path)


def test_capture_carries_default_domain_field(atelier_env: Dict) -> None:
    path = _cap.capture(text="undetermined note")
    fm = _read_fm(path)
    # Classification is a frontmatter field, defaulting to inbox/undetermined.
    assert fm["domain"] == "inbox/undetermined"


def test_capture_honors_explicit_domain_field(atelier_env: Dict) -> None:
    path = _cap.capture(text="a knowledge clip", domain="knowledge")
    fm = _read_fm(path)
    assert fm["domain"] == "knowledge"
    # The classifying field does NOT change the cosmetic landing dir.
    assert "raw/inbox/" in str(path)


def test_capture_default_sensitivity_and_status(atelier_env: Dict) -> None:
    path = _cap.capture(text="note")
    fm = _read_fm(path)
    assert fm["sensitivity"] == "private"
    assert fm["inbox_status"] == "pending"


def test_capture_honors_explicit_sensitivity(atelier_env: Dict) -> None:
    path = _cap.capture(text="public note", sensitivity="shareable")
    fm = _read_fm(path)
    assert fm["sensitivity"] == "shareable"


def test_capture_text_api_threads_domain(atelier_env: Dict) -> None:
    from runtime.service import api as _api
    out = _api.capture_text("via api", source="manual", domain="workshop")
    fm = _read_fm(Path(out["path"]))
    assert fm["domain"] == "workshop"
    assert "raw/inbox/" in out["path"]
