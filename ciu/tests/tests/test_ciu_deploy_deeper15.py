"""Missing-stack deployment boundary for :mod:`ciu.deploy`."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402


def test_missing_stack_directory_fails_deploy_before_engine_and_later_phase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S7.3 never sends an absent stack to Compose or proceeds to a later stack."""
    profile = Profile(
        name=None,
        phase_keys=None,
        config={
            "deploy": {
                "project_name": "ciu",
                "environment_tag": "test",
                "phases": {
                    "phase_1": {"services": [{"path": "infra/missing", "name": "Missing"}]},
                    "phase_2": {"services": [{"path": "applications/later", "name": "Later"}]},
                },
            }
        },
    )
    monkeypatch.setattr(
        deploy.engine,
        "main_execution",
        lambda **_kwargs: pytest.fail("missing stack directory must not invoke Compose engine"),
    )

    assert deploy.action_deploy(
        tmp_path,
        profile,
        deploy.build_selection(profile),
        dry_run=False,
        ignore_errors=False,
        health_after_phase=False,
        update_cert_permission=False,
    ) == 1

    output = capsys.readouterr().out
    assert f"stack directory not found: {tmp_path / 'infra/missing'}" in output
    assert "SKIP phase phase_2" in output
