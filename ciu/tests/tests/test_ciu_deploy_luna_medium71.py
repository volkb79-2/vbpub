"""Environment and error-boundary contracts for :func:`ciu.deploy._run_stack`."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from ciu import engine  # noqa: E402


def _run(stack: Path, *, env: dict[str, str]) -> bool:
    return deploy._run_stack(
        stack,
        env=env,
        compose_profiles=[],
        dry_run=True,
        update_cert_permission=False,
    )


def test_missing_stack_reports_path_and_skips_both_engine_entrypoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(deploy.engine, "main_execution", lambda **_: calls.append("native"))
    monkeypatch.setattr(deploy.engine, "run_shipped", lambda **_: calls.append("shipped"))
    missing = tmp_path / "missing-stack"

    assert _run(missing, env={}) is False
    assert calls == []
    assert f"stack directory not found: {missing}" in capsys.readouterr().out


def test_native_stack_overlays_environment_and_passes_native_boundary_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = tmp_path / "stack"
    stack.mkdir()
    monkeypatch.setenv("CIU_LUNA_CHANGED", "before")
    monkeypatch.setenv("CIU_LUNA_SAME", "same")
    observed: dict[str, object] = {}

    def fake_main_execution(**kwargs: object) -> dict[str, str]:
        observed.update(kwargs)
        observed["changed_during"] = os.environ["CIU_LUNA_CHANGED"]
        observed["absent_during"] = os.environ["CIU_LUNA_ABSENT"]
        observed["same_during"] = os.environ["CIU_LUNA_SAME"]
        return {"status": "success"}

    monkeypatch.setattr(deploy.engine, "main_execution", fake_main_execution)

    assert _run(
        stack,
        env={
            "CIU_LUNA_CHANGED": "during",
            "CIU_LUNA_ABSENT": "temporary",
            "CIU_LUNA_SAME": "same",
        },
    ) is True
    assert observed["working_dir"] == stack
    assert observed["dry_run"] is True
    assert observed["yes"] is True
    assert observed["update_cert_permission"] is False
    assert observed["compose_profiles"] is None
    assert observed["changed_during"] == "during"
    assert observed["absent_during"] == "temporary"
    assert observed["same_during"] == "same"
    assert os.environ["CIU_LUNA_CHANGED"] == "before"
    assert os.environ["CIU_LUNA_SAME"] == "same"
    assert "CIU_LUNA_ABSENT" not in os.environ


def test_compose_error_reports_message_and_restores_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stack = tmp_path / "stack"
    stack.mkdir()
    monkeypatch.setenv("CIU_LUNA_CHANGED", "before")

    def fail(**_: object) -> dict[str, str]:
        raise engine.ComposeError("compose failed for luna")

    monkeypatch.setattr(deploy.engine, "main_execution", fail)

    assert _run(stack, env={"CIU_LUNA_CHANGED": "during", "CIU_LUNA_ABSENT": "temporary"}) is False
    assert "compose failed for luna" in capsys.readouterr().out
    assert os.environ["CIU_LUNA_CHANGED"] == "before"
    assert "CIU_LUNA_ABSENT" not in os.environ


def test_dependency_error_propagates_and_restores_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = tmp_path / "stack"
    stack.mkdir()
    monkeypatch.setenv("CIU_LUNA_CHANGED", "before")

    def fail(**_: object) -> dict[str, str]:
        raise engine.DependencyError("docker unavailable")

    monkeypatch.setattr(deploy.engine, "main_execution", fail)

    with pytest.raises(engine.DependencyError, match="docker unavailable"):
        _run(stack, env={"CIU_LUNA_CHANGED": "during", "CIU_LUNA_ABSENT": "temporary"})
    assert os.environ["CIU_LUNA_CHANGED"] == "before"
    assert "CIU_LUNA_ABSENT" not in os.environ
