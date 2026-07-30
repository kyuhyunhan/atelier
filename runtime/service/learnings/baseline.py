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


def degraded_engine_reason(baseline: Dict[str, Any], *,
                           embeddings_enabled: Optional[bool] = None
                           ) -> Optional[str]:
    """Why this capture must NOT be pinned as a round baseline — or None.

    A round baseline is only comparable to a later verify run under the SAME
    retrieval engine (RFC 0009 §4.2; `goal._guard_eval_engine` raises on a
    mismatch). The failure this closes: G7 captured its baseline with
    `ATELIER_EMBED=off` — the convention every test invocation here uses — so
    `eval.engine` froze as `lexical-rrf` while verify measured `hybrid`. The
    guard caught it, but only after the Implement stage had already run, and the
    run was unscorable against its own pin. Refusing at CAPTURE is cheap.

    Intent is read from the CONFIG BLOCK ONLY, deliberately ignoring the
    `ATELIER_EMBED=off` env override: that switch being set is not an excuse
    here, it is the G7 mistake itself (an agent copied the repo's test
    convention into a goal capture). A `lexical-rrf` capture is legitimate only
    for an adopter whose config disables embeddings outright — then capture and
    verify agree, because lexical is the only engine either can measure.
    """
    engine = str(baseline.get("engine") or "")
    if embeddings_enabled is None:
        try:
            from ...util import config as _config
            emb = (_config.load().raw or {}).get("embedding") or {}
            embeddings_enabled = bool(emb.get("enabled", True))
        except Exception:                    # pragma: no cover - unloadable env
            return None                      # cannot judge intent → do not block
    if not embeddings_enabled:
        return None                          # lexical by intent: consistent
    if engine == "hybrid":
        return None
    return (f"round baseline captured at engine={engine!r} while config enables "
            f"embeddings — pinning it makes the run unscorable (§4.2: verify "
            f"would measure a different engine and hard-abort). Unset "
            f"ATELIER_EMBED=off and confirm the provider answers, then recapture")


def _serialize(baseline: Dict[str, Any]) -> str:
    """Stable serialization: sorted keys + trailing newline, so regenerating an
    unchanged vault yields a byte-identical file (clean git diffs)."""
    return json.dumps(baseline, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write(path: Path, *, k: int = 5, vault: Optional[Path] = None,
          captured_date: Optional[str] = None,
          about: Optional[str] = None) -> Dict[str, Any]:
    """Generate and write the baseline to `path`; return the dict."""
    baseline = generate(k=k, vault=vault, captured_date=captured_date,
                        about=about)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_serialize(baseline), encoding="utf-8")
    return baseline
