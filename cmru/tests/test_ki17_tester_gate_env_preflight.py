"""KI-17: a tester-gate step copied out of cmru.toml and run by hand must fail
ONCE, naming EVERY missing orchestration-injected variable, and point at the
real source of those values — not fail one container spin-up at a time and send
the reader to the wrong file.

Two fixes are exercised here:
  (a) `_missing_orchestration_env` / the `main()` preflight aggregate every
      missing required variable and report them together, before any resolver
      with a side effect (the slice probe, the container launch) runs.
  (b) every message names `cmru.orchestration.toml [env]` (inherited through
      `cmru release`), the place the values actually come from — NOT the
      project's own `cmru.toml [env]`.
"""
from __future__ import annotations

import argparse

import pytest

from cmru import standards, tester_gate


# ---------------------------------------------------------------------------
# _missing_orchestration_env — pure, no container, mirrors resolver precedence
# ---------------------------------------------------------------------------

_ALL_REQUIRED = (
    "CMRU_TESTER_UNIFIED_IMAGE",
    "CMRU_TESTER_MEMORY",
    "CMRU_TESTER_MEMORY_SWAP",
    "CMRU_TESTER_CPUS",
    "CMRU_TESTER_CGROUP_PROBE_IMAGE",
)


def _args(**overrides) -> argparse.Namespace:
    base = dict(
        image=None, memory=None, memory_swap=None, cpus=None,
        cgroup_probe_image=None, dind_image=None, enable_docker=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _clear_env(monkeypatch) -> None:
    for name in (*_ALL_REQUIRED, "CMRU_TESTER_DIND_IMAGE"):
        monkeypatch.delenv(name, raising=False)


def test_reports_every_missing_variable_at_once_not_one_at_a_time(monkeypatch):
    """The core KI-17 (a) behaviour: with nothing set, the report lists ALL
    five required variables in one shot, in declared order."""
    _clear_env(monkeypatch)
    assert tester_gate._missing_orchestration_env(_args()) == list(_ALL_REQUIRED)


def test_the_required_set_is_shared_with_cmru_standards(monkeypatch):
    """Single source of truth: `cmru standards`' static check and this runtime
    preflight validate the identical variable set (mutation guard against the
    two drifting apart)."""
    assert standards.REQUIRED_TESTER_ENV == tester_gate.REQUIRED_TESTER_ENV
    assert tuple(tester_gate.REQUIRED_TESTER_ENV) == _ALL_REQUIRED


def test_environment_satisfies_each_required_variable(monkeypatch):
    _clear_env(monkeypatch)
    for name in _ALL_REQUIRED:
        monkeypatch.setenv(name, "value")
    assert tester_gate._missing_orchestration_env(_args()) == []


def test_explicit_flags_suppress_missing_env_matching_resolver_precedence(monkeypatch):
    """`explicit > env` for every variable: passing the CLI flag resolves it
    even with the environment empty, exactly as the real resolvers do."""
    _clear_env(monkeypatch)
    args = _args(
        image="img", memory="3g", memory_swap="16g", cpus="1.5",
        cgroup_probe_image="debian:test",
    )
    assert tester_gate._missing_orchestration_env(args) == []


def test_a_single_gap_is_reported_alone(monkeypatch):
    _clear_env(monkeypatch)
    for name in _ALL_REQUIRED:
        monkeypatch.setenv(name, "value")
    monkeypatch.delenv("CMRU_TESTER_CPUS", raising=False)
    assert tester_gate._missing_orchestration_env(_args()) == ["CMRU_TESTER_CPUS"]


def test_dind_required_only_under_enable_docker(monkeypatch):
    """`CMRU_TESTER_DIND_IMAGE` is demanded only with --enable-docker, matching
    where `resolve_dind_image` is actually reached; without the flag its
    absence is not a gap, with the flag it is, and it appends after the base
    five."""
    _clear_env(monkeypatch)
    for name in _ALL_REQUIRED:
        monkeypatch.setenv(name, "value")

    assert tester_gate._missing_orchestration_env(_args(enable_docker=False)) == []
    assert tester_gate._missing_orchestration_env(_args(enable_docker=True)) == [
        "CMRU_TESTER_DIND_IMAGE"
    ]
    # An explicit --dind-image resolves it even under --enable-docker.
    assert tester_gate._missing_orchestration_env(
        _args(enable_docker=True, dind_image="dind:test")
    ) == []


def test_blank_or_whitespace_values_count_as_missing(monkeypatch):
    """A variable set to empty/whitespace is not a real value — the preflight
    must treat it as missing, matching every resolver's `.strip()` guard."""
    _clear_env(monkeypatch)
    for name in _ALL_REQUIRED:
        monkeypatch.setenv(name, "value")
    monkeypatch.setenv("CMRU_TESTER_MEMORY", "   ")
    assert tester_gate._missing_orchestration_env(_args()) == ["CMRU_TESTER_MEMORY"]


# ---------------------------------------------------------------------------
# (b) messages name the real source
# ---------------------------------------------------------------------------

def test_aggregate_message_names_the_orchestration_document(monkeypatch, tmp_path):
    """KI-17 (b): the up-front failure sends the reader to
    cmru.orchestration.toml [env] and `cmru release`, and explicitly says it is
    NOT usually the project's own cmru.toml."""
    _clear_env(monkeypatch)
    monkeypatch.setattr(tester_gate.Path, "cwd", staticmethod(lambda: tmp_path))

    with pytest.raises(SystemExit) as excinfo:
        tester_gate.main(["--cwd", "cmru", "--", "true"])

    message = str(excinfo.value)
    assert "cmru.orchestration.toml [env]" in message
    assert "cmru release" in message
    assert "NOT usually" in message
    # Every missing variable is present in the one report.
    for name in _ALL_REQUIRED:
        assert name in message


@pytest.mark.parametrize(
    ("resolver_call", "distinctive_label"),
    [
        (lambda: tester_gate.resolve_memory(None), "no memory limit resolvable"),
        (lambda: tester_gate.resolve_memory_swap(None), "no memory-swap limit resolvable"),
        (lambda: tester_gate.resolve_cpus(None), "no CPU limit resolvable"),
        (lambda: tester_gate.resolve_cgroup_probe_image(None), "no cgroup probe image resolvable"),
        (lambda: tester_gate.resolve_dind_image(None), "no nested Docker image resolvable"),
    ],
)
def test_each_resolver_message_names_the_orchestration_document(
    monkeypatch, resolver_call, distinctive_label
):
    """KI-17 (b): each individual resolver (still reachable for direct callers)
    points at cmru.orchestration.toml, not the project's cmru.toml — AND keeps
    its own distinctive label, so a resolver's wording cannot be garbled into a
    different variable's message while still passing the shared-substring check."""
    _clear_env(monkeypatch)
    with pytest.raises(SystemExit) as excinfo:
        resolver_call()
    message = str(excinfo.value)
    assert distinctive_label in message
    assert "cmru.orchestration.toml [env]" in message
