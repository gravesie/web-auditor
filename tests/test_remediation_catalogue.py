"""The catalogue must cover every check the audits can emit.

A new check that ships without a remediation entry would appear in a run with no
why-or-how and would be dropped from the action list. This test harvests the check
keys straight from the audit source (the literal first argument of every
CheckResult) and asserts each resolves, so the gap is caught at build time.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from app.acquisition.fetcher import SECURITY_HEADERS
from app.remediation.catalogue import CATALOGUE, FAMILIES, lookup
from app.remediation.schema import MAX_EFFORT, MAX_IMPACT, MIN_EFFORT, MIN_IMPACT

AUDITS_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "audits"


def _module_audit_key(tree: ast.Module) -> str | None:
    """The `key` class attribute of the audit module's class."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and getattr(stmt.target, "id", None) == "key":
                if isinstance(stmt.value, ast.Constant):
                    return stmt.value.value
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if getattr(target, "id", None) == "key" and isinstance(
                        stmt.value, ast.Constant
                    ):
                        return stmt.value.value
    return None


def _literal_check_keys() -> set[tuple[str, str]]:
    """(audit_key, check_key) for every CheckResult built with a literal key."""
    pairs: set[tuple[str, str]] = set()
    for path in AUDITS_DIR.glob("*.py"):
        if path.name in ("__init__.py", "base.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        audit_key = _module_audit_key(tree)
        assert audit_key, f"no audit key found in {path.name}"
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "CheckResult":
                first = node.args[0] if node.args else None
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    pairs.add((audit_key, first.value))
    return pairs


# The checks generated per item (not literal), with a representative member each.
_GENERATED = (
    [("build_security", f"header_{h.replace('-', '_')}") for h in SECURITY_HEADERS]
    + [("performance", "cwv_lcp"), ("performance", "cwv_inp"), ("performance", "cwv_cls")]
    + [("build_security", "component_php"), ("build_security", "component_wordpress")]
)


def test_every_literal_check_has_a_remediation():
    missing = sorted(
        f"{audit}.{check}"
        for audit, check in _literal_check_keys()
        if lookup(audit, check) is None
    )
    assert not missing, f"checks with no catalogue entry: {missing}"


@pytest.mark.parametrize("audit_key,check_key", _GENERATED)
def test_generated_checks_resolve(audit_key: str, check_key: str):
    assert lookup(audit_key, check_key) is not None


def test_harvest_found_a_realistic_number_of_checks():
    # Guards against the AST harvest silently matching nothing and the coverage test
    # passing vacuously.
    assert len(_literal_check_keys()) >= 90


def test_all_entries_within_scales():
    for key, entry in {**CATALOGUE, **FAMILIES}.items():
        assert MIN_IMPACT <= entry.impact <= MAX_IMPACT, key
        assert MIN_EFFORT <= entry.effort <= MAX_EFFORT, key
        assert entry.why.strip() and entry.how.strip(), key


def test_catalogue_keys_are_composite():
    for key in CATALOGUE:
        assert "." in key, f"catalogue key not in 'audit.check' form: {key}"
