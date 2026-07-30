"""Generate the RFC 0006 verification baseline — the frozen *before* picture an
independent verifier diffs against (RFC 0006 §5/§6).

This is the **comparison** artifact, never the rollback one: it is regenerated,
committed as `docs/rfc/0006-baseline.json`, and re-run after a change so a pillar
can prove it did not regress. It composes three read-only measurements that all
already exist:

- `eval.run()`      — P@k / R@k / MRR over the live retrieval path (RFC 0002); its
                      `engine` label records whether embeddings were on.
- `surfacing.audit` — the omission picture; we freeze the AGGREGATE
                      (`total/visible/dark_count`), not the noisy per-entry map,
                      so the determinism gate (§11.2) is implementable.
- `census.census()` — node composition, partitioned by kind.

Determinism holds *per embedding env*: the `engine` label and paraphrase scores
depend on `ATELIER_EMBED` (see RFC 0006 §11.2), so regenerate at a fixed env.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from . import census as _census
from . import eval as _eval
from . import metrics as _metrics
from . import surfacing as _surfacing
from . import vault_state as _vault_state

_ABOUT = (
    "RFC 0006 P0 foundation baseline (read-only). Produced by "
    "runtime.service.learnings.baseline.generate(). The independent verifier "
    "re-runs the after-state and diffs against this; every later phase must not "
    "regress. engine records the live retrieval mode (embeddings on/off)."
)


def generate(*, k: int = 5, vault: Optional[Path] = None,
             captured_date: Optional[str] = None,
             about: Optional[str] = None) -> Dict[str, Any]:
    """The full baseline dict (JSON-serializable). `captured_date` defaults to
    today (UTC); pass it explicitly for reproducible fixtures/tests.

    `about` names the program this baseline anchors. It is a parameter because
    there is now more than one: `0006-baseline.json` stays frozen as the evidence
    that pillars ①–④ did not regress, and RFC 0009 captures its own anchor rather
    than rewriting that record (RFC 0009 §4).

    The `metrics` block (RFC 0009 §5) is a SIBLING of `census`, never part of it.
    INV-1 (`verify._census_kind_totals`) reads `census` as a monotone no-shrink
    gate, so a counter a goal must drive DOWN would become a gate against its own
    goal if it landed there (§3.3).
    """
    captured = captured_date or datetime.now(timezone.utc).date().isoformat()
    try:
        as_of = date.fromisoformat(captured)
    except ValueError:
        # `verify_against` feeds this from an on-disk anchor's `captured_date`.
        # A hand-edited or truncated value must not abort the whole
        # verification; fall back to today and let the date itself show it.
        as_of = datetime.now(timezone.utc).date()
    ev = _eval.run(k=k, vault=vault)
    aud = _surfacing.audit(vault=vault)
    return {
        "_about": about or _ABOUT,
        "captured_date": captured,
        "engine": ev.get("engine"),          # surfaced top-level for a quick read
        "eval": ev,
        "surfacing": {
            "total": aud["total"],
            "visible": aud["visible"],
            "dark_count": aud["dark_count"],
        },
        "census": _census.census(vault=vault),
        # `as_of` is capture metadata, NOT a metric: §3.4 makes ENVELOPE
        # default-deny over the leaf keys under `metrics`, and a value that
        # changes every run by construction would trip it with no legal waiver
        # shape (§3.5 requires a numeric bound). It sits beside captured_date.
        "as_of": as_of.isoformat(),
        "metrics": _metrics.metrics(as_of=as_of, vault=vault),
        # The ENVELOPE's vault-content primitive (§5.7): one aggregate hash the
        # envelope checks for equality, and that a vault-mutating goal releases
        # through a waiver. The per-file map is a round-baseline artifact, not
        # committed here — 7k entries would bloat the frozen anchor.
        "vault": _vault_state.vault_block(vault),
    }


def _config_disables_embeddings() -> bool:
    """True when the config BLOCK itself turns embeddings off (not the env
    switch). Such a machine is structurally lexical at both ends, so the
    `ATELIER_EMBED=off` check is provably redundant there — refusing would tell
    the operator to unset something that changes nothing for them."""
    try:
        from ...util import config as _config
        emb = (_config.load().raw or {}).get("embedding") or {}
        return not bool(emb.get("enabled", True))
    except Exception:                        # pragma: no cover - unloadable env
        return False                         # cannot prove redundancy → check


def engine_capture_precheck() -> Optional[str]:
    """The part of the round-baseline gate knowable BEFORE measuring — today,
    the env kill switch. Callers run this first so a refusal costs nothing: a
    full `generate()` is an eval + surfacing + census + metrics pass (minutes on
    a real vault), and paying it to report an env var is pure waste. Returns the
    same reason string `degraded_engine_reason` would, or None."""
    return degraded_engine_reason({})      # no engine known yet, by design


def degraded_engine_reason(baseline: Dict[str, Any], *,
                           env_override: Optional[str] = None
                           ) -> Optional[str]:
    """Why this capture must NOT be pinned as a round baseline — or None.

    A round baseline is comparable to a later verify run only under the SAME
    retrieval engine (RFC 0009 §4.2; `goal._guard_eval_engine` raises on a
    mismatch). The failure this closes: G7 captured its baseline with
    `ATELIER_EMBED=off` — the convention every test invocation here uses — so
    `eval.engine` froze as `lexical-rrf` while verify measured `hybrid`. The
    guard caught it, but only after the Implement stage had run, and the run was
    unscorable against its own pin. Refusing at CAPTURE is cheap.

    The question is not "is this engine the best one?" but **"will verify
    reproduce this engine?"** — so the discriminator is whether the degrade is
    TRANSIENT or STRUCTURAL:

    - `ATELIER_EMBED=off` in this invocation's env → refuse. A per-invocation
      kill switch is not reproducible: verify runs without it. This is the G7
      case, and an earlier draft of this guard excused it by reading intent
      through `gateway.settings_from` (which honours the switch) — so the guard
      did not fire on the very case it exists to catch.
    - `hybrid-degraded` / `unknown` → refuse. The provider was unreachable, or
      the projection would not open; scores are lexical while the config asks
      for hybrid, and a later run with a healthy provider will not match.
    - `hybrid`, or `lexical-rrf` with no env override → accept. The latter is a
      machine with no semantic wiring at all (no provider, no sqlite-vec, or
      config disables embeddings): stable, so verify measures it too and the
      §4.2 gate passes. Refusing it would be a false alarm on an adopter who
      never asked for semantic retrieval.
    """
    import os as _os
    override = (env_override if env_override is not None
                else _os.environ.get("ATELIER_EMBED", ""))
    engine = str(baseline.get("engine") or "")
    if str(override).lower() == "off" and not _config_disables_embeddings():
        # Deliberately does NOT name the engine: this branch is also reached
        # from `engine_capture_precheck`, before any measurement exists, and a
        # placeholder label in an operator-facing message is worse than none.
        return ("ATELIER_EMBED=off was set for this capture — a per-invocation "
                "kill switch is not reproducible at verify, which runs without "
                "it, so the pin would make the run unscorable (§4.2). That "
                "switch is this repo's convention for TEST runs; unset it and "
                "recapture")
    if engine in ("hybrid-degraded", "unknown"):
        return (f"engine={engine!r} — the embedding provider did not answer (or "
                "the projection would not open), so these scores are lexical "
                "while config asks for hybrid. A later verify with a healthy "
                "provider will not match this pin (§4.2). Confirm the provider "
                "answers, then recapture")
    return None


def serialize(baseline: Dict[str, Any]) -> str:
    """Stable serialization: sorted keys + trailing newline, so regenerating an
    unchanged vault yields a byte-identical file (clean git diffs)."""
    return json.dumps(baseline, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


_serialize = serialize          # back-compat alias for existing callers


def write(path: Path, *, k: int = 5, vault: Optional[Path] = None,
          captured_date: Optional[str] = None,
          about: Optional[str] = None) -> Dict[str, Any]:
    """Generate and write the baseline to `path`; return the dict."""
    baseline = generate(k=k, vault=vault, captured_date=captured_date,
                        about=about)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_serialize(baseline), encoding="utf-8")
    return baseline
