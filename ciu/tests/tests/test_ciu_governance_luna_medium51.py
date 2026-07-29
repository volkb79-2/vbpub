"""Focused regression coverage for empty device autodetection output."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import governance as gov  # noqa: E402


def test_findmnt_whitespace_only_output_returns_no_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=" \n\t ", stderr="")

    monkeypatch.setattr(gov.subprocess, "run", fake_run)
    assert gov.detect_device() == ""
