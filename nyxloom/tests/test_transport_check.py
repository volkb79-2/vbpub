"""Tests for transport_check.probe_docker_transport.

No real docker: every subprocess call is mocked, so these run anywhere and
assert the CLASSIFICATION logic (healthy / lying / unavailable) exactly.
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from nyxloom import transport_check as tc
from nyxloom.transport_check import (
    _MARK_EARLY,
    _MARK_LATE,
    TransportProbe,
    probe_docker_transport,
)


def _completed(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_healthy_when_both_markers_return(monkeypatch):
    """Both sentinel markers present across the delay -> healthy/trustworthy."""
    captured = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv
        captured["kw"] = kw
        return _completed(stdout=f"{_MARK_EARLY}\n{_MARK_LATE}\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    probe = probe_docker_transport("busybox")

    assert probe.status == "healthy"
    assert probe.healthy is True
    assert probe.lying is False
    # It must use the ATTACHED form (no -d), or it would not exercise the fault.
    assert "-d" not in captured["argv"]
    assert captured["argv"][:3] == ["docker", "run", "--rm"]
    assert "busybox" in captured["argv"]
    # It must judge on output, so it captures it.
    assert captured["kw"]["capture_output"] is True


def test_lying_when_only_early_marker_returns(monkeypatch):
    """Early marker but NOT the post-delay one -> the truncation signature.

    This is the only status that should block a caller, and the message must
    name the fault so an operator can act.
    """
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _completed(stdout=f"{_MARK_EARLY}\n")
    )
    probe = probe_docker_transport("busybox")

    assert probe.status == "lying"
    assert probe.lying is True
    assert probe.healthy is False
    assert "TRUNCATION" in probe.detail
    assert "L18" in probe.detail  # points at the canonical lesson


def test_lying_ignores_a_correct_exit_code(monkeypatch):
    """Judge on OUTPUT, never the exit code. A truncated stream that still
    returned 0 (the measured non-deterministic case) is STILL lying."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: _completed(stdout=f"{_MARK_EARLY}\n", returncode=0),
    )
    assert probe_docker_transport("busybox").status == "lying"


def test_unavailable_when_no_markers_at_all(monkeypatch):
    """Neither marker (image error / daemon down) -> unavailable, NOT lying,
    so a caller skips rather than manufacturing a failure."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: _completed(
            stderr="Unable to find image 'busybox' locally\nerror", returncode=125
        ),
    )
    probe = probe_docker_transport("busybox")
    assert probe.status == "unavailable"
    assert probe.lying is False
    assert probe.healthy is False


def test_unavailable_when_docker_cli_missing(monkeypatch):
    def raise_fnf(*a, **k):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(subprocess, "run", raise_fnf)
    probe = probe_docker_transport("busybox", docker_bin="docker")
    assert probe.status == "unavailable"
    assert "not found" in probe.detail


def test_unavailable_on_timeout_not_lying(monkeypatch):
    """A hang is ambiguous, not the deterministic truncation signature, so it
    is unavailable (skip) -- never a block."""

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=1)

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    probe = probe_docker_transport("busybox", timeout_s=1)
    assert probe.status == "unavailable"
    assert probe.lying is False


def test_marker_appearing_only_on_stderr_still_counts(monkeypatch):
    """Some transports interleave; markers on stderr must still be seen."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: _completed(stdout="", stderr=f"{_MARK_EARLY}\n{_MARK_LATE}\n"),
    )
    assert probe_docker_transport("busybox").status == "healthy"


def test_delay_and_timeout_are_passed_through(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        seen["timeout"] = kw.get("timeout")
        return _completed(stdout=f"{_MARK_EARLY}{_MARK_LATE}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    probe_docker_transport("img", delay_s=7, timeout_s=99)
    # the sleep in the sentinel script carries the delay
    joined = " ".join(seen["argv"])
    assert "sleep 7" in joined
    assert seen["timeout"] == 99


def test_transportprobe_dataclass_flags():
    assert TransportProbe("healthy", "x").healthy
    assert not TransportProbe("healthy", "x").lying
    assert TransportProbe("lying", "x").lying
    assert not TransportProbe("lying", "x").healthy
    assert not TransportProbe("unavailable", "x").healthy
    assert not TransportProbe("unavailable", "x").lying
