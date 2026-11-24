"""
CIU runtime dependency checks.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ciu.engine import check_runtime_dependencies, DependencyError  # noqa: E402


def _install_fake_docker(tmp_path: Path, monkeypatch, body: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "docker"
    executable.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))


class TestDependencyChecking:
    def test_skips_check_when_env_var_set(self, monkeypatch):
        monkeypatch.setenv("SKIP_DEPENDENCY_CHECK", "1")
        check_runtime_dependencies()

    def test_checks_docker_availability(self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("SKIP_DEPENDENCY_CHECK", raising=False)
        _install_fake_docker(tmp_path, monkeypatch, 'exit 127')

        with pytest.raises(DependencyError):
            check_runtime_dependencies()
        assert "Docker Engine" in capsys.readouterr().out

    def test_checks_docker_compose_availability(self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("SKIP_DEPENDENCY_CHECK", raising=False)
        _install_fake_docker(
            tmp_path,
            monkeypatch,
            'if [ "$1" = "--version" ]; then exit 0; fi\nexit 1',
        )

        with pytest.raises(DependencyError):
            check_runtime_dependencies()
        assert "Docker Compose v2" in capsys.readouterr().out

    def test_warns_on_missing_hvac(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SKIP_DEPENDENCY_CHECK", raising=False)
        _install_fake_docker(tmp_path, monkeypatch, "exit 0")

        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "hvac":
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with patch("builtins.print") as mock_print:
                check_runtime_dependencies()

            printed = [str(call[0][0]) for call in mock_print.call_args_list]
            assert any("hvac" in msg.lower() for msg in printed)

    def test_jinja2_missing_is_fatal(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SKIP_DEPENDENCY_CHECK", raising=False)
        _install_fake_docker(tmp_path, monkeypatch, "exit 0")

        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "jinja2":
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(DependencyError):
                check_runtime_dependencies()
