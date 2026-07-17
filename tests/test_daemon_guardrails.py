"""Always-on serve (launchd) + resource guardrails.

The guardrails are a SPEC, not prose (the statusline CPU-melt lesson): G2/G3
live in the plist this module renders, G5 in the autosync piggyback reindex.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

from runtime.service import daemon as _daemon
from runtime.service import vault_autosync as _autosync


# ── plist spec (G2 crash-loop, G3 low priority) ─────────────────────────────

def test_plist_encodes_the_guardrails() -> None:
    spec = _daemon.render_plist(python_exe="/usr/bin/python3",
                                engine_root=Path("/eng"),
                                log_dir=Path("/logs"))
    assert spec["Label"] == "io.atelier.serve"
    assert spec["KeepAlive"] is True                    # restart on crash …
    assert spec["ThrottleInterval"] == 60               # … at most 1/min (G2)
    assert spec["ProcessType"] == "Background"          # low priority (G3)
    assert spec["Nice"] == 10                           # (G3)
    assert spec["ProgramArguments"][0] == "/usr/bin/python3"
    assert spec["ProgramArguments"][-2:] == ["serve", "--http"]
    assert spec["WorkingDirectory"] == "/eng"
    assert spec["StandardOutPath"].startswith("/logs/")


def test_install_writes_plist_and_loads(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def fake_launchctl(*args):
        calls.append(args)
        class R:  # noqa: N801 - stub
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(_daemon, "_launchctl", fake_launchctl)
    monkeypatch.setattr(_daemon, "plist_path", lambda: tmp_path / "agent.plist")
    monkeypatch.setattr(_daemon, "_log_dir", lambda: tmp_path / "logs")

    out = _daemon.install()
    assert out["loaded"] is True
    assert (tmp_path / "agent.plist").is_file()          # plist written
    assert any(a[0] == "bootstrap" for a in calls)       # agent loaded

    out2 = _daemon.uninstall()
    assert out2["plist_removed"] is True
    assert not (tmp_path / "agent.plist").exists()       # kill switch removes it
    assert any(a[0] == "bootout" for a in calls)


# ── G5: embed cap on the piggyback reindex ──────────────────────────────────

def _fake_status(n: int) -> str:
    return "\n".join(f" M raw/f{i}.md" for i in range(n))


def _capture_reindex(monkeypatch):
    from runtime.index import reindex as _reindex
    seen: Dict[str, object] = {}

    def fake_reindex_space(cfg, name, full=False, **kw):
        seen["embed_gateway"] = kw.get("embed_gateway", "AUTO(default)")
        return _reindex.ReindexStats(space=name)

    monkeypatch.setattr(_reindex, "reindex_space", fake_reindex_space)
    monkeypatch.setattr(_reindex, "canonical_spaces", lambda cfg: ["gorae"])
    return seen


def test_small_commit_keeps_embeddings(atelier_env: Dict, monkeypatch) -> None:
    seen = _capture_reindex(monkeypatch)
    _autosync._reindex_changed(_fake_status(3))          # 3 ≤ cap(50)
    assert seen["embed_gateway"] == "AUTO(default)"      # auto gateway kept


def test_bulk_commit_skips_embeddings(atelier_env: Dict, monkeypatch) -> None:
    seen = _capture_reindex(monkeypatch)
    _autosync._reindex_changed(_fake_status(51))         # 51 > cap(50)
    assert seen["embed_gateway"] is None                 # G5: vectors deferred
