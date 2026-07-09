"""Tests for groop acceptance smoke harness (P33).

Each test invokes ``python -m groop.acceptance``` as a subprocess to verify
the module-level entry point.  Direct unit tests of ``run_smoke()`` cover
the core logic without subprocess overhead.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import fixture_root, ROOT, SRC

# Paths used in multiple tests
FIXTURE_CGROUP = fixture_root() / "cgroupfs" / "gstammtisch"
FIXTURE_FRAME = fixture_root() / "frames" / "gstammtisch-once.jsonl"
NONEXISTENT = fixture_root() / "frames" / "nonexistent.jsonl"

PYTHON = sys.executable
SMOKE_ARGS = [str(PYTHON), "-m", "groop.acceptance", "smoke"]
ENV = {**{k: v for k, v in os.environ.items()}, "PYTHONPATH": str(SRC)}


def _run_smoke(*extra_args: str, **kwargs) -> subprocess.CompletedProcess:
    """Run the smoke harness as a subprocess and return the result."""
    cmd = [*SMOKE_ARGS, *extra_args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=ENV,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Unit tests for run_smoke()
# ---------------------------------------------------------------------------


def test_run_smoke_json_fixture_root() -> None:
    """JSON smoke with the existing fixture cgroup root produces ok=true."""
    from groop.acceptance import run_smoke

    result = run_smoke(cgroup_root=FIXTURE_CGROUP)
    assert result.ok is True
    assert result.version == "0.1.0"
    assert len(result.checks) == 4  # collect, serialize, source_labels, replay
    assert result.checks[0].name == "collect"
    assert result.checks[0].ok is True
    assert result.checks[1].name == "serialize"
    assert result.checks[1].ok is True

    # Measurements should have the expected keys
    for key in ("wall_s", "user_s", "sys_s", "rss_kb"):
        assert key in result.measurements, f"Missing measurement: {key}"

    # Frame summary should have reasonable values
    fs = result.frame_summary
    assert fs is not None
    assert fs["schema_version"] == 1
    assert isinstance(fs["ts"], float)
    assert fs["entity_count"] >= 7
    assert "entity_keys" in fs


def test_run_smoke_text_fixture_root() -> None:
    """Text smoke with the fixture root produces expected text markers."""
    from groop.acceptance import format_text, run_smoke

    result = run_smoke(cgroup_root=FIXTURE_CGROUP)
    text = format_text(result)
    assert "groop acceptance smoke" in text
    assert "ALL CHECKS PASSED" in text
    assert "collect:" in text
    assert "serialize:" in text
    assert "source_labels:" in text
    assert "wall:" in text
    assert "RSS:" in text


def test_run_smoke_replay_summary() -> None:
    """Replay summary with the fixture frame."""
    from groop.acceptance import run_smoke

    result = run_smoke(cgroup_root=FIXTURE_CGROUP, replay_path=FIXTURE_FRAME)
    assert result.ok is True
    replay_check = result.checks[3]
    assert replay_check.name == "replay"
    assert replay_check.ok is True
    assert "1 frame(s)" in replay_check.message
    assert "first ts=100.000" in replay_check.message


def test_run_smoke_nonexistent_replay() -> None:
    """Non-existent replay path returns a controlled non-ok result."""
    from groop.acceptance import run_smoke

    result = run_smoke(cgroup_root=FIXTURE_CGROUP, replay_path=NONEXISTENT)
    # Collect, serialize, source_labels still pass; only replay fails
    assert result.ok is False
    replay_check = result.checks[3]
    assert replay_check.name == "replay"
    assert replay_check.ok is False
    assert "does not exist" in replay_check.message


def test_no_textual_import() -> None:
    """Ensure textual is not imported by the acceptance module.

    This checks the module's own import provenance rather than ``sys.modules``,
    which may already contain textual from other test-file imports during
    collection.  A subprocess-level check is in
    ``test_subprocess_no_textual_import``.
    """
    import groop.acceptance as mod

    # Verify that groop.ui (the only textual user) was not transitively imported
    import sys as _sys
    assert "groop.ui" not in _sys.modules, "acceptance should not import groop.ui"


def test_json_output_parseable() -> None:
    """Pretty JSON output from run_smoke is parseable and contains expected keys."""
    from groop.acceptance import format_json, run_smoke

    result = run_smoke(cgroup_root=FIXTURE_CGROUP)
    json_str = format_json(result, pretty=True)
    obj = json.loads(json_str)
    assert obj["ok"] is True
    assert "version" in obj
    assert "python" in obj
    assert "platform" in obj
    assert "checks" in obj
    assert len(obj["checks"]) == 4
    assert "measurements" in obj
    assert "frame_summary" in obj

    # Verify all check keys
    for check in obj["checks"]:
        assert "name" in check
        assert "ok" in check
        assert "message" in check
        assert "details" in check


# ---------------------------------------------------------------------------
# Subprocess tests — verify the ``python -m groop.acceptance`` entry point
# ---------------------------------------------------------------------------


def test_subprocess_smoke_json() -> None:
    """``python -m groop.acceptance smoke --json`` on fixture root exit 0."""
    cp = _run_smoke(
        "--cgroup-root", str(FIXTURE_CGROUP),
        "--json",
    )
    assert cp.returncode == 0, f"stderr={cp.stderr}"
    obj = json.loads(cp.stdout)
    assert obj["ok"] is True
    assert obj["version"] == "0.1.0"


def test_subprocess_smoke_text() -> None:
    """``python -m groop.acceptance smoke`` text output exit 0."""
    cp = _run_smoke("--cgroup-root", str(FIXTURE_CGROUP))
    assert cp.returncode == 0, f"stderr={cp.stderr}"
    assert "ALL CHECKS PASSED" in cp.stdout


def test_subprocess_smoke_replay() -> None:
    """``python -m groop.acceptance smoke --replay`` exit 0."""
    cp = _run_smoke(
        "--cgroup-root", str(FIXTURE_CGROUP),
        "--replay", str(FIXTURE_FRAME),
    )
    assert cp.returncode == 0, f"stderr={cp.stderr}"
    assert "Replay loaded:" in cp.stdout


def test_subprocess_smoke_nonexistent_replay() -> None:
    """``python -m groop.acceptance smoke`` with bad --replay path exit 1."""
    cp = _run_smoke(
        "--cgroup-root", str(FIXTURE_CGROUP),
        "--replay", str(NONEXISTENT),
    )
    assert cp.returncode == 1
    assert "does not exist" in cp.stdout


def test_subprocess_pretty_json_parseable() -> None:
    """``--pretty-json`` produces parseable indented JSON."""
    cp = _run_smoke(
        "--cgroup-root", str(FIXTURE_CGROUP),
        "--pretty-json",
    )
    assert cp.returncode == 0
    obj = json.loads(cp.stdout)
    assert obj["ok"] is True
    # Pretty JSON should have newlines (indented)
    assert "\n" in cp.stdout


def test_subprocess_no_textual_import() -> None:
    """Verify that importing acceptance doesn't import textual."""
    cp = subprocess.run(
        [PYTHON, "-c", "import groop.acceptance; import sys; print('textual' in sys.modules)"],
        capture_output=True, text=True, env=ENV,
    )
    assert cp.returncode == 0
    assert cp.stdout.strip() == "False"


def test_subprocess_missing_command_shows_usage() -> None:
    """Running without a subcommand exits 2."""
    cp = subprocess.run(
        [PYTHON, "-m", "groop.acceptance"],
        capture_output=True, text=True, env=ENV,
    )
    assert cp.returncode == 2
