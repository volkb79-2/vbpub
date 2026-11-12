"""fix-rg-2 (2026-11-04): RG-114, RG-117a-e and RG-118 regression tests.

Every test fails against the code before the fix. Helpers come from
`test_run_gate`, the same way `test_run_gate_workers` borrows them.
"""

import os
import signal
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from test_run_gate import (EXEC_LANE, FakeClock, commit_all, docker_execs,
                           fake_docker, lane_runs, make_history_repo,
                           make_project, make_repo, run_gate, shim_dir_of,
                           write_progress)

from test_run_gate import (  # noqa: E402,F401
    ambient_cgroup, isolate_host_profiler_socket, profiling_off_by_default)

SECRET = "hunter2-VALUE"


def _log_client_env(monkeypatch, tmp_path, name="SECRET_TOKEN") -> Path:
    """Make the fake docker also record the VALUE of `name` in its own
    environment, so a test can prove the value travels there and not on argv."""
    envlog = tmp_path / "docker-client-env.log"
    shim = shim_dir_of(monkeypatch) / "docker"
    body = shim.read_text()
    shim.write_text(body.replace(
        'case "$1" in',
        f'printf \'%s\\n\' "${name}" >> "{envlog}"\ncase "$1" in', 1))
    return envlog


class TestSecretsNotOnArgv:
    """RG-117a: forwarded values ride in the docker client's environment."""

    def test_env_flags_by_name_carries_values_in_the_carrier_only(self):
        carrier = {}
        flags = run_gate.env_flags_by_name({"A": "1", "B": "two words"}, carrier)
        assert flags == ["-e", "A", "-e", "B"]
        assert carrier == {"A": "1", "B": "two words"}

    def test_exec_lane_argv_has_no_values_and_the_client_env_has_them(
            self, tmp_path, monkeypatch, capsys):
        repo = make_repo(tmp_path)
        proj = make_project(repo, EXEC_LANE)
        (repo / "ciu.global.toml").write_text(
            "[deploy]\nproject_name = 'myproj'\nenvironment_tag = 'dev1'\n")
        commit_all(repo, "ciu config")
        log = fake_docker(tmp_path, monkeypatch)
        shim = shim_dir_of(monkeypatch) / "docker"
        shim.write_text(shim.read_text().replace(
            'case "$1" in', 'case "$1" in\n  ps) echo "myproj-dev1-runner" ;;', 1))
        envlog = _log_client_env(monkeypatch, tmp_path)
        monkeypatch.setenv("SECRET_TOKEN", SECRET)
        cfg = proj / "run-gate.toml"
        cfg.write_text(cfg.read_text().replace(
            'mode = "exec"', 'mode = "exec"\n    forward_env = ["SECRET_TOKEN"]'))
        monkeypatch.chdir(proj)
        assert run_gate.main(["suite", "--worktree", str(repo)]) == 0
        execs = docker_execs(log)
        assert execs, "no docker exec was recorded"
        for argv in execs:
            assert SECRET not in "\n".join(argv)
        lane_exec = [argv for argv in execs if "-e" in argv][-1]
        assert lane_exec[lane_exec.index("SECRET_TOKEN") - 1] == "-e"
        assert SECRET in envlog.read_text()
        assert SECRET not in capsys.readouterr().out

    def test_ephemeral_lane_argv_has_no_values_and_the_client_env_has_them(
            self, tmp_path, monkeypatch):
        repo, proj = make_history_repo(tmp_path, textwrap.dedent("""\
            schema_version = 1

            [environments.tester-unified]
            image = "tester-unified:local"
            forward_env = ["SECRET_TOKEN"]

            [lanes.suite]
            kind = "command"
            environment = "tester-unified"
            argv = ["bash", "-c", "echo gate-ran"]
            clean_tree = false
            """))
        log = fake_docker(tmp_path, monkeypatch)
        envlog = _log_client_env(monkeypatch, tmp_path)
        monkeypatch.setenv("SECRET_TOKEN", SECRET)
        monkeypatch.chdir(proj)
        monkeypatch.setattr(sys, "argv", ["run-gate"])
        assert run_gate.main(["suite"]) == 0
        runs = lane_runs(log)
        assert runs
        for argv in runs:
            assert SECRET not in "\n".join(argv)
            assert "SECRET_TOKEN" in argv
            assert argv[argv.index("SECRET_TOKEN") - 1] == "-e"
        assert SECRET in envlog.read_text()


class TestSecondSigtermDuringExecCleanup:
    """RG-114: the exec lane's cleanup finally defers a second SIGTERM."""

    def _exec_repo(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        proj = make_project(repo, EXEC_LANE)
        (repo / "ciu.global.toml").write_text(
            "[deploy]\nproject_name = 'myproj'\nenvironment_tag = 'dev1'\n")
        commit_all(repo, "ciu config")
        fake_docker(tmp_path, monkeypatch)
        shim = shim_dir_of(monkeypatch) / "docker"
        shim.write_text(shim.read_text().replace(
            'case "$1" in', 'case "$1" in\n  ps) echo "myproj-dev1-runner" ;;', 1))
        return repo, proj

    def test_a_signal_inside_the_cleanup_does_not_skip_the_record_clear(
            self, tmp_path, monkeypatch, capsys):
        repo, proj = self._exec_repo(tmp_path, monkeypatch)
        real_finish = run_gate.finish_lane_profiling
        cleared = []
        real_clear = run_gate.clear_inflight_record

        def finish_then_second_sigterm(state):
            os.kill(os.getpid(), signal.SIGTERM)   # lands INSIDE the finally
            return real_finish(state)

        def spy_clear(project_dir, lane_name):
            cleared.append(lane_name)
            return real_clear(project_dir, lane_name)

        monkeypatch.setattr(run_gate, "finish_lane_profiling",
                            finish_then_second_sigterm)
        monkeypatch.setattr(run_gate, "clear_inflight_record", spy_clear)
        monkeypatch.chdir(proj)
        # The deferred signal is re-raised once the cleanup finished: ERROR 2.
        assert run_gate.main(["suite", "--worktree", str(repo)]) == 2
        assert cleared == ["suite"], capsys.readouterr().out
        assert not (proj / ".run-gate" / "inflight" / "suite.json").exists()


class TestUncaughtExceptionIsError:
    """RG-117d: an unexpected exception is ERROR (exit 2), never FAIL (1)."""

    def test_an_exception_escaping_dispatch_exits_2_with_a_traceback(
            self, monkeypatch, capsys):
        def boom(_arguments):
            raise RuntimeError("synthetic bug")

        monkeypatch.setattr(run_gate, "_dispatch", boom)
        monkeypatch.setattr(sys, "argv", ["run-gate"])
        assert run_gate.main(["suite"]) == 2
        err = capsys.readouterr().err
        assert "RuntimeError: synthetic bug" in err      # the traceback
        assert "unexpected RuntimeError" in err and "exit 2" in err

    def test_the_json_form_is_an_error_document_not_a_traceback_exit(
            self, monkeypatch, capsys):
        def boom(_arguments):
            raise ValueError("synthetic bug")

        monkeypatch.setattr(run_gate, "_dispatch", boom)
        monkeypatch.setattr(sys, "argv", ["run-gate"])
        assert run_gate.main(["suite", "--json"]) == 2
        assert '"ERROR"' in capsys.readouterr().out


class TestSaveContainerLogsCreation:
    """RG-117b: created owner-only, never through a planted symlink."""

    @staticmethod
    def _docker_logs(monkeypatch, text="line one\nsecret-ish\n"):
        monkeypatch.setattr(
            run_gate.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, text, ""))

    def test_the_file_is_mode_0600_and_its_directory_0700(
            self, tmp_path, monkeypatch):
        self._docker_logs(monkeypatch)
        target = tmp_path / "evidence" / "x.log"
        written = run_gate.save_container_logs("docker", "x", target)
        assert written == target
        assert target.read_text() == "line one\nsecret-ish\n"
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700

    def test_the_mode_is_set_at_creation_not_after_the_write(
            self, tmp_path, monkeypatch):
        self._docker_logs(monkeypatch)
        modes = []
        real_open = os.open

        def spy_open(path, flags, mode=0o777, **kw):
            if str(path).endswith("m.log"):
                modes.append((flags & os.O_CREAT, mode))
            return real_open(path, flags, mode, **kw)

        monkeypatch.setattr(run_gate.os, "open", spy_open)
        monkeypatch.setattr(Path, "chmod", lambda *a, **k: pytest.fail(
            "chmod after write: the world-readable window is back"))
        old = os.umask(0)
        try:
            assert run_gate.save_container_logs(
                "docker", "m", tmp_path / "m.log") is not None
        finally:
            os.umask(old)
        assert modes == [(os.O_CREAT, 0o600)]
        assert stat.S_IMODE((tmp_path / "m.log").stat().st_mode) == 0o600

    def test_a_planted_symlink_is_refused_and_its_target_untouched(
            self, tmp_path, monkeypatch):
        self._docker_logs(monkeypatch)
        victim = tmp_path / "victim.txt"
        victim.write_text("precious")
        target = tmp_path / "x.log"
        target.symlink_to(victim)
        assert run_gate.save_container_logs("docker", "x", target) is None
        assert victim.read_text() == "precious"

    def test_a_directory_owned_by_someone_else_is_refused(
            self, tmp_path, monkeypatch):
        self._docker_logs(monkeypatch)
        monkeypatch.setattr(run_gate.os, "geteuid", lambda: os.getuid() + 1)
        target = tmp_path / "foreign" / "x.log"
        assert run_gate.save_container_logs("docker", "x", target) is None
        assert not target.exists()

    def test_a_file_owned_by_someone_else_is_refused_untouched(
            self, tmp_path, monkeypatch):
        self._docker_logs(monkeypatch)
        target = tmp_path / "planted.log"
        target.write_text("theirs")
        # The directory check passes (it is ours); the file check must not.
        real_stat = os.fstat

        class _Foreign:
            def __init__(self, inner):
                self._inner = inner
                self.st_uid = inner.st_uid + 1

            def __getattr__(self, name):
                # Mirror the complete os.stat_result shape while changing only
                # ownership, as a real fstat result does on Python 3.14 too.
                return getattr(self._inner, name)

        real_target_stat = target.stat()
        foreign_stat = _Foreign(real_target_stat)
        assert foreign_stat.st_uid == real_target_stat.st_uid + 1
        assert foreign_stat.st_mode == real_target_stat.st_mode
        assert foreign_stat.st_size == real_target_stat.st_size
        assert foreign_stat.st_mtime == real_target_stat.st_mtime
        assert foreign_stat.st_mtime_ns == real_target_stat.st_mtime_ns

        monkeypatch.setattr(run_gate.os, "fstat",
                            lambda fd: _Foreign(real_stat(fd)))
        assert run_gate.save_container_logs("docker", "x", target) is None
        assert target.read_text() == "theirs"

    def test_a_partial_write_is_completed(self, tmp_path, monkeypatch):
        self._docker_logs(monkeypatch, "0123456789" * 50)
        real_write = os.write
        monkeypatch.setattr(run_gate.os, "write",
                            lambda fd, data: real_write(fd, bytes(data[:7])))
        target = tmp_path / "big.log"
        assert run_gate.save_container_logs("docker", "x", target) == target
        assert target.read_text() == "0123456789" * 50

    def test_an_existing_file_is_truncated_and_made_private(
            self, tmp_path, monkeypatch):
        """The lane log is pre-created by `allocate_lane_log`; it is rewritten."""
        self._docker_logs(monkeypatch, "fresh\n")
        target = tmp_path / "lane.log"
        target.write_text("old old old old old\n")
        target.chmod(0o644)
        assert run_gate.save_container_logs("docker", "x", target) == target
        assert target.read_text() == "fresh\n"
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


class TestSliceNesting:
    """RG-117c: the parent of `a-b-c.slice` is `a-b.slice`."""

    @pytest.mark.parametrize(("name", "expected"), [
        ("gates.slice", "gates.slice"),
        ("dev-gates.slice", "dev.slice/dev-gates.slice"),
        ("dev-test-gates.slice", "dev.slice/dev-test.slice/dev-test-gates.slice"),
        ("a-b-c-d.slice", "a.slice/a-b.slice/a-b-c.slice/a-b-c-d.slice"),
    ])
    def test_cumulative_prefixes(self, name, expected):
        assert run_gate.slice_cgroupfs_dir(name) == Path(expected)

    def test_admission_reads_memory_max_of_a_three_part_slice(
            self, tmp_path, monkeypatch, capsys):
        root = tmp_path / "cgroup"
        directory = root / run_gate.slice_cgroupfs_dir("dev-test-gates.slice")
        directory.mkdir(parents=True)
        (directory / "memory.max").write_text("4000000000")
        (directory / "memory.current").write_text("100000000")
        monkeypatch.setenv(run_gate.CGROUPFS_ROOT_ENV_VAR, str(root))
        run_gate.check_slice_memory_admission(
            {"resources": {"memory": "1g"}}, "unit", "dev-test-gates.slice", "t")
        assert "admission OK" in capsys.readouterr().out


class TestMemoryMaxGarbage:
    """RG-117e: an empty or garbled memory.max is "no ceiling", not a crash."""

    @pytest.mark.parametrize("content", ["", "garbage", "²"])
    def test_unparsable_memory_max_admits_with_a_warning(
            self, tmp_path, monkeypatch, capsys, content):
        root = tmp_path / "cgroup"
        directory = root / run_gate.slice_cgroupfs_dir("dev-gates.slice")
        directory.mkdir(parents=True)
        (directory / "memory.max").write_text(content)
        (directory / "memory.current").write_text("1")
        monkeypatch.setenv(run_gate.CGROUPFS_ROOT_ENV_VAR, str(root))
        run_gate.check_slice_memory_admission(
            {"resources": {"memory": "1g"}}, "unit", "dev-gates.slice", "t")
        assert "no derivable memory ceiling" in capsys.readouterr().out


class TestPressurePauseIsBounded:
    """RG-117e: sustained host pressure cannot pause the stall clock forever."""

    def test_a_budgetless_lane_under_endless_pressure_still_stalls_out(
            self, tmp_path):
        clock = FakeClock()
        path = tmp_path / ".assay" / "progress-psi-bound.jsonl"
        write_progress(path, {"event": "command_started", "phase": "baseline"})
        watch = run_gate.ProgressWatch(
            path, "sql-mutation", None, clock, auto_bound=True,
            pressure_reader=lambda: 50.0)
        assert watch.poll() is None
        clock.advance(run_gate.PRESSURE_PAUSE_MAX_SECONDS - 1)
        assert watch.poll() is None          # still inside the credited pause
        clock.advance(3600)
        stalled = watch.poll()
        assert stalled is not None
        assert "last event seen: baseline command progress" in stalled


class TestImageSelectionAfterHostOverride:
    """RG-117e: effective_image is recorded AFTER the host-image override."""

    def test_effective_image_names_the_image_the_lane_runs(
            self, tmp_path, monkeypatch):
        project = {"environments": {run_gate.HOST_ENV: {
            "mode": "ephemeral", "image": "declared:1",
            "image_env": "RG2_IMAGE_PICK"}}}
        monkeypatch.setenv("RG2_IMAGE_PICK", "picked:2")
        monkeypatch.setenv(run_gate.HOST_IMAGE_ENV_VAR, "host-override:3")
        resolved, _source = run_gate.resolve_environment(
            {"environment": run_gate.HOST_ENV}, "lane", project, {},
            tmp_path / "run-gate.toml", None)
        assert resolved["image"] == "host-override:3"
        assert resolved["_image_selection"]["effective_image"] == \
            "host-override:3"


class TestPasswdEntryGuard:
    """RG-118: a lane user with no passwd entry is refused with a named line."""

    @staticmethod
    def _run(tmp_path, getent_body):
        shim = tmp_path / "bin"
        shim.mkdir(exist_ok=True)
        if getent_body is not None:
            getent = shim / "getent"
            getent.write_text("#!/bin/sh\n" + getent_body)
            getent.chmod(0o755)
        # bash, id and echo live in /usr/bin:/bin; never put real getent first.
        env = {"PATH": f"{shim}:/usr/bin:/bin"}
        if getent_body is None:
            env["PATH"] = f"{shim}:{_without_getent(tmp_path)}"
        return subprocess.run(
            ["bash", "-c", run_gate.passwd_entry_guard() + " && echo LANE-RAN"],
            capture_output=True, text=True, env=env)

    def test_a_uid_without_an_entry_is_refused_with_exit_2(self, tmp_path):
        proc = self._run(tmp_path, "exit 2\n")
        assert proc.returncode == 2
        assert "has no passwd entry in the image" in proc.stderr
        assert "LANE-RAN" not in proc.stdout

    def test_a_uid_with_an_entry_runs_the_lane(self, tmp_path):
        proc = self._run(tmp_path, "exit 0\n")
        assert proc.returncode == 0 and "LANE-RAN" in proc.stdout

    def test_an_image_without_getent_is_not_refused(self, tmp_path):
        proc = self._run(tmp_path, None)
        assert proc.returncode == 0 and "LANE-RAN" in proc.stdout

    def test_only_a_lane_with_a_user_gets_the_guard(
            self, tmp_path, monkeypatch):
        guard = run_gate.passwd_entry_guard()
        for user, present in (("1234:1234", True), (None, False)):
            (tmp_path / f"u{present}").mkdir()
            repo, proj = make_history_repo(tmp_path / f"u{present}", textwrap.dedent(f"""\
                schema_version = 1

                [environments.tester-unified]
                image = "tester-unified:local"
                {'user = "' + user + '"' if user else ''}

                [lanes.suite]
                kind = "command"
                environment = "tester-unified"
                argv = ["bash", "-c", "echo gate-ran"]
                clean_tree = false
                """))
            log = fake_docker(tmp_path / f"u{present}", monkeypatch)
            monkeypatch.chdir(proj)
            monkeypatch.setattr(sys, "argv", ["run-gate"])
            run_gate.main(["suite"])
            runs = lane_runs(log)
            assert runs
            assert (guard in runs[-1][-1]) is present


def _without_getent(tmp_path) -> str:
    """A PATH directory holding only the tools the guard needs, no getent."""
    tools = tmp_path / "tools"
    tools.mkdir(exist_ok=True)
    for name in ("bash", "id", "echo"):
        link = tools / name
        if not link.exists():
            real = subprocess.run(["bash", "-c", f"command -v {name}"],
                                  capture_output=True, text=True).stdout.strip()
            if real.startswith("/"):
                link.symlink_to(real)
    return str(tools)
