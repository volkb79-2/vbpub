#!/usr/bin/env python3
"""CIU v2 reset_service() tests (S6.4 / S4.25).

v2 signature:
    reset_service(config, stack_dir, *, compose_file=..., remove_secrets=False,
                  assume_yes=..., specs=None, repo_root=None)

- Uses procutil.run_cmd (no subprocess.run, no sys.exit; raises RuntimeError).
- vol-* are resolved against the STACK DIR, never the process cwd (B14): the
  tests chdir somewhere else and still expect the stack's vol-* removed.
- Secret store files are KEPT unless remove_secrets (S4.25).
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ciu import engine  # noqa: E402
from ciu.engine import reset_service  # noqa: E402


def _base_config() -> dict:
    return {"deploy": {"project_name": "test-project", "labels": {"prefix": "dstdns"}}}


def _ok(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


class TestResetServiceDockerComposeDown:
    def test_runs_docker_compose_down(self, tmp_path):
        config = _base_config()
        with patch.object(engine.procutil, "run_cmd", return_value=_ok()) as mock_run:
            reset_service(config, tmp_path, assume_yes=True)
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("compose" in c and "down" in c for c in calls)

    def test_down_includes_overlay_when_present(self, tmp_path):
        config = _base_config()
        overlay = tmp_path / ".ciu" / "ciu.compose.overlay.yml"
        overlay.parent.mkdir(parents=True)
        overlay.write_text("secrets: {}\n")
        with patch.object(engine.procutil, "run_cmd", return_value=_ok()) as mock_run:
            reset_service(config, tmp_path, assume_yes=True)
            first_cmd = mock_run.call_args_list[0].args[0]
            assert ".ciu/ciu.compose.overlay.yml" in first_cmd


class TestResetServiceVolumeDirectories:
    def test_removes_vol_directories_in_stack_dir_not_cwd(self, tmp_path, monkeypatch):
        """B14: vol-* removed from the STACK dir, even when cwd is elsewhere."""
        stack = tmp_path / "stack"
        stack.mkdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        (stack / "vol-postgres-data").mkdir()
        (stack / "vol-redis-data").mkdir()
        (stack / "not-a-volume").mkdir()
        # A decoy vol-* in cwd must NOT be touched.
        (elsewhere / "vol-decoy").mkdir()

        config = _base_config()
        with patch.object(engine.procutil, "run_cmd", return_value=_ok()):
            reset_service(config, stack, assume_yes=True)

        assert not (stack / "vol-postgres-data").exists()
        assert not (stack / "vol-redis-data").exists()
        assert (stack / "not-a-volume").exists()
        assert (elsewhere / "vol-decoy").exists()  # cwd decoy untouched (B14)


class TestResetServiceConfigFiles:
    def test_removes_rendered_files_and_overlay(self, tmp_path):
        config = _base_config()
        (tmp_path / "ciu.compose.yml").touch()
        (tmp_path / "ciu.toml").touch()
        overlay = tmp_path / ".ciu" / "ciu.compose.overlay.yml"
        overlay.parent.mkdir(parents=True)
        overlay.touch()
        rendered = tmp_path / ".ciu" / "rendered"
        rendered.mkdir(parents=True)
        (rendered / "svc").mkdir()

        with patch.object(engine.procutil, "run_cmd", return_value=_ok()):
            reset_service(config, tmp_path, assume_yes=True)

        assert not (tmp_path / "ciu.compose.yml").exists()
        assert not (tmp_path / "ciu.toml").exists()
        assert not overlay.exists()
        assert not rendered.exists()


class TestResetServiceSecrets:
    def test_secret_store_kept_by_default(self, tmp_path):
        config = _base_config()
        secrets_dir = tmp_path / ".ciu" / "secrets"
        secrets_dir.mkdir(parents=True)
        (secrets_dir / "redis_password").write_text("x")

        with patch.object(engine.procutil, "run_cmd", return_value=_ok()):
            reset_service(config, tmp_path, assume_yes=True)

        assert (secrets_dir / "redis_password").exists()  # kept (S4.25)

    def test_secret_store_removed_with_remove_secrets(self, tmp_path):
        config = _base_config()
        secrets_dir = tmp_path / ".ciu" / "secrets"
        secrets_dir.mkdir(parents=True)
        (secrets_dir / "redis_password").write_text("x")

        with patch.object(engine.procutil, "run_cmd", return_value=_ok()):
            reset_service(config, tmp_path, assume_yes=True, remove_secrets=True)

        assert not secrets_dir.exists()  # removed (S4.25 --secrets)


class TestResetServiceOrphanedContainers:
    def test_uses_anchored_component_label(self, tmp_path):
        config = _base_config()

        def run(cmd, **kw):
            if "ps" in cmd:
                return _ok(stdout="orphan-1\norphan-2\n")
            return _ok()

        with patch.object(engine.procutil, "run_cmd", side_effect=run) as mock_run:
            reset_service(config, tmp_path, assume_yes=True)

        ps_calls = [c.args[0] for c in mock_run.call_args_list if "ps" in c.args[0]]
        assert ps_calls, "expected a docker ps call"
        # Anchored label equality: <prefix>.component=<service> (S6.4).
        assert any(f"label=dstdns.component={tmp_path.name}" in c for c in ps_calls)


class TestResetServiceValidation:
    def test_requires_project_name(self, tmp_path):
        with pytest.raises(ValueError, match="deploy.project_name"):
            reset_service({}, tmp_path, assume_yes=True)

    def test_requires_label_prefix(self, tmp_path):
        config = {"deploy": {"project_name": "test"}}
        with pytest.raises(ValueError, match="deploy.labels.prefix"):
            reset_service(config, tmp_path, assume_yes=True)
