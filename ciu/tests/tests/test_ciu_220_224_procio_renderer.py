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
import tomllib
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine, output, procutil  # noqa: E402
from ciu.output import SeverityStream  # noqa: E402
from ciu.procutil import run_cmd  # noqa: E402
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

def test_dependency_probe_spawn_failures_are_reported(monkeypatch, capsys):
    monkeypatch.delenv("SKIP_DEPENDENCY_CHECK", raising=False)

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(engine.procutil, "run_cmd", missing)
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
def test_compose_child_exit_keeps_code_and_warns_once_about_pipe_holder(
    tmp_path, monkeypatch, capsys
):
    pid_file = tmp_path / "holder.pid"
    _docker, env = _fake_docker(
        tmp_path, '/bin/sleep 600 &\nholder=$!\nprintf "%s\\n" "$holder" > "$HOLDER_PID_FILE"\n'
        'printf "compose finished\\n"\nexit 17',
    )
    env["HOLDER_PID_FILE"] = str(pid_file)
    monkeypatch.setattr(engine, "verify_workspace_runtime_network", lambda _root: None)

    holder_pid: int | None = None
    try:
        result = engine.execute_docker_compose_with_logs(
            ["-f", "ciu.compose.yml"], cwd=tmp_path, env=env,
            project="ciu-220-project", repo_root=tmp_path,
        )
        holder_pid = int(pid_file.read_text(encoding="utf-8"))
        warn_lines = [line for line in capsys.readouterr().out.splitlines()
                      if line.startswith("[WARN] ")]

        assert result["status"] == "error"
        assert result["message"] == "Docker compose failed with exit code 17"
        assert result["stdout"] == "compose finished\n"
        assert len(warn_lines) == 1
        assert str(holder_pid) in warn_lines[0]
        assert _pidfd_is_alive(holder_pid)
    finally:
        if holder_pid is not None:
            _reap_pid(holder_pid, signal_first=True)

@pytest.mark.requires_contained_cgroup
@pytest.mark.estate_allow(
    "short-timeout",
    reason="the timeout is the CIU-220 oracle; 30s leaves ample startup headroom under arbitrary load",
    backlog="CIU-220",
)
def test_run_cmd_timeout_reaps_the_child_group_and_lingering_grandchild(
    tmp_path,
):
    pid_file = tmp_path / "pids"
    executable = _write_executable(
        tmp_path / "lingering-child",
        '/bin/sleep 600 &\ngrandchild=$!\n'
        'printf "%s\\n%s\\n" "$$" "$grandchild" > "$PID_FILE"\nwait',
    )
    env = dict(os.environ)
    env["PID_FILE"] = str(pid_file)

    with pytest.raises(subprocess.TimeoutExpired):
        run_cmd([str(executable)], timeout=30, env=env)

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
    assert procutil._decode_output(None) is None

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
    ("cli.py", "_bake", "subprocess.run", 0): "Buildx --print preview is an unbounded captured query; P-core-10 defers migration.",
    ("cli.py", "_bake", "subprocess.call", 0): "Buildx's real build inherits terminal stdio, so there is no output pipe to drain.",
    ("dev.py", "run_dev", "subprocess.call", 0): "The HMR loop inherits terminal stdio so interactive input and output stay attached.",
    ("diagnose.py", "_run", "subprocess.run", 0): "Diagnose is a read-only Docker evidence query; migration is outside P-core-10.",
    ("host_enroll.py", "_run", "subprocess.run", 0): "Host enrollment captures ssh-keygen/hostname probes; migration is outside P-core-10.",
    ("transport_ssh.py", "_ssh_exec_subprocess", "subprocess.run", 0): "SSH execution inherits terminal stdio so prompts and Ctrl-C stay attached.",
    ("transport_ssh.py", "ssh_sync", "subprocess.run", 0): "rsync inherits terminal stdio for progress and terminal signal delivery.",
    ("transport_ssh.py", "scp_file", "subprocess.run", 0): "scp inherits terminal stdio for progress and terminal signal delivery.",
    ("workspace.py", "discover_committed_roots", "subprocess.run", 0): "Git-tree inventory is a captured read-only query; migration is deferred.",
    ("workspace.py", "_tracked_blob", "subprocess.run", 0): "Git object-type lookup is a captured read-only query; migration is deferred.",
    ("workspace.py", "_validate_committed_marker", "subprocess.run", 0): "Git blob lookup is a captured read-only query; migration is deferred.",
    ("worktree.py", "_git", "subprocess.run", 0): "The worktree Git adapter captures a read-only query; migration is deferred.",
    ("worktree.py", "_generate_env_in", "subprocess.run", 0): "Env generation inherits terminal stdio to preserve the child verb's I/O contract.",
    ("worktree.py", "_run_cleanup_child", "subprocess.run", 0): "Non-JSON cleanup inherits terminal stdio so the child owns its visible output.",
    ("worktree.py", "_run_cleanup_child", "subprocess.run", 1): "JSON cleanup captures its child to move diagnostics off the primary JSON stdout.",
    ("worktree.py", "_run_child", "subprocess.run", 0): "Worktree start inherits terminal stdio to preserve the child's signal and output contract.",
    ("workspace_env.py", "_detect_physical_repo_root", "subprocess.run", 0): "Physical-root detection is a Docker capture with known EOF risk; migration is deferred.",
    ("workspace_env.py", "_detect_host_mdt_tmp", "subprocess.run", 0): "Host temp-path detection is a Docker capture with known EOF risk; migration is deferred.",
    ("workspace_env.py", "_docker_available", "subprocess.run", 0): "Docker availability is a captured read-only probe; migration is deferred.",
    ("workspace_env.py", "_ensure_network_exists", "subprocess.run", 0): "Network inspection is a Docker capture; migration is deferred.",
    ("workspace_env.py", "_ensure_network_exists", "subprocess.run", 1): "Network creation is a Docker capture; migration is deferred.",
    ("workspace_env.py", "_resolve_own_container_name", "subprocess.run", 0): "Own-container inspection is a Docker capture; migration is deferred.",
    ("workspace_env.py", "_connect_devcontainer_to_network", "subprocess.run", 0): "Network membership inspection is a Docker capture; migration is deferred.",
    ("workspace_env.py", "_connect_devcontainer_to_network", "subprocess.run", 1): "Network connection is a Docker capture; migration is deferred.",
    ("workspace_env.py", "_check_tls_access", "subprocess.run", 0): "TLS access checks are Docker captures; migration is deferred.",
    ("workspace_env.py", "update_cert_permissions", "subprocess.run", 0): "Certificate updates inherit terminal stdio so the privileged child reports directly.",
    ("procutil.py", "popen", "subprocess.Popen", 0): "The long-lived run primitive exposes its streams to the caller; only procutil.run_cmd captures.",
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
