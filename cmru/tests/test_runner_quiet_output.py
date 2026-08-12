"""Coverage for runner.run_command's quiet-mode failure surfacing (S3 runner contract).

Added after an Opus review flagged that a last-N error-line policy would let
docker buildx bake's generic "ERROR: failed to solve" summary crowd out an
earlier, actually-informative "[ERROR] ..." line once a failure produced more
than ERROR_LINES_ON_FAILURE matches.
"""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import tempfile
from dataclasses import replace
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


def _step(*, quiet: bool = True) -> runner.StepConfig:
    return runner.StepConfig(
        name="run-tests",
        commands=[{
            "label": "gate",
            "argv": ["bash", "-c", "echo detail; echo '==== 3 passed in 0.1s ===='"],
            "cwd": ".",
        }],
        bake_set_prefix=None,
        bake_set_vars=[],
        no_cache_env=None,
        clean_dirs=[],
        required_env=[],
        login=None,
        step_env={},
        env_command=None,
        quiet=quiet,
    )


def test_execute_step_overwrites_stable_log_mirrors_quiet_detail_and_summarizes(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs" / "demo"
    step_log = log_dir / "run-tests.log"
    step_log.parent.mkdir(parents=True)
    step_log.write_text("old output\n", encoding="utf-8")
    full_log = tmp_path / "cmru.release.log"
    monkeypatch.setenv("CMRU_RUN_LOG", str(full_log))
    monkeypatch.delenv("CMRU_SHOW_RUN_DETAILS", raising=False)
    monkeypatch.delenv("CMRU_LOG_APPEND", raising=False)

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        runner.execute_step(_step(), tmp_path, log_dir)

    console = out.getvalue()
    assert "detail\n" not in console
    assert "3 passed in 0.1s" in console
    assert "old output" not in step_log.read_text(encoding="utf-8")
    assert "detail\n" in step_log.read_text(encoding="utf-8")
    assert "detail\n" in full_log.read_text(encoding="utf-8")


def test_execute_step_show_details_streams_without_duplicate_aggregate(tmp_path, monkeypatch):
    full_log = tmp_path / "cmru.release.log"
    full_log.write_text("outer tee owns this stream\n", encoding="utf-8")
    monkeypatch.setenv("CMRU_RUN_LOG", str(full_log))
    monkeypatch.setenv("CMRU_SHOW_RUN_DETAILS", "1")
    monkeypatch.delenv("CMRU_LOG_APPEND", raising=False)

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        runner.execute_step(_step(), tmp_path, tmp_path / "logs" / "demo")

    assert "detail\n" in out.getvalue()
    assert full_log.read_text(encoding="utf-8") == "outer tee owns this stream\n"


def test_execute_step_log_append_inserts_exact_divider(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs" / "demo"
    log_file = log_dir / "run-tests.log"
    log_file.parent.mkdir(parents=True)
    log_file.write_text("previous\n", encoding="utf-8")
    monkeypatch.setenv("CMRU_LOG_APPEND", "1")
    monkeypatch.delenv("CMRU_RUN_LOG", raising=False)
    monkeypatch.delenv("CMRU_SHOW_RUN_DETAILS", raising=False)

    runner.execute_step(_step(), tmp_path, log_dir)

    contents = log_file.read_text(encoding="utf-8")
    assert contents.startswith("previous\n\n---\n")


def test_execute_step_explicit_empty_env_masks_ambient_then_restores_it(tmp_path, monkeypatch):
    """A declared empty value must not silently inherit the caller's shell value."""
    monkeypatch.setenv("CMRU_TEST_MASKED", "stale-shell-value")
    step = replace(
        _step(),
        commands=[{
            "label": "assert masked env",
            "argv": ["bash", "-c", 'test -z "${CMRU_TEST_MASKED}"'],
            "cwd": ".",
        }],
        step_env={"CMRU_TEST_MASKED": ""},
    )

    runner.execute_step(step, tmp_path, tmp_path / "logs")

    assert os.environ["CMRU_TEST_MASKED"] == "stale-shell-value"


@pytest.mark.parametrize("missing", ["GITHUB_USERNAME", "GITHUB_PUSH_PAT"])
def test_additional_registry_login_requires_explicit_credentials(monkeypatch, missing):
    """A declared second registry must never turn into a skipped publication."""
    monkeypatch.setenv("GITHUB_USERNAME", "octocat")
    monkeypatch.setenv("GITHUB_PUSH_PAT", "token")
    monkeypatch.delenv(missing)
    monkeypatch.setattr(runner, "maybe_login", lambda _login: None)

    with pytest.raises(RuntimeError, match=missing):
        runner.maybe_login_multi(None, ["ghcr.io", "registry.example.test"])


def test_project_runner_config_rejects_unknown_execution_key():
    from cmru.config import load_forge_config

    with tempfile.TemporaryDirectory() as raw:
        config = Path(raw) / "cmru.toml"
        config.write_text(
            """schema_version = 1
[github]
owner = "octocat"
repo = "demo"
owner_type = "user"
[targets]
host = "github"
registry = []
[project]
id = "demo"
description = "demo"
prefix = "demo-v"
artifacts = ["wheel"]
[project.version]
strategy = "scm"
[steps.build]
quiet = true
no_cache = false
commands = [{ label = "build", argv = ["true"], cwd = "." }]
""",
            encoding="utf-8",
        )
        with pytest.raises(SystemExit) as exc:
            load_forge_config(config)
    assert exc.value.code == 2


def test_project_runner_config_allows_explicit_project_metadata_namespace():
    from cmru.config import load_forge_config

    with tempfile.TemporaryDirectory() as raw:
        config = Path(raw) / "cmru.toml"
        config.write_text(
            """schema_version = 1
[github]
owner = "octocat"
repo = "demo"
owner_type = "user"
[targets]
host = "github"
registry = []
[project]
id = "demo"
description = "demo"
prefix = "demo-v"
artifacts = ["wheel"]
[project.version]
strategy = "scm"
bump = "conventional"
[project.release]
git_tag = true
build_step = "build"
[project_metadata.builder]
name = "owned-by-project"
[steps.run-tests]
quiet = true
commands = [{ label = "test", argv = ["true"], cwd = "." }]
[steps.build]
quiet = true
commands = [{ label = "build", argv = ["true"], cwd = "." }]
[steps.push]
quiet = true
commands = [{ label = "push", argv = ["true"], cwd = "." }]
""",
            encoding="utf-8",
        )
        assert "demo" in load_forge_config(config).projects


def test_raw_runner_uses_project_local_log_root(tmp_path, monkeypatch):
    project = tmp_path / "demo"
    project.mkdir()
    project_config = project / "cmru.toml"
    project_config.write_text(
        """schema_version = 1
[github]
owner = "octocat"
repo = "demo"
owner_type = "user"
[targets]
host = "github"
registry = []
[project]
id = "demo"
description = "demo"
prefix = "demo-v"
artifacts = ["wheel"]
[project.version]
strategy = "scm"
bump = "conventional"
[project.release]
git_tag = true
build_step = "build"
[steps.run-tests]
quiet = true
commands = [{ label = "test", argv = ["true"], cwd = "." }]
[steps.build]
quiet = true
commands = [{ label = "build", argv = ["bash", "-c", "echo inner detail"], cwd = "." }]
[steps.push]
quiet = true
commands = [{ label = "push", argv = ["true"], cwd = "." }]
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("CMRU_RUN_LOG", raising=False)

    runner.run_step(project_config, "build")

    assert (project / "logs" / "cmru" / "build.log").exists()
