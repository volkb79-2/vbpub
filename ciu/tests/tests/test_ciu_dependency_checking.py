"""
CIU runtime dependency checks.
"""

import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ciu.engine import check_runtime_dependencies, DependencyError  # noqa: E402


class TestDependencyChecking:
    def test_skips_check_when_env_var_set(self):
        os.environ["SKIP_DEPENDENCY_CHECK"] = "1"
        check_runtime_dependencies()
        os.environ.pop("SKIP_DEPENDENCY_CHECK", None)

    def test_checks_docker_availability(self):
        os.environ.pop("SKIP_DEPENDENCY_CHECK", None)

        with patch("subprocess.run", side_effect=FileNotFoundError()):
            with pytest.raises(DependencyError):
                check_runtime_dependencies()

    def test_checks_docker_compose_availability(self):
        os.environ.pop("SKIP_DEPENDENCY_CHECK", None)

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                Mock(returncode=0),
                FileNotFoundError(),
            ]

            with pytest.raises(DependencyError):
                check_runtime_dependencies()

    def test_warns_on_missing_hvac(self):
        os.environ.pop("SKIP_DEPENDENCY_CHECK", None)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0)

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

    def test_jinja2_missing_is_fatal(self):
        os.environ.pop("SKIP_DEPENDENCY_CHECK", None)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0)

            import builtins
            real_import = builtins.__import__

            def mock_import(name, *args, **kwargs):
                if name == "jinja2":
                    raise ImportError(f"No module named '{name}'")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                with pytest.raises(DependencyError):
                    check_runtime_dependencies()
