"""CIU-220/CIU-224 behavioral contracts for shared rendering and process I/O."""
from __future__ import annotations

import ast
import io
import os
import re
import select
import signal
import subprocess
import sys
import time
import tomllib
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine, output, procutil  # noqa: E402
from ciu.output import SeverityStream  # noqa: E402
from ciu.procutil import run_cmd  # noqa: E402
from cli_extended import line_stream  # noqa: E402
from cli_extended.line_stream import LineStreamRenderer  # noqa: E402
from cli_extended.procio import run_capture, run_streaming  # noqa: E402


_INFO = "\033[32m[INFO]\033[0m"
_WARN = "\033[33m[WARN]\033[0m"
_ERROR = "\033[31m[ERROR]\033[0m"

class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True

def _render(text: str, *, time_short: bool, colour: bool) -> str:
    target = io.StringIO()
    stream = SeverityStream(target, time_short=time_short, colour=colour)
    stream.write(text)
    stream.flush()
    return target.getvalue()

def test_multiline_severity_writes_use_the_shared_renderer_for_each_line():
    rendered = _render(
        "[INFO] first\n[WARN] second\n[ERROR] third\n",
        time_short=False,
        colour=True,
    )

    assert rendered == f"{_INFO} first\n{_WARN} second\n{_ERROR} third\n"

def test_colour_off_sanitises_hostile_input_before_severity_recognition():
    rendered = _render(
        "\x1b[31m[ERROR] forged\x1b[0m\n"
        "\x1b]8;;https://example.invalid\x07[INFO] safe\x1b]8;;\x07\n",
        time_short=False,
        colour=False,
    )

    assert rendered == "[ERROR] forged\n[INFO] safe\n"
    assert "\x1b" not in rendered
    assert "\x07" not in rendered

def test_plain_adapter_removes_every_renderer_sgr_and_keeps_the_time_prefix(
    monkeypatch,
):
    monkeypatch.setitem(line_stream._SEVERITY_COLOUR, "[INFO]", "\033[1;36m")

    class _FixedDatetime:
        @classmethod
        def now(cls, _timezone=None):
            return datetime(2026, 11, 24, 12, 34, 56)

    monkeypatch.setattr(output, "datetime", _FixedDatetime)
    rendered = _render("[INFO] first\n[WARN] second\n[ERROR] third\n",
                       time_short=True, colour=False)
    assert rendered == (
        "12:34:56 [INFO] first\n"
        "12:34:56 [WARN] second\n"
        "12:34:56 [ERROR] third\n"
    )
    assert "\x1b" not in rendered

@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({"TERM": "xterm", "NO_COLOR": ""}, True),
        ({"TERM": "xterm", "NO_COLOR": "1"}, False),
        ({"TERM": "dumb", "NO_COLOR": ""}, False),
    ],
)
def test_auto_colour_policy_is_tty_no_color_and_dumb_term_aware(
    monkeypatch, environment, expected
):
    class _BrokenTTY:
        def isatty(self) -> bool:
            raise OSError("terminal disappeared")

    monkeypatch.delenv("NO_COLOR", raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    assert output._colour_enabled(_TTY()) is expected
    assert output._colour_enabled(io.StringIO()) is False
    assert output._colour_enabled(object()) is False
    assert output._colour_enabled(_BrokenTTY()) is False

@pytest.mark.parametrize(
    ("colour", "expected"),
    [
        (False, "12:34:56 [INFO] ready\n12:34:57 [WARN] waiting\n"),
        (
            True,
            "\033[32m12:34:56 [INFO]\033[0m ready\n"
            "\033[33m12:34:57 [WARN]\033[0m waiting\n",
        ),
    ],
)
def test_short_time_prefix_decorates_each_shared_renderer_line(
    monkeypatch, colour, expected
):
    moments = iter((
        datetime(2026, 11, 24, 12, 34, 56),
        datetime(2026, 11, 24, 12, 34, 57),
    ))

    class _FixedDatetime:
        @classmethod
        def now(cls, _timezone=None):
            return next(moments)

    monkeypatch.setattr(output, "datetime", _FixedDatetime)

    assert _render("[INFO] ready\n[WARN] waiting\n", time_short=True, colour=colour) == expected

def test_cli_extended_floor_carries_the_shared_renderer_and_procio():
    project = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))

    dependencies = pyproject["project"]["dependencies"]
    assert "cli-extended>=0.8.0" in dependencies
    assert LineStreamRenderer is not None
    assert callable(run_capture) and callable(run_streaming)
    assert engine.COMPOSE_UP_NO_PROGRESS_SECONDS == 1800
    assert engine.COMPOSE_UP_DRAIN_SECONDS == 2.0

def test_dependency_probe_spawn_failures_are_reported(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.delenv("SKIP_DEPENDENCY_CHECK", raising=False)
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    with pytest.raises(engine.DependencyError):
        engine.check_runtime_dependencies()
    output = capsys.readouterr().out
    assert "Docker Engine" in output and "Docker Compose v2" in output

def _write_executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path

def _fake_docker(tmp_path: Path, body: str) -> tuple[Path, dict[str, str]]:
    executable = _write_executable(tmp_path / "bin" / "docker", body)
    env = dict(os.environ)
    env["PATH"] = f"{executable.parent}:{env.get('PATH', '')}"
    return executable, env

@pytest.fixture
def pipe_holder_docker(tmp_path):
    pid_file = tmp_path / "holder.pid"
    docker, env = _fake_docker(
        tmp_path,
        '/bin/sleep 600 &\n'
        'holder=$!\n'
        'printf "%s\\n" "$holder" > "$HOLDER_PID_FILE"\n'
        'printf "compose finished\\n"\n'
        'exit 17',
    )
    env["HOLDER_PID_FILE"] = str(pid_file)
    yield docker, env, pid_file
    if pid_file.exists():
        _reap_pid(int(pid_file.read_text(encoding="utf-8")), signal_first=True)

def _pidfd_is_alive(pid: int) -> bool:
    pidfd = os.pidfd_open(pid)
    poller = select.poll()
    poller.register(pidfd, select.POLLIN)
    try:
        return poller.poll(0) == []
    finally:
        os.close(pidfd)

def _reap_pid(pid: int, *, signal_first: bool) -> bool:
    """Wait for exactly *pid*; return whether the test had to force-kill it."""
    try:
        pidfd = os.pidfd_open(pid)
    except ProcessLookupError:
        return False
    poller = select.poll()
    poller.register(pidfd, select.POLLIN)
    forced = False
    try:
        if signal_first:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        events = poller.poll(60_000)
        if not events:
            forced = True
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            events = poller.poll(60_000)
        assert events, f"pid {pid} did not exit"
        try:
            os.waitid(os.P_PID, pid, os.WEXITED)
        except ChildProcessError:
            pass
        return forced
    finally:
        os.close(pidfd)

@pytest.mark.requires_contained_cgroup
@pytest.mark.estate_allow(
    "wall-time-assert",
    reason="the bound is the CIU-220 oracle; 60s is a generous failure ceiling, not a speed expectation",
    backlog="CIU-220",
)
def test_compose_child_exit_keeps_code_and_warns_once_about_pipe_holder(
    tmp_path, monkeypatch, capsys, pipe_holder_docker,
):
    _docker, env, pid_file = pipe_holder_docker
    monkeypatch.setattr(engine, "verify_workspace_runtime_network", lambda _root: None)

    started = time.monotonic()
    result = engine.execute_docker_compose_with_logs(
        ["-f", "ciu.compose.yml"], cwd=tmp_path, env=env,
        project="ciu-220-project", repo_root=tmp_path,
    )
    elapsed = time.monotonic() - started
    holder_pid = int(pid_file.read_text(encoding="utf-8"))
    warn_lines = [line for line in capsys.readouterr().out.splitlines()
                  if line.startswith("[WARN] ")]

    assert result["status"] == "error"
    assert result["message"] == "Docker compose failed with exit code 17"
    assert result["stdout"] == "compose finished\n"
    assert elapsed < 60
    assert len(warn_lines) == 1
    assert str(holder_pid) in warn_lines[0]
    assert _pidfd_is_alive(holder_pid)

@pytest.mark.requires_contained_cgroup
def test_run_cmd_warns_once_to_stderr_about_the_pipe_holder(
    pipe_holder_docker, capsys,
):
    _docker, env, pid_file = pipe_holder_docker

    result = run_cmd(["docker", "compose", "up"], env=env)
    holder_pid = int(pid_file.read_text(encoding="utf-8"))
    stderr_lines = capsys.readouterr().err.splitlines()

    assert result.returncode == 17
    assert result.stdout == "compose finished\n"
    assert len(stderr_lines) == 1
    assert stderr_lines[0].startswith("[WARN] ")
    assert str(holder_pid) in stderr_lines[0]
    assert _pidfd_is_alive(holder_pid)

@pytest.mark.requires_contained_cgroup
@pytest.mark.estate_allow(
    "short-timeout",
    reason="the timeout is the CIU-220 oracle; 10s leaves startup headroom under arbitrary load",
    backlog="CIU-220",
)
@pytest.mark.parametrize("caller_signal_handler", [False, True])
def test_run_cmd_timeout_reaps_the_child_group_and_lingering_grandchild(
    tmp_path, caller_signal_handler,
):
    pid_file = tmp_path / "pids"
    executable = _write_executable(
        tmp_path / "lingering-child",
        'printf "%s\\n" "$$" > "$PID_FILE"\n'
        '/bin/sleep 600 &\n'
        'grandchild=$!\n'
        'printf "%s\\n" "$grandchild" >> "$PID_FILE"\n'
        'wait',
    )
    env = dict(os.environ)
    env["PID_FILE"] = str(pid_file)

    previous = None
    if caller_signal_handler:
        previous = signal.signal(signal.SIGTERM, lambda _signum, _frame: None)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            run_cmd([str(executable)], timeout=10, env=env)
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)

    leader_pid, grandchild_pid = (
        int(value)
        for value in pid_file.read_text(encoding="utf-8").splitlines()
    )
    forced = _reap_pid(grandchild_pid, signal_first=False)
    assert forced is False
    with pytest.raises(ProcessLookupError):
        os.killpg(leader_pid, 0)

def test_malformed_utf8_in_streamed_output_is_replacement_text_and_success(
    tmp_path, monkeypatch
):
    _docker, env = _fake_docker(
        tmp_path,
        "printf 'malformed: \\377\\n'\nexit 0",
    )
    monkeypatch.setattr(
        engine, "verify_workspace_runtime_network", lambda _root: None
    )

    result = engine.execute_docker_compose_with_logs(
        ["-f", "ciu.compose.yml"],
        cwd=tmp_path,
        env=env,
        project="ciu-220-project",
        repo_root=tmp_path,
    )

    assert result == {
        "status": "success",
        "message": "",
        "stdout": "malformed: \ufffd\n",
    }

def test_run_cmd_keeps_text_input_and_replacement_decoding():
    assert run_cmd(["cat"], input="hello").stdout == "hello"
    assert run_cmd(["sh", "-c", "printf '\\377'"]).stdout == "\ufffd"
    assert run_cmd(["sh", "-c", "printf 'a\\r\\nb\\rc\\n'"]).stdout == "a\nb\nc\n"
    assert procutil._decode_output(None) is None


@pytest.mark.requires_contained_cgroup
@pytest.mark.estate_allow(
    "short-timeout",
    reason="the command timeout is a deadlock failsafe; the real-child signal handshake decides the assertion",
    backlog="CIU-232",
)
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_run_cmd_preserves_a_python_callers_termination_handler(signum):
    received: list[int] = []

    def record(signum, _frame):
        received.append(signum)

    previous = signal.signal(signum, record)
    signal_name = signal.Signals(signum).name.removeprefix("SIG")
    try:
        result = run_cmd(
            ["sh", "-c", f"kill -s {signal_name} $PPID; sleep 0.3; echo ok"],
            timeout=10,
        )
    finally:
        signal.signal(signum, previous)

    assert received == [signum]
    assert result.returncode == 0
    assert result.stdout == "ok\n"

def test_compose_watchdog_maps_to_error_and_prints_the_pre_signal_process_tree(
    tmp_path, monkeypatch, capsys
):
    report = engine.procio.WatchdogReport(
        "docker",
        engine.COMPOSE_UP_NO_PROGRESS_SECONDS,
        "waiting\n",
        (
            engine.procio.ProcessEntry(4321, 1, "S", 4321, "docker compose up -d"),
        ),
        (),
    )

    def timed_out(_argv, **_kwargs):
        return engine.procio.StreamResult(
            -15, "watchdog", (), None, "waiting\n", report,
        )

    monkeypatch.setattr(engine, "verify_workspace_runtime_network", lambda _root: None)
    monkeypatch.setattr(engine.procio, "run_streaming", timed_out)

    result = engine.execute_docker_compose_with_logs(
        ["-f", "ciu.compose.yml"],
        cwd=tmp_path,
        project="ciu-220-project",
        repo_root=tmp_path,
    )
    console = capsys.readouterr().out

    assert result["status"] == "error"
    assert result["message"] == report.render("COMPOSE_UP_NO_PROGRESS_SECONDS")
    assert f"  {report.tree_lines[0]}\n" in console

_UNBOUNDED_EXCEPTIONS = {
    ("cli.py", "_bake", "subprocess.run", 0): "Buildx --print preview is an unbounded captured query; bounded-drain migration is owned by CIU-233.",
    ("cli.py", "_bake", "subprocess.call", 0): "Buildx's real build inherits terminal stdio, so there is no output pipe to drain.",
    ("dev.py", "run_dev", "subprocess.call", 0): "The HMR loop inherits terminal stdio so interactive input and output stay attached.",
    ("diagnose.py", "_run", "subprocess.run", 0): "Diagnose is an unbounded captured Docker query; bounded-drain migration is owned by CIU-233.",
    ("host_enroll.py", "_run", "subprocess.run", 0): "Host enrollment has unbounded captured probes; bounded-drain migration is owned by CIU-233.",
    ("transport_ssh.py", "_ssh_exec_subprocess", "subprocess.run", 0): "SSH execution inherits terminal stdio so prompts and Ctrl-C stay attached.",
    ("transport_ssh.py", "ssh_sync", "subprocess.run", 0): "rsync inherits terminal stdio for progress and terminal signal delivery.",
    ("transport_ssh.py", "scp_file", "subprocess.run", 0): "scp inherits terminal stdio for progress and terminal signal delivery.",
    ("workspace.py", "discover_committed_roots", "subprocess.run", 0): "Git-tree inventory is an unbounded captured query; bounded-drain migration is owned by CIU-233.",
    ("workspace.py", "_tracked_blob", "subprocess.run", 0): "Git object-type lookup is an unbounded captured query; bounded-drain migration is owned by CIU-233.",
    ("workspace.py", "_validate_committed_marker", "subprocess.run", 0): "Git blob lookup is an unbounded captured query; bounded-drain migration is owned by CIU-233.",
    ("worktree.py", "_git", "subprocess.run", 0): "The worktree Git adapter is an unbounded captured query; bounded-drain migration is owned by CIU-233.",
    ("worktree.py", "_generate_env_in", "subprocess.run", 0): "Env generation inherits terminal stdio to preserve the child verb's I/O contract.",
    ("worktree.py", "_run_cleanup_child", "subprocess.run", 0): "Non-JSON cleanup inherits terminal stdio so the child owns its visible output.",
    ("worktree.py", "_run_cleanup_child", "subprocess.run", 1): "JSON cleanup has an unbounded capture; bounded-drain migration is owned by CIU-233.",
    ("worktree.py", "_run_child", "subprocess.run", 0): "Worktree start inherits terminal stdio to preserve the child's signal and output contract.",
    ("workspace_env.py", "_detect_physical_repo_root", "subprocess.run", 0): "Physical-root detection is an unbounded Docker capture; bounded-drain migration is owned by CIU-233.",
    ("workspace_env.py", "_detect_host_mdt_tmp", "subprocess.run", 0): "Host temp-path detection is an unbounded Docker capture; bounded-drain migration is owned by CIU-233.",
    ("workspace_env.py", "_docker_available", "subprocess.run", 0): "Docker availability is an unbounded captured probe; bounded-drain migration is owned by CIU-233.",
    ("workspace_env.py", "_ensure_network_exists", "subprocess.run", 0): "Network inspection is an unbounded Docker capture; bounded-drain migration is owned by CIU-233.",
    ("workspace_env.py", "_ensure_network_exists", "subprocess.run", 1): "Network creation is an unbounded Docker capture; bounded-drain migration is owned by CIU-233.",
    ("workspace_env.py", "_resolve_own_container_name", "subprocess.run", 0): "Own-container inspection is an unbounded Docker capture; bounded-drain migration is owned by CIU-233.",
    ("workspace_env.py", "_connect_devcontainer_to_network", "subprocess.run", 0): "Network membership inspection is an unbounded Docker capture; bounded-drain migration is owned by CIU-233.",
    ("workspace_env.py", "_connect_devcontainer_to_network", "subprocess.run", 1): "Network connection is an unbounded Docker capture; bounded-drain migration is owned by CIU-233.",
    ("workspace_env.py", "_check_tls_access", "subprocess.run", 0): "TLS access checks are unbounded Docker captures; bounded-drain migration is owned by CIU-233.",
    ("workspace_env.py", "update_cert_permissions", "subprocess.run", 0): "Certificate updates inherit terminal stdio so the privileged child reports directly.",
    ("procutil.py", "popen", "subprocess.Popen", 0): "The long-lived run primitive exposes its streams to the caller; only procutil.run_cmd captures.",
}

_POPEN_CALL_SITES = {
    ("oneshot.py", "_run_foreground", "procutil.popen", 0): "stdout=<inherited>",
}

_BOUNDED_EXCEPTIONS = {
    ("deploy_pkg/health.py", "probe_image_tools", "subprocess.run", 0): "Image-config inspection uses the caller timeout (default 20 s).",
    ("deploy_pkg/health.py", "probe_image_tools", "subprocess.run", 1): "The image tool probe uses the caller timeout (default 20 s).",
    ("doctor.py", "_probe", "subprocess.run", 0): "Doctor probes use the explicit 20-second _PROBE_TIMEOUT_SECONDS bound.",
    ("governance.py", "detect_device", "subprocess.run", 0): "Filesystem device discovery has an explicit 5-second timeout.",
    ("governance.py", "check_slice_unit", "subprocess.run", 0): "The systemd slice preflight has an explicit 5-second timeout.",
    ("governance.py", "check_slice_memory_min", "subprocess.run", 0): "The MemoryMin preflight has an explicit 5-second timeout.",
    ("governance.py", "set_scope_memory_min", "subprocess.run", 0): "The runtime systemd update has an explicit 10-second timeout.",
    ("governance.py", "select_fio_engine", "subprocess.run", 0): "The fio capability probe has an explicit 10-second timeout.",
    ("governance.py", "run_iops_baseline", "subprocess.run", 0): "The fio baseline is bounded by measured runtime plus 120 seconds.",
    ("ksm.py", "build", "subprocess.run", 0): "The shim compiler uses the exported 300-second BUILD_TIMEOUT_SECONDS.",
    ("ksm.py", "build_wrapper", "subprocess.run", 0): "The wrapper compiler uses the exported 300-second BUILD_TIMEOUT_SECONDS.",
    ("ksm.py", "image_entrypoint", "subprocess.run", 0): "The image entrypoint query has an explicit 30-second timeout.",
    ("procutil.py", "run_cmd", "subprocess.run", 0): "capture=False preserves inherited terminal stdio, so there is no pipe to drain.",
}

def _enclosing_name(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    names: list[str] = []
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(current.name)
        current = parents.get(current)
    return ".".join(reversed(names)) or "<module>"

def _resolved_call_name(node: ast.expr, aliases: dict[str, str]) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(aliases.get(node.id, node.id))
    return ".".join(reversed(parts))

def _direct_subprocess_calls(
    src_root: Path,
) -> tuple[set[tuple[str, str, str, int]], set[tuple[str, str, str, int]]]:
    """Return ``(all_calls, calls_without_a_timeout_keyword)``."""
    names = {"run", "Popen", "call", "check_call", "check_output"}
    found: set[tuple[str, str, str, int]] = set()
    unbounded: set[tuple[str, str, str, int]] = set()
    for path in sorted(src_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    if item.name == "subprocess":
                        aliases[item.asname or item.name] = "subprocess"
            elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                for item in node.names:
                    aliases[item.asname or item.name] = f"subprocess.{item.name}"
        ordinals: Counter[tuple[str, str, str]] = Counter()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            resolved = _resolved_call_name(node.func, aliases)
            if resolved.startswith("subprocess.") and resolved.rsplit(".", 1)[-1] in names:
                callee = resolved
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "communicate":
                callee = "communicate"
            else:
                continue
            family = (
                str(path.relative_to(src_root)),
                _enclosing_name(node, parents),
                callee,
            )
            key = (*family, ordinals[family])
            found.add(key)
            if not any(keyword.arg == "timeout" for keyword in node.keywords):
                unbounded.add(key)
            ordinals[family] += 1
    return found, unbounded


def _procutil_popen_call_sites(
    src_root: Path,
) -> dict[tuple[str, str, str, int], str]:
    """Find every ``procutil.popen`` call and its ``stdout=`` value."""
    found: dict[tuple[str, str, str, int], str] = {}
    for path in sorted(src_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: parent for parent in ast.walk(tree)
                   for child in ast.iter_child_nodes(parent)}
        ordinals: Counter[tuple[str, str, str]] = Counter()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            resolved = _resolved_call_name(node.func, {})
            if not resolved.endswith(".popen") and resolved != "popen":
                continue
            stdout = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "stdout"), None,
            )
            family = (str(path.relative_to(src_root)), _enclosing_name(node, parents), resolved)
            key = (*family, ordinals[family])
            found[key] = ("stdout=<inherited>" if stdout is None
                          else f"stdout={ast.unparse(stdout)}")
            ordinals[family] += 1
    return found


def _run_cmd_subprocess_run_keywords(src_root: Path) -> list[set[str]]:
    """Return keyword names on each ``subprocess.run`` call inside ``run_cmd``."""
    tree = ast.parse((src_root / "procutil.py").read_text(encoding="utf-8"))
    run_cmd = next(node for node in tree.body if getattr(node, "name", None) == "run_cmd")
    return [
        {keyword.arg for keyword in node.keywords if keyword.arg is not None}
        for node in ast.walk(run_cmd)
        if isinstance(node, ast.Call) and _resolved_call_name(node.func, {}) == "subprocess.run"
    ]


def test_every_remaining_direct_subprocess_call_is_an_explicit_exception():
    src_root = Path(engine.__file__).resolve().parent
    found, unbounded = _direct_subprocess_calls(src_root)
    expected = set(_UNBOUNDED_EXCEPTIONS) | set(_BOUNDED_EXCEPTIONS)

    missing = sorted(found - expected)
    stale = sorted(expected - found)
    misclassified = sorted(unbounded ^ set(_UNBOUNDED_EXCEPTIONS))
    assert not missing, (
        "new direct subprocess call(s) need an explicit one-line justification: "
        f"{missing}"
    )
    assert not stale, (
        "guard exception(s) no longer describe a direct subprocess call: "
        f"{stale}"
    )
    assert not misclassified, (
        "direct calls are in the wrong bound exception table: "
        f"{misclassified}"
    )


def test_run_cmd_and_procutil_popen_keep_their_stream_contracts_structural():
    src_root = Path(engine.__file__).resolve().parent

    run_keywords = _run_cmd_subprocess_run_keywords(src_root)
    assert len(run_keywords) == 1
    assert "capture_output" not in run_keywords[0]
    assert "stdout" not in run_keywords[0]
    assert _procutil_popen_call_sites(src_root) == _POPEN_CALL_SITES
