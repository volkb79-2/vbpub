"""Focused reset safety outcome tests for P60."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ciu import engine  # noqa: E402
from ciu.engine import reset_service  # noqa: E402


def _config() -> dict:
    return {"deploy": {"project_name": "test-project", "labels": {"prefix": "dstdns"}}}


def test_compose_down_warning_does_not_abort_remaining_cleanup(tmp_path, capsys, monkeypatch):
    (tmp_path / "ciu.env").write_text(
        'export REPO_NAME="dstdns"\nexport INSTANCE_ID="abc123"\n', encoding="utf-8"
    )
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    volume = stack_dir / "vol-data"
    volume.mkdir()
    generated = stack_dir / "ciu.compose.yml"
    generated.touch()

    def run_cmd(command, **kwargs):
        if "compose" in command and "down" in command:
            return SimpleNamespace(returncode=1, stdout="", stderr="stale project")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(engine, "to_physical_path", lambda pth, **k: pth)
    with patch.object(engine.procutil, "run_cmd", side_effect=run_cmd):
        reset_service(_config(), stack_dir, assume_yes=True, repo_root=tmp_path)

    output = capsys.readouterr().out
    assert "docker compose down failed" in output
    assert not volume.exists()
    assert not generated.exists()
    assert "Reset complete for service: stack" in output


def test_reset_with_secrets_reports_materialized_stores_and_removes_fallback(tmp_path, capsys):
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    fallback_store = stack_dir / ".ciu" / "secrets"
    fallback_store.mkdir(parents=True)
    (fallback_store / "stale").write_text("secret")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "ciu.env").write_text(
        'export REPO_NAME="dstdns"\nexport INSTANCE_ID="abc123"\n', encoding="utf-8"
    )
    stores = [
        repo_root / ".ciu" / "secrets" / "project-token",
        stack_dir / ".ciu" / "secrets" / "stack-token",
    ]

    specs = [object()]
    with (
        patch.object(
            engine.procutil,
            "run_cmd",
            return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
        ),
        patch.object(engine.secret_materialize, "reset_secrets", return_value=stores) as reset,
    ):
        reset_service(
            _config(),
            stack_dir,
            assume_yes=True,
            remove_secrets=True,
            specs=specs,
            repo_root=repo_root,
        )

    output = capsys.readouterr().out
    reset.assert_called_once_with(stack_dir.resolve(), repo_root, specs)
    for store in stores:
        assert f"Removed secret store: {store}" in output
    assert "Removed secret store dir:" in output
    assert not fallback_store.exists()
