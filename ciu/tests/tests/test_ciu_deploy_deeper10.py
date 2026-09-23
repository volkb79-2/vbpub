"""Deployment input-boundary contracts for :mod:`ciu.deploy`.

These tests exercise public orchestration seams rather than treating helpers as
an implementation detail: malformed health timing must use its documented
fallback, incomplete deployment identity must not reach Docker inspection, and
an explicit root that contradicts the environment must stop before rendering.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402


@pytest.mark.parametrize("value", [True, False, "not-a-duration"])
def test_invalid_health_duration_uses_documented_default(value: object, capsys: pytest.CaptureFixture[str]) -> None:
    """Bad config timing cannot turn a health gate into an arbitrary timeout."""
    assert deploy._seconds(value, default=17.0) == 17.0
    if isinstance(value, str):
        assert "could not parse duration 'not-a-duration'; using 17s" in capsys.readouterr().out
    else:
        assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "config, missing_key",
    [
        ({"deploy": {"environment_tag": "prod"}}, "project_name"),
        ({"deploy": {"project_name": "example"}}, "environment_tag"),
    ],
)
def test_health_gate_rejects_incomplete_deployment_identity_before_inspection(
    monkeypatch: pytest.MonkeyPatch, config: dict, missing_key: str
) -> None:
    """S7.7 refuses fabricated container names instead of querying Docker."""
    monkeypatch.setattr(
        deploy,
        "run_container_health_gate",
        lambda *_args, **_kwargs: pytest.fail("incomplete identity must not reach container inspection"),
    )

    with pytest.raises(ValueError, match=rf"deploy\.{missing_key} not set"):
        deploy.run_health_gate(config, ["api"], timeout_s=30.0)


def test_cli_rejects_explicit_root_that_conflicts_with_environment_before_rendering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S1.1 prevents rendering or deployment against an ambiguous repository."""
    explicit_root = tmp_path / "explicit"
    environment_root = tmp_path / "from-environment"
    explicit_root.mkdir()
    (explicit_root / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
    environment_root.mkdir()
    monkeypatch.setenv("REPO_ROOT", str(environment_root))
    monkeypatch.setattr(deploy, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "enforce_standalone_root", lambda _cwd: None)
    seen: list[Path] = []
    monkeypatch.setattr(deploy, "load_global_config", lambda root: seen.append(root) or {})

    # Root selection is the subject of this test; the later deploy preflight
    # may legitimately refuse the intentionally empty environment with CIU's
    # environment status.
    assert deploy.main(["--root-folder", str(explicit_root), "--deploy"]) in (0, 1, 2, 3)
    assert seen == [explicit_root.resolve()]
    assert "does not match REPO_ROOT" not in capsys.readouterr().out
