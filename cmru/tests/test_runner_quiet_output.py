"""Coverage for runner.run_command's quiet-mode failure surfacing (S3 runner contract).

Added after an Opus review flagged that a last-N error-line policy would let
docker buildx bake's generic "ERROR: failed to solve" summary crowd out an
earlier, actually-informative "[ERROR] ..." line once a failure produced more
than ERROR_LINES_ON_FAILURE matches.
"""
from __future__ import annotations

import contextlib
import io
import subprocess
import tempfile
from pathlib import Path

import pytest

from cmru import runner


def _run_quiet(script: str):
    tmp = Path(tempfile.mkdtemp())
    log_file = tmp / "test.log"
    out, err = io.StringIO(), io.StringIO()
    exc = None
    with log_file.open("a", encoding="utf-8") as handle:
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                runner.run_command(["bash", "-c", script], tmp, handle, quiet=True, log_path=log_file)
        except subprocess.CalledProcessError as e:
            exc = e
    return out.getvalue(), err.getvalue(), log_file.read_text(encoding="utf-8"), exc


def test_quiet_success_prints_one_line_and_logs_everything_else():
    out, err, log_contents, exc = _run_quiet("echo one; echo two; echo three")
    assert exc is None
    assert out.count("\n") == 1  # only the "Running: ..." line
    assert "Running: bash -c" in out
    assert err == ""
    assert log_contents == "one\ntwo\nthree\n"


def test_quiet_failure_surfaces_error_lines_first_seen_not_last_seen():
    # More than ERROR_LINES_ON_FAILURE (20) "[ERROR]" lines, so a last-N policy
    # would drop the first (real) one in favor of later, less useful ones.
    script = (
        'echo "[ERROR] Missing staged artifact: real-root-cause"; '
        + "; ".join(f'echo "[ERROR] noise {i}"' for i in range(1, 25))
        + "; exit 1"
    )
    out, err, _log, exc = _run_quiet(script)
    assert exc is not None and exc.returncode == 1
    # The "error-looking lines" block (not the trailing raw tail, which legitimately
    # still contains late noise lines) must be capped at the first 20 matches.
    error_block = err.split("Last ", 1)[0]
    assert "[ERROR] Missing staged artifact: real-root-cause" in error_block
    assert "[ERROR] noise 24" not in error_block  # evicted by the 20-line cap, as intended
    assert err.count("error-looking line(s)") == 1


def test_quiet_failure_falls_back_to_tail_when_nothing_looks_like_an_error():
    out, err, _log, exc = _run_quiet('echo plain progress line; exit 1')
    assert exc is not None
    assert "error-looking line(s)" not in err
    assert "Last 1 line(s) of output" in err
    assert "plain progress line" in err


def test_quiet_failure_shows_both_error_lines_and_tail_as_context():
    script = 'echo "[ERROR] the real problem"; echo trailing context line; exit 1'
    out, err, _log, exc = _run_quiet(script)
    assert exc is not None
    assert "[ERROR] the real problem" in err
    assert "(context)" in err
    assert "trailing context line" in err


def test_non_quiet_mode_echoes_every_line_live_unchanged():
    tmp = Path(tempfile.mkdtemp())
    log_file = tmp / "test.log"
    out = io.StringIO()
    with log_file.open("a", encoding="utf-8") as handle:
        with contextlib.redirect_stdout(out):
            runner.run_command(["bash", "-c", "echo hello"], tmp, handle)
    assert "hello" in out.getvalue()
