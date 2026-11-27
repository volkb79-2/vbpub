"""P7 behavioural contract for CMRU's adoption of ``cli_extended.procio``."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli_extended import procio
from cmru import cli, process_run, runner
from cmru.errors import StepFailed
from cmru.standards import assess_projects

pytestmark = pytest.mark.requires_contained_cgroup

JOIN_LIMIT = 60.0
HANDOFF_TIMEOUT = 30.0


def _command(label: str, argv: list[str], cwd: str = ".") -> str:
    return (
        "{ label = "
        + json.dumps(label)
        + ", argv = "
        + json.dumps(argv)
        + ", cwd = "
        + json.dumps(cwd)
        + " }"
    )


def _project_config(
    root: Path,
    *,
    runner_value: str | None = None,
    build_value: str | None = None,
    build_argv: list[str] | None = None,
    clean_block: str = "",
) -> Path:
    """Write the smallest strict revision-5 project document needed by the oracles."""
    build_argv = build_argv or ["true"]
    runner_block = (
        f"[runner]\nno_progress_timeout_seconds = {runner_value}\n\n"
        if runner_value is not None
        else ""
    )
    build_timeout = (
        f"no_progress_timeout_seconds = {build_value}\n"
        if build_value is not None
        else ""
    )
    clean = f"\n{clean_block}\n" if clean_block else ""
    config = root / "cmru.toml"
    config.write_text(
        f"""schema_version = 1

[github]
owner = "octocat"
repo = "demo"
owner_type = "user"
token_source = "cmru"

[targets]
host = "github"
registry = []

[runtime]
kind = "none"

[env]
PROJECT_ENV = "project"

[project]
id = "demo"
description = "demo"
prefix = "demo-v"
template_revision = 5
artifacts = ["wheel"]

[project.version]
strategy = "scm"
bump = "conventional"

[project.release]
git_tag = false
build_step = "build"

{runner_block}[steps.run-tests]
quiet = true
commands = [{_command("test", ["true"])}]

[steps.build]
quiet = true
{build_timeout}commands = [{_command("build", build_argv)}]

[steps.push]
quiet = true
commands = [{_command("push", ["true"])}]
{clean}""",
        encoding="utf-8",
    )
    return config


def _load(config: Path):
    return cli.load_config(config)


def _alive(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (FileNotFoundError, ProcessLookupError):
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def _wait_file(path: Path) -> str:
    deadline = time.monotonic() + JOIN_LIMIT
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
        time.sleep(0.02)
    raise AssertionError(f"{path} was never written")


def _wait_for_process_group_to_disappear(shell: int, holder: int) -> bool:
    deadline = time.monotonic() + JOIN_LIMIT
    while time.monotonic() < deadline:
        if not _alive(shell) and not _alive(holder):
            try:
                os.killpg(shell, 0)
            except ProcessLookupError:
                return True
        time.sleep(0.05)
    return False


def _kill_quietly(pid: int | None) -> None:
    if pid is None:
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass


def _run_in_thread(function, *args, **kwargs):
    outcome: dict[str, object] = {}

    def target() -> None:
        try:
            outcome["result"] = function(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - the test inspects the real outcome
            outcome["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, outcome


def _install_fake_procio_clock(monkeypatch, trigger: Path) -> None:
    """Freeze procio's clock until the child confirms it is alive, then expire immediately."""
    state = {"now": 0.0}

    def monotonic() -> float:
        if trigger.exists():
            state["now"] += 1000.0
        return state["now"]

    monkeypatch.setattr(
        procio,
        "time",
        SimpleNamespace(monotonic=monotonic, sleep=time.sleep),
    )


def test_absent_runner_and_step_keys_resolve_the_finite_default_and_keep_revision_5(tmp_path):
    config = _project_config(tmp_path)
    root, projects, order, *_ = _load(config)
    project = projects["demo"]

    for step in project.runner_steps.values():
        assert step.no_progress_timeout_seconds == 1800.0
        assert step.no_progress_timeout_source == "default runner.no_progress_timeout_seconds"

    result = assess_projects(root, projects, order, order)[0]
    assert result.problems == ()
    assert "project template r5" in result.messages


def test_step_override_wins_over_project_runner_and_other_steps_inherit_it(tmp_path):
    config = _project_config(tmp_path, runner_value="17.5", build_value="3.5")
    project = _load(config)[1]["demo"]

    build = project.runner_steps["build"]
    assert build.no_progress_timeout_seconds == 3.5
    assert build.no_progress_timeout_source == "steps.build.no_progress_timeout_seconds"
    for name in ("run-tests", "push"):
        step = project.runner_steps[name]
        assert step.no_progress_timeout_seconds == 17.5
        assert step.no_progress_timeout_source == "runner.no_progress_timeout_seconds"


@pytest.mark.parametrize(
    ("toml_value", "python_value"),
    [
        ("true", True),
        ("0", 0),
        ("-1", -1),
        ("nan", float("nan")),
        ("inf", float("inf")),
        ('"5"', "5"),
        (str(10**400), 10**400),
    ],
)
def test_runner_timeout_refusals_use_the_exact_message_and_exit_2(
    tmp_path, capsys, toml_value, python_value,
):
    config = _project_config(tmp_path, runner_value=toml_value)
    with pytest.raises(SystemExit) as refused:
        cli.load_forge_config(config)
    assert refused.value.code == 2
    assert (
        "runner.no_progress_timeout_seconds must be a finite number of seconds "
        f"greater than 0; got {python_value!r}"
    ) in capsys.readouterr().err


@pytest.mark.parametrize(
    ("toml_value", "python_value"),
    [
        ("true", True),
        ("0", 0),
        ("-1", -1),
        ("nan", float("nan")),
        ("inf", float("inf")),
        ('"5"', "5"),
        (str(10**400), 10**400),
    ],
)
def test_step_timeout_refusals_use_the_exact_message_and_exit_2(
    tmp_path, capsys, toml_value, python_value,
):
    config = _project_config(tmp_path, build_value=toml_value)
    with pytest.raises(SystemExit) as refused:
        cli.load_forge_config(config)
    assert refused.value.code == 2
    assert (
        "steps.build.no_progress_timeout_seconds must be a finite number of seconds "
        f"greater than 0; got {python_value!r}"
    ) in capsys.readouterr().err


def test_runner_rejects_unknown_runner_keys(tmp_path, capsys):
    config = _project_config(tmp_path)
    text = config.read_text(encoding="utf-8")
    config.write_text(
        text.replace("[runtime]", "[runner]\nunknown_key = 1\n\n[runtime]"),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as refused:
        cli.load_forge_config(config)
    assert refused.value.code == 2
    assert "runner: unknown keys ['unknown_key']" in capsys.readouterr().err


def test_runner_table_must_be_a_table(tmp_path, capsys):
    config_path = _project_config(tmp_path)
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        text.replace(
            "schema_version = 1\n\n",
            "schema_version = 1\nrunner = \"not a table\"\n\n",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as refused:
        cli.load_forge_config(config_path)

    assert refused.value.code == 2
    assert "[ERROR] [runner] must be a table" in capsys.readouterr().err


def test_direct_parse_step_validates_runner_shape_and_timeout_for_coverage():
    raw = {
        "runner": {"no_progress_timeout_seconds": 30},
        "steps": {
            "build": {
                "quiet": True,
                "commands": [{"label": "build", "argv": ["true"], "cwd": "."}],
            },
        },
    }

    parsed = runner.parse_step(raw, "build")
    assert parsed.no_progress_timeout_seconds == 30.0
    assert parsed.no_progress_timeout_source == "runner.no_progress_timeout_seconds"

    for invalid in (True, "5"):
        raw["runner"] = {"no_progress_timeout_seconds": invalid}
        with pytest.raises(ValueError, match="runner.no_progress_timeout_seconds"):
            runner.parse_step(raw, "build")

    raw["runner"] = "not a table"
    with pytest.raises(ValueError, match=r"\[runner\] must be a table"):
        runner.parse_step(raw, "build")

    raw["runner"] = {}
    raw["steps"]["build"]["no_progress_timeout_seconds"] = 10**400
    with pytest.raises(ValueError, match="steps.build.no_progress_timeout_seconds"):
        runner.parse_step(raw, "build")


def test_fake_clock_watchdog_exits_1_and_names_the_winning_source(tmp_path, monkeypatch, capsys):
    shell_pidfile = tmp_path / "shell.pid"
    ready = tmp_path / "ready"
    script = (
        "import os, time\n"
        "from pathlib import Path\n"
        f"Path({str(shell_pidfile)!r}).write_text(str(os.getpid()))\n"
        "print('started', flush=True)\n"
        f"Path({str(ready)!r}).write_text('ready')\n"
        "time.sleep(600)\n"
    )
    config = _project_config(
        tmp_path,
        runner_value="1800",
        build_value="30",
        build_argv=[sys.executable, "-c", script],
    )
    project = _load(config)[1]["demo"]
    step = project.runner_steps["build"]
    _install_fake_procio_clock(monkeypatch, ready)

    with pytest.raises(StepFailed) as failed:
        runner.execute_step(step, tmp_path, tmp_path / "logs")

    shell_pid = int(shell_pidfile.read_text(encoding="utf-8"))
    assert failed.value.returncode == 1
    err = capsys.readouterr().err
    source = "steps.build.no_progress_timeout_seconds"
    assert source in err
    assert "process tree before termination:" in err
    assert str(shell_pid) in err
    log = (tmp_path / "logs" / "build.log").read_text(encoding="utf-8")
    assert f"[cmru] " in log and source in log and str(shell_pid) in log
    assert not _alive(shell_pid)


def test_holder_warning_is_once_on_stdout_and_once_in_the_quiet_step_log(
    tmp_path, monkeypatch, capsys,
):
    pidfile = tmp_path / "holder.pid"
    step_log = tmp_path / "logs" / "build.log"
    aggregate = tmp_path / "aggregate.log"
    script = f"sleep 600 & echo $! > {pidfile}; echo child-output; exit 0"
    step = runner.StepConfig(
        name="build",
        commands=[{"label": "holder", "argv": ["bash", "-c", script], "cwd": "."}],
        bake_set_prefix=None,
        bake_set_vars=[],
        no_cache_env=None,
        clean_dirs=[],
        required_env=[],
        login=None,
        step_env={},
        env_command=None,
        quiet=True,
    )
    monkeypatch.setenv("CMRU_INTERNAL_RUN_LOG", str(aggregate))
    monkeypatch.delenv("CMRU_INTERNAL_SHOW_RUN_DETAILS", raising=False)
    holder = None
    try:
        thread, outcome = _run_in_thread(
            runner.execute_step, step, tmp_path, tmp_path / "logs",
        )
        _wait_file(pidfile)
        thread.join(JOIN_LIMIT)
        assert not thread.is_alive(), "runner still waited for pipe EOF"
        assert "error" not in outcome
        holder = int(pidfile.read_text(encoding="utf-8"))
        out = capsys.readouterr().out
        warnings = [line for line in out.splitlines() if line.startswith("[WARN] ")]
        assert len(warnings) == 1 and str(holder) in warnings[0]
        body = warnings[0].removeprefix("[WARN] ")
        log = step_log.read_text(encoding="utf-8")
        assert log.count(f"[cmru] {body}\n") == 1
        assert log.count(child_output := "child-output\n") >= 1
        assert body not in aggregate.read_text(encoding="utf-8")
        assert _alive(holder), "the holder is named, never killed"
    finally:
        _kill_quietly(holder)


def test_malformed_streamed_utf8_is_replaced_and_does_not_fail_a_zero_exit(
    tmp_path, monkeypatch,
):
    aggregate = tmp_path / "aggregate.log"
    script = "import sys; sys.stdout.buffer.write(b'ok \\xff end\\n'); sys.stdout.flush()"
    step = runner.StepConfig(
        name="build",
        commands=[{"label": "bytes", "argv": [sys.executable, "-c", script], "cwd": "."}],
        bake_set_prefix=None,
        bake_set_vars=[],
        no_cache_env=None,
        clean_dirs=[],
        required_env=[],
        login=None,
        step_env={},
        env_command=None,
        quiet=True,
    )
    monkeypatch.setenv("CMRU_INTERNAL_RUN_LOG", str(aggregate))
    monkeypatch.delenv("CMRU_INTERNAL_SHOW_RUN_DETAILS", raising=False)

    runner.execute_step(step, tmp_path, tmp_path / "logs")

    expected = "ok \ufffd end\n"
    assert expected in (tmp_path / "logs" / "build.log").read_text(encoding="utf-8")
    assert expected in aggregate.read_text(encoding="utf-8")


def test_quiet_failure_surfaces_error_lines_and_context(tmp_path, capsys):
    script = (
        "import sys\n"
        "print('[ERROR] build failed', flush=True)\n"
        "print('context after failure', flush=True)\n"
        "raise SystemExit(7)\n"
    )
    step = runner.StepConfig(
        name="build",
        commands=[{"label": "fail", "argv": [sys.executable, "-c", script], "cwd": "."}],
        bake_set_prefix=None,
        bake_set_vars=[],
        no_cache_env=None,
        clean_dirs=[],
        required_env=[],
        login=None,
        step_env={},
        env_command=None,
        quiet=True,
    )

    with pytest.raises(subprocess.CalledProcessError) as failed:
        runner.execute_step(step, tmp_path, tmp_path / "logs")

    assert failed.value.returncode == 7
    err = capsys.readouterr().err
    assert "error-looking line(s)" in err and "[ERROR] build failed" in err
    assert "context after failure" in err


def test_cleanup_stub_copies_only_the_two_watchdog_fields(tmp_path, monkeypatch):
    command = cli.Command("clean", ["ignored"], tmp_path)
    parsed = runner.StepConfig(
        name="clean",
        commands=[{"label": "ignored", "argv": ["ignored"], "cwd": "/wrong"}],
        bake_set_prefix="SHOULD_NOT_COPY",
        bake_set_vars=["SHOULD_NOT_COPY"],
        no_cache_env="SHOULD_NOT_COPY",
        clean_dirs=["SHOULD_NOT_COPY"],
        required_env=["SHOULD_NOT_COPY"],
        login={"registry": "SHOULD_NOT_COPY"},
        step_env={"SHOULD_NOT_COPY": "set"},
        env_command=["SHOULD_NOT_COPY"],
        registries=["SHOULD_NOT_COPY"],
        quiet=False,
        no_progress_timeout_seconds=2.5,
        no_progress_timeout_source="steps.clean.no_progress_timeout_seconds",
    )
    project = cli.ProjectConfig(
        "demo", {"BASE": "yes"}, {"clean": [command]},
        runner_steps={"clean": parsed},
    )
    observed: dict[str, object] = {}

    def capture(step, root, logs, *, extra_env, protected_env=None):
        observed.update(
            step=step, root=root, logs=logs,
            extra_env=extra_env, protected_env=protected_env,
        )

    monkeypatch.setattr(runner, "execute_step", capture)

    assert cli.cleanup_project_step(tmp_path, project, "2.3.4", False) is True
    step = observed["step"]
    assert isinstance(step, runner.StepConfig)
    assert step.no_progress_timeout_seconds == 2.5
    assert step.no_progress_timeout_source == "steps.clean.no_progress_timeout_seconds"
    assert step.commands == [{"label": "clean", "argv": ["ignored"], "cwd": str(tmp_path)}]
    assert step.bake_set_prefix is None
    assert step.bake_set_vars == []
    assert step.no_cache_env is None
    assert step.clean_dirs == []
    assert step.required_env == []
    assert step.login is None
    assert step.step_env == {}
    assert step.env_command is None
    assert step.registries is None
    assert step.quiet is True
    assert observed["root"] == tmp_path
    assert observed["logs"] == tmp_path / "logs"
    assert observed["extra_env"] == {"BASE": "yes", "CMRU_VERSION": "2.3.4"}
    assert observed["protected_env"] == {
        "CMRU_RELEASE_COMMIT": None,
        "CMRU_RELEASE_TAG": None,
    }


def test_cleanup_policy_live_override_keeps_the_cleanup_contract(tmp_path, monkeypatch, capsys):
    dump = tmp_path / "clean-env.json"
    ready = tmp_path / "clean-ready"
    inert_env_helper = tmp_path / "env-helper-ran"
    inert_dir = tmp_path / "inert-clean-dir"
    inert_dir.mkdir()
    child = (
        "import json, os, time\n"
        "from pathlib import Path\n"
        f"payload = {{'cwd': os.getcwd(), 'version': os.getenv('CMRU_VERSION'), "
        f"'project': os.getenv('PROJECT_ENV'), 'token': os.getenv('GITHUB_PUSH_PAT'), "
        f"'commit': os.getenv('CMRU_RELEASE_COMMIT'), 'tag': os.getenv('CMRU_RELEASE_TAG'), "
        f"'inert': os.getenv('CMRU_STEP_INERT')}}\n"
        f"Path({str(dump)!r}).write_text(json.dumps(payload))\n"
        "print('clean-visible', flush=True)\n"
        f"Path({str(ready)!r}).write_text('ready')\n"
        "time.sleep(600)\n"
    )
    clean_block = f"""[steps.clean]
quiet = false
no_progress_timeout_seconds = 30
bake_set_prefix = "SHOULD_NOT_COPY"
bake_set_vars = ["SHOULD_NOT_COPY"]
no_cache_env = "SHOULD_NOT_COPY"
clean_dirs = [{json.dumps(str(inert_dir))}]
required_env = ["CMRU_REQUIRED_INERT"]
env = {{ CMRU_STEP_INERT = "set" }}
env_command = {json.dumps(["bash", "-c", f"touch {inert_env_helper}"])}
login = {{ registry = "invalid.example", username_env = "CMRU_LOGIN_USER_INERT", token_env = "CMRU_LOGIN_TOKEN_INERT", required = true }}
commands = [{_command("clean", [sys.executable, "-c", child], str(tmp_path))}]"""
    config = _project_config(
        tmp_path,
        runner_value="1800",
        build_argv=["true"],
        clean_block=clean_block,
    )
    monkeypatch.setenv("CMRU_RELEASE_COMMIT", "stale-commit")
    monkeypatch.setenv("CMRU_RELEASE_TAG", "stale-tag")
    monkeypatch.delenv("CMRU_STEP_INERT", raising=False)
    monkeypatch.delenv("CMRU_REQUIRED_INERT", raising=False)
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli, "github_for_project",
        lambda *_args, **_kwargs: cli.GitHubConfig("owner", "repo", "scope-token", "user"),
    )
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli, "cleanup_project_releases_and_tags",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(cli, "_latest_version_for_prefix", lambda *_args, **_kwargs: "9.9.9")
    monkeypatch.setattr(cli, "_cleanup_worktree_paths", lambda *_args, **_kwargs: set())
    monkeypatch.setattr(cli, "cleanup_commit_deletions", lambda *_args, **_kwargs: None)
    _install_fake_procio_clock(monkeypatch, ready)

    returncode = cli.main([
        "cleanup", "--policy", "--yes", "--config", str(config),
    ])

    assert returncode != 0
    assert "steps.clean.no_progress_timeout_seconds" in capsys.readouterr().err
    payload = json.loads(dump.read_text(encoding="utf-8"))
    assert payload == {
        "cwd": str(tmp_path),
        "version": "9.9.9",
        "project": "project",
        "token": "scope-token",
        "commit": None,
        "tag": None,
        "inert": None,
    }
    assert not inert_env_helper.exists(), "cleanup ran the ignored env_command"
    assert inert_dir.is_dir(), "cleanup ran the ignored clean_dirs"
    log = (tmp_path / "logs" / "clean.log").read_text(encoding="utf-8")
    assert "clean-visible\n" in log
    assert "steps.clean.no_progress_timeout_seconds" in log


def test_captured_probe_timeout_reaps_its_lingering_grandchild(tmp_path, monkeypatch):
    shell_pidfile = tmp_path / "capture-shell.pid"
    holder_pidfile = tmp_path / "capture-holder.pid"
    ready = tmp_path / "capture-ready"
    script = (
        f"echo $$ > {shell_pidfile}; "
        f"sleep 600 & echo $! > {holder_pidfile}; "
        f"printf ready > {ready}; wait"
    )
    _install_fake_procio_clock(monkeypatch, ready)
    thread, outcome = _run_in_thread(
        process_run.run_process,
        ["bash", "-c", script],
        timeout=HANDOFF_TIMEOUT,
    )
    shell = holder = None
    try:
        _wait_file(ready)
        shell = int(_wait_file(shell_pidfile))
        holder = int(_wait_file(holder_pidfile))
        thread.join(JOIN_LIMIT)
        assert not thread.is_alive()
        assert isinstance(outcome["error"], subprocess.TimeoutExpired)
        assert _wait_for_process_group_to_disappear(shell, holder), (
            "timed out waiting for the signalled group and its members to disappear"
        )
        assert not _alive(shell) and not _alive(holder)
        with pytest.raises(ProcessLookupError):
            os.killpg(shell, 0)
    finally:
        if thread.is_alive():
            thread.join(JOIN_LIMIT)
        _kill_quietly(holder)
        _kill_quietly(shell)


@pytest.mark.parametrize("capture", [True, False])
def test_run_process_stdin_devnull_is_expressed_as_immediate_eof(tmp_path, capture):
    marker = tmp_path / "stdin.json"
    script = (
        "import json, sys\n"
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text(json.dumps({{'data': sys.stdin.read()}}))\n"
    )
    result = process_run.run_process(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture=capture,
    )
    assert result.returncode == 0
    assert json.loads(marker.read_text(encoding="utf-8")) == {"data": ""}
    if capture:
        assert result.stdout == "" and result.stderr == ""
    else:
        assert result.stdout is None and result.stderr is None


def test_uncaptured_process_forwards_scoped_input_and_text_mode(tmp_path):
    marker = tmp_path / "uncaptured.json"
    script = (
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "raw = sys.stdin.buffer.read()\n"
        f"Path({str(marker)!r}).write_text(json.dumps({{'cwd': os.getcwd(), "
        "'value': os.environ['CMRU_UNCAPTURED_VALUE'], 'raw': raw.decode()}))\n"
    )
    environment = {**os.environ, "CMRU_UNCAPTURED_VALUE": "scoped"}

    result = process_run.run_process(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        timeout=120,
        input=b"payload",
        capture=False,
        text=None,
    )

    assert result.returncode == 0 and result.stdout is None and result.stderr is None
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "cwd": str(tmp_path),
        "value": "scoped",
        "raw": "payload",
    }


@pytest.mark.parametrize("stdin", [subprocess.PIPE, subprocess.STDOUT, "pipe"])
@pytest.mark.parametrize("capture", [True, False])
def test_run_process_refuses_any_other_stdin_value_before_spawning(
    tmp_path, stdin, capture,
):
    marker = tmp_path / "spawned"
    with pytest.raises(ValueError, match="stdin must be None or subprocess.DEVNULL"):
        process_run.run_process(
            [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('x')"],
            cwd=tmp_path,
            stdin=stdin,
            capture=capture,
        )
    assert not marker.exists()
