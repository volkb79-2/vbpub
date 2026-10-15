"""Focused reset safety outcome tests for P60."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ciu import engine  # noqa: E402
from ciu.engine import reset_service  # noqa: E402


def _write_identity_facts(root):
    """CIU-75: reset's config-less down path derives its compose project from
    the checkout's generated `[ciu.instance.generated]` overlay table, not its
    legacy `ciu.env` export."""
    from ciu.workspace_env import GENERATED_FACTS_KEYS, write_generated_facts

    facts = {key: "" for key in GENERATED_FACTS_KEYS}
    facts["repo_name"] = "dstdns"
    facts["instance_id"] = "abc123"
    write_generated_facts(root, facts)



def _config() -> dict:
    return {"deploy": {"project_name": "test-project", "labels": {"prefix": "dstdns"}}}


def test_compose_down_failure_continues_only_after_empty_project_inspection(
    tmp_path, capsys, monkeypatch,
):
    _write_identity_facts(tmp_path)
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    volume = stack_dir / "vol-data"
    volume.mkdir()
    generated = stack_dir / "ciu.compose.yml"
    generated.touch()

    def run_cmd(command, **kwargs):
        if "compose" in command and "down" in command:
            return SimpleNamespace(returncode=1, stdout="", stderr="stale project")
        if command[:2] == ["docker", "ps"] and not any(
            token.startswith("label=dstdns.component=") for token in command
        ):
            assert "--no-trunc" in command
            assert "label=com.docker.compose.project=test-project" in command
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(engine, "to_physical_path", lambda pth, **k: pth)
    with patch.object(engine.procutil, "run_cmd", side_effect=run_cmd):
        reset_service(_config(), stack_dir, assume_yes=True, repo_root=tmp_path)

    output = capsys.readouterr().out
    assert "docker compose down failed" in output
    assert "No containers remain for compose project 'test-project'" in output
    assert not volume.exists()
    assert not generated.exists()
    assert "Reset complete for service: stack" in output


@pytest.mark.parametrize(
    ("inspection_result", "message"),
    [
        (SimpleNamespace(returncode=0, stdout="container-123\n", stderr=""), "containers remain"),
        (SimpleNamespace(returncode=1, stdout="", stderr="daemon unavailable"), "inspection also failed"),
    ],
)
def test_compose_down_failure_preserves_host_data_when_inspection_is_not_empty_or_fails(
    tmp_path, monkeypatch, inspection_result, message,
):
    _write_identity_facts(tmp_path)
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    volume = stack_dir / "vol-data"
    volume.mkdir()
    generated = stack_dir / "ciu.compose.yml"
    generated.touch()

    def run_cmd(command, **kwargs):
        if "compose" in command and "down" in command:
            return SimpleNamespace(returncode=1, stdout="", stderr="stale project")
        if command[:2] == ["docker", "ps"] and not any(
            token.startswith("label=dstdns.component=") for token in command
        ):
            assert "label=com.docker.compose.project=test-project" in command
            return inspection_result
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(engine, "to_physical_path", lambda pth, **k: pth)
    with patch.object(engine.procutil, "run_cmd", side_effect=run_cmd):
        with pytest.raises(RuntimeError, match=message) as exc:
            reset_service(_config(), stack_dir, assume_yes=True, repo_root=tmp_path)

    assert str(stack_dir) in str(exc.value)
    assert volume.is_dir()
    assert generated.is_file()


def test_reset_refuses_symlinked_volume_without_deleting_its_target(tmp_path, monkeypatch):
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    target = tmp_path / "operator-data"
    target.mkdir()
    (target / "keep.txt").write_text("preserve")
    volume_link = stack_dir / "vol-data"
    volume_link.symlink_to(target, target_is_directory=True)
    generated = stack_dir / "ciu.compose.yml"
    generated.touch()

    with (
        patch.object(engine.procutil, "run_cmd", return_value=SimpleNamespace(
            returncode=0, stdout="", stderr=""
        )),
        patch.object(engine, "_rmtree_with_fallback") as remove_volume,
    ):
        with pytest.raises(RuntimeError, match="symlinked volume path"):
            reset_service(_config(), stack_dir, assume_yes=True, repo_root=tmp_path)

    remove_volume.assert_not_called()
    assert volume_link.is_symlink()
    assert (target / "keep.txt").read_text() == "preserve"
    assert generated.is_file()


def test_reset_with_secrets_reports_materialized_stores_and_removes_fallback(tmp_path, capsys):
    stack_dir = tmp_path / "stack"
    stack_dir.mkdir()
    fallback_store = stack_dir / ".ciu" / "secrets"
    fallback_store.mkdir(parents=True)
    (fallback_store / "stale").write_text("secret")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _write_identity_facts(repo_root)
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
