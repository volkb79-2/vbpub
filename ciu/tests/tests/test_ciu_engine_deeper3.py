"""Remaining public runtime-dependency contracts for CIU's engine.

These tests keep the host boundary fake while asserting the operator-visible
contract: CIU must reject an unavailable Docker/Compose runtime before it can
enter the deployment pipeline, while an explicit emergency bypass performs no
probe at all.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402


def _result(returncode: int) -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout="", stderr="unavailable")


def test_dependency_check_reports_both_required_daemon_commands(monkeypatch, capsys):
    """CIU refuses deployment when either Docker runtime command is unavailable.

    A non-zero command result is the actual daemon boundary; merely patching
    ``subprocess.run`` would not exercise CIU, which delegates through
    ``procutil.run_cmd``.
    """
    monkeypatch.delenv("SKIP_DEPENDENCY_CHECK", raising=False)
    calls: list[list[str]] = []

    def unavailable(argv, **kwargs):
        calls.append(argv)
        return _result(1)

    monkeypatch.setattr(engine.procutil, "run_cmd", unavailable)

    with pytest.raises(engine.DependencyError, match="missing required runtime dependencies"):
        engine.check_runtime_dependencies()

    assert calls == [["docker", "--version"], ["docker", "compose", "version"]]
    output = capsys.readouterr().out
    assert "Docker Engine" in output
    assert "Docker Compose v2" in output


def test_dependency_check_explicit_bypass_never_contacts_daemon(monkeypatch):
    """The documented emergency bypass is a complete no-op, not a partial check."""
    monkeypatch.setenv("SKIP_DEPENDENCY_CHECK", "1")
    monkeypatch.setattr(
        engine.procutil,
        "run_cmd",
        lambda *args, **kwargs: pytest.fail("bypass must not contact Docker"),
    )

    engine.check_runtime_dependencies()
