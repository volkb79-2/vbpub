"""Missing optional test dependencies FAIL by default (operator rule 2026-10-27).

`tests/_testdeps.py` is the one policy; this file pins both the policy itself and that no
test in this suite bypasses it with a bare `pytest.importorskip`, a `skipif(... is None)`
or an undeclared `pytest.skip(...)`. The two structural exceptions carry a
`testdeps-exception` marker on the line or the line before it, and nothing else may.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

import _testdeps

TESTS = Path(__file__).resolve().parent
MARKER = "testdeps-exception"


def _suite_files() -> list[Path]:
    return sorted(p for p in TESTS.glob("*.py") if p.name != "_testdeps.py")


def test_missing_fails_by_default_and_names_the_opt_out(monkeypatch):
    monkeypatch.delenv(_testdeps.ALLOW_VAR, raising=False)
    with pytest.raises(pytest.fail.Exception) as failed:
        _testdeps.missing("PyYAML", "not importable")
    text = str(failed.value)
    assert "PyYAML is not available (not importable)" in text
    assert "FAIL by default" in text and _testdeps.ALLOW_VAR in text


def test_missing_skips_only_when_explicitly_allowed(monkeypatch):
    monkeypatch.setenv(_testdeps.ALLOW_VAR, "1")
    with pytest.raises(pytest.skip.Exception, match="not importable"):
        _testdeps.missing("PyYAML", "not importable")
    monkeypatch.setenv(_testdeps.ALLOW_VAR, "yes")   # only the literal "1" opts out
    with pytest.raises(pytest.fail.Exception):
        _testdeps.missing("PyYAML", "not importable")


def test_optional_module_and_require_binary_follow_the_policy(monkeypatch):
    monkeypatch.delenv(_testdeps.ALLOW_VAR, raising=False)
    assert _testdeps.optional_module("json", "stdlib").__name__ == "json"
    with pytest.raises(pytest.fail.Exception, match="no_such_module_for_run_gate"):
        _testdeps.optional_module("no_such_module_for_run_gate", "absent")
    assert _testdeps.require_binary("sh", "POSIX shell").endswith("sh")
    with pytest.raises(pytest.fail.Exception, match="no-such-binary-for-run-gate"):
        _testdeps.require_binary("no-such-binary-for-run-gate", "absent")
    monkeypatch.setenv(_testdeps.REQUIRE_CONTAINER_SMOKE_VAR, "1")
    assert _testdeps.require_container_smoke()
    monkeypatch.delenv(_testdeps.REQUIRE_CONTAINER_SMOKE_VAR)
    assert not _testdeps.require_container_smoke()


def test_no_test_bypasses_the_policy():
    """Bare `importorskip` / `skipif(... is None)` are forbidden outright; every
    `pytest.skip(` outside `_testdeps.py` must sit on or right after a
    `testdeps-exception` marker line, and exactly the documented exceptions
    may carry one."""
    forbidden = re.compile(r"importorskip\(|skipif\([^)]*\bis None\b")
    offenders: list[str] = []
    marked: list[str] = []
    for path in _suite_files():
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, 1):
            where = f"{path.name}:{number}"
            if path.name == Path(__file__).name:
                continue
            if forbidden.search(line):
                offenders.append(f"{where}: {line.strip()}")
            if "pytest.skip(" in line or "pytest.mark.skipif(" in line:
                previous = lines[number - 2] if number >= 2 else ""
                if MARKER in line or MARKER in previous:
                    marked.append(where)
                else:
                    offenders.append(f"{where}: undeclared skip: {line.strip()}")
    assert offenders == [], (
        "these tests can turn a gate green without testing anything; route them "
        "through tests/_testdeps.py (fail by default, opt-out skip):\n" + "\n".join(offenders))
    # The exception list is closed: a new structural skip is a policy change,
    # documented in _testdeps.py, not a marker someone adds in passing.
    assert len(marked) == 2, marked
    assert all(m.startswith("test_run_gate.py:") for m in marked), marked


def test_the_selftest_lane_names_every_skip_in_the_gate_log():
    text = (TESTS.parent / "run-gate.toml").read_text(encoding="utf-8")
    assert "-m pytest tests -q -rs " in text
    readme = (TESTS.parent / "README.md").read_text(encoding="utf-8")
    assert "pytest tests -q -rs" in readme and _testdeps.ALLOW_VAR in readme


def test_the_policy_is_the_estate_convention():
    """Same opt-out shape as pwmcp's `tests/_testdeps.py` (the convention this
    rule cites); only the variable prefix differs."""
    assert _testdeps.ALLOW_VAR == "RUN_GATE_ALLOW_MISSING_TEST_DEPS"
    assert _testdeps.REQUIRE_CONTAINER_SMOKE_VAR == "RUN_GATE_REQUIRE_CONTAINER_SMOKE"
