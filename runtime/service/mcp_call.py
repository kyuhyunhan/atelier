"""atelier-mcp-call — thin CLI that talks to the running MCP HTTP server.

Used by:
- `~/.atelier/bin/capture-learning.sh` (Claude Code Stop / SessionEnd hooks)
- Ad-hoc shell scripts that want to fire-and-forget an MCP tool call

The script reads the MCP HTTP endpoint + bearer token from
`~/.atelier/config.yaml` (loopback) and POSTs a JSON-RPC `tools/call`
frame for the requested tool. By default it **always exits 0** so a
failing capture never breaks the calling user flow; pass `--strict` to
make it exit non-zero on RPC errors.

Usage:
    atelier-mcp-call <tool_name> --json '<json params>'
    atelier-mcp-call atelier_learning_capture \\
        --working_dir "$PWD" --hook Stop --payload-from-stdin
    echo '{"observation":"foo","hook":"Stop"}' | \\
        atelier-mcp-call atelier_learning_capture --payload-from-stdin
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


_DEFAULT_CONFIG = Path.home() / ".atelier" / "config.yaml"
_DEFAULT_LOG = Path.home() / ".atelier" / "logs" / "capture.log"


def _read_config(path: Path) -> Dict[str, Any]:
    import yaml
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def _endpoint(cfg: Dict[str, Any]) -> tuple[str, str]:
    """Return (url, token) for the MCP HTTP server. Resolves env vars."""
    svc = (cfg.get("service") or {}).get("mcp_http") or {}
    host = svc.get("bind", "127.0.0.1")
    port = int(svc.get("port", 7322))
    path = svc.get("path", "/mcp")
    token_env = svc.get("token_env", "ATELIER_MCP_HTTP_TOKEN")
    token = os.environ.get(token_env, "")
    return f"http://{host}:{port}{path}", token


def _log(message: str, *, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"{ts}  {message}\n")


def _call(url: str, token: str, tool: str, params: Dict[str, Any],
          *, timeout: float = 15.0) -> Dict[str, Any]:
    """Send a single JSON-RPC tools/call frame and return the parsed result."""
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": params},
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - localhost
        raw = resp.read()
    # MCP servers may use SSE for some calls; for one-shot tools/call,
    # JSON is typical. Try JSON first; fall back to extracting the data:
    # frames from SSE.
    text = raw.decode("utf-8", "replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for line in text.splitlines():
            if line.startswith("data:"):
                blob = line[len("data:"):].strip()
                try:
                    return json.loads(blob)
                except json.JSONDecodeError:
                    continue
        raise


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="atelier-mcp-call")
    p.add_argument("tool", help="MCP tool name (e.g. atelier_learning_capture)")
    p.add_argument("--json", help="raw JSON params dict")
    p.add_argument("--payload-from-stdin", action="store_true",
                   help="read params dict as JSON from stdin")
    p.add_argument("--config", default=str(_DEFAULT_CONFIG))
    p.add_argument("--strict", action="store_true",
                   help="exit non-zero on RPC error (default: exit 0)")
    p.add_argument("--log", default=str(_DEFAULT_LOG))
    # Free-form key=value pairs for common params; lighter than --json
    # for shell hooks.
    p.add_argument("--working_dir")
    p.add_argument("--hook")
    p.add_argument("--observation")
    p.add_argument("--project_hint")
    args, extra = p.parse_known_args(argv)

    params: Dict[str, Any] = {}
    if args.json:
        params.update(json.loads(args.json))
    if args.payload_from_stdin and not sys.stdin.isatty():
        stdin_raw = sys.stdin.read().strip()
        if stdin_raw:
            try:
                params.update(json.loads(stdin_raw))
            except json.JSONDecodeError:
                # Hook stdin may be plain text — store it as observation.
                params.setdefault("observation", stdin_raw)
    for fld in ("working_dir", "hook", "observation", "project_hint"):
        v = getattr(args, fld)
        if v is not None:
            params[fld] = v

    log_path = Path(args.log).expanduser()
    cfg = _read_config(Path(args.config).expanduser())
    url, token = _endpoint(cfg)

    if not token:
        _log(f"no bearer token in env; skipping {args.tool}", log_path=log_path)
        return 0 if not args.strict else 2

    try:
        result = _call(url, token, args.tool, params)
    except (urllib.error.URLError, OSError) as e:
        _log(f"{args.tool}: rpc-error {type(e).__name__}: {e}", log_path=log_path)
        return 0 if not args.strict else 1

    if "error" in result:
        _log(f"{args.tool}: error {result['error']}", log_path=log_path)
        return 0 if not args.strict else 1

    _log(f"{args.tool}: ok", log_path=log_path)
    if args.strict:
        json.dump(result.get("result", {}), sys.stdout)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
