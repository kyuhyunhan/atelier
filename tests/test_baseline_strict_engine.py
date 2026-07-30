"""RFC 0009 §4.2 — refuse to capture a round baseline verify cannot reproduce.

The G7 run burned a whole Implement stage to learn this: its round baseline was
captured with `ATELIER_EMBED=off` (the convention every test invocation in this
repo uses), so `eval.engine` froze as `lexical-rrf` while verify measured
`hybrid`. `goal._guard_eval_engine` caught the mismatch correctly — but only at
verify, after the work was done, and the run was then unscorable against its own
pin. A round baseline verify cannot reproduce is worthless by construction, so
the refusal belongs at CAPTURE.

The discriminator is TRANSIENT vs STRUCTURAL, not "is this the best engine":
a per-invocation kill switch and an unreachable provider are not reproducible; a
machine with no semantic wiring at all measures `lexical-rrf` at both ends and is
perfectly scorable.
"""
from __future__ import annotations

from typing import Dict

from runtime.service.learnings import baseline as _bl


def test_hybrid_capture_is_trustworthy() -> None:
    assert _bl.degraded_engine_reason({"engine": "hybrid"},
                                      env_override="") is None


def test_structural_lexical_is_trustworthy() -> None:
    """A machine with no provider / no sqlite-vec / embeddings disabled in config
    measures lexical-rrf at BOTH ends — stable, so the §4.2 gate passes and
    refusing would be a false alarm on an adopter who never wanted semantic."""
    assert _bl.degraded_engine_reason({"engine": "lexical-rrf"},
                                      env_override="") is None


def test_the_env_kill_switch_is_refused(atelier_env: Dict, monkeypatch) -> None:
    """THE G7 case, and the one an earlier draft of this guard got wrong by
    reading intent through gateway.settings_from (which honours the switch), so
    the guard did not fire on the very case it exists to catch."""
    monkeypatch.setenv("ATELIER_EMBED", "off")
    reason = _bl.degraded_engine_reason({"engine": "lexical-rrf"})
    assert reason is not None and "ATELIER_EMBED=off" in reason


def test_the_kill_switch_is_refused_even_at_hybrid() -> None:
    """The switch, not the label, is the unreproducible part: refuse whatever
    engine happened to be measured under it."""
    assert _bl.degraded_engine_reason({"engine": "hybrid"},
                                      env_override="OFF") is not None


def test_degraded_hybrid_is_refused() -> None:
    """Provider unreachable at capture: semantic is wired but the canary failed,
    so scores are lexical while config asks for hybrid."""
    reason = _bl.degraded_engine_reason({"engine": "hybrid-degraded"},
                                        env_override="")
    assert reason is not None and "hybrid-degraded" in reason


def test_unknown_engine_is_refused() -> None:
    """`unknown` means `_engine_label` could not even open the projection."""
    assert _bl.degraded_engine_reason({"engine": "unknown"},
                                      env_override="") is not None


def test_reads_the_live_env_when_no_override_is_passed(atelier_env: Dict,
                                                      monkeypatch) -> None:
    monkeypatch.delenv("ATELIER_EMBED", raising=False)
    assert _bl.degraded_engine_reason({"engine": "lexical-rrf"}) is None
