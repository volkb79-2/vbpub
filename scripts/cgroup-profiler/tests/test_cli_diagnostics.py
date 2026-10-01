"""Focused tests for the dependency-free cgprofile CLI surface."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cgprofile as cg


def test_top_level_version_is_one_identity_line(capsys, monkeypatch):
    monkeypatch.delenv("CGPROFILE_VERSION", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        cg.build_parser().parse_args(["--version"])
    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert captured.out == "cgprofile 0.1.0\n"
    assert captured.err == ""


def test_shell_entrypoint_version_probe_is_clean(monkeypatch):
    monkeypatch.delenv("CGPROFILE_VERSION", raising=False)
    proc = subprocess.run(
        [str(Path(cg.HERE) / "cgprofile"), "--version"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0
    assert proc.stdout == "cgprofile 0.1.0\n"
    assert proc.stderr == ""


def test_shell_entrypoint_uses_embedded_release_version():
    env = dict(os.environ, CGPROFILE_VERSION="1.0.0")
    proc = subprocess.run(
        [str(Path(cg.HERE) / "cgprofile"), "--version"],
        capture_output=True, text=True, check=False, env=env,
    )
    assert proc.returncode == 0
    assert proc.stdout == "cgprofile 1.0.0\n"
    assert proc.stderr == ""


def test_nested_help_starts_with_the_headline(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cg.build_parser().parse_args(["mark", "--help"])
    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert captured.err == ""
    assert captured.out.splitlines()[0] == cg.cli_headline()


def test_usage_starts_with_the_headline():
    assert cg.build_parser().format_usage().splitlines()[0] == cg.cli_headline()


def test_nested_missing_required_argument_starts_with_the_headline(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cg.build_parser().parse_args(["mark"])
    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert captured.out == ""
    assert captured.err.splitlines()[0] == cg.cli_headline()
