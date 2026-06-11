"""RFC 0002 P2 — the embedding gateway (runtime/ai/gateway.py).

The gateway is the only component that turns text into vectors. It is tested
against a fake transport — no live Ollama in unit tests. Contract points:
batching, signature stability (stale-detection key), dim validation, and
graceful None when the provider is unreachable.
"""
from __future__ import annotations

import pytest

from runtime.ai import gateway as gw


class FakeTransport:
    """Records calls; returns deterministic vectors of the configured dim."""

    def __init__(self, dim: int = 4, fail: bool = False):
        self.dim = dim
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(self, url: str, payload: dict) -> dict:
        if self.fail:
            raise OSError("connection refused")
        texts = payload["input"]
        self.calls.append(list(texts))
        return {"embeddings": [[float(len(t))] * self.dim for t in texts]}


def test_embed_returns_one_vector_per_text():
    t = FakeTransport(dim=4)
    g = gw.OllamaGateway(model="bge-m3", dim=4, transport=t)
    vecs = g.embed(["alpha", "be"])
    assert len(vecs) == 2
    assert all(len(v) == 4 for v in vecs)
    assert vecs[0][0] == 5.0 and vecs[1][0] == 2.0   # transport echoes len()


def test_embed_batches_large_input():
    t = FakeTransport(dim=4)
    g = gw.OllamaGateway(model="bge-m3", dim=4, transport=t, batch_size=10)
    g.embed([f"text-{i}" for i in range(25)])
    assert [len(c) for c in t.calls] == [10, 10, 5]


def test_signature_pins_provider_model_dim_chunker():
    g = gw.OllamaGateway(model="bge-m3", dim=1024, transport=FakeTransport())
    assert g.signature == "ollama:bge-m3:1024:chunker_v1"


def test_dim_mismatch_raises():
    """A model that returns the wrong dimensionality must fail loudly — silently
    storing mismatched vectors would poison the kNN index."""
    t = FakeTransport(dim=8)
    g = gw.OllamaGateway(model="bge-m3", dim=4, transport=t)
    with pytest.raises(gw.EmbeddingError):
        g.embed(["x"])


def test_embed_empty_input_no_transport_call():
    t = FakeTransport()
    g = gw.OllamaGateway(model="bge-m3", dim=4, transport=t)
    assert g.embed([]) == []
    assert t.calls == []


def test_from_config_returns_none_when_unreachable():
    """Auto-when-reachable: a down provider yields no gateway (caller skips the
    embed pass), never an exception."""
    t = FakeTransport(fail=True)
    g = gw.from_config(gw.EmbeddingSettings(provider="ollama", model="bge-m3",
                                            dim=4, url="http://localhost:1"),
                       transport=t)
    assert g is None


def test_from_config_disabled_returns_none():
    g = gw.from_config(gw.EmbeddingSettings(enabled=False))
    assert g is None


def test_from_config_warmup_pings_once():
    """Default (write-path) construction probes the provider once."""
    t = FakeTransport(dim=4)
    g = gw.from_config(gw.EmbeddingSettings(dim=4), transport=t)
    assert g is not None
    assert t.calls == [["ping"]]


def test_from_config_no_warmup_makes_no_transport_call():
    """Read-path construction (RFC 0002 P3): the gateway is returned WITHOUT a
    provider round-trip — recall fires per UserPromptSubmit, so the ping tax is
    removed. A down provider is discovered lazily at the first real embed."""
    t = FakeTransport(dim=4)
    g = gw.from_config(gw.EmbeddingSettings(dim=4), transport=t, warmup=False)
    assert g is not None
    assert t.calls == []


def test_from_config_disabled_none_even_without_warmup():
    """`enabled=False` / ATELIER_EMBED=off still wins over warmup=False."""
    assert gw.from_config(gw.EmbeddingSettings(enabled=False), warmup=False) is None
