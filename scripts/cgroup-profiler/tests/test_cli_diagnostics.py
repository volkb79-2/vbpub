"""Focused tests for the dependency-free cgprofile CLI surface."""

from __future__ import annotations

import subprocess
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cgprofile as cg


def test_top_level_version_is_one_identity_line(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cg.build_parser().parse_args(["--version"])
    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert captured.out == "cgprofile 0.1.0\n"
    assert captured.err == ""


def test_shell_entrypoint_version_probe_is_clean():
    proc = subprocess.run(
        [str(Path(cg.HERE) / "cgprofile"), "--version"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0
    assert proc.stdout == "cgprofile 0.1.0\n"
    assert proc.stderr == ""


def test_nested_help_starts_with_the_headline(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cg.build_parser().parse_args(["mark", "--help"])
    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert captured.err == ""
    assert captured.out.splitlines()[0] == cg.cli_headline()


def test_nested_missing_required_argument_starts_with_the_headline(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cg.build_parser().parse_args(["mark"])
    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert captured.out == ""
    assert captured.err.splitlines()[0] == cg.cli_headline()
