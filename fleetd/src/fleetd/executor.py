"""Root-owned lifecycle boundary for admitted fleet host jobs.

The agent starts one detached systemd scope per job.  The scope process owns
the inherited admission flock, so restarting the socket service does not
release a live job's charge.  Repository operations are implemented by the
worker and run as ``agent.toml.runner_user``.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

from .admission import AdmissionError, AdmissionHandle, ExitCode
from .agent_policy import AgentPolicy, DEFAULT_AGENT_POLICY_PATH

_JOB_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


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
    main_pid: int


class ScopeLauncher(Protocol):
    def launch(self, unit: str, argv: Sequence[str], lock_fd: int) -> ScopeState: ...


class SystemdScopeLauncher:
    """Start and inspect a transient scope without a shell command string."""

    def __init__(
        self,
        query_timeout_s: float,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
        run: Callable[..., Any] = subprocess.run,
    ) -> None:
        self.query_timeout_s = query_timeout_s
        self._popen = popen
        self._run = run

    def launch(self, unit: str, argv: Sequence[str], lock_fd: int) -> ScopeState:
        command = [
            "systemd-run", "--scope", "--quiet", "--collect", "--unit", unit,
            "--", *argv,
        ]
        try:
            self._popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                pass_fds=(lock_fd,),
                start_new_session=True,
            )
        except OSError as exc:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot start job scope {unit}: {exc}") from exc

        try:
            result = self._run(
                ["systemctl", "show", "--no-pager", "--property=ActiveState", "--property=MainPID", unit],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=self.query_timeout_s,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, f"cannot confirm job scope {unit}: {exc}") from exc
        if result.returncode != 0:
            detail = str(result.stderr).strip()
            raise AdmissionError(
                ExitCode.RUNNER_FAILURE,
                f"cannot confirm job scope {unit}: {detail or f'exit {result.returncode}'}",
            )
        values: dict[str, str] = {}
        for line in str(result.stdout).splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key] = value
        try:
            main_pid = int(values.get("MainPID", "0"))
        except ValueError:
            main_pid = 0
        active = values.get("ActiveState") == "active" and main_pid > 0
        if not active:
            raise AdmissionError(
                ExitCode.RUNNER_FAILURE,
                f"job scope {unit} did not confirm an active worker (state={values.get('ActiveState', 'unknown')}, pid={main_pid})",
            )
        return ScopeState(unit, True, main_pid)


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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
        clock: Callable[[], str] = _utc_now,
        python_executable: str = sys.executable,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.jobs_dir = self.state_dir / "jobs"
        self.policy = policy
        self.policy_path = Path(policy_path)
        self.scope_launcher = scope_launcher or SystemdScopeLauncher(policy.query_timeout_s)
        self.user_resolver = user_resolver
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
        initial: dict[str, object] = {
            "schema": "fleet-host-job-state/1",
            "job_id": job_id,
            "state": "starting",
            "scope": unit,
            "started_utc": self.clock(),
            "ended_utc": None,
            "exit_code": None,
            "verdict": None,
            "reason": None,
            "checkout": None,
            "container": {"id": None, "name": None, "image": None},
            "guard": {"present": None, "reason": "not-checked"},
        }
        _atomic_json(job_dir / "state.json", initial)
        argv = [
            self.python_executable, "-m", "fleetd.executor_worker", "worker",
            "--state-dir", str(self.state_dir), "--policy", str(self.policy_path),
            "--job-id", job_id, "--lock-fd", str(admission.fileno()),
        ]
        try:
            launched = self.scope_launcher.launch(unit, argv, admission.fileno())
        except Exception:
            # The durable request and the original admission handle remain with the caller.
            raise
        if not launched.active or launched.main_pid <= 0 or launched.unit != unit:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, "executor scope start was not confirmed")
        admission.handoff()
        running = dict(initial)
        running["state"] = "running"
        running["executor_pid"] = launched.main_pid
        return running

    def request_stop(self, job_id: str, reason: str) -> Mapping[str, object] | None:
        _job_id(job_id)
        raise AdmissionError(ExitCode.REFUSAL, "executor stop path is not available")

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
            raw = os.read(fd, 65537)
        finally:
            os.close(fd)
        if len(raw) > 65536:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "executor state record exceeds 64 KiB")
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

