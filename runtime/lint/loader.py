"""Load schema/data/lint.yaml as runtime rule objects."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

LINT_YAML = Path(__file__).resolve().parents[2] / "schema" / "data" / "lint.yaml"


@dataclass
class Rule:
    id: str
    name: str
    severity: str         # FAIL | WARN | INFO
    automation: str       # auto | manual
    spaces: list[str]
    description: str
    check: str | None
    fix: str | None
    db_query: str | None
    extras: dict[str, Any]


def load_rules() -> dict[str, Rule]:
    raw = yaml.safe_load(LINT_YAML.read_text())
    out: dict[str, Rule] = {}
    for rid, rd in (raw.get("rules") or {}).items():
        known = {"id", "name", "severity", "automation", "spaces", "description",
                 "check", "fix", "db_query"}
        out[rid] = Rule(
            id=rd["id"],
            name=rd["name"],
            severity=rd["severity"],
            automation=rd["automation"],
            spaces=rd.get("spaces") or [],
            description=rd.get("description", ""),
            check=rd.get("check"),
            fix=rd.get("fix"),
            db_query=rd.get("db_query"),
            extras={k: v for k, v in rd.items() if k not in known},
        )
    return out


def defaults() -> dict[str, Any]:
    raw = yaml.safe_load(LINT_YAML.read_text())
    return raw.get("defaults", {})
