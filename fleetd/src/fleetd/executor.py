"""Root-owned lifecycle boundary for admitted fleet host jobs.

The agent starts one detached systemd scope per job.  The scope process owns
the inherited admission flock, so restarting the socket service does not
release a live job's charge.  Repository operations are implemented by the
worker and run as ``agent.toml.runner_user``.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import re
import select
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence

from .admission import AdmissionError, AdmissionHandle, ExitCode
from .agent_policy import AgentPolicy, DEFAULT_AGENT_POLICY_PATH

_JOB_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_EXECUTOR_JSON_MAX = 1024 * 1024


@dataclass(frozen=True)
class RunnerIdentity:
    """Resolved non-root Unix identity for every repository-touching step."""

    username: str
    uid: int
    gid: int
    groups: tuple[int, ...]
    home: str = ""


@dataclass(frozen=True)
class ScopeState:
    """The systemd confirmation that the worker is running in its job scope."""

    unit: str
    active: bool
    worker_pid: int


class ScopeLauncher(Protocol):
    def launch(
        self, unit: str, argv: Sequence[str], lock_fd: int,
        handshake_read_fd: int, handshake_write_fd: int,
    ) -> ScopeState: ...


class ProcessSignals(Protocol):
    def terminate_group(self, pid: int) -> None: ...


class SystemProcessSignals:
    """Send TERM to the isolated Run-Gate process group."""

    def terminate_group(self, pid: int) -> None:
        os.killpg(pid, signal.SIGTERM)


class SystemdScopeLauncher:
    """Start a transient scope and confirm its exec'd worker through a pipe."""

    def __init__(
        self,
        query_timeout_s: float,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
        run: Callable[..., Any] = subprocess.run,
        pid_cgroup: Callable[[int], str] | None = None,
        waiter: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        reaper_start: Callable[[Callable[[], int]], None] | None = None,
    ) -> None:
        self.query_timeout_s = query_timeout_s
        self._popen = popen
        self._run = run
        self._pid_cgroup = pid_cgroup or _read_pid_cgroup
        self._waiter = waiter
        self._monotonic = monotonic
        self._reaper_start = reaper_start or _start_reaper

    def launch(
        self, unit: str, argv: Sequence[str], lock_fd: int,
        handshake_read_fd: int, handshake_write_fd: int,
    ) -> ScopeState:
        deadline = self._monotonic() + self.query_timeout_s
        command = ["systemd-run", "--scope", "--quiet", "--collect", "--unit", unit, "--", *argv]
        try:
            process = self._popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                pass_fds=(lock_fd, handshake_write_fd),
                start_new_session=True,
            )
        except OSError as exc:
            try:
                os.close(handshake_write_fd)
            except OSError:
                pass
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot start job scope {unit}: {exc}") from exc
        try:
            os.close(handshake_write_fd)
        except OSError:
            pass
        try:
            self._reaper_start(process.wait)
        except BaseException as exc:
            if isinstance(exc, Exception):
                raise AdmissionError(
                    ExitCode.RUNNER_FAILURE, f"cannot reap job scope command {unit}: {exc}",
                ) from exc
            # An interruption can arrive after the reaper thread was created.
            # Retry once to ensure the Popen child has an owner, then preserve
            # the interruption so HostJobExecutor leaves the start unconfirmed.
            try:
                self._reaper_start(process.wait)
            except BaseException as retry_exc:
                raise AdmissionError(
                    ExitCode.RUNNER_FAILURE,
                    f"cannot reap job scope command {unit} after interrupted reaper setup: {retry_exc}",
                ) from exc
            raise

        worker_pid = self._read_worker_handshake(handshake_read_fd, deadline, unit)
        if worker_pid != process.pid:
            raise AdmissionError(
                ExitCode.RUNNER_FAILURE,
                f"job scope {unit} handshake pid {worker_pid} differs from systemd-run exec pid {process.pid}",
            )

        while True:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise AdmissionError(ExitCode.RUNNER_FAILURE, f"timed out confirming active job scope {unit}")
            try:
                result = self._run(
                    ["systemctl", "show", "--no-pager", "--property=ActiveState", unit],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                    timeout=remaining,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                if self._monotonic() >= deadline:
                    raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot confirm job scope {unit}: {exc}") from exc
                self._waiter(min(0.05, remaining))
                continue
            active = result.returncode == 0 and any(
                line.strip() == "ActiveState=active" for line in str(result.stdout).splitlines()
            )
            if active:
                try:
                    cgroup = self._pid_cgroup(worker_pid)
                except (OSError, ValueError) as exc:
                    raise AdmissionError(
                        ExitCode.RUNNER_FAILURE, f"cannot read worker {worker_pid} cgroup: {exc}",
                    ) from exc
                if cgroup.rstrip("/").rsplit("/", 1)[-1] != unit:
                    raise AdmissionError(
                        ExitCode.RUNNER_FAILURE,
                        f"worker {worker_pid} is in cgroup {cgroup!r}, not scope {unit}",
                    )
                return ScopeState(unit, True, worker_pid)
            self._waiter(min(0.05, remaining))

    def _read_worker_handshake(self, read_fd: int, deadline: float, unit: str) -> int:
        received = bytearray()
        while b"\n" not in received:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise AdmissionError(ExitCode.RUNNER_FAILURE, f"worker handshake timed out for scope {unit}")
            readable, _, _ = select.select([read_fd], [], [], remaining)
            if not readable:
                raise AdmissionError(ExitCode.RUNNER_FAILURE, f"worker handshake timed out for scope {unit}")
            chunk = os.read(read_fd, 65 - len(received))
            if not chunk:
                raise AdmissionError(ExitCode.RUNNER_FAILURE, f"worker exited before its handshake for scope {unit}")
            received.extend(chunk)
            if len(received) > 64:
                raise AdmissionError(ExitCode.RUNNER_FAILURE, f"worker handshake is too large for scope {unit}")
        line, _tail = bytes(received).split(b"\n", 1)
        if not line.isdigit():
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"worker handshake is invalid for scope {unit}")
        pid = int(line)
        if pid <= 0:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"worker handshake pid is invalid for scope {unit}")
        return pid


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_pid_cgroup(pid: int) -> str:
    with open(f"/proc/{pid}/cgroup", "r", encoding="ascii") as stream:
        for line in stream:
            hierarchy, _controllers, path = line.rstrip("\n").split(":", 2)
            if hierarchy == "0":
                return path
    raise OSError(f"pid {pid} has no unified cgroup entry")


def _start_reaper(
    wait: Callable[[], int], *, thread_factory: Callable[..., Any] = threading.Thread,
) -> None:
    thread_factory(target=wait, daemon=True).start()


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_json(path: Path, document: Mapping[str, object]) -> None:
    temporary: str | None = None
    try:
        payload = json.dumps(
            document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
        if len(payload) > _EXECUTOR_JSON_MAX:
            raise ValueError(f"executor JSON record exceeds {_EXECUTOR_JSON_MAX} bytes")
        fd, temporary = tempfile.mkstemp(prefix=".executor-", dir=path.parent)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        _fsync_directory(path.parent)
    except (OSError, TypeError, ValueError) as exc:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot persist executor state: {exc}") from exc


@contextmanager
def _job_state_lock(job_dir: Path) -> Iterator[None]:
    """Serialize agent and worker read/modify/write operations on state.json."""

    try:
        fd = os.open(job_dir / "state.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot lock executor state: {exc}") from exc
    locked = False
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "executor state lock is not a private regular file")
        fcntl.flock(fd, fcntl.LOCK_EX)
        locked = True
        yield
    except OSError as exc:
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot lock executor state: {exc}") from exc
    finally:
        if locked:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _job_id(value: object) -> str:
    if not isinstance(value, str) or _JOB_ID_RE.fullmatch(value) is None:
        raise AdmissionError(ExitCode.REFUSAL, "executor job id is not a safe path component")
    return value


def validate_job_request(request: Mapping[str, object], policy: AgentPolicy) -> tuple[str, Path]:
    """Recheck path-bearing request fields at the root executor boundary."""

    job_id = _job_id(request.get("job_id"))
    repo = request.get("repo")
    if not isinstance(repo, str) or repo not in policy.repo_roots:
        raise AdmissionError(ExitCode.REFUSAL, "executor request names an unknown repository")
    sha = request.get("sha")
    if not isinstance(sha, str) or _COMMIT_RE.fullmatch(sha) is None:
        raise AdmissionError(ExitCode.REFUSAL, "executor request sha is not a full commit id")
    project = request.get("project")
    if not isinstance(project, str) or project.startswith("/") or "\\" in project:
        raise AdmissionError(ExitCode.REFUSAL, "executor project path must be relative")
    if project != "." and any(part in {"", ".", ".."} for part in project.split("/")):
        raise AdmissionError(ExitCode.REFUSAL, "executor project path contains an unsafe component")
    root = policy.repo_roots[repo]
    if not root.is_absolute() or root == Path("/"):
        raise AdmissionError(ExitCode.REFUSAL, "executor repo root is not an allowed absolute directory")
    return job_id, root


class HostJobExecutor:
    """AgentExecutor implementation; workers own lifecycle after scope confirmation."""

    def __init__(
        self,
        state_dir: Path,
        policy: AgentPolicy,
        *,
        policy_path: Path = DEFAULT_AGENT_POLICY_PATH,
        scope_launcher: ScopeLauncher | None = None,
        user_resolver: Callable[[str], RunnerIdentity] | None = None,
        guard_presence: Callable[[], bool | None] | None = None,
        clock: Callable[[], str] = _utc_now,
        python_executable: str = sys.executable,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.jobs_dir = self.state_dir / "jobs"
        self.policy = policy
        self.policy_path = Path(policy_path)
        self.scope_launcher = scope_launcher
        self.user_resolver = user_resolver
        self.guard_presence = guard_presence or (lambda: None)
        self.clock = clock
        self.python_executable = python_executable

    def _job_dir(self, job_id: str, *, create: bool) -> Path:
        path = self.jobs_dir / _job_id(job_id)
        if self.jobs_dir.is_symlink() or not self.jobs_dir.is_dir():
            raise AdmissionError(ExitCode.REFUSAL, "executor jobs directory is not a real directory")
        if stat.S_IMODE(self.jobs_dir.stat().st_mode) & 0o077:
            raise AdmissionError(ExitCode.REFUSAL, "executor jobs directory is accessible by other users")
        if path.is_symlink():
            raise AdmissionError(ExitCode.REFUSAL, f"executor job directory {job_id!r} is a symlink")
        if create:
            try:
                path.mkdir(mode=0o700, exist_ok=True)
            except OSError as exc:
                raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot create executor job directory: {exc}") from exc
        if not path.is_dir() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise AdmissionError(ExitCode.REFUSAL, f"executor job directory {job_id!r} is not private")
        return path

    def _resolve_runner(self) -> RunnerIdentity:
        if not isinstance(self.policy.runner_user, str):
            raise AdmissionError(
                ExitCode.REFUSAL,
                "agent.toml.runner_user is required when the executor is enabled",
            )
        try:
            if self.user_resolver is not None:
                identity = self.user_resolver(self.policy.runner_user)
            else:
                identity = _resolve_system_user(self.policy.runner_user)
        except (KeyError, LookupError, OSError, ValueError) as exc:
            raise AdmissionError(
                ExitCode.REFUSAL, f"agent.toml.runner_user {self.policy.runner_user!r} is unknown: {exc}",
            ) from exc
        if identity.uid == 0:
            raise AdmissionError(ExitCode.REFUSAL, "agent.toml.runner_user must resolve to a non-root uid")
        return identity

    def start(self, request: Mapping[str, object], admission: AdmissionHandle) -> Mapping[str, object]:
        job_id, _repo_root = validate_job_request(request, self.policy)
        if not admission.live:
            raise AdmissionError(ExitCode.REFUSAL, "executor start requires a live admission handle")
        self._resolve_runner()
        job_dir = self._job_dir(job_id, create=True)
        request_path = job_dir / "request.json"
        try:
            request_fd = os.open(request_path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        except OSError as exc:
            if isinstance(exc, FileNotFoundError):
                reason = "executor has no durable request record"
            else:
                reason = f"cannot open executor request record: {exc}"
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, reason) from exc
        else:
            try:
                if not stat.S_ISREG(os.fstat(request_fd).st_mode):
                    raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "executor request record is not a regular file")
            finally:
                os.close(request_fd)
        unit = f"fleetd-job-{job_id}.scope"
        try:
            guard_present = self.guard_presence()
        except Exception:
            guard_present = None
        if not isinstance(guard_present, bool):
            guard_present = None
        guard_reason = "thrashguard-absent" if guard_present is False else (
            "not-collected" if guard_present is None else "registrant-client-unavailable"
        )
        initial: dict[str, object] = {
            "schema": "fleet-host-job-state/1",
            "job_id": job_id,
            "state": "starting",
            "start_confirmed": False,
            "scope": unit,
            "started_utc": self.clock(),
            "ended_utc": None,
            "exit_code": None,
            "reason": None,
            "checkout": None,
            "container": {"id": None, "name": None, "image": None},
            "guard": {"present": guard_present, "reason": guard_reason},
        }
        with _job_state_lock(job_dir):
            _atomic_json(job_dir / "state.json", initial)
        argv = [
            self.python_executable, "-m", "fleetd.executor_worker", "worker",
            "--state-dir", str(self.state_dir), "--policy", str(self.policy_path),
            "--job-id", job_id, "--lock-fd", str(admission.fileno()),
        ]
        start_gate_fd = -1
        handshake_read_fd = -1
        handshake_write_fd = -1
        try:
            handshake_read_fd, handshake_write_fd = os.pipe()
            argv.extend(("--handshake-fd", str(handshake_write_fd)))
            start_gate_fd = os.open(
                job_dir / "start.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600,
            )
            fcntl.flock(start_gate_fd, fcntl.LOCK_EX)
            launcher = self.scope_launcher or SystemdScopeLauncher(self.policy.query_timeout_s)
            launched = launcher.launch(
                unit, argv, admission.fileno(), handshake_read_fd, handshake_write_fd,
            )
            if not launched.active or launched.worker_pid <= 0 or launched.unit != unit:
                raise AdmissionError(ExitCode.RUNNER_FAILURE, "executor scope start was not confirmed")
            # The worker is blocked on start.lock. Persist confirmation before
            # transferring the charge; a failed save leaves the agent's copy live.
            with _job_state_lock(job_dir):
                current = self.read_state(job_id)
                if current is None:
                    raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "executor state disappeared during scope start")
                running = dict(current)
                running.update(start_confirmed=True, executor_pid=launched.worker_pid)
                if running.get("state") != "stopping":
                    running["state"] = "running"
                _atomic_json(job_dir / "state.json", running)
            admission.handoff()
            return running
        except BaseException as exc:
            try:
                with _job_state_lock(job_dir):
                    current = self.read_state(job_id)
                    if current is not None and current.get("start_confirmed") is not True:
                        failed = dict(current)
                        failed["start_error"] = str(exc)[:1000]
                        _atomic_json(job_dir / "state.json", failed)
            except BaseException:
                pass
            raise
        finally:
            if handshake_write_fd >= 0:
                try:
                    os.close(handshake_write_fd)
                except OSError:
                    pass
            if handshake_read_fd >= 0:
                try:
                    os.close(handshake_read_fd)
                except OSError:
                    pass
            if start_gate_fd >= 0:
                try:
                    fcntl.flock(start_gate_fd, fcntl.LOCK_UN)
                finally:
                    os.close(start_gate_fd)

    def request_stop(self, job_id: str, reason: str) -> Mapping[str, object] | None:
        job_id = _job_id(job_id)
        job_dir = self._job_dir(job_id, create=False)
        with _job_state_lock(job_dir):
            current = self.read_state(job_id)
            if current is None:
                raise AdmissionError(ExitCode.REFUSAL, f"executor has no state for job {job_id!r}")
            if current.get("state") in {"done", "failed", "cancelled"}:
                raise AdmissionError(ExitCode.REFUSAL, f"finished job {job_id!r} cannot be stopped")
            if current.get("lane_process_status") is not None:
                raise AdmissionError(ExitCode.REFUSAL, f"lane for job {job_id!r} has exited and is being finalized")
            state = dict(current)
            state.update(state="stopping", reason=reason, cancel_requested=True, cancel_reason=reason)
            _atomic_json(job_dir / "state.json", state)
        return state

    def read_state(self, job_id: str) -> Mapping[str, object] | None:
        job_dir = self._job_dir(job_id, create=False)
        path = job_dir / "state.json"
        if path.is_symlink():
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "executor state record is a symlink")
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot open executor state record: {exc}") from exc
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "executor state record is not a regular file")
            raw = os.read(fd, _EXECUTOR_JSON_MAX + 1)
        finally:
            os.close(fd)
        if len(raw) > _EXECUTOR_JSON_MAX:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "executor state record exceeds 1 MiB")
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"executor state record is invalid: {exc}") from exc
        if not isinstance(document, dict) or document.get("schema") != "fleet-host-job-state/1" or document.get("job_id") != job_id:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "executor state record has an invalid identity")
        return document


def _resolve_system_user(username: str) -> RunnerIdentity:
    """Resolve the configured user and its supplementary groups on this host."""

    import grp
    import pwd

    account = pwd.getpwnam(username)
    if account.pw_uid == 0:
        raise ValueError("uid 0 is not permitted")
    groups = tuple(sorted(set(os.getgrouplist(username, account.pw_gid))))
    if account.pw_gid not in groups:
        groups = tuple(sorted((*groups, account.pw_gid)))
    # Resolve every group so a stale NSS gid is a refusal rather than a failed switch.
    for gid in groups:
        grp.getgrgid(gid)
    return RunnerIdentity(username, account.pw_uid, account.pw_gid, groups, account.pw_dir)

