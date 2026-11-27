"""Scoped host-job worker and safe runner-user filesystem helpers.

The service process remains root.  This module owns a job after the transient
systemd scope starts; every command that reads or writes the checkout runs in
a child that has the configured runner uid, gid, supplementary groups, and
checkout directory fd.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Protocol, Sequence

from .admission import AdmissionError, ExitCode
from .agent_admission import AgentAdmission
from .agent_policy import AgentPolicy, load_agent_policy
from .executor import RunnerIdentity, _atomic_json, _job_id, _resolve_system_user
from .hostside import open_beneath

_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_RG_VERSION_RE = re.compile(
    r"(\d+(?:\.\d+)*)(?:(a|b|rc)(\d+))?(?:\.post(\d+))?(?:\.dev(\d+))?(?:\+[A-Za-z0-9.]+)?\Z",
)
_RG_VERSION_FLOOR = "25.0.1"
_RG_FALLBACK = "/home/vscode/.venvs/gate-tools2502/bin/run-gate"
_STORE_PATH_RE = re.compile(r"[A-Za-z0-9._@+=,:-]+(?:/[A-Za-z0-9._@+=,:-]+)*\Z")
_IMAGE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,254}\Z")
_CONTAINER_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_PSI_LIMIT = 25.0
_PSI_WAIT_S = 900
_PSI_POLL_S = 30
_MEMORY_FLOOR_MIB = 1024
_MEMORY_WAIT_S = 900
_MEMORY_POLL_S = 10
_MEMORY_SETTLE_S = 20
_STORE_MAX_BYTES = 50 * 1024 * 1024
_ARTIFACT_MAX_BYTES = 50 * 1024 * 1024
_ARTIFACT_EXTENSIONS = {".json", ".jsonl", ".log", ".xml"}


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


class RunnerProcess(Protocol):
    pid: int

    def wait(self) -> int: ...


class RunnerCommands(Protocol):
    def run(
        self, argv: Sequence[str], *, cwd_fd: int, env: Mapping[str, str],
        timeout: float | None = None,
    ) -> CommandResult: ...

    def run_with_input(
        self, argv: Sequence[str], *, cwd_fd: int, env: Mapping[str, str], source_fd: int,
        timeout: float | None = None,
    ) -> CommandResult: ...

    def start(
        self, argv: Sequence[str], *, cwd_fd: int, env: Mapping[str, str], output_fd: int,
    ) -> RunnerProcess: ...


class SubprocessRunnerCommands:
    """Run argv children after dropping privileges and fchdir'ing by fd."""

    def __init__(
        self,
        identity: RunnerIdentity,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.identity = identity
        self._popen = popen

    def _preexec(self, cwd_fd: int) -> Callable[[], None]:
        identity = self.identity

        def switch() -> None:
            os.setgroups(list(identity.groups))
            os.setgid(identity.gid)
            os.setuid(identity.uid)
            os.fchdir(cwd_fd)

        return switch

    def run(
        self, argv: Sequence[str], *, cwd_fd: int, env: Mapping[str, str],
        timeout: float | None = None,
    ) -> CommandResult:
        process = self._popen(
            list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=dict(env), close_fds=True, pass_fds=(cwd_fd,), preexec_fn=self._preexec(cwd_fd),
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            raise
        return CommandResult(process.returncode, stdout or b"", stderr or b"")

    def run_with_input(
        self, argv: Sequence[str], *, cwd_fd: int, env: Mapping[str, str], source_fd: int,
        timeout: float | None = None,
    ) -> CommandResult:
        process = self._popen(
            list(argv), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            env=dict(env), close_fds=True, pass_fds=(cwd_fd,), preexec_fn=self._preexec(cwd_fd),
        )
        assert process.stdin is not None
        try:
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                process.stdin.write(chunk)
            process.stdin.close()
            process.stdin = None
            stderr = process.communicate(timeout=timeout)[1]
        except (BrokenPipeError, OSError):
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
                process.stdin = None
            _stdout, stderr = process.communicate()
        except subprocess.TimeoutExpired:
            process.kill()
            _stdout, stderr = process.communicate()
            raise
        return CommandResult(process.returncode, b"", stderr or b"")

    def start(
        self, argv: Sequence[str], *, cwd_fd: int, env: Mapping[str, str], output_fd: int,
    ) -> RunnerProcess:
        return self._popen(
            list(argv), stdin=subprocess.DEVNULL, stdout=output_fd, stderr=subprocess.STDOUT,
            env=dict(env), close_fds=True, pass_fds=(cwd_fd, output_fd),
            preexec_fn=self._preexec(cwd_fd), start_new_session=True,
        )


class DockerClient:
    """Small argv-only Docker inspection adapter used by the executor."""

    def __init__(
        self,
        timeout_s: float,
        *,
        run: Callable[..., Any] = subprocess.run,
    ) -> None:
        self.timeout_s = timeout_s
        self._run = run

    def command(self, *argv: str) -> CommandResult:
        try:
            result = self._run(
                ["docker", *argv], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=False, check=False, timeout=self.timeout_s,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker query failed: {exc}") from exc
        return CommandResult(result.returncode, result.stdout or b"", result.stderr or b"")

    def image(self, image: str) -> Mapping[str, object]:
        result = self.command("image", "inspect", image)
        if result.returncode != 0:
            raise AdmissionError(ExitCode.PREREQUISITE_MISSING, f"Docker image {image!r} is not present")
        try:
            value = json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker returned unreadable image inspection: {exc}") from exc
        if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "Docker returned an invalid image inspection")
        return value[0]

    def check_passwd(self, image: str, username_or_uid: str, job_id: str) -> None:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "-", job_id)[:100]
        result = self.command(
            "run", "--rm", "--pull=never", "--network", "none", "--name", f"fleetd-uid-{safe}",
            "--entrypoint", "getent", image, "passwd", username_or_uid,
        )
        if result.returncode != 0:
            raise AdmissionError(
                ExitCode.PREREQUISITE_MISSING,
                f"image {image!r} has no passwd entry for lane user {username_or_uid!r}",
            )

    def inspect(self, target: str) -> Mapping[str, object] | None:
        result = self.command("inspect", target)
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace").strip()
            if detail == f"Error: No such object: {target}":
                return None
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker inspect {target!r} failed: {detail}")
        try:
            value = json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker inspect {target!r} returned invalid JSON") from exc
        if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker inspect {target!r} returned an invalid object")
        return value[0]


def _read_regular(path: Path, *, limit: int = 65536) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except OSError as exc:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot open {path.name}: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"{path.name} is not a regular file")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(fd, min(65536, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"{path.name} exceeds {limit} bytes")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _open_absolute_directory(path: Path) -> int:
    if not path.is_absolute() or ".." in path.parts:
        raise AdmissionError(ExitCode.REFUSAL, "repo root must be an absolute non-escaping path")
    root_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return open_beneath(root_fd, path.relative_to("/").as_posix(), directory=True)
    except (OSError, ValueError) as exc:
        raise AdmissionError(ExitCode.REFUSAL, f"repo root is unavailable or contains a symlink: {exc}") from exc
    finally:
        os.close(root_fd)


def _close(*fds: int) -> None:
    for fd in fds:
        if fd >= 0:
            os.close(fd)


def _parse_toml_fd(root_fd: int, relative: str) -> Mapping[str, object] | None:
    try:
        fd = open_beneath(root_fd, relative)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise AdmissionError(ExitCode.REFUSAL, f"Run-Gate config path {relative!r} is unsafe: {exc}") from exc
    try:
        raw = os.read(fd, 1024 * 1024 + 1)
    finally:
        os.close(fd)
    if len(raw) > 1024 * 1024:
        raise AdmissionError(ExitCode.PREREQUISITE_MISSING, f"Run-Gate config {relative!r} exceeds 1 MiB")
    try:
        result = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise AdmissionError(ExitCode.PREREQUISITE_MISSING, f"Run-Gate config {relative!r} is unreadable: {exc}") from exc
    if not isinstance(result, dict):
        raise AdmissionError(ExitCode.PREREQUISITE_MISSING, f"Run-Gate config {relative!r} is not a table")
    return result


def _lane_environment(
    checkout_fd: int, project: str, lane_name: str,
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    project_config_path = "run-gate.toml" if project == "." else f"{project}/run-gate.toml"
    project_config = _parse_toml_fd(checkout_fd, project_config_path)
    root_config = _parse_toml_fd(checkout_fd, "run-gate.root.toml")
    project_config = project_config or {}
    root_config = root_config or {}
    project_lanes = project_config.get("lanes")
    root_lanes = root_config.get("lanes")
    lane = project_lanes.get(lane_name) if isinstance(project_lanes, Mapping) else None
    if lane is None and isinstance(root_lanes, Mapping):
        lane = root_lanes.get(lane_name)
    if not isinstance(lane, Mapping):
        raise AdmissionError(ExitCode.PREREQUISITE_MISSING, f"Run-Gate lane {lane_name!r} is not declared")
    environment_name = lane.get("environment")
    if not isinstance(environment_name, str) or not environment_name:
        raise AdmissionError(ExitCode.PREREQUISITE_MISSING, f"Run-Gate lane {lane_name!r} has no environment")
    root_environments = root_config.get("environments")
    project_environments = project_config.get("environments")
    environment: dict[str, object] = {}
    if isinstance(root_environments, Mapping) and isinstance(root_environments.get(environment_name), Mapping):
        environment.update(root_environments[environment_name])
    if isinstance(project_environments, Mapping) and isinstance(project_environments.get(environment_name), Mapping):
        environment.update(project_environments[environment_name])
    mode = environment.get("mode")
    if mode not in {"host", "exec", "ephemeral"}:
        raise AdmissionError(ExitCode.PREREQUISITE_MISSING, f"Run-Gate environment {environment_name!r} has no valid mode")
    return lane, environment


def _exec_container_name(
    checkout_fd: int, environment_name: str, environment: Mapping[str, object],
) -> str:
    declared = environment.get("container_name")
    if isinstance(declared, str) and declared:
        return declared
    deploy = _parse_toml_fd(checkout_fd, "ciu.global.toml")
    deploy_table = deploy.get("deploy") if isinstance(deploy, Mapping) else None
    if not isinstance(deploy_table, Mapping):
        raise AdmissionError(ExitCode.PREREQUISITE_MISSING, "exec-mode lane has no persistent runner name")
    project_name = deploy_table.get("project_name")
    environment_tag = deploy_table.get("environment_tag")
    if isinstance(project_name, str) and project_name and isinstance(environment_tag, str) and environment_tag:
        return f"{project_name}-{environment_tag}-{environment_name}"
    network = deploy_table.get("network_name")
    if isinstance(network, str) and network.endswith("-network"):
        return f"{network[:-len('-network')]}-{environment_name}"
    raise AdmissionError(ExitCode.PREREQUISITE_MISSING, "exec-mode lane has no persistent runner name")


def _version_key(value: str) -> tuple[tuple[int, ...], int, int, int, int]:
    match = _RG_VERSION_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"unsupported run-gate version {value!r}")
    release = tuple(int(part) for part in match.group(1).split("."))
    release = release + (0,) * (8 - len(release))
    prerelease = {None: 4, "a": 1, "b": 2, "rc": 3}[match.group(2)]
    if match.group(5) and not match.group(4):
        prerelease = 0
    return (
        release, prerelease, int(match.group(3) or 0),
        int(match.group(4)) if match.group(4) else -1,
        int(match.group(5)) if match.group(5) else 10**9,
    )


def _version_from_output(output: bytes) -> str | None:
    try:
        first_line = output.decode("utf-8").splitlines()[0]
    except (UnicodeDecodeError, IndexError):
        return None
    versions = re.findall(
        r"(?<![0-9.])\d+(?:\.\d+)*(?:(?:a|b|rc)\d+)?(?:\.post\d+)?(?:\.dev\d+)?(?:\+[A-Za-z0-9.]+)?",
        first_line,
    )
    return versions[-1] if versions else None


class HostJobWorker:
    """One admitted job's checkout, prerequisites, preflight, and Run-Gate call."""

    def __init__(
        self,
        state_dir: Path,
        policy: AgentPolicy,
        job_id: str,
        lock_fd: int,
        *,
        user_resolver: Callable[[str], RunnerIdentity] = _resolve_system_user,
        runner_factory: Callable[[RunnerIdentity], RunnerCommands] = SubprocessRunnerCommands,
        docker: DockerClient | None = None,
        pressure_reader: Callable[[str], str] | None = None,
        meminfo_reader: Callable[[], str] | None = None,
        waiter: Callable[[float], None] = time.sleep,
        clock: Callable[[], str] | None = None,
        run_gate_finder: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.jobs_dir = self.state_dir / "jobs"
        self.job_id = _job_id(job_id)
        self.job_dir = self.jobs_dir / self.job_id
        self.policy = policy
        self.lock_fd = lock_fd
        self.user_resolver = user_resolver
        self.runner_factory = runner_factory
        self.docker = docker or DockerClient(policy.query_timeout_s)
        self.pressure_reader = pressure_reader or _read_text_file
        self.meminfo_reader = meminfo_reader or _read_meminfo
        self.waiter = waiter
        self.clock = clock or _utc_now
        self.run_gate_finder = run_gate_finder
        self.request: dict[str, object] = {}
        self.group = ""
        self.runner_identity: RunnerIdentity | None = None
        self.runner: RunnerCommands | None = None
        self.repo_root = Path()
        self.repo_root_fd = -1
        self.checkout_rel = ""
        self.checkout_fd = -1
        self.project_fd = -1
        self.event_rel = ""

    def _state(self) -> dict[str, object]:
        raw = _read_regular(self.job_dir / "state.json")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"executor state is unreadable: {exc}") from exc
        if not isinstance(value, dict) or value.get("schema") != "fleet-host-job-state/1" or value.get("job_id") != self.job_id:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "executor state identity is invalid")
        return value

    def _save_state(self, **updates: object) -> dict[str, object]:
        state = self._state()
        state.update(updates)
        _atomic_json(self.job_dir / "state.json", state)
        return state

    def _log(self, message: str) -> None:
        text = " ".join(str(message).splitlines())[:2000]
        fd = os.open(self.job_dir / "job.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, f"{self.clock()} {text}\n".encode("utf-8", "replace"))
            os.fsync(fd)
        finally:
            os.close(fd)

    def _read_request(self) -> None:
        raw = _read_regular(self.job_dir / "request.json")
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"durable request is unreadable: {exc}") from exc
        if not isinstance(record, dict) or record.get("schema") != "fleet-host-job-request/1":
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "durable request schema is invalid")
        group = record.get("group")
        request = record.get("request")
        if not isinstance(group, str) or not isinstance(request, dict) or request.get("job_id") != self.job_id:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "durable request identity is invalid")
        self.group = group
        self.request = request
        try:
            admission = AgentAdmission.request_model(request)
        except (KeyError, TypeError, ValueError) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"durable request payload is invalid: {exc}") from exc
        digest = admission.digest()
        if record.get("digest") != digest:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "durable request digest does not match its payload")

    def _environment(self) -> dict[str, str]:
        assert self.runner_identity is not None
        path = os.environ.get("PATH", os.defpath)
        env = {
            "PATH": path,
            "HOME": self.runner_identity.home,
            "USER": self.runner_identity.username,
            "LOGNAME": self.runner_identity.username,
            "RUN_GATE_PROFILE": "off",
            "RUN_GATE_ALLOW_MAIN_CHECKOUT": "1",
            "RUN_GATE_JOB": self.job_id,
            "ASSAY_GATE_HOST_REPO_ROOT": str(self.repo_root),
        }
        extra = self.request.get("env")
        if isinstance(extra, Mapping):
            for key, value in extra.items():
                if isinstance(key, str) and isinstance(value, str):
                    env[key] = value
        return env

    def _run(self, argv: Sequence[str], *, cwd_fd: int | None = None, timeout: float | None = None) -> CommandResult:
        if self.runner is None:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, "runner command interface is unavailable")
        result = self.runner.run(argv, cwd_fd=self.repo_root_fd if cwd_fd is None else cwd_fd,
                                 env=self._environment(), timeout=timeout)
        return result

    def _run_checked(
        self, argv: Sequence[str], *, cwd_fd: int | None = None, exit_code: int = ExitCode.RUNNER_FAILURE,
        detail: str,
    ) -> CommandResult:
        result = self._run(argv, cwd_fd=cwd_fd)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", "replace").strip()
            raise AdmissionError(exit_code, f"{detail}{': ' + stderr if stderr else f' (exit {result.returncode})'}")
        return result

    def _helper(self, mode: str, *args: str, cwd_fd: int | None = None) -> CommandResult:
        return self._run_checked(
            [sys.executable, "-m", "fleetd.executor_worker", mode, *args],
            cwd_fd=self.repo_root_fd if cwd_fd is None else cwd_fd,
            detail=f"runner-user helper {mode!r} failed",
        )

    def _create_checkout(self) -> None:
        repo = self.request.get("repo")
        sha = self.request.get("sha")
        if not isinstance(repo, str) or repo not in self.policy.repo_roots:
            raise AdmissionError(ExitCode.REFUSAL, "job request names an unknown repo")
        if not isinstance(sha, str) or _COMMIT_RE.fullmatch(sha) is None:
            raise AdmissionError(ExitCode.REFUSAL, "job request sha is not a full commit id")
        self.repo_root = self.policy.repo_roots[repo]
        self.repo_root_fd = _open_absolute_directory(self.repo_root)
        self.checkout_rel = f".fleetd-worktrees/{self.job_id}"
        self._helper("prepare", "--relative", self.checkout_rel)
        try:
            self.checkout_fd = open_beneath(self.repo_root_fd, self.checkout_rel, directory=True)
        except (OSError, ValueError) as exc:
            raise AdmissionError(ExitCode.REFUSAL, f"host checkout path is unsafe: {exc}") from exc
        checkout_record = {"schema": "fleet-host-checkout/1", "repo": repo, "relative": self.checkout_rel}
        _atomic_json(self.job_dir / "checkout.json", checkout_record)

        # A prior have() result may let the client omit its bundle.  The
        # existing host checkout is then the seed; otherwise a verified bundle
        # is fetched below.  Both paths execute git as the runner user.
        source_rel = sha
        source_fd = -1
        try:
            source_fd = open_beneath(self.repo_root_fd, source_rel, directory=True)
        except FileNotFoundError:
            source_fd = -1
        except (OSError, ValueError) as exc:
            raise AdmissionError(ExitCode.REFUSAL, f"existing checkout {sha!r} is unsafe: {exc}") from exc
        if source_fd >= 0:
            _close(self.checkout_fd)
            self.checkout_fd = -1
            try:
                self._run_checked(
                    ["git", "clone", "--quiet", "--no-checkout", str(self.repo_root / source_rel),
                     str(self.repo_root / self.checkout_rel)],
                    cwd_fd=self.repo_root_fd,
                    exit_code=ExitCode.CHECKOUT_FAILURE,
                    detail="cannot clone the host's existing checkout",
                )
            finally:
                _close(source_fd)
            self.checkout_fd = open_beneath(self.repo_root_fd, self.checkout_rel, directory=True)
        else:
            self._run_checked(
                ["git", "init", "--quiet", "--initial-branch=main", str(self.repo_root / self.checkout_rel)],
                cwd_fd=self.repo_root_fd, exit_code=ExitCode.CHECKOUT_FAILURE, detail="cannot initialize job checkout",
            )
            self.checkout_fd = open_beneath(self.repo_root_fd, self.checkout_rel, directory=True)
            self._fetch_uploaded_bundle()

        base = self.request.get("base")
        if isinstance(base, str):
            base_result = self._run(["git", "-C", str(self.repo_root / self.checkout_rel), "cat-file", "-e", f"{base}^{{commit}}"])
            if base_result.returncode != 0:
                self._log(f"base {base} missing on the host")
                raise AdmissionError(ExitCode.MISSING_BASE, f"base {base} missing on the host")
        self._run_checked(
            ["git", "-C", str(self.repo_root / self.checkout_rel), "checkout", "--quiet", "--detach", sha],
            cwd_fd=self.repo_root_fd, exit_code=ExitCode.CHECKOUT_FAILURE, detail="cannot check out requested commit",
        )
        actual = self._run_checked(
            ["git", "-C", str(self.repo_root / self.checkout_rel), "rev-parse", "HEAD"],
            cwd_fd=self.repo_root_fd, exit_code=ExitCode.CHECKOUT_FAILURE, detail="cannot read host checkout commit",
        ).stdout.decode("ascii", "strict").strip()
        if actual != sha:
            raise AdmissionError(ExitCode.CHECKOUT_FAILURE, f"host checkout commit {actual!r} does not match request sha")
        self._log(f"checkout {self.repo_root / self.checkout_rel} at {actual}")
        self._save_state(checkout={"ready": True, "sha": actual})

    def _uploaded_objects(self, name: str) -> list[tuple[str, int]]:
        root_fd = os.open(self.state_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        uploads_fd = group_fd = name_fd = -1
        try:
            uploads_fd = open_beneath(root_fd, "uploads", directory=True)
            group_fd = open_beneath(uploads_fd, self.group, directory=True)
            name_fd = open_beneath(group_fd, name, directory=True)
            result: list[tuple[str, int]] = []
            for digest in sorted(os.listdir(name_fd)):
                if _SHA256_RE.fullmatch(digest) is None:
                    continue
                try:
                    fd = open_beneath(name_fd, digest)
                except (OSError, ValueError):
                    continue
                try:
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    hasher = hashlib.sha256()
                    while True:
                        chunk = os.read(fd, 1024 * 1024)
                        if not chunk:
                            break
                        hasher.update(chunk)
                    if hasher.hexdigest() == digest:
                        result.append((digest, info.st_size))
                finally:
                    os.close(fd)
            return result
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot inspect host upload objects: {exc}") from exc
        finally:
            _close(name_fd, group_fd, uploads_fd, root_fd)

    def _fetch_uploaded_bundle(self) -> None:
        objects = self._uploaded_objects("in.bundle")
        if not objects:
            raise AdmissionError(ExitCode.CHECKOUT_FAILURE, "no verified in.bundle upload contains the requested commit")
        assert self.runner is not None
        for digest, size in objects:
            root_fd = os.open(self.state_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            source_fd = -1
            uploads_fd = group_fd = name_fd = -1
            try:
                uploads_fd = open_beneath(root_fd, "uploads", directory=True)
                group_fd = open_beneath(uploads_fd, self.group, directory=True)
                name_fd = open_beneath(group_fd, "in.bundle", directory=True)
                source_fd = open_beneath(name_fd, digest)
                target_rel = f"{self.checkout_rel}/in.bundle-{digest}"
                copied = self.runner.run_with_input(
                    [
                        sys.executable, "-m", "fleetd.executor_worker", "write-file",
                        "--relative", target_rel, "--sha256", digest, "--bytes", str(size),
                    ],
                    cwd_fd=self.repo_root_fd, env=self._environment(), source_fd=source_fd,
                )
                if copied.returncode != 0:
                    raise AdmissionError(ExitCode.RUNNER_FAILURE, "runner could not stage the verified bundle")
            except OSError as exc:
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot open verified bundle upload: {exc}") from exc
            finally:
                _close(source_fd, name_fd, group_fd, uploads_fd, root_fd)
            bundle = str(self.repo_root / target_rel)
            fetched = self._run(
                ["git", "-C", str(self.repo_root / self.checkout_rel), "fetch", "--quiet", bundle, "HEAD"],
                cwd_fd=self.repo_root_fd,
            )
            if fetched.returncode == 0:
                tags = self._run([
                    "git", "-C", str(self.repo_root / self.checkout_rel), "fetch", "--quiet",
                    bundle, "refs/tags/*:refs/tags/*",
                ])
                if tags.returncode != 0:
                    self._log("release tag fetch failed; retaining bundle HEAD")
                for ref in ("refs/remote-gate/base", "refs/remotes/origin/main"):
                    self._run([
                        "git", "-C", str(self.repo_root / self.checkout_rel), "fetch", "--quiet", bundle, ref,
                    ])
                return
        raise AdmissionError(ExitCode.CHECKOUT_FAILURE, "verified host bundles do not contain the requested commit")

    def _select_run_gate(self) -> tuple[str, str]:
        assert self.runner is not None
        candidates: list[str] = []
        discovered = self.run_gate_finder("run-gate")
        if discovered:
            candidates.append(discovered)
        if _RG_FALLBACK not in candidates:
            candidates.append(_RG_FALLBACK)
        rejected: list[str] = []
        floor = _version_key(_RG_VERSION_FLOOR)
        for binary in candidates:
            try:
                answer = self.runner.run(
                    [binary, "--version"], cwd_fd=self.repo_root_fd, env=self._environment(),
                    timeout=self.policy.query_timeout_s,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                rejected.append(f"{binary}: {exc}")
                continue
            version = _version_from_output(answer.stdout)
            if answer.returncode != 0 or version is None:
                rejected.append(f"{binary}: --version was unusable (exit {answer.returncode})")
                continue
            try:
                supported = _version_key(version) >= floor
            except ValueError:
                supported = False
            if supported:
                return binary, version
            rejected.append(f"{binary}: version {version} is below {_RG_VERSION_FLOOR}")
        detail = "; ".join(rejected) if rejected else "no run-gate executable was found"
        raise AdmissionError(ExitCode.PREREQUISITE_MISSING, f"no usable run-gate >= {_RG_VERSION_FLOOR}: {detail}")

    def _prerequisites(self) -> tuple[str, str, Mapping[str, object]]:
        assert self.runner is not None and self.checkout_fd >= 0
        binary, version = self._select_run_gate()
        project = self.request.get("project")
        lane_name = self.request.get("lane")
        if not isinstance(project, str) or not isinstance(lane_name, str):
            raise AdmissionError(ExitCode.REFUSAL, "request project or lane is invalid")
        lane, environment = _lane_environment(self.checkout_fd, project, lane_name)
        mode = str(environment["mode"])
        if mode == "ephemeral":
            image = environment.get("image")
            image_env = environment.get("image_env")
            if isinstance(image_env, str):
                image = os.environ.get(image_env)
            if not isinstance(image, str) or _IMAGE_RE.fullmatch(image) is None:
                raise AdmissionError(ExitCode.PREREQUISITE_MISSING, "ephemeral Run-Gate environment has no resolved image")
            image_info = self.docker.image(image)
            self._check_lane_user(image, image_info, environment)
            return binary, mode, {"image": image, "lane": lane, "environment": environment,
                                 "run_gate_version": version}
        if mode == "exec":
            container_name = _exec_container_name(self.checkout_fd, str(lane["environment"]), environment)
            if _CONTAINER_NAME_RE.fullmatch(container_name) is None:
                raise AdmissionError(ExitCode.PREREQUISITE_MISSING, "exec-mode persistent runner name is invalid")
            existing = self.docker.inspect(container_name)
            container_state = existing.get("State") if isinstance(existing, Mapping) else None
            if existing is None or not isinstance(container_state, Mapping) or container_state.get("Running") is not True:
                raise AdmissionError(ExitCode.PREREQUISITE_MISSING, f"persistent runner {container_name!r} is not running")
            return binary, mode, {"container_name": container_name, "lane": lane, "environment": environment,
                                 "run_gate_version": version}
        return binary, mode, {"lane": lane, "environment": environment, "run_gate_version": version}

    def _check_lane_user(
        self, image: str, image_info: Mapping[str, object], environment: Mapping[str, object],
    ) -> None:
        declared = environment.get("user")
        if isinstance(declared, str) and declared:
            user = declared.split(":", 1)[0]
        else:
            config = image_info.get("Config")
            config_user = config.get("User") if isinstance(config, Mapping) else None
            user = config_user if isinstance(config_user, str) else ""
        if user in {"", "0", "root"}:
            return
        if user.isdigit():
            if int(user) == 0:
                return
            self.docker.check_passwd(image, user, self.job_id)
            return
        # Named users are established facts only after the selected image
        # resolves them.  Run-Gate's own preflight handles non-numeric users;
        # this check ensures the user exists without inventing a uid.
        self.docker.check_passwd(image, user, self.job_id)

    def _wait_for_psi(self) -> None:
        waited = 0
        while True:
            try:
                memory = _pressure_value(self.pressure_reader("/proc/pressure/memory"), "some")
                io = _pressure_value(self.pressure_reader("/proc/pressure/io"), "full")
            except (OSError, ValueError) as exc:
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"PSI facts are unreadable: {exc}") from exc
            if max(memory, io) <= _PSI_LIMIT:
                self._log(f"PSI ok (waited {waited}s)")
                return
            if waited >= _PSI_WAIT_S:
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"PSI above {_PSI_LIMIT:g} after {waited}s")
            self.waiter(_PSI_POLL_S)
            waited += _PSI_POLL_S

    def _memory_admission(self) -> int | None:
        resources = self.request.get("resources")
        peak = resources.get("declared_peak_bytes") if isinstance(resources, Mapping) else None
        if peak is None:
            return None
        if type(peak) is not int or peak < 0:
            raise AdmissionError(ExitCode.REFUSAL, "declared_peak_bytes is invalid")
        needed = peak // (1024 * 1024)
        if needed == 0:
            return None
        path = self.state_dir / "memory-admit.lock"
        try:
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        except OSError as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot open memory admission ticket: {exc}") from exc
        waited = 0
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if waited >= _MEMORY_WAIT_S:
                        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "memory admission ticket wait expired")
                    self.waiter(_MEMORY_POLL_S)
                    waited += _MEMORY_POLL_S
            while True:
                try:
                    total, available = _parse_meminfo(self.meminfo_reader())
                except (OSError, ValueError) as exc:
                    raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"memory facts are unreadable: {exc}") from exc
                effective_need = min(needed, max(0, total - 2 * _MEMORY_FLOOR_MIB))
                if available - effective_need >= _MEMORY_FLOOR_MIB:
                    self._log(f"memory admission ok (waited {waited}s)")
                    return fd
                if waited >= _MEMORY_WAIT_S:
                    raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "memory admission wait expired")
                self.waiter(_MEMORY_POLL_S)
                waited += _MEMORY_POLL_S
        except BaseException:
            os.close(fd)
            raise

    def _seed_stores(self) -> None:
        stores = self.request.get("stores")
        seed = stores.get("seed") if isinstance(stores, Mapping) else None
        if seed is None:
            return
        if not isinstance(seed, Mapping):
            raise AdmissionError(ExitCode.STORE_SEED_FAILED, "STORE-SEED-FAILED . invalid-descriptor")
        target = seed.get("path")
        digest = seed.get("sha256")
        size = seed.get("bytes")
        if (
            not isinstance(target, str) or _STORE_PATH_RE.fullmatch(target) is None
            or any(part in {".", "..", ".git"} for part in target.split("/"))
            or not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None
            or type(size) is not int or size <= 0
        ):
            raise AdmissionError(ExitCode.STORE_SEED_FAILED, f"STORE-SEED-FAILED {target!s} invalid-descriptor")
        upload_name = self._find_seed_upload(str(digest), int(size))
        if upload_name is None:
            raise AdmissionError(ExitCode.STORE_SEED_FAILED, f"STORE-SEED-FAILED {target} checksum")
        tracked = self._run([
            "git", "-C", str(self.repo_root / self.checkout_rel), "ls-files", "--", target,
        ], cwd_fd=self.repo_root_fd)
        if tracked.returncode != 0:
            raise AdmissionError(ExitCode.STORE_SEED_FAILED, f"STORE-SEED-FAILED {target} tracked-check")
        if tracked.stdout:
            raise AdmissionError(ExitCode.STORE_SEED_FAILED, f"STORE-SEED-FAILED {target} tracked-files")
        source_fd = self._open_upload_object(upload_name, str(digest))
        try:
            result = self.runner.run_with_input(
                [sys.executable, "-m", "fleetd.executor_worker", "extract-seed",
                 "--relative", target, "--sha256", str(digest), "--bytes", str(size),
                 "--max-bytes", str(_STORE_MAX_BYTES)],
                cwd_fd=self.checkout_fd, env=self._environment(), source_fd=source_fd,
            )
        finally:
            os.close(source_fd)
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace").strip()
            raise AdmissionError(ExitCode.STORE_SEED_FAILED, detail or f"STORE-SEED-FAILED {target} unpack-failed")
        self._log(f"STORE-SEEDED {target}")

    def _find_seed_upload(self, digest: str, size: int) -> str | None:
        if _SHA256_RE.fullmatch(digest) is None:
            return None
        root_fd = os.open(self.state_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        uploads_fd = group_fd = -1
        try:
            uploads_fd = open_beneath(root_fd, "uploads", directory=True)
            group_fd = open_beneath(uploads_fd, self.group, directory=True)
            for name in sorted(os.listdir(group_fd)):
                if re.fullmatch(r"assay-seed-[0-9]+\.tgz", name) is None:
                    continue
                if not any(
                    found_digest == digest and found_size == size
                    for found_digest, found_size in self._uploaded_objects(name)
                ):
                    continue
                checksum_name = f"{name}.sha256"
                for checksum_digest, checksum_size in self._uploaded_objects(checksum_name):
                    if checksum_size > 512:
                        continue
                    checksum_fd = self._open_upload_object(checksum_name, checksum_digest)
                    try:
                        checksum = os.read(checksum_fd, 513)
                    finally:
                        os.close(checksum_fd)
                    if checksum == f"{digest}  {name}\n".encode("ascii"):
                        return name
            return None
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot inspect seed upload directory: {exc}") from exc
        finally:
            _close(group_fd, uploads_fd, root_fd)

    def _open_upload_object(self, name: str, digest: str) -> int:
        root_fd = os.open(self.state_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        uploads_fd = group_fd = name_fd = -1
        try:
            uploads_fd = open_beneath(root_fd, "uploads", directory=True)
            group_fd = open_beneath(uploads_fd, self.group, directory=True)
            name_fd = open_beneath(group_fd, name, directory=True)
            fd = open_beneath(name_fd, digest)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                os.close(fd)
                raise AdmissionError(ExitCode.REFUSAL, "uploaded object is not a regular file")
            return fd
        except (OSError, ValueError) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot open uploaded object: {exc}") from exc
        finally:
            _close(name_fd, group_fd, uploads_fd, root_fd)

    def _run_lane(self, binary: str, mode: str, facts: Mapping[str, object], memory_lock_fd: int | None) -> None:
        assert self.runner is not None and self.checkout_fd >= 0
        project = self.request.get("project")
        lane = self.request.get("lane")
        if not isinstance(project, str) or not isinstance(lane, str):
            raise AdmissionError(ExitCode.REFUSAL, "request project or lane is invalid")
        project_rel = "" if project == "." else project
        self.project_fd = os.dup(self.checkout_fd) if not project_rel else open_beneath(self.checkout_fd, project_rel, directory=True)
        self.event_rel = f".fleetd-events/{self.job_id}.jsonl"
        self._helper("prepare-event", "--relative", self.event_rel)
        argv = [binary, lane]
        extra = self.request.get("extra_argv")
        if not isinstance(extra, list) or any(not isinstance(item, str) for item in extra):
            raise AdmissionError(ExitCode.REFUSAL, "request extra_argv is invalid")
        argv.extend(extra)
        base = self.request.get("base")
        if isinstance(base, str):
            argv.extend(("--base", base))
        argv.extend(("--event-file", str(self.repo_root / self.event_rel), "--job-id", self.job_id))
        if mode == "exec":
            env = dict(self._environment())
            env["RUN_GATE_EXPECTED_RUNNER"] = str(facts["container_name"])
        else:
            env = self._environment()
        log_fd = os.open(self.job_dir / "lane.log", os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            process = self.runner.start(argv, cwd_fd=self.project_fd, env=env, output_fd=log_fd)
        finally:
            os.close(log_fd)
        self._save_state(lane_pid=process.pid, state="running", lane=lane, container={
            "id": None, "name": facts.get("container_name"), "image": facts.get("image"),
        })
        if memory_lock_fd is not None:
            self.waiter(_MEMORY_SETTLE_S)
            fcntl.flock(memory_lock_fd, fcntl.LOCK_UN)
            os.close(memory_lock_fd)
        status = process.wait()
        self._save_state(state="collecting", lane_process_status=status, exit_code=status)
        self._log(f"lane exited rc={status}")

    def run(self) -> int:
        """Run checkout and lane prerequisites, then return the Run-Gate status."""

        try:
            self._read_request()
            try:
                self.runner_identity = self.user_resolver(self.policy.runner_user)
            except (KeyError, LookupError, OSError, ValueError) as exc:
                raise AdmissionError(ExitCode.REFUSAL, f"configured runner user is unknown: {exc}") from exc
            if self.runner_identity.uid == 0:
                raise AdmissionError(ExitCode.REFUSAL, "agent.toml.runner_user must resolve to a non-root uid")
            self.runner = self.runner_factory(self.runner_identity)
            self._save_state(state="running", api="fleet-host-agent/1", lane=self.request.get("lane"))
            self._log("executor worker started")
            self._create_checkout()
            if self.request.get("kind") != "lane":
                raise AdmissionError(ExitCode.REFUSAL, "kind=dev is outside the C3b-1 lane executor")
            binary, mode, facts = self._prerequisites()
            self._wait_for_psi()
            self._seed_stores()
            memory_lock_fd = self._memory_admission()
            self._run_lane(binary, mode, facts, memory_lock_fd)
            return 0
        except AdmissionError as exc:
            self._log(f"executor stopped exit={exc.exit_code}: {exc.reason}")
            self._save_state(
                state="failed", exit_code=exc.exit_code, verdict=None, reason=exc.reason,
                ended_utc=self.clock(),
            )
            return int(exc.exit_code)
        except (OSError, ValueError, UnicodeError, subprocess.SubprocessError) as exc:
            self._log(f"executor stopped exit={ExitCode.RUNNER_FAILURE}: {exc}")
            self._save_state(
                state="failed", exit_code=ExitCode.RUNNER_FAILURE, verdict=None,
                reason=str(exc), ended_utc=self.clock(),
            )
            return int(ExitCode.RUNNER_FAILURE)
        finally:
            _close(self.project_fd, self.checkout_fd, self.repo_root_fd)


def _read_text_file(path: str) -> str:
    with open(path, "r", encoding="ascii") as stream:
        return stream.read()


def _read_meminfo() -> str:
    return _read_text_file("/proc/meminfo")


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _pressure_value(text: str, row: str) -> float:
    for line in text.splitlines():
        fields = line.split()
        if fields and fields[0] == row:
            for field in fields[1:]:
                key, separator, value = field.partition("=")
                if separator and key == "avg10":
                    result = float(value)
                    if result < 0 or result != result:
                        break
                    return result
    raise ValueError(f"{row} avg10 is unavailable")


def _parse_meminfo(text: str) -> tuple[int, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, separator, rest = line.partition(":")
        if not separator:
            continue
        field = rest.strip().split()
        if field and field[0].isdigit():
            values[key] = int(field[0]) // 1024
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if total is None or total <= 0 or available is None or available < 0:
        raise ValueError("MemTotal/MemAvailable are unavailable")
    return total, available


def _mkdir_beneath(root_fd: int, relative: str) -> int:
    if not isinstance(relative, str) or relative.startswith("/") or "\\" in relative:
        raise ValueError("target directory must be relative")
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("target directory contains an unsafe component")
    current = os.dup(root_fd)
    try:
        for part in parts:
            try:
                os.mkdir(part, mode=0o700, dir_fd=current)
            except FileExistsError:
                pass
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = next_fd
        result, current = current, -1
        return result
    finally:
        _close(current)


def _open_parent(root_fd: int, relative: str) -> tuple[int, str]:
    parts = PurePosixPath(relative).parts
    if not parts or relative.startswith("/") or "\\" in relative or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("target file path is unsafe")
    parent_rel = "/".join(parts[:-1])
    parent_fd = os.dup(root_fd) if not parent_rel else open_beneath(root_fd, parent_rel, directory=True)
    return parent_fd, parts[-1]


def _remove_tree_at(parent_fd: int, name: str) -> None:
    """Remove one entry beneath parent_fd without following any links."""

    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        os.unlink(name, dir_fd=parent_fd)
        return
    child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        for child in os.listdir(child_fd):
            _remove_tree_at(child_fd, child)
    finally:
        os.close(child_fd)
    os.rmdir(name, dir_fd=parent_fd)


def _write_stdin_file(
    root_fd: int, relative: str, input_fd: int, expected_sha256: str, expected_bytes: int,
) -> None:
    if _SHA256_RE.fullmatch(expected_sha256) is None or expected_bytes <= 0:
        raise ValueError("uploaded file has an invalid expected digest or size")
    parent_fd, name = _open_parent(root_fd, relative)
    fd = -1
    created = False
    verified = False
    size = 0
    hasher = hashlib.sha256()
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
        created = True
        while True:
            chunk = os.read(input_fd, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > expected_bytes:
                raise ValueError("uploaded file size changed after host verification")
            hasher.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("uploaded file write made no progress")
                view = view[written:]
        if size != expected_bytes or hasher.hexdigest() != expected_sha256:
            raise ValueError("uploaded file digest or size changed after host verification")
        os.fsync(fd)
        verified = True
    finally:
        _close(fd)
        if created and not verified:
            try:
                os.unlink(name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        _close(parent_fd)


def _prepare_event(root_fd: int, relative: str) -> None:
    parent_rel, name = relative.rsplit("/", 1)
    parent_fd = _mkdir_beneath(root_fd, parent_rel)
    fd = -1
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
        os.fsync(fd)
        os.fsync(parent_fd)
    finally:
        _close(fd, parent_fd)


def _extract_seed(
    root_fd: int, input_fd: int, relative: str, expected_sha256: str, expected_bytes: int, max_bytes: int,
) -> None:
    if (
        _STORE_PATH_RE.fullmatch(relative) is None
        or any(part in {".", "..", ".git"} for part in relative.split("/"))
    ):
        raise ValueError(f"STORE-SEED-FAILED {relative or '.'} unsafe-target")
    if _SHA256_RE.fullmatch(expected_sha256) is None or expected_bytes <= 0 or max_bytes <= 0:
        raise ValueError(f"STORE-SEED-FAILED {relative} invalid-descriptor")
    temporary = f".fleetd-seed-input-{os.getpid()}-{time.monotonic_ns()}"
    fd = -1
    created = False
    size = 0
    hasher = hashlib.sha256()
    try:
        fd = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root_fd)
        created = True
        while True:
            chunk = os.read(input_fd, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > expected_bytes:
                raise ValueError(f"STORE-SEED-FAILED {relative} size")
            hasher.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(fd, view)
                view = view[written:]
        if size != expected_bytes:
            raise ValueError(f"STORE-SEED-FAILED {relative} size")
        if hasher.hexdigest() != expected_sha256:
            raise ValueError(f"STORE-SEED-FAILED {relative} checksum")
        os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(fd), "rb") as archive_stream:
            try:
                archive = tarfile.open(fileobj=archive_stream, mode="r:gz")
                members = archive.getmembers()
            except (OSError, tarfile.TarError, EOFError) as exc:
                raise ValueError(f"STORE-SEED-FAILED {relative} unreadable-archive") from exc
            seen: set[str] = set()
            total = 0
            normalized: list[tuple[tarfile.TarInfo, str]] = []
            for member in members:
                name = member.name.rstrip("/")
                parts = name.split("/")
                if (
                    not name or name.startswith("/") or "\\" in name
                    or any(part in {"", ".", ".."} for part in parts)
                    or not (name == relative or name.startswith(relative + "/"))
                ):
                    raise ValueError(f"STORE-SEED-FAILED {relative} member-outside-path")
                if name in seen:
                    raise ValueError(f"STORE-SEED-FAILED {relative} duplicate-member")
                seen.add(name)
                if member.isdir():
                    normalized.append((member, name))
                elif member.isfile():
                    if name == relative:
                        raise ValueError(f"STORE-SEED-FAILED {relative} target-is-not-directory")
                    if member.size < 0:
                        raise ValueError(f"STORE-SEED-FAILED {relative} invalid-size")
                    total += member.size
                    if total > max_bytes:
                        raise ValueError(f"STORE-SEED-FAILED {relative} too-large {total}")
                    normalized.append((member, name))
                else:
                    raise ValueError(f"STORE-SEED-FAILED {relative} special-member")

            target_parent = relative.rpartition("/")[0]
            if target_parent:
                parent_created_fd = _mkdir_beneath(root_fd, target_parent)
                os.close(parent_created_fd)
            parent_fd, target_name = _open_parent(root_fd, relative)
            try:
                try:
                    existing = os.stat(target_name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    existing = None
                if existing is not None:
                    if stat.S_ISLNK(existing.st_mode) or not stat.S_ISDIR(existing.st_mode):
                        raise ValueError(f"STORE-SEED-FAILED {relative} unsafe-existing-target")
                    _remove_tree_at(parent_fd, target_name)
            finally:
                os.close(parent_fd)

            target_fd = _mkdir_beneath(root_fd, relative)
            os.close(target_fd)
            for member, name in normalized:
                if member.isdir():
                    directory_fd = _mkdir_beneath(root_fd, name)
                    os.close(directory_fd)
                    continue
                parent_rel = name.rpartition("/")[0]
                if parent_rel:
                    created_parent = _mkdir_beneath(root_fd, parent_rel)
                    os.close(created_parent)
                parent_fd, leaf = _open_parent(root_fd, name)
                member_fd = -1
                try:
                    member_fd = os.open(
                        leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o644, dir_fd=parent_fd,
                    )
                    source = archive.extractfile(member)
                    if source is None:
                        raise ValueError(f"STORE-SEED-FAILED {relative} unreadable-archive")
                    with source:
                        remaining = member.size
                        while remaining:
                            chunk = source.read(min(65536, remaining))
                            if not chunk:
                                raise ValueError(f"STORE-SEED-FAILED {relative} unreadable-archive")
                            view = memoryview(chunk)
                            while view:
                                written = os.write(member_fd, view)
                                if written <= 0:
                                    raise OSError("seed member write made no progress")
                                view = view[written:]
                            remaining -= len(chunk)
                    os.fsync(member_fd)
                finally:
                    _close(member_fd, parent_fd)
            archive.close()
    except (tarfile.TarError, OSError) as exc:
        raise ValueError(f"STORE-SEED-FAILED {relative} unpack-failed") from exc
    finally:
        _close(fd)
        if created:
            try:
                os.unlink(temporary, dir_fd=root_fd)
            except FileNotFoundError:
                pass


def _helper_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="fleetd-executor-worker-helper")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "prepare-event", "write-file"):
        command = commands.add_parser(name)
        command.add_argument("--relative", required=True)
        if name == "write-file":
            command.add_argument("--sha256", required=True)
            command.add_argument("--bytes", type=int, required=True)
    seed = commands.add_parser("extract-seed")
    seed.add_argument("--relative", required=True)
    seed.add_argument("--sha256", required=True)
    seed.add_argument("--bytes", type=int, required=True)
    seed.add_argument("--max-bytes", type=int, required=True)
    args = parser.parse_args(argv)
    cwd_fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if args.command == "prepare":
            fd = _mkdir_beneath(cwd_fd, args.relative)
            os.close(fd)
        elif args.command == "prepare-event":
            _prepare_event(cwd_fd, args.relative)
        elif args.command == "extract-seed":
            _extract_seed(cwd_fd, 0, args.relative, args.sha256, args.bytes, args.max_bytes)
        else:
            _write_stdin_file(cwd_fd, args.relative, 0, args.sha256, args.bytes)
    except (OSError, ValueError, tarfile.TarError) as exc:
        print(f"fleetd-executor-worker: {exc}", file=sys.stderr)
        return int(ExitCode.RUNNER_FAILURE)
    finally:
        os.close(cwd_fd)
    return 0


def _worker_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="fleetd-executor-worker")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--lock-fd", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        policy = load_agent_policy(args.policy)
        os.fstat(args.lock_fd)
        worker = HostJobWorker(args.state_dir, policy, args.job_id, args.lock_fd)
        return worker.run()
    except (AdmissionError, OSError, ValueError) as exc:
        print(f"fleetd-executor-worker: {exc}", file=sys.stderr)
        return int(exc.exit_code) if isinstance(exc, AdmissionError) else int(ExitCode.RUNNER_FAILURE)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"prepare", "prepare-event", "write-file", "extract-seed"}:
        return _helper_main(args)
    if args and args[0] == "worker":
        return _worker_main(args[1:])
    print("fleetd-executor-worker: expected worker, prepare, prepare-event, or write-file", file=sys.stderr)
    return int(ExitCode.REFUSAL)


if __name__ == "__main__":
    raise SystemExit(main())
