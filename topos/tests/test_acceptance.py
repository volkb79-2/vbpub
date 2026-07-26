"""Tests for topos acceptance smoke harness (P33).

Each test invokes ``python -m topos.acceptance`` as a subprocess to verify
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

from conftest import fixture_root, SRC

# Paths used in multiple tests
FIXTURE_CGROUP = fixture_root() / "cgroupfs" / "gstammtisch"
FIXTURE_FRAME = fixture_root() / "frames" / "gstammtisch-once.jsonl"
NONEXISTENT = fixture_root() / "frames" / "nonexistent.jsonl"

PYTHON = sys.executable
SMOKE_ARGS = [str(PYTHON), "-m", "topos.acceptance", "smoke"]
ENV = {**{k: v for k, v in os.environ.items()}, "PYTHONPATH": str(SRC)}

STEADY_ARGS = [str(PYTHON), "-m", "topos.acceptance", "steady"]


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
    from topos.acceptance import run_smoke

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
    from topos.acceptance import format_text, run_smoke

    result = run_smoke(cgroup_root=FIXTURE_CGROUP)
    text = format_text(result)
    assert "topos acceptance smoke" in text
    assert "ALL CHECKS PASSED" in text
    assert "collect:" in text
    assert "serialize:" in text
    assert "source_labels:" in text
    assert "wall:" in text
    assert "RSS:" in text


def test_run_smoke_replay_summary() -> None:
    """Replay summary with the fixture frame."""
    from topos.acceptance import run_smoke

    result = run_smoke(cgroup_root=FIXTURE_CGROUP, replay_path=FIXTURE_FRAME)
    assert result.ok is True
    replay_check = result.checks[3]
    assert replay_check.name == "replay"
    assert replay_check.ok is True
    assert "1 frame(s)" in replay_check.message
    assert "first ts=100.000" in replay_check.message


def test_run_smoke_nonexistent_replay() -> None:
    """Non-existent replay path returns a controlled non-ok result."""
    from topos.acceptance import run_smoke

    result = run_smoke(cgroup_root=FIXTURE_CGROUP, replay_path=NONEXISTENT)
    # Collect, serialize, source_labels still pass; only replay fails
    assert result.ok is False
    replay_check = result.checks[3]
    assert replay_check.name == "replay"
    assert replay_check.ok is False
    assert "does not exist" in replay_check.message


def test_run_smoke_contains_collection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A collector failure skips dependent frame checks without escaping."""
    import topos.acceptance as acceptance

    def _explode(_cgroup_root: Path | None) -> dict[str, object]:
        raise RuntimeError("collector unavailable")

    monkeypatch.setattr(acceptance, "_collect_frame", _explode)

    result = acceptance.run_smoke(cgroup_root=FIXTURE_CGROUP)

    checks = {check.name: check for check in result.checks}
    assert result.ok is False
    assert checks["collect"].ok is False
    assert "collector unavailable" in checks["collect"].message
    assert checks["collect"].details == {"error": "collector unavailable"}
    assert checks["serialize"].ok is False
    assert checks["serialize"].message == "Skipped (collect did not produce a frame)"
    assert checks["source_labels"].ok is False
    assert checks["source_labels"].message == "Skipped (no frame available)"
    assert checks["replay"].ok is True
    assert result.frame_summary is None


def test_run_smoke_contains_serialization_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A round-trip failure preserves collection and source evidence."""
    import topos.acceptance as acceptance
    import topos.model as model

    def _explode(_frame_dict: dict[str, object]) -> object:
        raise RuntimeError("round-trip unavailable")

    monkeypatch.setattr(model, "frame_from_jsonable", _explode)

    result = acceptance.run_smoke(cgroup_root=FIXTURE_CGROUP)

    checks = {check.name: check for check in result.checks}
    assert result.ok is False
    assert checks["collect"].ok is True
    assert checks["serialize"].ok is False
    assert "round-trip unavailable" in checks["serialize"].message
    assert checks["serialize"].details == {"error": "round-trip unavailable"}
    assert checks["source_labels"].ok is True
    assert checks["source_labels"].details
    assert checks["replay"].ok is True
    assert result.frame_summary is not None


def test_run_smoke_contains_existing_replay_load_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A loader failure on an existing replay is distinct from a missing path."""
    import topos.acceptance as acceptance

    def _explode(_path: Path) -> dict[str, object]:
        raise RuntimeError("replay unreadable")

    monkeypatch.setattr(acceptance, "_run_replay", _explode)

    result = acceptance.run_smoke(cgroup_root=FIXTURE_CGROUP, replay_path=FIXTURE_FRAME)

    checks = {check.name: check for check in result.checks}
    assert result.ok is False
    assert checks["collect"].ok is True
    assert checks["serialize"].ok is True
    assert checks["source_labels"].ok is True
    assert checks["replay"].ok is False
    assert "Replay load failed: replay unreadable" == checks["replay"].message
    assert checks["replay"].details == {"error": "replay unreadable"}
    assert result.frame_summary is not None


def test_no_textual_import() -> None:
    """Ensure textual is not imported by the acceptance module.

    This checks the module's own import provenance rather than ``sys.modules``,
    which may already contain textual from other test-file imports during
    collection.  A subprocess-level check is in
    ``test_subprocess_no_textual_import``.
    """
    import sys as _sys

    before = set(_sys.modules)
    import topos.acceptance  # noqa: F401
    after = set(_sys.modules)
    new_ui_modules = {name for name in after - before if name == "topos.ui" or name.startswith("topos.ui.")}
    assert not new_ui_modules, f"acceptance import added UI modules: {sorted(new_ui_modules)}"


def test_json_output_parseable() -> None:
    """Pretty JSON output is parseable, indented, and deterministically sorted."""
    from topos.acceptance import format_json, run_smoke

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
# Subprocess tests verify the ``python -m topos.acceptance`` entry point
# ---------------------------------------------------------------------------


def test_subprocess_smoke_json() -> None:
    """``python -m topos.acceptance smoke --json`` on fixture root exit 0."""
    cp = _run_smoke(
        "--cgroup-root", str(FIXTURE_CGROUP),
        "--json",
    )
    assert cp.returncode == 0, f"stderr={cp.stderr}"
    obj = json.loads(cp.stdout)
    assert obj["ok"] is True
    assert obj["version"] == "0.1.0"


def test_subprocess_smoke_text() -> None:
    """``python -m topos.acceptance smoke`` text output exit 0."""
    cp = _run_smoke("--cgroup-root", str(FIXTURE_CGROUP))
    assert cp.returncode == 0, f"stderr={cp.stderr}"
    assert "ALL CHECKS PASSED" in cp.stdout


def test_subprocess_smoke_replay() -> None:
    """``python -m topos.acceptance smoke --replay`` exit 0."""
    cp = _run_smoke(
        "--cgroup-root", str(FIXTURE_CGROUP),
        "--replay", str(FIXTURE_FRAME),
    )
    assert cp.returncode == 0, f"stderr={cp.stderr}"
    assert "Replay loaded:" in cp.stdout


def test_subprocess_smoke_nonexistent_replay() -> None:
    """``python -m topos.acceptance smoke`` with bad --replay path exit 1."""
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
        [PYTHON, "-c", "import topos.acceptance; import sys; print('textual' in sys.modules)"],
        capture_output=True, text=True, env=ENV,
    )
    assert cp.returncode == 0
    assert cp.stdout.strip() == "False"


def test_subprocess_missing_command_shows_usage() -> None:
    """Running without a subcommand exits 2."""
    cp = subprocess.run(
        [PYTHON, "-m", "topos.acceptance"],
        capture_output=True, text=True, env=ENV,
    )
    assert cp.returncode == 2


# ---------------------------------------------------------------------------
# Unit tests for run_steady()
# ---------------------------------------------------------------------------


def test_run_steady_json_small_samples() -> None:
    """Steady JSON with fixture root and --samples 2 --interval-s 0 produces ok=true."""
    from topos.acceptance import run_steady

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
    from topos.acceptance import format_steady_text, run_steady

    result = run_steady(cgroup_root=FIXTURE_CGROUP, samples=2, interval_s=0)
    text = format_steady_text(result)
    assert "topos acceptance steady" in text
    assert "ALL CHECKS PASSED" in text
    assert "samples completed" in text
    assert "Entity count:" in text
    assert "wall:" in text
    assert "cpu%:" in text
    assert "RSS:" in text


def test_run_steady_cpu_threshold_failure() -> None:
    """CPU threshold failure returns ok=false with threshold_errors."""
    from topos.acceptance import run_steady

    result = run_steady(cgroup_root=FIXTURE_CGROUP, samples=2, interval_s=0, max_cpu_pct=0.0001)
    assert result.ok is False
    assert len(result.threshold_errors) >= 1
    assert "CPU percent" in result.threshold_errors[0]


def test_run_steady_rss_threshold_failure() -> None:
    """RSS threshold failure returns ok=false with threshold_errors."""
    from topos.acceptance import run_steady

    result = run_steady(cgroup_root=FIXTURE_CGROUP, samples=2, interval_s=0, max_rss_kb=1)
    assert result.ok is False
    assert len(result.threshold_errors) >= 1
    assert "RSS" in result.threshold_errors[0]


def test_run_steady_collection_failure_is_not_success() -> None:
    """Collection failures are reported and make the steady run fail."""
    from topos.acceptance import run_steady

    def _failing_collect(_root: Path | None):
        raise RuntimeError("fixture boom")

    result = run_steady(cgroup_root=FIXTURE_CGROUP, samples=2, interval_s=0, _collect=_failing_collect)
    assert result.ok is False
    assert result.samples_completed == 0
    assert len(result.collection_errors) == 2
    assert "fixture boom" in result.collection_errors[0]


def test_run_steady_injectable_sleep() -> None:
    """Injected sleep/perf_counter make the test fast and deterministic."""
    from topos.acceptance import run_steady

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
    from topos.acceptance import format_steady_json, run_steady

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
    """``python -m topos.acceptance steady --json`` on fixture root exit 0."""
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


TUI_SMOKE_ARGS = [str(PYTHON), "-m", "topos.acceptance", "tui-smoke"]


def _run_tui_smoke(*extra_args: str, **kwargs) -> subprocess.CompletedProcess:
    """Run the TUI smoke harness as a subprocess and return the result."""
    cmd = [*TUI_SMOKE_ARGS, *extra_args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=ENV,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Unit tests for run_tui_smoke()
# ---------------------------------------------------------------------------


def test_parse_ui_smoke_line_parses_correctly() -> None:
    """``_parse_ui_smoke_line`` extracts frames, view, profile from a valid line."""
    from topos.acceptance import _parse_ui_smoke_line

    result = _parse_ui_smoke_line("ui smoke ok frames=1 view=tree profile=auto")
    assert result == {"frames": 1, "view": "tree", "profile": "auto"}


def test_parse_ui_smoke_line_handles_garbage() -> None:
    """``_parse_ui_smoke_line`` returns empty dict for unparseable input."""
    from topos.acceptance import _parse_ui_smoke_line

    assert _parse_ui_smoke_line("") == {}
    assert _parse_ui_smoke_line("random text here") == {}
    assert _parse_ui_smoke_line("ui smoke fail") == {}


def test_parse_ui_smoke_line_keeps_valid_fields_when_frames_is_malformed() -> None:
    """A bad frame count does not prevent parsing the remaining smoke fields."""
    from topos.acceptance import _parse_ui_smoke_line

    result = _parse_ui_smoke_line("ui smoke ok frames=not-a-number view=tree profile=minimal")

    assert result == {"view": "tree", "profile": "minimal"}
    assert "frames" not in result


def test_run_tui_smoke_fixture_replay() -> None:
    """``run_tui_smoke`` with fixture replay returns ok=true with measurements."""
    from topos.acceptance import run_tui_smoke

    result = run_tui_smoke(
        replay_path=FIXTURE_FRAME,
        timeout_s=10,
    )
    assert result.ok is True
    assert result.exit_code == 0
    assert result.smoke_line is not None
    assert "ui smoke ok" in result.smoke_line
    assert result.frames == 1
    assert result.view == "tree"
    assert result.profile == "auto"

    for key in ("wall_s", "user_s", "sys_s", "rss_kb"):
        assert key in result.measurements, f"Missing measurement: {key}"


def test_run_tui_smoke_bad_replay_path() -> None:
    """``run_tui_smoke`` with a bad replay path exits 1."""
    from topos.acceptance import run_tui_smoke

    result = run_tui_smoke(
        replay_path=NONEXISTENT,
        timeout_s=10,
    )
    assert result.ok is False
    assert result.exit_code == 1
    assert result.smoke_line is None


def test_run_tui_smoke_timeout() -> None:
    """``run_tui_smoke`` reports a bounded failure when the child times out."""
    from topos.acceptance import run_tui_smoke

    result = run_tui_smoke(
        replay_path=FIXTURE_FRAME,
        timeout_s=0.000001,
    )
    assert result.ok is False
    assert result.exit_code == -1
    assert result.smoke_line is None
    assert result.stderr_snippet == "(timeout)"
    assert result.measurements["wall_s"] >= 0


def test_run_tui_smoke_with_profile() -> None:
    """``run_tui_smoke`` with ``--profile minimal`` passes through and appears in output."""
    from topos.acceptance import run_tui_smoke

    result = run_tui_smoke(
        replay_path=FIXTURE_FRAME,
        profile="minimal",
        timeout_s=10,
    )
    assert result.ok is True
    assert result.profile == "minimal"


def test_run_tui_smoke_forwards_config_and_profile_to_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configured child receives all TUI arguments and its smoke line is parsed."""
    import topos.acceptance as acceptance

    config_path = tmp_path / "topos.toml"
    captured: dict[str, object] = {}
    real_run = acceptance.subprocess.run

    def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "topos.cli" not in cmd:
            return real_run(cmd, **kwargs)
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="ui smoke ok frames=7 view=tree profile=ci\n",
            stderr="",
        )

    monkeypatch.setattr(acceptance.subprocess, "run", _run)
    monkeypatch.setattr(acceptance.platform, "platform", lambda: "test-platform")

    result = acceptance.run_tui_smoke(
        FIXTURE_FRAME, config_path=config_path, profile="ci"
    )

    assert captured["cmd"] == [
        sys.executable,
        "-m",
        "topos.cli",
        "--replay",
        str(FIXTURE_FRAME),
        "--step",
        "--ui-smoke",
        "--config",
        str(config_path),
        "--profile",
        "ci",
    ]
    assert result.ok is True
    assert result.smoke_line == "ui smoke ok frames=7 view=tree profile=ci"
    assert (result.frames, result.view, result.profile) == (7, "tree", "ci")


def test_run_tui_smoke_rejects_incomplete_and_clips_child_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero exit without a smoke line fails and bounds both evidence snippets."""
    import topos.acceptance as acceptance

    stdout = "o" * 700
    stderr = "e" * 700

    def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(acceptance.subprocess, "run", _run)
    monkeypatch.setattr(acceptance.platform, "platform", lambda: "test-platform")

    result = acceptance.run_tui_smoke(FIXTURE_FRAME)

    assert result.ok is False
    assert result.exit_code == 0
    assert result.smoke_line is None
    assert result.frames is None
    assert result.view is None
    assert result.profile is None
    assert result.stdout_snippet == stdout[:500]
    assert result.stderr_snippet == stderr[:500]
    assert len(result.stdout_snippet) == len(result.stderr_snippet) == 500


def test_format_tui_smoke_text_contains_expected_markers() -> None:
    """Text output contains expected markers."""
    from topos.acceptance import TuiSmokeResult, format_tui_smoke_text

    result = TuiSmokeResult(
        ok=True,
        exit_code=0,
        version="0.1.0",
        python="3.13.5",
        platform="Linux-x86_64",
        smoke_line="ui smoke ok frames=1 view=tree profile=auto",
        stdout_snippet="ui smoke ok frames=1 view=tree profile=auto\n",
        stderr_snippet="",
        frames=1,
        view="tree",
        profile="auto",
        measurements={"wall_s": 0.3, "user_s": 0.1, "sys_s": 0.02, "rss_kb": 40000.0},
    )
    text = format_tui_smoke_text(result)
    assert "topos acceptance tui-smoke" in text
    assert "ALL CHECKS PASSED" in text
    assert "ui smoke ok" in text
    assert "wall:" in text
    assert "user:" in text
    assert "sys:" in text
    assert "RSS:" in text
    assert "exit code: 0" in text


def test_format_tui_smoke_text_reports_absent_line_and_bounded_stderr() -> None:
    """A failed child without a smoke line remains transparent and concise."""
    from topos.acceptance import TuiSmokeResult, format_tui_smoke_text

    result = TuiSmokeResult(
        ok=False,
        exit_code=1,
        version="0.1.0",
        python="3.13.5",
        platform="Linux-x86_64",
        smoke_line=None,
        stdout_snippet="ordinary child output\n",
        stderr_snippet="one\ntwo\nthree\nfour\nfive\nsix",
        frames=None,
        view=None,
        profile=None,
        measurements={"wall_s": 0.3, "user_s": 0.1, "sys_s": 0.02, "rss_kb": 40000.0},
    )

    text = format_tui_smoke_text(result)

    assert "UI smoke: line not found" in text
    assert "SOME CHECKS FAILED" in text
    for line in ("one", "two", "three", "four", "five"):
        assert f"    {line}" in text
    assert "    six" not in text


def test_format_tui_smoke_json_parseable() -> None:
    """JSON output from tui-smoke is parseable and has expected fields."""
    from topos.acceptance import TuiSmokeResult, format_tui_smoke_json

    result = TuiSmokeResult(
        ok=True,
        exit_code=0,
        version="0.1.0",
        python="3.13.5",
        platform="Linux-x86_64",
        smoke_line="ui smoke ok frames=1 view=tree profile=auto",
        stdout_snippet="ui smoke ok frames=1 view=tree profile=auto\n",
        stderr_snippet="",
        frames=1,
        view="tree",
        profile="auto",
        measurements={"wall_s": 0.3, "user_s": 0.1, "sys_s": 0.02, "rss_kb": 40000.0},
    )
    json_str = format_tui_smoke_json(result, pretty=True)
    obj = json.loads(json_str)
    assert obj["ok"] is True
    assert obj["exit_code"] == 0
    assert obj["smoke_line"] is not None
    assert obj["frames"] == 1
    assert obj["view"] == "tree"
    assert obj["profile"] == "auto"
    assert "measurements" in obj
    assert "wall_s" in obj["measurements"]


# ---------------------------------------------------------------------------
# Subprocess tests for tui-smoke
# ---------------------------------------------------------------------------


def test_subprocess_tui_smoke_json() -> None:
    """``python -m topos.acceptance tui-smoke --json`` on fixture replay exit 0."""
    cp = _run_tui_smoke(
        "--replay", str(FIXTURE_FRAME),
        "--timeout-s", "10",
        "--json",
    )
    assert cp.returncode == 0, f"stderr={cp.stderr}"
    obj = json.loads(cp.stdout)
    assert obj["ok"] is True
    assert obj["exit_code"] == 0
    assert obj["frames"] == 1


def test_subprocess_tui_smoke_text() -> None:
    """``python -m topos.acceptance tui-smoke`` text output exit 0."""
    cp = _run_tui_smoke(
        "--replay", str(FIXTURE_FRAME),
        "--timeout-s", "10",
    )
    assert cp.returncode == 0, f"stderr={cp.stderr}"
    assert "ALL CHECKS PASSED" in cp.stdout
    assert "ui smoke ok" in cp.stdout


def test_subprocess_tui_smoke_bad_replay() -> None:
    """Bad replay path exits 1."""
    cp = _run_tui_smoke(
        "--replay", str(NONEXISTENT),
        "--timeout-s", "3",
    )
    assert cp.returncode == 1
    assert "SOME CHECKS FAILED" in cp.stdout


def test_subprocess_tui_smoke_pretty_json_parseable() -> None:
    """``--pretty-json`` produces parseable indented JSON."""
    cp = _run_tui_smoke(
        "--replay", str(FIXTURE_FRAME),
        "--timeout-s", "10",
        "--pretty-json",
    )
    assert cp.returncode == 0
    obj = json.loads(cp.stdout)
    assert obj["ok"] is True
    assert "\n" in cp.stdout


def test_subprocess_tui_smoke_profile_minimal() -> None:
    """``--profile minimal`` is reflected in the smoke output."""
    cp = _run_tui_smoke(
        "--replay", str(FIXTURE_FRAME),
        "--timeout-s", "10",
        "--profile", "minimal",
        "--json",
    )
    assert cp.returncode == 0, f"stderr={cp.stderr}"
    obj = json.loads(cp.stdout)
    assert obj["profile"] == "minimal"


def test_subprocess_tui_smoke_invalid_timeout() -> None:
    """Invalid timeout exits 2."""
    cp = _run_tui_smoke(
        "--replay", str(FIXTURE_FRAME),
        "--timeout-s", "-1",
    )
    assert cp.returncode == 2


# ---------------------------------------------------------------------------
# Unit tests for MCP smoke (no live daemon required)
# ---------------------------------------------------------------------------


MCP_SMOKE_ARGS = [str(PYTHON), "-m", "topos.acceptance", "mcp-smoke"]


def _run_mcp_smoke(*extra_args: str, **kwargs) -> subprocess.CompletedProcess:
    """Run the MCP smoke harness as a subprocess and return the result."""
    cmd = [*MCP_SMOKE_ARGS, *extra_args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=ENV,
        **kwargs,
    )


def test_format_mcp_smoke_json_outputs_known_fixture() -> None:
    """``format_mcp_smoke_json`` produces deterministic JSON with all pass checks."""
    from topos.acceptance import Check, McpSmokeResult, format_mcp_smoke_json

    result = McpSmokeResult(
        ok=True,
        version="0.1.0",
        python="3.14.6",
        platform="Linux-x86_64",
        extra_installed=True,
        checks=[
            Check(name="hello", ok=True, message="Tool discovery passed", details={"found_count": 4}),
            Check(name="tool_discovery", ok=True, message="All 4 tools present"),
            Check(name="tool_calls", ok=True, message="All 4 tools succeeded"),
            Check(name="response_cap", ok=True, message="Largest response: 51200 bytes", details={"max_response_bytes": 51200}),
            Check(name="daemon_loss", ok=True, message="Typed error returned"),
            Check(name="invalid_selector", ok=True, message="Typed error returned"),
        ],
        max_response_bytes=51200,
        measurements={"wall_s": 1.23, "user_s": 0.45, "sys_s": 0.12, "rss_kb": 80000.0},
    )
    json_str = format_mcp_smoke_json(result, pretty=True)
    obj = json.loads(json_str)
    assert obj["ok"] is True
    assert obj["version"] == "0.1.0"
    assert obj["extra_installed"] is True
    assert obj["max_response_bytes"] == 51200
    assert len(obj["checks"]) == 6
    assert obj["checks"][0]["name"] == "hello"
    assert obj["checks"][0]["ok"] is True
    for check in obj["checks"]:
        assert "name" in check
        assert "ok" in check
        assert "message" in check
        assert "details" in check
    assert "wall_s" in obj["measurements"]


def test_format_mcp_smoke_json_absent_extra() -> None:
    """Extra-absent skip shape is distinguishable in JSON."""
    from topos.acceptance import McpSmokeResult, format_mcp_smoke_json

    result = McpSmokeResult(
        ok=True,
        version="0.1.0",
        python="3.14.6",
        platform="Linux-x86_64",
        extra_installed=False,
        checks=[],
        max_response_bytes=None,
        measurements={"wall_s": 0.01, "user_s": 0.0, "sys_s": 0.0, "rss_kb": 10000.0},
    )
    json_str = format_mcp_smoke_json(result, pretty=True)
    obj = json.loads(json_str)
    assert obj["ok"] is True
    assert obj["extra_installed"] is False
    assert obj["max_response_bytes"] is None
    assert obj["checks"] == []


def test_format_mcp_smoke_text_mixed_pass_fail() -> None:
    """Text output shows [OK] and [FAIL] markers for mixed pass/fail checks."""
    from topos.acceptance import Check, McpSmokeResult, format_mcp_smoke_text

    result = McpSmokeResult(
        ok=False,
        version="0.1.0",
        python="3.14.6",
        platform="Linux-x86_64",
        extra_installed=True,
        checks=[
            Check(name="hello", ok=True, message="Tool discovery passed"),
            Check(name="tool_discovery", ok=True, message="All 4 tools present"),
            Check(name="tool_calls", ok=False, message="One or more tool calls failed"),
        ],
        max_response_bytes=None,
        measurements={"wall_s": 0.5, "user_s": 0.1, "sys_s": 0.02, "rss_kb": 20000.0},
    )
    text = format_mcp_smoke_text(result)
    assert "[OK] hello:" in text
    assert "[OK] tool_discovery:" in text
    assert "[FAIL] tool_calls:" in text
    assert "SOME CHECKS FAILED" in text
    assert "wall:" in text
    assert "user:" in text
    assert "sys:" in text
    assert "RSS:" in text


def test_format_mcp_smoke_text_absent_extra() -> None:
    """Extra-absent path yields skipped, exit 0, and is textually distinguishable."""
    from topos.acceptance import McpSmokeResult, format_mcp_smoke_text

    result = McpSmokeResult(
        ok=True,
        version="0.1.0",
        python="3.14.6",
        platform="Linux-x86_64",
        extra_installed=False,
        checks=[],
        max_response_bytes=None,
        measurements={"wall_s": 0.01, "user_s": 0.0, "sys_s": 0.0, "rss_kb": 10000.0},
    )
    text = format_mcp_smoke_text(result)
    assert "SKIPPED" in text
    assert "topos[mcp] extra not installed" in text
    assert "ALL CHECKS PASSED" in text
    assert "[OK]" not in text
    assert "[FAIL]" not in text


def test_build_parser_wires_mcp_smoke() -> None:
    """``build_parser`` wires ``mcp-smoke`` with ``--socket``, ``--timeout-s``, ``--json``, ``--pretty-json``."""
    from topos.acceptance import build_parser

    parser = build_parser()
    # Try parsing mcp-smoke with each flag shape
    args = parser.parse_args(["mcp-smoke"])
    assert args.command == "mcp-smoke"

    args = parser.parse_args(["mcp-smoke", "--socket", "/tmp/test.sock", "--timeout-s", "10", "--json"])
    assert args.socket == Path("/tmp/test.sock")
    assert args.timeout_s == 10.0
    assert args.json is True

    args = parser.parse_args(["mcp-smoke", "--pretty-json"])
    assert args.pretty_json is True


def test_build_parser_rejects_negative_timeout() -> None:
    """``mcp-smoke`` with negative --timeout-s exits 2 via acceptance_main."""
    from topos.acceptance import acceptance_main

    rc = acceptance_main(["mcp-smoke", "--timeout-s", "-1"])
    assert rc == 2


def test_terminate_process_handles_none() -> None:
    """``_terminate_process`` handles None process gracefully."""
    from topos.acceptance import _terminate_process

    _terminate_process(None)  # must not raise


def test_terminate_process_already_dead() -> None:
    """``_terminate_process`` handles a process that has already exited."""
    import subprocess
    from topos.acceptance import _terminate_process

    proc = subprocess.Popen([PYTHON, "-c", ""])
    proc.wait()
    _terminate_process(proc)  # must not raise


def test_mcp_smoke_contains_daemon_start_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A daemon spawn error becomes a typed failing smoke result."""
    import topos.acceptance as acceptance

    def _fail_to_spawn(*_args: object, **_kwargs: object) -> object:
        raise OSError("permission denied")

    monkeypatch.setattr(acceptance.subprocess, "Popen", _fail_to_spawn)

    result = acceptance.run_mcp_smoke(timeout_s=0.1)

    assert result.ok is False
    daemon_start = [check for check in result.checks if check.name == "daemon_start"]
    assert len(daemon_start) == 1
    assert daemon_start[0].ok is False
    assert "permission denied" in daemon_start[0].message
    assert daemon_start[0].details == {"error": "permission denied"}


class _FakeToolResult:
    """A CallToolResult stand-in: transport status plus a JSON text block."""

    def __init__(self, payload: object, *, is_error: bool = False) -> None:
        self.isError = is_error
        block = type("Block", (), {"text": json.dumps(payload), "type": "text"})()
        self.content = [block]


def test_tool_call_failure_reads_the_payload_not_just_is_error() -> None:
    """A tool call that *returns* a typed error is a failure, even with isError False.

    topos's MCP tools return their typed failures as an ordinary
    ``{"error": {"code": ...}}`` payload, and the SDK only sets ``isError`` when
    a tool *raises*.  So ``isError`` alone is an assertion that cannot fail, and
    the live acceptance leg would report "all tools succeeded" while the daemon
    rejected every call.  The payload is the authority.
    """
    from topos.acceptance import _tool_call_failure

    ok = _FakeToolResult({"data": {"rows": [{"key": "system.slice"}]}})
    assert _tool_call_failure(ok) is None

    typed = _FakeToolResult({"error": {"code": "invalid-selector", "message": "no"}})
    assert _tool_call_failure(typed) is not None
    assert "invalid-selector" in _tool_call_failure(typed)

    unavailable = _FakeToolResult({"error": {"code": "daemon-unavailable", "message": "no"}})
    assert _tool_call_failure(unavailable) is not None

    raised = _FakeToolResult({"data": {}}, is_error=True)
    assert _tool_call_failure(raised) is not None


def test_mcp_helper_classifies_malformed_payloads_and_typed_success() -> None:
    """MCP payload parsing preserves raw text and rejects invalid result shapes."""
    from topos.acceptance import _parse_tool_content, _tool_call_failure

    raw_block = type("Block", (), {"text": "not json", "type": "text"})()
    raw_result = type("ToolResult", (), {"content": [raw_block], "isError": False})()
    assert _parse_tool_content(raw_result) == "not json"
    assert _tool_call_failure(raw_result) == "non-dict payload: 'not json'"

    non_dict = _FakeToolResult(["not", "a", "mapping"])
    assert _tool_call_failure(non_dict) == "non-dict payload: ['not', 'a', 'mapping']"

    malformed_error = _FakeToolResult({"error": "not a typed error"})
    assert _tool_call_failure(malformed_error) == "malformed error field: 'not a typed error'"

    typed_success = _FakeToolResult({"data": {"rows": []}})
    assert _tool_call_failure(typed_success) is None


def test_update_byte_size_uses_utf8_and_handles_absent_or_untextual_content() -> None:
    """Response accounting counts UTF-8 text and treats no text as zero bytes."""
    from topos.acceptance import _update_byte_size

    text_blocks = [
        type("Block", (), {"text": "plain", "type": "text"})(),
        type("Block", (), {"text": "é", "type": "text"})(),
    ]
    text_result = type("ToolResult", (), {"content": text_blocks})()
    absent_result = type("ToolResult", (), {})()
    untextual_block = type("Block", (), {"type": "image"})()
    untextual_result = type("ToolResult", (), {"content": [untextual_block]})()

    details: dict[str, object] = {}
    _update_byte_size(text_result, "text", details)
    _update_byte_size(absent_result, "absent", details)
    _update_byte_size(untextual_result, "image", details)

    assert details["text"] == {"bytes": 7}
    assert details["absent"] == {"bytes": 0}
    assert details["image"] == {"bytes": 0}


class _FakeProc:
    """A daemon handle that records teardown instead of being one."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode if self.returncode is not None else 0


class _TimeoutThenKillProc:
    """A fake process whose graceful wait times out before kill succeeds."""

    def __init__(self, *, post_kill_wait_error: BaseException | None = None) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.wait_calls = 0
        self.post_kill_wait_error = post_kill_wait_error

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        if self.wait_calls == 1:
            raise subprocess.TimeoutExpired("fake-daemon", timeout)
        if self.post_kill_wait_error is not None:
            raise self.post_kill_wait_error
        return self.returncode if self.returncode is not None else 0


def test_terminate_process_kills_after_graceful_wait_timeout() -> None:
    """A process that ignores terminate is killed and waited for."""
    from topos.acceptance import _terminate_process

    proc = _TimeoutThenKillProc()
    _terminate_process(proc)

    assert proc.terminated is True
    assert proc.killed is True
    assert proc.wait_calls == 2


def test_terminate_process_swallows_post_kill_wait_failure() -> None:
    """A cleanup-only failure after kill does not escape teardown."""
    from topos.acceptance import _terminate_process

    proc = _TimeoutThenKillProc(post_kill_wait_error=OSError("wait failed"))
    _terminate_process(proc)

    assert proc.terminated is True
    assert proc.killed is True
    assert proc.wait_calls == 2


def _listening_unix_socket(path: Path):
    import socket

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(path))
    sock.listen(8)
    return sock


def test_mcp_smoke_terminates_the_daemon_when_the_session_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check that raises mid-run must still tear the daemon down.

    This is the contract most likely to be silently broken, so it is asserted
    against injected process handles: delete the ``finally`` block in
    ``run_mcp_smoke`` and this test fails.  It also pins the typed-failure
    contract -- an exploding session must become a failing check, not an
    escaping traceback that leaves ``--json`` consumers with no JSON.
    """
    import topos.acceptance as acceptance

    socket_path = tmp_path / "daemon.sock"
    listener = _listening_unix_socket(socket_path)
    fake = _FakeProc()
    monkeypatch.setattr(acceptance.subprocess, "Popen", lambda *a, **k: fake)

    def _explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("session blew up mid-run")

    monkeypatch.setattr(acceptance, "_run_mcp_client_session", _explode)
    try:
        result = acceptance.run_mcp_smoke(socket_path=socket_path, timeout_s=2.0)
    finally:
        listener.close()

    assert fake.terminated or fake.killed, "daemon was not torn down on the exception path"
    assert result.ok is False
    assert any(c.name == "mcp_session" and c.ok is False for c in result.checks)


def test_mcp_smoke_reports_a_daemon_that_dies_before_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A daemon that exits at startup is reported at once, not after the timeout."""
    import topos.acceptance as acceptance

    fake = _FakeProc()
    fake.returncode = 2  # already dead
    monkeypatch.setattr(acceptance.subprocess, "Popen", lambda *a, **k: fake)

    result = acceptance.run_mcp_smoke(socket_path=tmp_path / "never.sock", timeout_s=30.0)

    assert result.ok is False
    hello = [c for c in result.checks if c.name == "hello"]
    assert hello and hello[0].ok is False
    assert hello[0].details["daemon_exit_code"] == 2


def test_mcp_smoke_does_not_unlink_a_caller_supplied_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Teardown must not delete a socket this process did not create.

    ``--socket /run/topos/topos.sock`` must not remove the packaged system
    daemon's socket on exit.
    """
    import topos.acceptance as acceptance

    socket_path = tmp_path / "system.sock"
    listener = _listening_unix_socket(socket_path)
    fake = _FakeProc()
    monkeypatch.setattr(acceptance.subprocess, "Popen", lambda *a, **k: fake)
    monkeypatch.setattr(
        acceptance, "_run_mcp_client_session", lambda *a, **k: ([], None)
    )
    try:
        acceptance.run_mcp_smoke(socket_path=socket_path, timeout_s=2.0)
        assert socket_path.exists(), "teardown deleted a caller-supplied socket"
    finally:
        listener.close()


def test_mcp_smoke_no_daemon_yields_checks() -> None:
    """``run_mcp_smoke`` with a non-existent socket yields a failing hello check, no crash."""
    from topos.acceptance import run_mcp_smoke

    result = run_mcp_smoke(socket_path=Path("/nonexistent/socket.sock"), timeout_s=0.5)
    assert isinstance(result.extra_installed, bool)
    if result.extra_installed:
        assert any(
            c.name == "hello" and c.ok is False
            for c in result.checks
        ) or any(
            c.name == "daemon_start" and c.ok is False
            for c in result.checks
        )


def test_subprocess_mcp_smoke_json_no_daemon() -> None:
    """``mcp-smoke --json`` with a non-existent socket exits 1 (checks fail)."""
    cp = _run_mcp_smoke(
        "--socket", "/nonexistent/mcp-smoke-test.sock",
        "--timeout-s", "1",
        "--json",
    )
    # Should exit 1 because no daemon is available
    assert cp.returncode == 1
    obj = json.loads(cp.stdout)
    assert "checks" in obj
    assert obj["ok"] is False


def test_subprocess_mcp_smoke_invalid_timeout() -> None:
    """Invalid --timeout-s exits 2."""
    cp = _run_mcp_smoke(
        "--timeout-s", "-1",
    )
    assert cp.returncode == 2
