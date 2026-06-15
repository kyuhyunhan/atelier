"""Unit tests for atelier-mcp-call param assembly (RFC 0004 phase 1).

Covers the two code paths added for session-end capture: the --require_why
string→bool coercion and the capture-tool field whitelist. Pure function tests —
no network, no running server.
"""
import inspect
import json

from runtime.service import mcp_call


def test_capture_fields_match_handler_signature():
    """The whitelist must mirror the capture handler's params exactly, or a valid
    arg gets silently dropped (missing) / dead weight accrues (stale)."""
    from runtime.service.tools import _h_learning_capture
    sig = set(inspect.signature(_h_learning_capture).parameters)
    assert sig == set(mcp_call._CAPTURE_FIELDS)


def test_require_why_false_coerced_to_bool():
    p = mcp_call._build_params("atelier_learning_capture", None, None,
                               require_why="false")
    assert p["require_why"] is False


def test_require_why_true_coerced_to_bool():
    p = mcp_call._build_params("atelier_learning_capture", None, None,
                               require_why="True")
    assert p["require_why"] is True


def test_require_why_non_bool_passes_through():
    p = mcp_call._build_params("atelier_learning_capture", None, None,
                               require_why="maybe")
    assert p["require_why"] == "maybe"


def test_kv_flag_overrides_json():
    """An explicit kv flag wins over the same key in --json (documented order)."""
    p = mcp_call._build_params("atelier_learning_capture",
                               json.dumps({"require_why": True}), None,
                               require_why="false")
    assert p["require_why"] is False


def test_capture_whitelist_strips_envelope_keys():
    stdin = json.dumps({
        "transcript_path": "/t.jsonl", "session_id": "s",
        "cwd": "/x", "hook_event_name": "PreCompact", "trigger": "manual",
    })
    p = mcp_call._build_params("atelier_learning_capture", None, stdin)
    assert p["transcript_path"] == "/t.jsonl"
    assert p["session_id"] == "s"
    for envelope in ("cwd", "hook_event_name", "trigger"):
        assert envelope not in p


def test_whitelist_not_applied_to_other_tools():
    """The whitelist is scoped to capture; other tools pass arbitrary args."""
    stdin = json.dumps({"slug": "foo", "direction": "both", "cwd": "/x"})
    p = mcp_call._build_params("atelier_links", None, stdin)
    assert p == {"slug": "foo", "direction": "both", "cwd": "/x"}


def test_plain_text_stdin_becomes_observation():
    p = mcp_call._build_params("atelier_learning_capture", None, "not json at all")
    assert p["observation"] == "not json at all"
