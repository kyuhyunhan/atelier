"""RFC 0005 P5.1 — recall over v7 CLAIM nodes (the §6 formula).

    recall = gate(surfacing) × domain_prior(context) × vector_relevance × sensitivity_gate

These tests pin the surfacing ladder (query ⊂ proactive ⊂ always), the
sensitivity gate (private NEVER pushed proactively), the T0 hard cap, and the
coding-session domain prior ordering.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from runtime.service import api
from runtime.service.learnings import recall_v7 as _rv
from tests.conftest import write_page


_BASE = {
    "schema_version": 7,
    "kind": "claim",
    "created_at": "2026-01-01T00:00:00Z",
    "content_hash": "h",
    "is_about": [],
    "derived_from": ["src1"],
    "attributed_to": "claude-code",
    "generated_by": "atomize",
}


def _claim(vault, entry_id: str, statement: str, *,
           surfacing: str = "proactive",
           domain: str = "operational",
           sensitivity: str = "public",
           project: Optional[str] = None) -> None:
    fm = {
        **_BASE, "entry_id": entry_id, "statement": statement,
        "surfacing": surfacing, "domain": domain, "sensitivity": sensitivity,
    }
    if project:
        fm["project"] = project
    write_page(vault / "graph" / f"{entry_id}.md", fm,
               f"## Claim\n\n{statement}\n")


# ── unit: the pure factors ─────────────────────────────────────────────────────


def test_gate_ladder_is_subset_chain() -> None:
    # query ⊂ proactive ⊂ always: a higher-level claim is eligible at lower tiers.
    assert _rv.gate("always", _rv.TIER_QUERY)
    assert _rv.gate("always", _rv.TIER_PROACTIVE)
    assert _rv.gate("always", _rv.TIER_ALWAYS)
    assert _rv.gate("proactive", _rv.TIER_PROACTIVE)
    assert not _rv.gate("proactive", _rv.TIER_ALWAYS)   # proactive can't fill T0
    assert _rv.gate("query", _rv.TIER_QUERY)
    assert not _rv.gate("query", _rv.TIER_PROACTIVE)    # on-query-only stays put


def test_sensitivity_gate_blocks_private_only_off_query() -> None:
    private = {"sensitivity": "private"}
    public = {"sensitivity": "public"}
    # T2 (on-query) reaches anything, incl. private.
    assert _rv.sensitivity_gate(private, _rv.TIER_QUERY)
    # T1/T0 never push private.
    assert not _rv.sensitivity_gate(private, _rv.TIER_PROACTIVE)
    assert not _rv.sensitivity_gate(private, _rv.TIER_ALWAYS)
    assert _rv.sensitivity_gate(public, _rv.TIER_PROACTIVE)


def test_domain_prior_coding_ordering() -> None:
    # coding session: operational/current-project HIGH, knowledge MID, personal LOW.
    op = _rv.domain_prior("operational", project_match=False)
    know = _rv.domain_prior("knowledge", project_match=False)
    personal = _rv.domain_prior("personal", project_match=False)
    assert op > know > personal
    # current-project compounds on top of the domain band.
    assert _rv.domain_prior("knowledge", project_match=True) > know


def test_score_claim_is_the_product_with_hard_gates() -> None:
    hit = {"score": 1.0, "fm": {"surfacing": "proactive", "domain": "operational",
                                "sensitivity": "public"}}
    # T1: relevance(1.0) × operational prior(2.0) = 2.0.
    assert _rv.score_claim(hit, tier=_rv.TIER_PROACTIVE, project=None) == 2.0
    # T2 ignores the prior → just relevance.
    assert _rv.score_claim(hit, tier=_rv.TIER_QUERY, project=None) == 1.0
    # a private claim is gated to 0 off-query.
    priv = {"score": 1.0, "fm": {"surfacing": "always", "domain": "operational",
                                 "sensitivity": "private"}}
    assert _rv.score_claim(priv, tier=_rv.TIER_PROACTIVE, project=None) == 0.0
    assert _rv.score_claim(priv, tier=_rv.TIER_QUERY, project=None) == 1.0


# ── integration: through the index ─────────────────────────────────────────────


def test_private_claim_never_pushed_proactively_but_reachable_on_query(
        vault_env: Dict) -> None:
    vault = vault_env["vault"]
    _claim(vault, "pub", "kafka rebalance storms under load tuning",
           surfacing="always", domain="operational", sensitivity="public")
    _claim(vault, "secret", "kafka rebalance secret credentials rotation",
           surfacing="always", domain="operational", sensitivity="private")
    api.reindex(full=True)

    # T1 proactive push: the private claim MUST NOT appear.
    t1 = _rv.recall_claims(query="kafka rebalance", tier=_rv.TIER_PROACTIVE,
                           top_k=10)
    slugs_t1 = {it["slug"] for it in t1["items"]}
    assert "pub" in slugs_t1
    assert "secret" not in slugs_t1, "private claim must never be pushed at T1"

    # T2 explicit on-query: the private claim IS reachable.
    t2 = _rv.recall_claims(query="kafka rebalance secret", tier=_rv.TIER_QUERY,
                           top_k=10)
    slugs_t2 = {it["slug"] for it in t2["items"]}
    assert "secret" in slugs_t2, "private claim must be reachable by explicit query"


def test_t0_always_budget_is_hard_capped(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    # More always-claims than the cap, all matching the probe.
    for i in range(_rv.T0_CAP + 4):
        _claim(vault, f"a{i}", f"always claim about retries idempotency {i}",
               surfacing="always", domain="operational", sensitivity="public")
    api.reindex(full=True)

    out = _rv.recall_claims(query="retries idempotency", tier=_rv.TIER_ALWAYS,
                            top_k=50)
    assert out["count"] == _rv.T0_CAP, "T0 must be hard-capped regardless of top_k"


def test_t0_only_admits_always_claims(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    _claim(vault, "alw", "shared concept widget lifecycle always",
           surfacing="always", domain="operational")
    _claim(vault, "pro", "shared concept widget lifecycle proactive",
           surfacing="proactive", domain="operational")
    api.reindex(full=True)

    out = _rv.recall_claims(query="widget lifecycle", tier=_rv.TIER_ALWAYS,
                            top_k=10)
    slugs = {it["slug"] for it in out["items"]}
    assert "alw" in slugs
    assert "pro" not in slugs, "T0 admits only surfacing:always claims"


def test_domain_prior_orders_proactive_push(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    # Same statement words → comparable relevance; only domain differs.
    _claim(vault, "op", "render flicker on mount needs stable keys",
           surfacing="proactive", domain="operational")
    _claim(vault, "kn", "render flicker on mount needs stable keys",
           surfacing="proactive", domain="knowledge")
    _claim(vault, "pe", "render flicker on mount needs stable keys",
           surfacing="proactive", domain="personal")
    api.reindex(full=True)

    out = _rv.recall_claims(query="render flicker mount stable keys",
                            tier=_rv.TIER_PROACTIVE, top_k=10)
    order = [it["slug"] for it in out["items"]]
    assert order.index("op") < order.index("kn") < order.index("pe"), \
        f"coding prior must order operational > knowledge > personal: {order}"


def test_current_project_match_lifts_a_claim(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    _claim(vault, "mine", "auth token refresh race condition fix",
           surfacing="proactive", domain="knowledge", project="lexio")
    _claim(vault, "other", "auth token refresh race condition fix",
           surfacing="proactive", domain="knowledge", project="bht")
    api.reindex(full=True)

    out = _rv.recall_claims(query="auth token refresh race",
                            tier=_rv.TIER_PROACTIVE, project="lexio", top_k=10)
    order = [it["slug"] for it in out["items"]]
    assert order.index("mine") < order.index("other"), \
        "current-project claim must outrank an off-project peer of equal relevance"


def test_query_tier_is_universal_prior_ignored(vault_env: Dict) -> None:
    vault = vault_env["vault"]
    # A personal claim would be dampened at T1, but T2 ignores the prior, so a
    # query-only claim is reachable and not penalised by its domain.
    _claim(vault, "qonly", "personal journaling habit reflection note",
           surfacing="query", domain="personal", sensitivity="public")
    api.reindex(full=True)

    # query-only is NOT eligible proactively.
    t1 = _rv.recall_claims(query="journaling habit reflection",
                           tier=_rv.TIER_PROACTIVE, top_k=10)
    assert "qonly" not in {it["slug"] for it in t1["items"]}

    # but it IS reachable on explicit query.
    t2 = _rv.recall_claims(query="journaling habit reflection",
                           tier=_rv.TIER_QUERY, top_k=10)
    assert "qonly" in {it["slug"] for it in t2["items"]}


def test_recall_empty_query_returns_nothing(vault_env: Dict) -> None:
    out = _rv.recall_claims(query="  ", tier=_rv.TIER_PROACTIVE)
    assert out["count"] == 0
    assert out["markdown"] == ""


def test_mcp_recall_surfaces_v7_claims(vault_env: Dict) -> None:
    """End-to-end through the atelier_recall tool: a proactive public claim
    surfaces, and a private one does not."""
    import asyncio
    from runtime.service import tools as _tools

    vault = vault_env["vault"]
    _claim(vault, "pubc", "graphql codegen cache invalidation strategy",
           surfacing="proactive", domain="operational", sensitivity="public")
    _claim(vault, "privc", "graphql codegen private vendor secret strategy",
           surfacing="proactive", domain="operational", sensitivity="private")
    api.reindex(full=True)

    async def go() -> Dict:
        return await _tools.invoke("atelier_recall",
                                   query="graphql codegen strategy", top_k=10)
    out = asyncio.run(go())
    slugs = {it["slug"] for it in out["items"]}
    assert "pubc" in slugs
    assert "privc" not in slugs, "private claim must not surface via the tool's T1 default"
