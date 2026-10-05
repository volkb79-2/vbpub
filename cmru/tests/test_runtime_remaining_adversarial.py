"""Remaining runtime behavior witnesses; external services are boundary fakes."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cmru import handlers, runner, tester_gate


def test_runner_helpers_cover_required_env_evidence_and_git_date(tmp_path, monkeypatch):
    pytest_line = "================= 5 passed in 0.2s ================="
    assert runner._success_evidence(["noise", pytest_line]) == "5 passed in 0.2s"
    assert runner._success_evidence(["noise"]) is None
    monkeypatch.delenv("MISSING", raising=False)
    with pytest.raises(RuntimeError, match="MISSING"):
        runner.ensure_required_env(["MISSING"])
    monkeypatch.setenv("MISSING", "present")
    runner.ensure_required_env(["MISSING"])
    assert runner.resolve_path(tmp_path, "x") == tmp_path / "x"


def test_handlers_refuse_missing_prerequisites(monkeypatch):
    monkeypatch.setattr(handlers.shutil, "which", lambda _: None)
    with pytest.raises(SystemExit) as error:
        handlers._check_prerequisites()
    assert error.value.code == 3


def test_tester_gate_slice_probe_distinguishes_real_transient_and_missing(monkeypatch):
    monkeypatch.setattr(tester_gate.shutil, "which", lambda _: None)
    assert tester_gate.check_slice_unit("dev.slice", "probe", "dev-gates.slice")[0] is None
    monkeypatch.setattr(tester_gate.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(tester_gate.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        stdout="LoadState=loaded\nFragmentPath=\n", stderr="", returncode=0))
    ok, note = tester_gate.check_slice_unit("typo.slice", "probe", "dev-gates.slice")
    assert ok is False and "TRANSIENT" in note
    monkeypatch.setattr(tester_gate.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        stdout="LoadState=loaded\nFragmentPath=/etc/systemd/system/dev.slice\n", stderr="", returncode=0))
    assert tester_gate.check_slice_unit("dev.slice", "probe", "dev-gates.slice")[0] is True
