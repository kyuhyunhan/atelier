"""PR-4: MCP HTTP transport — loopback + bearer.

We test the middleware behavior end-to-end without binding to a real
port: Starlette accepts a directly-invoked ASGI app via its TestClient,
which speaks HTTP in-process.
"""
from __future__ import annotations

import asyncio
from typing import Dict, List

import pytest
import yaml


# ── _resolve_settings: loopback enforcement ───────────────────────────────────


def test_resolve_settings_refuses_non_loopback(atelier_env: Dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.service import mcp_http
    from runtime.util import config as _config

    cfg = _config.load()
    cfg.raw["service"] = {
        "enabled": True,
        "allowed_user": "test@example.com",
        "mcp_http": {"enabled": True, "bind": "0.0.0.0", "port": 7322,
                     "token_env": "ATELIER_MCP_HTTP_TOKEN"},
    }
    with pytest.raises(ValueError, match="loopback"):
        mcp_http._resolve_settings(cfg)


def test_resolve_settings_accepts_loopback(atelier_env: Dict) -> None:
    from runtime.service import mcp_http
    from runtime.util import config as _config

    cfg = _config.load()
    cfg.raw["service"] = {
        "mcp_http": {"bind": "127.0.0.1", "port": 7322,
                     "token_env": "ATELIER_MCP_HTTP_TOKEN"}
    }
    bind, port, env = mcp_http._resolve_settings(cfg)
    assert bind == "127.0.0.1"
    assert port == 7322
    assert env == "ATELIER_MCP_HTTP_TOKEN"


# ── BearerMiddleware: 401 vs pass-through ─────────────────────────────────────


def _make_middleware_with_inner(seen: List[str]):
    from runtime.service import mcp_http

    async def inner(scope, receive, send) -> None:
        # Record the active session caller so we can assert it was set.
        from runtime.service import tools as _tools
        seen.append(_tools.current_session().caller)
        # Reply with a tiny 200 so Starlette TestClient is happy.
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"ok"})

    return mcp_http.BearerMiddleware(inner, token_env="ATELIER_MCP_HTTP_TOKEN")


def test_middleware_rejects_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.testclient import TestClient

    monkeypatch.setenv("ATELIER_MCP_HTTP_TOKEN", "secret")
    seen: List[str] = []
    client = TestClient(_make_middleware_with_inner(seen))
    r = client.get("/")
    assert r.status_code == 401
    assert seen == []


def test_middleware_rejects_bad_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.testclient import TestClient

    monkeypatch.setenv("ATELIER_MCP_HTTP_TOKEN", "secret")
    seen: List[str] = []
    client = TestClient(_make_middleware_with_inner(seen))
    r = client.get("/", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    assert seen == []


def test_middleware_accepts_good_token_and_sets_session(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.testclient import TestClient

    monkeypatch.setenv("ATELIER_MCP_HTTP_TOKEN", "secret")
    seen: List[str] = []
    client = TestClient(_make_middleware_with_inner(seen))
    r = client.get("/", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    # Session caller should reflect the bearer-authenticated transport.
    assert len(seen) == 1
    assert seen[0].startswith("mcp-http:")


def test_middleware_refuses_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.testclient import TestClient

    monkeypatch.delenv("ATELIER_MCP_HTTP_TOKEN", raising=False)
    seen: List[str] = []
    client = TestClient(_make_middleware_with_inner(seen))
    r = client.get("/", headers={"Authorization": "Bearer anything"})
    assert r.status_code == 401


# ── Config validator: service.mcp_http loopback enforcement ───────────────────


def test_config_strict_rejects_non_loopback_bind(atelier_env: Dict) -> None:
    from runtime.util import config as _config

    cfg_path = atelier_env["home"] / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text())
    data["service"] = {
        "enabled": True,
        "allowed_user": "test@example.com",
        "mcp_http": {"enabled": True, "bind": "0.0.0.0", "port": 7322,
                     "token_env": "ATELIER_MCP_HTTP_TOKEN"},
    }
    cfg_path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="loopback"):
        _config.load()
