"""Boundary contracts for the Docker inspect-state helper."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402


def test_inspect_state_missing_docker_returns_none_and_uses_exact_boundary(monkeypatch):
    calls = []

    def docker(argv, *, check):
        calls.append((argv, check))
        raise FileNotFoundError

    monkeypatch.setattr(deploy.procutil, "docker", docker)

    assert deploy._inspect_state("project-prod-cache") is None
    assert calls == [
        (["inspect", "--format", "{{json .State}}", "project-prod-cache"], False)
    ]


def test_inspect_state_nonzero_result_returns_none(monkeypatch):
    monkeypatch.setattr(
        deploy.procutil,
        "docker",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout='{"Status":"running"}'),
    )

    assert deploy._inspect_state("project-prod-cache") is None


@pytest.mark.parametrize("stdout", ["", "   \n\t"])
def test_inspect_state_empty_stdout_returns_none(monkeypatch, stdout):
    monkeypatch.setattr(
        deploy.procutil,
        "docker",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=stdout),
    )

    assert deploy._inspect_state("project-prod-cache") is None


def test_inspect_state_valid_json_returns_decoded_mapping(monkeypatch):
    monkeypatch.setattr(
        deploy.procutil,
        "docker",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout='  {"Status": "running", "Health": null}\n'
        ),
    )

    assert deploy._inspect_state("project-prod-cache") == {
        "Status": "running",
        "Health": None,
    }


def test_inspect_state_malformed_json_returns_none(monkeypatch):
    monkeypatch.setattr(
        deploy.procutil,
        "docker",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="not-json"),
    )

    assert deploy._inspect_state("project-prod-cache") is None
