"""RFC 0002 P0 — the retrieval eval harness.

Two probe sets, two questions:
  - self-probe (single gold)   → Recall@k / MRR : "is this learning retrievable?"
  - concept-grouped (multi gold) → P@k / R@k     : "do the best k surface?"
The metric math is pure and tested here against hand-made answer keys; the
probe-set + run() integration is tested over the vault fixture.
"""
from __future__ import annotations

from typing import Dict

from runtime.service.learnings import eval as _eval
from tests.conftest import write_page


# ── pure metric math ────────────────────────────────────────────────────────

def test_precision_at_k_counts_correct_over_k():
    # 3 of the top-5 are gold → 3/5
    ranked = ["L2", "X", "L1", "Y", "L4"]
    gold = {"L1", "L2", "L3", "L4"}
    assert _eval.precision_at_k(ranked, gold, 5) == 0.6


def test_recall_at_k_counts_found_over_total_gold():
    # found 3 of the 4 gold docs (missed L3) → 3/4
    ranked = ["L2", "X", "L1", "Y", "L4"]
    gold = {"L1", "L2", "L3", "L4"}
    assert _eval.recall_at_k(ranked, gold, 5) == 0.75


def test_recall_at_k_truncates_at_k():
    # L4 sits at position 6, beyond k=5 → not counted
    ranked = ["L1", "a", "b", "c", "d", "L4"]
    gold = {"L1", "L4"}
    assert _eval.recall_at_k(ranked, gold, 5) == 0.5


def test_reciprocal_rank_is_inverse_of_first_gold_position():
    assert _eval.reciprocal_rank(["x", "L1", "y"], {"L1"}) == 0.5    # rank 2
    assert _eval.reciprocal_rank(["L1", "y"], {"L1"}) == 1.0         # rank 1


def test_reciprocal_rank_zero_when_absent():
    assert _eval.reciprocal_rank(["x", "y"], {"L1"}) == 0.0


def test_empty_gold_or_k_is_zero_not_crash():
    assert _eval.precision_at_k(["a"], set(), 5) == 0.0
    assert _eval.precision_at_k(["a"], {"a"}, 0) == 0.0
    assert _eval.recall_at_k(["a"], set(), 5) == 0.0


# ── probe-set construction + run() over the vault ───────────────────────────

_BASE = {
    "schema_version": 4, "agent_kind": "claude-code", "status": "accepted",
    "ac_status": "passed", "observation_kind": "feedback",
    "captured_at": "2026-01-01T00:00:00Z", "accepted_at": "2026-01-02T00:00:00Z",
}


def _accepted(vault, entry_id, body, *, topic=None, touches=None):
    fm = {**_BASE, "entry_id": entry_id}
    if topic:
        fm["target_topic"] = topic
    if touches:
        fm["touches"] = touches
    write_page(vault / "learnings" / "notes" / "2026-01" / f"{entry_id}.md", fm, body)


def test_concept_probes_group_only_shared_concepts(vault_env: Dict):
    vault = vault_env["vault"]
    # two learnings share `caching`; one solo `logging`
    _accepted(vault, "c1", "the cache body words\n", touches=["caching"])
    _accepted(vault, "c2", "another cache body\n", touches=["caching"])
    _accepted(vault, "s1", "solo body\n", touches=["logging"])
    from runtime.service import api
    api.reindex(full=True)

    probes = _eval.concept_probes(vault)
    by_concept = {p["concept"]: p for p in probes}
    assert "caching" in by_concept
    assert set(by_concept["caching"]["gold"]) == {"c1", "c2"}
    # a concept with only one learning is NOT a multi-gold probe (it is the
    # self-probe's job) — it must be excluded.
    assert "logging" not in by_concept


def test_paraphrase_block_scores_against_fixture(vault_env: Dict, tmp_path):
    """The paraphrase set measures meaning-match without word-match: a probe
    whose gold exists is scored; a probe whose gold has been retracted is
    flagged stale, never silently dropped."""
    vault = vault_env["vault"]
    _accepted(vault, "g1", "## Observation\n\ncache eviction policy notes\n",
              touches=["caching"])
    from runtime.service import api
    api.reindex(full=True)

    import json
    fixture = tmp_path / "probes.json"
    fixture.write_text(json.dumps({"version": 1, "probes": [
        # lexical hit possible: query shares the word 'eviction' with the body
        {"query": "eviction strategy", "gold": ["g1"]},
        # stale: gold id no longer exists in the vault
        {"query": "anything", "gold": ["gone-id"]},
    ]}))

    block = _eval.paraphrase_block(vault, k=5, fixture_path=fixture)
    assert block["probes"] == 2
    assert block["scored"] == 1                 # stale probe excluded from scoring
    assert block["stale"] == [{"query": "anything", "gold": ["gone-id"]}]
    assert block["recall_at_k"] == 1.0          # the lexical-findable one
    assert "mrr" in block


def test_gate_fails_on_newly_dark_passes_otherwise():
    before = {"x": {"visible": True, "rank": 0, "title": "X", "project": "p",
                    "probe": "x"}}
    dark = {"x": {"visible": False, "rank": None, "title": "X", "project": "p",
                  "probe": "x"}}
    assert _eval.gate(before, dark)["passed"] is False
    assert _eval.gate(before, before)["passed"] is True


def test_run_reports_both_probe_sets(vault_env: Dict):
    vault = vault_env["vault"]
    _accepted(vault, "c1", "the cache eviction body words\n", touches=["caching"])
    _accepted(vault, "c2", "cache warming body words\n", touches=["caching"])
    from runtime.service import api
    api.reindex(full=True)

    report = _eval.run(k=5, vault=vault)
    assert report["k"] == 5
    # CI runs ATELIER_EMBED=off → no semantic mode → RRF over lexical alone.
    assert report["engine"] == "lexical-rrf"
    # self-probe block: known-item metrics + the omission gate
    sp = report["self_probe"]
    assert "recall_at_k" in sp and "mrr" in sp and "dark_count" in sp
    assert sp["probes"] == 2
    # concept-grouped block: ranked-retrieval metrics
    cg = report["concept_grouped"]
    assert "precision_at_k" in cg and "recall_at_k" in cg
    assert cg["probes"] == 1          # one shared concept (`caching`)
