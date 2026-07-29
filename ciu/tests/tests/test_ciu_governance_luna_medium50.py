"""Focused tests for CIU IOPS baseline fallback behavior."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import governance as gov  # noqa: E402


def test_pick_test_dir_falls_back_to_var_tmp_when_parent_cannot_be_created(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def refuse_mkdir(self: Path, *args, **kwargs) -> None:
        raise OSError("controlled mkdir failure")

    monkeypatch.setattr(Path, "mkdir", refuse_mkdir)

    assert gov._pick_test_dir(tmp_path / "unavailable" / "io-baseline.env") == Path("/var/tmp")


def test_run_iops_baseline_publishes_result_and_engine_fallback_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_path = tmp_path / "io-baseline.env"
    fio_json = '{"jobs": [{"read": {"iops": 1235}}]}'
    warning = (
        "[WARN] fio has no libaio engine — falling back to psync. "
        "psync caps iodepth at 1"
    )
    monkeypatch.setattr(gov.shutil, "which", lambda name: "/usr/bin/fio")
    monkeypatch.setattr(gov, "select_fio_engine", lambda fio_bin: ("psync", warning))

    def fake_run(cmd, **kwargs):
        output_arg = next(arg for arg in cmd if arg.startswith("--output="))
        Path(output_arg.split("=", 1)[1]).write_text(fio_json, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(gov.subprocess, "run", fake_run)

    assert gov.run_iops_baseline(output_path, force=True) == 0
    published = output_path.read_text(encoding="utf-8")
    assert "RIOPS_MAX=1235" in published
    assert "RIOPS_ENGINE=psync" in published
    assert warning in capsys.readouterr().out
