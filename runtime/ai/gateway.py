"""Embedding gateway (RFC 0002 §5) — text → vector, behind one contract.

The gateway is the ONLY place atelier turns text into embeddings. Storage
(`vecstore`) consumes pre-computed vectors; provider choice (local Ollama vs a
hosted API) is invisible to it. Local-first: the vault is personal, so the
default provider runs on-device and content never leaves the machine.

`signature` is the stale-detection key (provider:model:dim:chunker_version),
stamped next to every cached vector — change the model and every chunk
re-embeds; change nothing and a full reindex re-embeds nothing.

Settings live here, not in util/config.py: the `embedding:` yaml block is an
AI-layer concern, parsed from `Config.raw` by `settings_from`, so the core
config module never grows an AI dependency.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, List, Optional, Protocol, Sequence, runtime_checkable

from ..util import logging as log

# Bump when the chunking strategy changes meaning (RFC 0002 §5): chunk text is
# what gets embedded, so a chunker change invalidates cached vectors even if
# provider+model+dim are unchanged.
CHUNKER_VERSION = "chunker_v1"


class EmbeddingError(RuntimeError):
    """Provider returned something unusable (wrong dim, malformed response)."""


@dataclass(frozen=True)
class EmbeddingSettings:
    """The `embedding:` block of ~/.atelier/config.yaml, with local defaults."""
    provider: str = "ollama"
    model: str = "bge-m3"
    dim: int = 1024
    url: str = "http://localhost:11434"
    enabled: bool = True
    batch_size: int = 64


def settings_from(raw: dict) -> EmbeddingSettings:
    """Parse a Config.raw dict's `embedding:` block (absent → defaults).

    `ATELIER_EMBED=off` force-disables regardless of config — the kill switch
    for test runs (a dev machine with a live Ollama must not embed fixture
    vaults) and for temporarily sparing the CPU without editing config."""
    e = (raw or {}).get("embedding") or {}
    d = EmbeddingSettings()
    enabled = bool(e.get("enabled", d.enabled))
    if os.environ.get("ATELIER_EMBED", "").lower() == "off":
        enabled = False
    return EmbeddingSettings(
        provider=e.get("provider", d.provider),
        model=e.get("model", d.model),
        dim=int(e.get("dim", d.dim)),
        url=e.get("url", d.url),
        enabled=enabled,
        batch_size=int(e.get("batch_size", d.batch_size)),
    )


@runtime_checkable
class EmbeddingGateway(Protocol):
    """One vector per input text, in order. `signature` pins the vector space;
    `dim` is the vector length (consumers size their indexes from it)."""

    @property
    def signature(self) -> str: ...

    @property
    def dim(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> List[List[float]]: ...


# transport: (url, payload) -> parsed JSON dict. Injected so unit tests never
# need a live provider; the default is a stdlib urllib POST.
Transport = Callable[[str, dict], dict]


def _http_transport(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


class OllamaGateway:
    """Ollama `/api/embed` (batch-capable), localhost by default."""

    def __init__(self, *, model: str, dim: int,
                 url: str = "http://localhost:11434",
                 transport: Transport = _http_transport,
                 batch_size: int = 64) -> None:
        self._model = model
        self._dim = dim
        self._url = url.rstrip("/")
        self._transport = transport
        self._batch = max(1, batch_size)

    @property
    def signature(self) -> str:
        return f"ollama:{self._model}:{self._dim}:{CHUNKER_VERSION}"

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        out: List[List[float]] = []
        for i in range(0, len(texts), self._batch):
            batch = list(texts[i:i + self._batch])
            resp = self._transport(f"{self._url}/api/embed",
                                   {"model": self._model, "input": batch})
            vecs = resp.get("embeddings")
            if not isinstance(vecs, list) or len(vecs) != len(batch):
                raise EmbeddingError(
                    f"provider returned {len(vecs) if isinstance(vecs, list) else 'no'}"
                    f" embeddings for {len(batch)} inputs")
            for v in vecs:
                if len(v) != self._dim:
                    raise EmbeddingError(
                        f"dim mismatch: configured {self._dim}, got {len(v)} "
                        f"(model {self._model!r}) — fix `embedding.dim` in config")
                out.append([float(x) for x in v])
        return out


def from_config(settings: EmbeddingSettings,
                transport: Transport = _http_transport,
                *, warmup: bool = True) -> Optional[EmbeddingGateway]:
    """Auto-when-reachable (RFC 0002 P2): return a live gateway, or None when
    embeddings are disabled (or, with warmup, the provider doesn't answer).
    Callers treat None as 'skip the semantic substrate' — never an error.

    `warmup` controls the eager `embed(["ping"])` probe:
      True  (default, the WRITE path)  — ping the provider so a reindex either
            has a working gateway or cleanly degrades up front, and the model is
            warm for the bulk pass. An unreachable provider returns None here.
      False (the READ path)            — skip the ping. Recall runs per
            `UserPromptSubmit`; a synchronous round-trip on every keystroke-turn
            is pure latency tax. The gateway is returned optimistically; if the
            provider is in fact down, the first real `embed` raises and the
            resolver degrades to lexical-only (`resolver._embed_query`). The cost
            of a down provider moves from every call to one call.
    """
    if not settings.enabled:
        return None
    g = OllamaGateway(model=settings.model, dim=settings.dim, url=settings.url,
                      transport=transport, batch_size=settings.batch_size)
    if not warmup:
        return g
    try:
        g.embed(["ping"])               # also warms the model for the real pass
    except Exception as e:              # any failure → degrade, never crash:
        # connection refused (OSError/URLError), model missing (HTTPError),
        # dim misconfigured (EmbeddingError) — all mean "no semantic substrate".
        log.info("embedding.unreachable", provider=settings.provider,
                 url=settings.url, error=str(e))
        return None
    return g
