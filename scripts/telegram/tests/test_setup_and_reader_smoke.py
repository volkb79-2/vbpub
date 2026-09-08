"""Smoke tests for telegram_setup.py / telegram_debug_reader.py.

These are large, human-operated setup/debug wizards (interactive MTProto
login, forum creation) - deep functional mocking of Telethon flows is out of
scope here (see scripts/telegram/run-gate.toml's rationale / the approved
plan). This just catches syntax errors and import breakage from the move:
both must at least import cleanly and print --help without crashing.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TELEGRAM_DIR = Path(__file__).resolve().parent.parent


def test_telegram_setup_compiles():
    subprocess.run(
        [sys.executable, "-m", "py_compile", str(TELEGRAM_DIR / "telegram_setup.py")],
        check=True, capture_output=True, text=True,
    )


def test_telegram_setup_help_runs_without_crashing():
    result = subprocess.run(
        [sys.executable, str(TELEGRAM_DIR / "telegram_setup.py"), "--help"],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0
    assert "--setup-forum" in result.stdout


def test_telegram_setup_default_env_file_points_at_netcup():
    result = subprocess.run(
        [sys.executable, str(TELEGRAM_DIR / "telegram_setup.py"), "--help"],
        capture_output=True, text=True, timeout=15,
    )
    assert "scripts/netcup/.env" in result.stdout


def test_telegram_debug_reader_compiles():
    subprocess.run(
        [sys.executable, "-m", "py_compile", str(TELEGRAM_DIR / "telegram_debug_reader.py")],
        check=True, capture_output=True, text=True,
    )


def test_telegram_debug_reader_help_runs_without_crashing():
    result = subprocess.run(
        [sys.executable, str(TELEGRAM_DIR / "telegram_debug_reader.py"), "--help"],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0
