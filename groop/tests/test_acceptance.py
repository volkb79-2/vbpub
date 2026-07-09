"""Tests for groop acceptance smoke harness (P33).

Each test invokes ``python -m groop.acceptance`` as a subprocess to verify
the module-level entry point.  Direct unit tests of ``run_smoke()`` cover
the core logic without subprocess overhead.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import fixture_root, SRC

# Paths used in multiple tests
FIXTURE_CGROUP = fixture_root() / "cgroupfs" / "gstammtisch"
FIXTURE_FRAME = fixture_root() / "frames" / "gstammtisch-once.jsonl"
NONEXISTENT = fixture_root() / "frames" / "nonexistent.jsonl"

PYTHON = sys.executable
SMOKE_ARGS = [str(PYTHON), "-m", "groop.acceptance", "smoke"]
ENV = {**{k: v for k, v in os.environ.items()}, "PYTHONPATH": str(SRC)}

STEADY_ARGS = [str(PYTHON), "-m", "groop.acceptance", "steady"]


def _run_steady(*extra_args: str, **kwargs) -> subprocess.CompletedProcess:
    """Run the steady harness as a subprocess and return the result."""
    cmd = [*STEADY_ARGS, *extra_args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=ENV,
        **kwargs,
    )


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
    import sys as _sys

    before = set(_sys.modules)
    import groop.acceptance  # noqa: F401
    after = set(_sys.modules)
    new_ui_modules = {name for name in after - before if name == "groop.ui" or name.startswith("groop.ui.")}
    assert not new_ui_modules, f"acceptance import added UI modules: {sorted(new_ui_modules)}"


def test_json_output_parseable() -> None:
    """Pretty JSON output is parseable, indented, and deterministically sorted."""
    from groop.acceptance import format_json, run_smoke

    result = run_smoke(cgroup_root=FIXTURE_CGROUP)
    json_str = format_json(result, pretty=True)
    obj = json.loads(json_str)
    assert json_str.startswith("{\n  \"checks\"")
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
# Subprocess tests verify the ``python -m groop.acceptance`` entry point
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
    assert cp.stdout.startswith("{\n  \"checks\"")
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


# ---------------------------------------------------------------------------
# Unit tests for run_steady()
# ---------------------------------------------------------------------------


def test_run_steady_json_small_samples() -> None:
    """Steady JSON with fixture root and --samples 2 --interval-s 0 produces ok=true."""
    from groop.acceptance import run_steady

    result = run_steady(
        cgroup_root=FIXTURE_CGROUP,
        samples=2,
        interval_s=0,
    )
    assert result.ok is True
    assert result.version == "0.1.0"
    assert result.samples_requested == 2
    assert result.samples_completed == 2
    assert result.threshold_errors == []

    for key in ("wall_s", "user_s", "sys_s", "rss_kb", "avg_sample_wall_s", "cpu_pct"):
        assert key in result.measurements, f"Missing measurement: {key}"
    ec = result.entity_counts
    assert ec.get("min", -1) >= 7
    assert ec.get("max", -1) >= 7
    assert ec.get("last", -1) >= 7


def test_run_steady_text_output() -> None:
    """Steady text output contains expected markers."""
    from groop.acceptance import format_steady_text, run_steady

    result = run_steady(cgroup_root=FIXTURE_CGROUP, samples=2, interval_s=0)
    text = format_steady_text(result)
    assert "groop acceptance steady" in text
    assert "ALL CHECKS PASSED" in text
    assert "samples completed" in text
    assert "Entity count:" in text
    assert "wall:" in text
    assert "cpu%:" in text
    assert "RSS:" in text


def test_run_steady_cpu_threshold_failure() -> None:
    """CPU threshold failure returns ok=false with threshold_errors."""
    from groop.acceptance import run_steady

    result = run_steady(cgroup_root=FIXTURE_CGROUP, samples=2, interval_s=0, max_cpu_pct=0.0001)
    assert result.ok is False
    assert len(result.threshold_errors) >= 1
    assert "CPU percent" in result.threshold_errors[0]


def test_run_steady_rss_threshold_failure() -> None:
    """RSS threshold failure returns ok=false with threshold_errors."""
    from groop.acceptance import run_steady

    result = run_steady(cgroup_root=FIXTURE_CGROUP, samples=2, interval_s=0, max_rss_kb=1)
    assert result.ok is False
    assert len(result.threshold_errors) >= 1
    assert "RSS" in result.threshold_errors[0]


def test_run_steady_collection_failure_is_not_success() -> None:
    """Collection failures are reported and make the steady run fail."""
    from groop.acceptance import run_steady

    def _failing_collect(_root: Path | None):
        raise RuntimeError("fixture boom")

    result = run_steady(cgroup_root=FIXTURE_CGROUP, samples=2, interval_s=0, _collect=_failing_collect)
    assert result.ok is False
    assert result.samples_completed == 0
    assert len(result.collection_errors) == 2
    assert "fixture boom" in result.collection_errors[0]


def test_run_steady_injectable_sleep() -> None:
    """Injected sleep/perf_counter make the test fast and deterministic."""
    from groop.acceptance import run_steady

    _times = [100.0, 100.1, 100.2, 100.3, 100.4, 100.5]

    def _fake_sleep(_secs: float) -> None:
        pass

    def _fake_perf_counter() -> float:
        return _times.pop(0)

    result = run_steady(
        cgroup_root=FIXTURE_CGROUP, samples=2, interval_s=999,
        _sleep=_fake_sleep, _perf_counter=_fake_perf_counter,
    )
    assert result.ok is True
    assert result.samples_completed == 2


def test_steady_pretty_json_parseable() -> None:
    """Pretty JSON from steady is parseable and deterministically sorted."""
    from groop.acceptance import format_steady_json, run_steady

    result = run_steady(cgroup_root=FIXTURE_CGROUP, samples=2, interval_s=0)
    json_str = format_steady_json(result, pretty=True)
    obj = json.loads(json_str)
    assert obj["ok"] is True
    assert "samples_requested" in obj
    assert "samples_completed" in obj
    assert "measurements" in obj
    assert "entity_counts" in obj
    assert json_str.startswith("{\n  \"collection_errors\"")


# ---------------------------------------------------------------------------
# Subprocess tests for steady
# ---------------------------------------------------------------------------


def test_subprocess_steady_json() -> None:
    """``python -m groop.acceptance steady --json`` on fixture root exit 0."""
    cp = _run_steady(
        "--cgroup-root", str(FIXTURE_CGROUP),
        "--samples", "2", "--interval-s", "0",
        "--json",
    )
    assert cp.returncode == 0, f"stderr={cp.stderr}"
    obj = json.loads(cp.stdout)
    assert obj["ok"] is True
    assert obj["samples_completed"] == 2


def test_subprocess_steady_cpu_threshold() -> None:
    """CPU threshold failure via subprocess exits 1."""
    cp = _run_steady(
        "--cgroup-root", str(FIXTURE_CGROUP),
        "--samples", "2", "--interval-s", "0",
        "--max-cpu-pct", "0.0001",
    )
    assert cp.returncode == 1
    assert "exceeds threshold" in cp.stdout


def test_subprocess_steady_invalid_samples() -> None:
    """Invalid --samples value exits 2."""
    cp = _run_steady(
        "--cgroup-root", str(FIXTURE_CGROUP),
        "--samples", "-1", "--interval-s", "0",
    )
    assert cp.returncode == 2


def test_subprocess_steady_invalid_interval() -> None:
    """Negative --interval-s exits 2."""
    cp = _run_steady(
        "--cgroup-root", str(FIXTURE_CGROUP),
        "--samples", "2", "--interval-s", "-0.1",
    )
    assert cp.returncode == 2


def test_subprocess_steady_invalid_thresholds() -> None:
    """Invalid threshold values exit 2."""
    cp = _run_steady(
        "--cgroup-root", str(FIXTURE_CGROUP),
        "--samples", "2", "--interval-s", "0",
        "--max-cpu-pct", "-1",
    )
    assert cp.returncode == 2

    cp = _run_steady(
        "--cgroup-root", str(FIXTURE_CGROUP),
        "--samples", "2", "--interval-s", "0",
        "--max-rss-kb", "0",
    )
    assert cp.returncode == 2


def test_subprocess_steady_pretty_json() -> None:
    """``--pretty-json`` with steady produces parseable indented JSON."""
    cp = _run_steady(
        "--cgroup-root", str(FIXTURE_CGROUP),
        "--samples", "2", "--interval-s", "0",
        "--pretty-json",
    )
    assert cp.returncode == 0
    obj = json.loads(cp.stdout)
    assert obj["ok"] is True
    assert "\n" in cp.stdout
    assert cp.stdout.startswith("{\n  \"collection_errors\"")
