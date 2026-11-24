"""Identity-checked host transport and deterministic in-memory transport."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Protocol, Sequence

from . import hostside
from .runner_layout import progress_home_path

_PROFILE_METRIC_KEYS = (
    "rss_peak_bytes", "memory_peak_bytes", "swap_peak_bytes", "cpu_seconds",
    "cpu_user_seconds", "cpu_system_seconds", "io_read_bytes", "io_write_bytes",
    "io_read_ops", "io_write_ops", "psi_memory_some_seconds", "psi_memory_full_seconds",
    "psi_cpu_some_seconds", "psi_io_some_seconds", "psi_io_full_seconds",
)


class TransportError(RuntimeError):
    """A host operation could not complete through the configured transport."""


class HostUnavailable(TransportError):
    """The wrapper could not contact a host within the requested timeout."""


class HostSlow(HostUnavailable):
    """The host did not answer within the timeout (rc 124), as opposed to ssh exit 255."""


def host_unavailable(rc: int, message: str) -> HostUnavailable:
    """The exception for a contact failure: ``HostSlow`` for a timeout, else ``HostUnavailable``."""
    return (HostSlow if rc == 124 else HostUnavailable)(message)


class HxNotConfigured(TransportError):
    """No hx wrapper was named and the source-checkout default does not exist."""


HX_ENVIRONMENT = "FLEETD_HX"


class Transport(Protocol):
    """Small remote command and file interface used by campaign orchestration."""

    def run(
        self,
        host: str,
        command: Sequence[str] | str,
        timeout: float | None = None,
    ) -> tuple[int, str, str]: ...

    def put(self, host: str, remote_path: str, content: bytes, timeout: float | None = None) -> None: ...

    def get(self, host: str, remote_path: str, timeout: float | None = None) -> bytes: ...


def _home_relative(path: str) -> str:
    value = PurePosixPath(path)
    if value.is_absolute() or not value.parts or any(part in {"", ".", ".."} for part in value.parts):
        raise TransportError("remote file path must be a non-empty path beneath the remote home")
    if any("\x00" in part for part in value.parts):
        raise TransportError("remote file path contains a NUL byte")
    return value.as_posix()


def shell_command(command: Sequence[str] | str) -> str:
    """Render argv as one safely quoted POSIX shell command."""
    if isinstance(command, str):
        if not command.strip():
            raise TransportError("remote script must not be empty")
        return command
    if (
        not command
        or not isinstance(command[0], str)
        or not command[0]
        or any(not isinstance(value, str) or "\x00" in value for value in command)
    ):
        raise TransportError("remote argv must start with a non-empty executable and contain NUL-free strings")
    return shlex.join(command)


def resolve_hx_wrapper(explicit: Path | str | None = None, environ: Mapping[str, str] | None = None) -> Path:
    """Pick the hx wrapper: ``--hx`` first, then ``FLEETD_HX``, then the source checkout's own.

    The result is always an absolute path, so the launch never searches ``PATH``
    for an ``hx`` of the same name. An installed wheel has no checkout beside it,
    so with neither setting the answer is one actionable refusal.
    """
    if explicit is not None:
        return Path(explicit).expanduser().absolute()
    configured = (os.environ if environ is None else environ).get(HX_ENVIRONMENT)
    if configured:
        return Path(configured).expanduser().absolute()
    default = Path(__file__).resolve().parents[3] / "scripts" / "controller" / "hx.sh"
    if default.is_file():
        return default
    raise HxNotConfigured(
        f"no hx wrapper: pass --hx PATH or set {HX_ENVIRONMENT} (an installed fleetd has no source checkout to default to)"
    )


class HxTransport:
    """Run commands only through the controller kit's identity-checked hx wrapper."""

    def __init__(self, wrapper: Path | str | None = None, *, environ: Mapping[str, str] | None = None):
        self.wrapper = resolve_hx_wrapper(wrapper, environ)

    def run(
        self,
        host: str,
        command: Sequence[str] | str,
        timeout: float | None = None,
    ) -> tuple[int, str, str]:
        if not host or any(char.isspace() for char in host) or "\x00" in host:
            raise TransportError("host alias must be a non-empty whitespace-free value")
        remote = shell_command(command)
        try:
            result = subprocess.run(
                [str(self.wrapper), host, remote],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or ""
            err = exc.stderr or "transport timed out"
            if isinstance(out, bytes):
                out = out.decode("utf-8", errors="replace")
            if isinstance(err, bytes):
                err = err.decode("utf-8", errors="replace")
            return 124, out, err
        except OSError as exc:
            raise TransportError(f"could not launch hx transport: {exc}") from exc
        return result.returncode, result.stdout, result.stderr

    def put(self, host: str, remote_path: str, content: bytes, timeout: float | None = None) -> None:
        relative = _home_relative(remote_path)
        encoded = base64.b64encode(content).decode("ascii")
        target = f'"$HOME"/{shlex.quote(relative)}'
        script = "\n".join(
            [
                "set -euo pipefail",
                f"target={target}",
                'mkdir -p -- "$(dirname -- "$target")"',
                'temporary="$target.tmp.$$"',
                f"printf %s {shlex.quote(encoded)} | base64 --decode > \"$temporary\"",
                'chmod 600 -- "$temporary"',
                'mv -f -- "$temporary" "$target"',
            ]
        )
        rc, _out, err = self.run(host, script, timeout)
        if rc in {124, 255}:
            raise host_unavailable(rc, f"host {host} is unavailable: {err.strip()}")
        if rc != 0:
            raise TransportError(f"could not upload {relative} to {host}: {err.strip()}")

    def get(self, host: str, remote_path: str, timeout: float | None = None) -> bytes:
        relative = _home_relative(remote_path)
        target = f'"$HOME"/{shlex.quote(relative)}'
        rc, out, err = self.run(host, f"set -euo pipefail\nbase64 --wrap=0 -- {target} | tr -d '\\n'", timeout)
        if rc in {124, 255}:
            raise host_unavailable(rc, f"host {host} is unavailable: {err.strip()}")
        if rc != 0:
            raise TransportError(f"could not fetch {relative} from {host}: {err.strip()}")
        try:
            return base64.b64decode(out, validate=True)
        except ValueError as exc:
            raise TransportError(f"host {host} returned invalid base64 for {relative}") from exc


@dataclass
class FakeJob:
    """One job-table row exposed by :class:`FakeTransport`."""

    name: str
    candidate_ids: tuple[str, ...]
    list_digest: str
    state: str = "RUNNING"
    exit_code: int | None = None
    started_at: float = 0.0
    progress_mtime: float | None = None
    progress_records: list[dict[str, object]] = field(default_factory=list)
    progress_path: str = ""
    verdict_path: str = ""
    progress_offset: int = 0
    progress_end_offset: int | None = None


@dataclass
class FakeHost:
    """Scriptable fake host with a job table and home-relative files."""

    online: bool = True
    slow: bool = False
    jobs: dict[str, FakeJob] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)
    missing_checkouts: set[str] = field(default_factory=set)
    starts: int = 0
    stops: int = 0
    # The probe's ``some avg10`` readings; ``None`` models an unreadable PSI file.
    pressure: dict[str, float | None] = field(default_factory=lambda: {"memory": 0.0, "io": 0.0})


def command_metadata(operation: str, data: Mapping[str, object]) -> str:
    """Add a shell-comment payload that the fake can interpret and bash ignores."""
    payload = json.dumps({"operation": operation, **data}, sort_keys=True, separators=(",", ":")).encode()
    marker = base64.b64encode(payload).decode("ascii")
    return f"# fleetd-campaign-command/1 {marker}"


def _read_command_metadata(script: str) -> dict[str, object] | None:
    first = script.splitlines()[0] if script.splitlines() else ""
    prefix = "# fleetd-campaign-command/1 "
    if not first.startswith(prefix):
        return None
    try:
        value = json.loads(base64.b64decode(first[len(prefix):], validate=True))
    except (ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_host_files(root: Path, files: Mapping[str, bytes]) -> None:
    """Materialize the fake's home-relative file table for the shared probe."""
    for relative, content in files.items():
        target = root / _home_relative(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


class FakeTransport:
    """In-memory transport for deterministic campaign and failure scenarios."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.hosts: dict[str, FakeHost] = {}
        self.calls: list[tuple[str, str, str]] = []

    def host(self, alias: str) -> FakeHost:
        if alias not in self.hosts:
            self.hosts[alias] = FakeHost()
        return self.hosts[alias]

    def set_unreachable(self, alias: str, unreachable: bool = True) -> None:
        self.host(alias).online = not unreachable

    def set_slow(self, alias: str, slow: bool = True) -> None:
        self.host(alias).slow = slow

    def set_pressure(self, alias: str, *, memory: float | None = 0.0, io: float | None = 0.0) -> None:
        self.host(alias).pressure = {"memory": memory, "io": io}

    def restart(self, alias: str) -> None:
        host = self.host(alias)
        for job in host.jobs.values():
            if job.state == "RUNNING":
                job.state = "EXIT"
                job.exit_code = 137
                job.progress_end_offset = len(host.files.get(job.progress_path, b""))
                host.files[f"jobs/{job.name}/EXIT"] = b"137\n"

    def add_progress(
        self,
        alias: str,
        job_name: str,
        candidate_id: str,
        outcome: str,
        *,
        elapsed_seconds: float = 1.0,
        emitted_at: str | None = None,
        memory_peak_bytes: int | None = None,
    ) -> None:
        host = self.host(alias)
        job = host.jobs[job_name]
        event: dict[str, object] = {
            "event": "candidate",
            "candidate_id": candidate_id,
            "outcome_bucket": outcome,
            "elapsed_seconds": elapsed_seconds,
            "emitted_at": emitted_at or f"2030-01-01T00:00:{len(job.progress_records):02d}+00:00",
        }
        if memory_peak_bytes is not None:
            # Shape from Assay's observer_windows.build_profile() and the
            # candidate event emitted by mutation.py (_record_candidate).
            metrics = {key: 0 for key in _PROFILE_METRIC_KEYS}
            metrics["memory_peak_bytes"] = memory_peak_bytes
            metrics["rss_peak_bytes"] = memory_peak_bytes
            event["profile"] = {
                "schema": "resource-profile/1",
                "subject": {"tool": "assay", "lane": "r2", "kind": "r2-candidate", "id": candidate_id},
                "wall_seconds": float(elapsed_seconds),
                "status": "MEASURED",
                "reason": None,
                "observer": {
                    "name": "cgprofile", "version": None, "contract": 3,
                    "mode": "cgroup", "window": "w-fake", "sample_interval_seconds": 0.25,
                },
                "metrics": metrics,
                "missing": {},
                "sources": {"memory_peak_bytes": "memory.peak"},
                "context": {},
                "attribution": {},
            }
        job.progress_records.append(event)
        job.progress_mtime = self.clock()
        host.files[job.progress_path] = host.files.get(job.progress_path, b"") + (
            json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()

    def finish(self, alias: str, job_name: str, exit_code: int = 0, verdict: bytes | None = None) -> None:
        host = self.host(alias)
        job = host.jobs[job_name]
        job.state = "EXIT"
        job.exit_code = exit_code
        job.progress_end_offset = len(host.files.get(job.progress_path, b""))
        host.files[f"jobs/{job.name}/EXIT"] = f"{exit_code}\n".encode()
        if verdict is None:
            verdict = json.dumps({"final_exit": exit_code}, sort_keys=True).encode()
        host.files[job.verdict_path] = verdict

    def run(
        self,
        host_alias: str,
        command: Sequence[str] | str,
        timeout: float | None = None,
    ) -> tuple[int, str, str]:
        script = shell_command(command)
        self.calls.append((host_alias, "run", script))
        host = self.host(host_alias)
        if not host.online:
            return 255, "", "host unreachable"
        if host.slow:
            return 124, "", "transport timed out"
        metadata = _read_command_metadata(script)
        if metadata is None:
            return 0, "", ""
        operation = metadata.get("operation")
        if operation == "probe":
            return 0, self._probe(host, script), ""
        if operation == "start":
            return self._start(host_alias, host, metadata)
        if operation == "stop":
            return self._stop(host, metadata)
        return 2, "", f"unsupported fake operation: {operation}"

    def put(self, host_alias: str, remote_path: str, content: bytes, timeout: float | None = None) -> None:
        relative = _home_relative(remote_path)
        self.calls.append((host_alias, "put", relative))
        host = self.host(host_alias)
        if not host.online:
            raise HostUnavailable(f"host {host_alias} is unreachable")
        if host.slow:
            raise HostSlow(f"transport to {host_alias} timed out")
        host.files[relative] = bytes(content)

    def get(self, host_alias: str, remote_path: str, timeout: float | None = None) -> bytes:
        relative = _home_relative(remote_path)
        self.calls.append((host_alias, "get", relative))
        host = self.host(host_alias)
        if not host.online:
            raise HostUnavailable(f"host {host_alias} is unreachable")
        if host.slow:
            raise HostSlow(f"transport to {host_alias} timed out")
        if relative not in host.files:
            raise TransportError(f"remote file does not exist: {relative}")
        return host.files[relative]

    def _probe(self, host: FakeHost, script: str) -> str:
        groups = hostside.decode_payload(shlex.split(script.splitlines()[-1])[-2])
        with tempfile.TemporaryDirectory(prefix="fleetd-fake-probe-") as temporary:
            home = Path(temporary)
            _write_host_files(home, host.files)
            for group in groups:
                checkout = home / "checkouts" / str(group["commit"])
                if str(group["commit"]) in host.missing_checkouts:
                    shutil.rmtree(checkout, ignore_errors=True)
                    continue
                project = checkout if group["project"] == "." else checkout / str(group["project"])
                project.mkdir(parents=True, exist_ok=True)
                (checkout / ".git").mkdir(exist_ok=True)
                for name in group["names"]:
                    if name not in host.jobs:
                        shutil.rmtree(home / "jobs" / str(name), ignore_errors=True)
            for name, job in host.jobs.items():
                job_dir = home / "jobs" / name
                job_dir.mkdir(parents=True, exist_ok=True)
                for marker in ("EXIT", "pid", "progress-end-offset"):
                    (job_dir / marker).unlink(missing_ok=True)
                if job.state == "RUNNING":
                    (job_dir / "pid").write_text(f"{os.getpid()}\n", encoding="ascii")
                elif job.state == "EXIT" and isinstance(job.exit_code, int):
                    (job_dir / "EXIT").write_text(f"{job.exit_code}\n", encoding="ascii")
                (job_dir / "progress-offset").write_text(f"{job.progress_offset}\n", encoding="ascii")
                if job.progress_end_offset is not None:
                    (job_dir / "progress-end-offset").write_text(
                        f"{job.progress_end_offset}\n", encoding="ascii"
                    )
                if job.progress_path and host.files.get(job.progress_path) and job.progress_mtime is not None:
                    progress = home / _home_relative(job.progress_path)
                    os.utime(progress, (job.progress_mtime, job.progress_mtime))
            proc = home / "proc"
            (proc / "pressure").mkdir(parents=True, exist_ok=True)
            for resource in ("memory", "io"):
                value = host.pressure[resource]
                if value is not None:
                    (proc / "pressure" / resource).write_text(
                        f"some avg10={value} avg60=0 avg300=0 total=0\n", encoding="ascii"
                    )
            document = hostside.probe_document(home, groups, proc, home=home)
            for row in document["jobs"]:
                job = host.jobs.get(str(row["name"]))
                if job is not None:
                    row["progress_mtime"] = (
                        job.progress_mtime if host.files.get(job.progress_path) else None
                    )
        return json_output(document)

    def _start(self, alias: str, host: FakeHost, metadata: Mapping[str, object]) -> tuple[int, str, str]:
        name = str(metadata.get("name", ""))
        digest = str(metadata.get("list_digest", ""))
        ids_path = str(metadata.get("ids_path", ""))
        try:
            candidate_ids = tuple(
                line for line in host.files[ids_path].decode("ascii").splitlines() if line
            )
        except (KeyError, UnicodeError):
            return 73, "", "candidate ids file is missing or invalid"
        actual_digest = hashlib.sha256("".join(f"{candidate_id}\n" for candidate_id in sorted(candidate_ids)).encode("ascii")).hexdigest()
        if actual_digest != digest:
            return 73, "", "candidate ids file digest mismatch"
        if name in host.jobs:
            old = host.jobs[name]
            if old.list_digest != digest:
                return 73, "", "named job exists with another candidate-list digest"
            return 0, "EXISTING\n", ""
        commit = str(metadata.get("commit", ""))
        project = str(metadata.get("project", ""))
        declared_lane = str(metadata.get("lane", ""))
        with tempfile.TemporaryDirectory(prefix="fleetd-fake-start-") as temporary:
            home = Path(temporary)
            _write_host_files(home, host.files)
            checkout = home / "checkouts" / commit
            project_dir = checkout if project == "." else checkout / project
            project_dir.mkdir(parents=True, exist_ok=True)
            try:
                assay_lane = hostside.resolve_assay_lane(checkout, project, declared_lane)
            except ValueError as exc:
                return 73, "", str(exc)
        progress_path = progress_home_path(commit, project, assay_lane)
        suffix = f"campaigns/{name}-s1"
        job = FakeJob(
            name=name,
            candidate_ids=candidate_ids,
            list_digest=digest,
            started_at=self.clock(),
            progress_mtime=None,
            progress_path=progress_path,
            verdict_path=f"{suffix}/verdict.json",
            progress_offset=len(host.files.get(progress_path, b"")),
        )
        host.jobs[name] = job
        host.starts += 1
        host.files[f"jobs/{name}/list.sha256"] = (digest + "\n").encode()
        return 0, "STARTED\n", ""

    def _stop(self, host: FakeHost, metadata: Mapping[str, object]) -> tuple[int, str, str]:
        name = str(metadata.get("name", ""))
        job = host.jobs.get(name)
        if job is None or job.state != "RUNNING":
            return 0, "NOT_RUNNING\n", ""
        job.state = "EXIT"
        job.exit_code = 143
        job.progress_end_offset = len(host.files.get(job.progress_path, b""))
        host.stops += 1
        host.files[f"jobs/{name}/EXIT"] = b"143\n"
        return 0, "STOPPED\n", ""


def json_output(value: object) -> str:
    """Canonical compact JSON for transport payloads."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
