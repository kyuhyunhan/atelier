"""RFC 0009 §4.2 — refuse to capture a round baseline under a degraded engine.

The G7 run burned a whole Implement stage to learn this: its round baseline was
captured with `ATELIER_EMBED=off` (the convention every test invocation in this
repo uses), so `eval.engine` froze as `lexical-rrf` while verify measured
`hybrid`. `goal._guard_eval_engine` caught the mismatch correctly — but only at
verify, after the work was done, and the run was then unscorable against its own
pin. A round baseline captured degraded is worthless by construction, so the
refusal belongs at CAPTURE.

`degraded_engine_reason(bl)` names the divergence between what config ASKS for
and what the run measured; `None` means the capture is trustworthy.
"""
from __future__ import annotations

from typing import Dict

from runtime.service.learnings import baseline as _bl


def test_hybrid_capture_is_trustworthy() -> None:
    assert _bl.degraded_engine_reason({"engine": "hybrid"},
                                      embeddings_enabled=True) is None


def test_lexical_capture_is_trustworthy_when_config_disables_embeddings() -> None:
    """CI and test runs legitimately measure lexical-rrf — embeddings are OFF by
    intent there, so the baseline is internally consistent and must not be
    refused (this is what keeps `ATELIER_EMBED=off` a usable kill switch)."""
    assert _bl.degraded_engine_reason({"engine": "lexical-rrf"},
                                      embeddings_enabled=False) is None


def test_lexical_capture_is_refused_when_config_wants_embeddings() -> None:
    """The G7 defect: config enables embeddings, but the capture measured
    lexical-only — the env kill switch was set, or the sidecar/gateway did not
    wire. Either way the baseline pins a mode verify will not reproduce."""
    reason = _bl.degraded_engine_reason({"engine": "lexical-rrf"},
                                        embeddings_enabled=True)
    assert reason is not None
    assert "lexical-rrf" in reason and "ATELIER_EMBED" in reason


def test_degraded_hybrid_is_refused() -> None:
    """Provider unreachable at capture: semantic is wired but the canary failed.
    Scores would be lexical while the label admits the degrade."""
    reason = _bl.degraded_engine_reason({"engine": "hybrid-degraded"},
                                        embeddings_enabled=True)
    assert reason is not None and "hybrid-degraded" in reason


def test_unknown_engine_is_refused() -> None:
    """`unknown` means `_engine_label` could not even open the projection."""
    assert _bl.degraded_engine_reason({"engine": "unknown"},
                                      embeddings_enabled=True) is not None


def test_the_env_kill_switch_is_not_an_excuse(atelier_env: Dict,
                                              monkeypatch) -> None:
    """THE G7 case, and the one an earlier draft of this guard got wrong:
    `ATELIER_EMBED=off` set at capture must still be REFUSED. That switch being
    on is not a statement of intent for a round baseline — it is the mistake
    (an agent copied this repo's test convention into a goal capture). Intent
    comes from the config block only, which by default enables embeddings."""
    monkeypatch.setenv("ATELIER_EMBED", "off")
    reason = _bl.degraded_engine_reason({"engine": "lexical-rrf"})
    assert reason is not None and "ATELIER_EMBED" in reason
