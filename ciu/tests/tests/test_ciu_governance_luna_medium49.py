"""Failure contracts for the CIU IOPS baseline measurement."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import governance  # noqa: E402


_FIO_JSON = '{"jobs": [{"read": {"iops": 1234}}]}'


def _wire_fio(monkeypatch: pytest.MonkeyPatch, *, run):
    monkeypatch.setattr(governance.shutil, "which", lambda _name: "/usr/bin/fio")
    monkeypatch.setattr(governance, "select_fio_engine", lambda _fio: ("libaio", None))
    monkeypatch.setattr(governance.subprocess, "run", run)


def test_fio_timeout_is_red_and_cleans_both_scratch_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "io-baseline.env"
    test_file = tmp_path / governance._FIO_TESTFILE_NAME
    fio_output = tmp_path / governance._FIO_OUTPUT_NAME

    def timed_out(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        test_file.write_bytes(b"scratch")
        fio_output.write_text(_FIO_JSON, encoding="utf-8")
        raise subprocess.TimeoutExpired(argv, 1)

    _wire_fio(monkeypatch, run=timed_out)

    assert governance.run_iops_baseline(output, runtime_s=1, force=True) == 1
    assert "fio timed out" in capsys.readouterr().out
    assert not output.exists()
    assert not test_file.exists()
    assert not fio_output.exists()


def test_baseline_write_failure_is_red_with_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "io-baseline.env"

    def successful_fio(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        fio_output = Path(next(arg.split("=", 1)[1] for arg in argv if arg.startswith("--output=")))
        fio_output.write_text(_FIO_JSON, encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    _wire_fio(monkeypatch, run=successful_fio)

    def fail_write(_path: Path, _riops: int, _engine: str) -> None:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(governance, "_write_baseline_file", fail_write)

    assert governance.run_iops_baseline(output, force=True) == 1
    captured = capsys.readouterr().out
    assert "could not write" in captured
    assert str(output) in captured
    assert "read-only filesystem" in captured
    assert not output.exists()
    assert list(tmp_path.iterdir()) == []
