"""Run lint rules. Each rule has a check function (auto rules) and optional fix."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import loader


@dataclass
class Finding:
    rule_id: str
    severity: str
    message: str
    page_slug: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LintReport:
    findings: List[Finding] = field(default_factory=list)
    rules_run: List[str] = field(default_factory=list)
    fixes_applied: int = 0

    def by_severity(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for f in self.findings:
            out[f.severity] = out.get(f.severity, 0) + 1
        return out

    def failed(self) -> bool:
        return any(f.severity == "FAIL" for f in self.findings)


CheckFn = Callable[[sqlite3.Connection, loader.Rule, Optional[str]], List[Finding]]
FixFn   = Callable[[sqlite3.Connection, Finding], bool]

_CHECKS: Dict[str, CheckFn] = {}
_FIXES:  Dict[str, FixFn]   = {}


def register_check(name: str):
    def deco(fn: CheckFn) -> CheckFn:
        _CHECKS[name] = fn
        return fn
    return deco


def register_fix(name: str):
    def deco(fn: FixFn) -> FixFn:
        _FIXES[name] = fn
        return fn
    return deco


def run(
    conn: sqlite3.Connection,
    space: Optional[str] = None,
    rule_ids: Optional[List[str]] = None,
    apply_fixes: bool = False,
) -> LintReport:
    rules = loader.load_rules()
    defaults = loader.defaults()
    selected = rule_ids or defaults.get("run_on_lint_command", list(rules))
    report = LintReport()

    # Import rule modules to register their check functions.
    from . import L1, L3, L5, L6, L8  # noqa: F401

    for rid in selected:
        rule = rules.get(rid)
        if not rule:
            continue
        if space and rule.spaces and space not in rule.spaces:
            continue
        if rule.automation == "manual" or not rule.check:
            continue
        fn = _CHECKS.get(rule.check)
        if not fn:
            continue
        report.rules_run.append(rid)
        findings = fn(conn, rule, space)
        report.findings.extend(findings)

        if apply_fixes and rule.fix:
            fix_fn = _FIXES.get(rule.fix)
            if fix_fn:
                for f in findings:
                    if fix_fn(conn, f):
                        report.fixes_applied += 1

    return report
