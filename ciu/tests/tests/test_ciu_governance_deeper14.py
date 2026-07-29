"""Fail-closed IOPS-baseline output contract."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import governance  # noqa: E402


def test_zero_exit_without_fio_output_is_red_and_writes_no_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A successful process exit is insufficient without the promised result file.

    A wrapper or interrupted fio invocation can return zero without creating
    its JSON output.  CIU must report that as a failed measurement, preserve no
    plausible baseline, and clean up its scratch paths rather than publishing a
    cap derived from absent data.
    """
    output = tmp_path / "io-baseline.env"
    monkeypatch.setattr(governance.shutil, "which", lambda _name: "/usr/bin/fio")
    monkeypatch.setattr(governance, "select_fio_engine", lambda _fio: ("libaio", None))

    def zero_without_output(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(governance.subprocess, "run", zero_without_output)

    assert governance.run_iops_baseline(output, force=True) == 1
    assert "fio wrote no output file" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []
