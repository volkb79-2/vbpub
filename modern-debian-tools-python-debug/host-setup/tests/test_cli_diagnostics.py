"""The shipped MDT parser roots identify their runtime version source."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_mdt_parser_help_uses_the_existing_image_version_environment():
    env = os.environ.copy()
    env["MDT_IMAGE_VERSION"] = "image-20260918"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate-oci-layout.py"), "--help"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines()[0] == (
        "MDT image-20260918 — modern Debian tools and Python debug"
    )


def test_mdt_host_parser_argument_errors_use_the_same_headline():
    env = os.environ.copy()
    env["MDT_VERSION"] = "host-20260918"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "host-setup" / "scripts" / "mdt-io-baseline.py"), "--bad"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 2
    assert proc.stderr.splitlines()[0] == (
        "MDT host-20260918 — modern Debian tools and Python debug"
    )


@pytest.mark.parametrize(
    "script",
    [
        "build-push.py",
        "check-mcr-devcontainer-tags.py",
        "host-setup/mdt-host-setup-wizard.py",
        "host-setup/scripts/mdt-io-baseline.py",
        "scripts/mdt_buildkit_builder.py",
    ],
)
def test_user_facing_mdt_entrypoints_support_top_level_version(script):
    env = os.environ.copy()
    env["MDT_VERSION"] = "mdt-test"
    proc = subprocess.run(
        [sys.executable, str(ROOT / script), "--version"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "MDT mdt-test\n"
    assert proc.stderr == ""
