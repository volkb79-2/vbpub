"""Dependency diagnosis contracts for the CIU engine."""

from __future__ import annotations

import builtins
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402


def _block_import(blocked: str):
    real_import = builtins.__import__

    def importing(name, *args, **kwargs):
        if name == blocked:
            raise ImportError(f"No module named '{blocked}'", name=blocked)
        return real_import(name, *args, **kwargs)

    return importing


def _successful_probe(*args, **kwargs):
    return SimpleNamespace(returncode=0)


def test_missing_tomllib_reports_python_standard_library_remediation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("SKIP_DEPENDENCY_CHECK", raising=False)
    monkeypatch.setattr(engine.procutil, "run_cmd", _successful_probe)
    monkeypatch.setattr(builtins, "__import__", _block_import("tomllib"))

    with pytest.raises(engine.DependencyError):
        engine.check_runtime_dependencies()

    output = capsys.readouterr().out
    assert "Python standard library" in output
    assert "Upgrade Python to 3.11+" in output


def test_missing_tomli_w_reports_toml_writer_remediation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("SKIP_DEPENDENCY_CHECK", raising=False)
    monkeypatch.setattr(engine.procutil, "run_cmd", _successful_probe)
    monkeypatch.setattr(builtins, "__import__", _block_import("tomli_w"))

    with pytest.raises(engine.DependencyError):
        engine.check_runtime_dependencies()

    output = capsys.readouterr().out
    assert "TOML writer library" in output
    assert "pip install tomli_w" in output
