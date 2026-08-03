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

import pytest

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


def test_the_env_kill_switch_is_refused(atelier_env: dict, monkeypatch) -> None:
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


def test_reads_the_live_env_when_no_override_is_passed(atelier_env: dict,
                                                      monkeypatch) -> None:
    monkeypatch.delenv("ATELIER_EMBED", raising=False)
    assert _bl.degraded_engine_reason({"engine": "lexical-rrf"}) is None


# ── CLI wiring — the surface that actually broke, twice ──────────────────────
#
# Both round-1 review MUSTs were WIRING defects, not logic ones: a refusal reason
# that never reached stderr (log console handler is TTY-gated, and the primary
# caller is a non-TTY agent subprocess), and a prescribed command that silently
# dropped `_file_digests` so a fingerprint waiver could not be scored. The pure
# `degraded_engine_reason` tests above would have missed either. These cover it.

def test_cli_refuses_before_measuring_and_reports_on_stderr(
        atelier_env: dict, tmp_path, monkeypatch, capsys) -> None:
    from runtime import cli as _cli
    calls = {"generate": 0}

    def _boom(*a, **k):                      # must NOT be reached
        calls["generate"] += 1
        raise AssertionError("generate() ran before the cheap env check")
    monkeypatch.setattr(_bl, "generate", _boom)
    monkeypatch.setenv("ATELIER_EMBED", "off")

    out = tmp_path / "before.json"
    code = _cli.main(["baseline", "--strict-engine", "--out", str(out)])
    assert code == 2
    assert calls["generate"] == 0             # refusal is cheap, not a full pass
    assert not out.exists()                   # a bad capture never becomes a pin
    assert "REFUSED" in capsys.readouterr().err


def test_cli_writes_file_digests_when_asked(atelier_env: dict, tmp_path,
                                            monkeypatch) -> None:
    """Without this key `goal._with_changed_paths` returns silently and a
    fingerprint waiver's changed_paths bound cannot be scored — the failure the
    prescribed Snapshot command used to walk into."""
    import json

    from runtime import cli as _cli
    monkeypatch.setattr(_bl, "generate",
                        lambda **k: {"engine": "hybrid", "metrics": {}})
    out = tmp_path / "before.json"
    assert _cli.main(["baseline", "--with-file-digests", "--out", str(out)]) == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(written.get("_file_digests"), dict)


def test_cli_omits_file_digests_by_default(atelier_env: dict, tmp_path,
                                           monkeypatch) -> None:
    """The committed program anchor must stay small — 7k entries belong only in a
    round baseline, so the key is opt-in."""
    import json

    from runtime import cli as _cli
    monkeypatch.setattr(_bl, "generate",
                        lambda **k: {"engine": "hybrid", "metrics": {}})
    out = tmp_path / "anchor.json"
    assert _cli.main(["baseline", "--out", str(out)]) == 0
    assert "_file_digests" not in json.loads(out.read_text(encoding="utf-8"))


def test_config_disabled_embeddings_makes_the_switch_redundant(
        atelier_env: dict, monkeypatch) -> None:
    """RFC §5.6's parenthetical, enforced: on a machine whose CONFIG disables
    embeddings, `ATELIER_EMBED=off` changes nothing — both ends measure
    lexical-rrf — so refusing would tell the operator to unset a no-op."""
    monkeypatch.setenv("ATELIER_EMBED", "off")
    monkeypatch.setattr(_bl, "_config_disables_embeddings", lambda: True)
    assert _bl.degraded_engine_reason({"engine": "lexical-rrf"}) is None


@pytest.mark.parametrize("engine", ["hybrid-degraded", "unknown"])
def test_cli_refuses_after_measuring_a_transient_degrade(
        engine: str, atelier_env: dict, tmp_path, monkeypatch, capsys) -> None:
    """The post-measurement half, through the real CLI: a provider that did not
    answer (or an unopenable projection) is only visible in the engine label, so
    this branch runs AFTER generate(). Round-1's defects were both wiring, which
    is why this path gets a CLI test and not only a unit one."""
    from runtime import cli as _cli
    monkeypatch.delenv("ATELIER_EMBED", raising=False)
    monkeypatch.setattr(_bl, "generate", lambda **k: {"engine": engine})
    out = tmp_path / "before.json"
    assert _cli.main(["baseline", "--strict-engine", "--out", str(out)]) == 2
    assert not out.exists()
    assert "REFUSED" in capsys.readouterr().err


def test_config_disabled_embeddings_read_from_a_real_config(
        atelier_env: dict) -> None:
    """Exercise the config READER itself, not a monkeypatch: round 1's near-miss
    was exactly a config-reading bug, so the real path needs pinning."""
    import yaml
    home = atelier_env["home"]
    cfg_path = home / "config.yaml"
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    raw["embedding"] = {"enabled": False}
    cfg_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert _bl._config_disables_embeddings() is True
    raw.pop("embedding")                       # absent block ⇒ gateway default True
    cfg_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert _bl._config_disables_embeddings() is False
