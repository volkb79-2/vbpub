"""Identity-checked host transport and deterministic in-memory transport."""

from __future__ import annotations

import base64
import json
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Protocol, Sequence


class TransportError(RuntimeError):
    """A host operation could not complete through the configured transport."""


class HostUnavailable(TransportError):
    """The wrapper could not contact a host within the requested timeout."""


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


class HxTransport:
    """Run commands only through the controller kit's identity-checked hx wrapper."""

    def __init__(self, wrapper: Path | None = None):
        self.wrapper = wrapper or Path(__file__).resolve().parents[3] / "scripts" / "controller" / "hx.sh"

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
            raise HostUnavailable(f"host {host} is unavailable: {err.strip()}")
        if rc != 0:
            raise TransportError(f"could not upload {relative} to {host}: {err.strip()}")

    def get(self, host: str, remote_path: str, timeout: float | None = None) -> bytes:
        relative = _home_relative(remote_path)
        target = f'"$HOME"/{shlex.quote(relative)}'
        rc, out, err = self.run(host, f"set -euo pipefail\nbase64 --wrap=0 -- {target} | tr -d '\\n'", timeout)
        if rc in {124, 255}:
            raise HostUnavailable(f"host {host} is unavailable: {err.strip()}")
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


@dataclass
class FakeHost:
    """Scriptable fake host with a job table and home-relative files."""

    online: bool = True
    slow: bool = False
    jobs: dict[str, FakeJob] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)
    starts: int = 0
    stops: int = 0


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

    def restart(self, alias: str) -> None:
        host = self.host(alias)
        for job in host.jobs.values():
            if job.state == "RUNNING":
                job.state = "EXIT"
                job.exit_code = 137
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
        job.progress_records.append(event)
        job.progress_mtime = self.clock()
        shared_records = sorted(
            (
                record for other in host.jobs.values() if other.progress_path == job.progress_path
                for record in other.progress_records
            ),
            key=lambda record: str(record.get("emitted_at", "")),
        )
        host.files[job.progress_path] = b"".join(
            (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
            for record in shared_records
        )

    def finish(self, alias: str, job_name: str, exit_code: int = 0, verdict: bytes | None = None) -> None:
        host = self.host(alias)
        job = host.jobs[job_name]
        job.state = "EXIT"
        job.exit_code = exit_code
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
            return 0, self._probe(host, metadata), ""
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
            raise HostUnavailable(f"transport to {host_alias} timed out")
        host.files[relative] = bytes(content)

    def get(self, host_alias: str, remote_path: str, timeout: float | None = None) -> bytes:
        relative = _home_relative(remote_path)
        self.calls.append((host_alias, "get", relative))
        host = self.host(host_alias)
        if not host.online:
            raise HostUnavailable(f"host {host_alias} is unreachable")
        if host.slow:
            raise HostUnavailable(f"transport to {host_alias} timed out")
        if relative not in host.files:
            raise TransportError(f"remote file does not exist: {relative}")
        return host.files[relative]

    def _probe(self, host: FakeHost, metadata: Mapping[str, object]) -> str:
        names = metadata.get("jobs", [])
        rows = []
        for name in names if isinstance(names, list) else []:
            job = host.jobs.get(str(name))
            if job is None:
                rows.append({"name": name, "state": "MISSING"})
                continue
            finished = {}
            for event in job.progress_records:
                candidate_id = event.get("candidate_id")
                if isinstance(candidate_id, str) and candidate_id not in finished:
                    finished[candidate_id] = event
            rows.append({
                "name": job.name,
                "state": job.state,
                "exit_code": job.exit_code,
                "list_digest": job.list_digest,
                "progress_count": len(finished),
                "progress_mtime": job.progress_mtime,
                "progress_path": job.progress_path,
                "verdict_path": job.verdict_path,
                "records": list(job.progress_records),
            })
        return json.dumps({"jobs": rows}, sort_keys=True, separators=(",", ":"))

    def _start(self, alias: str, host: FakeHost, metadata: Mapping[str, object]) -> tuple[int, str, str]:
        name = str(metadata.get("name", ""))
        digest = str(metadata.get("list_digest", ""))
        candidate_ids = tuple(str(value) for value in metadata.get("candidate_ids", []))
        if name in host.jobs:
            old = host.jobs[name]
            if old.list_digest != digest:
                return 73, "", "named job exists with another candidate-list digest"
            return 0, "EXISTING\n", ""
        suffix = f"campaigns/{name}-s1"
        project = str(metadata.get("project"))
        project_prefix = "" if project in {"", "."} else f"{project}/"
        job = FakeJob(
            name=name,
            candidate_ids=candidate_ids,
            list_digest=digest,
            started_at=self.clock(),
            progress_mtime=None,
            progress_path=f"checkouts/{metadata.get('commit')}/{project_prefix}.assay/progress-{metadata.get('lane')}.jsonl",
            verdict_path=f"{suffix}/verdict.json",
        )
        host.jobs[name] = job
        host.starts += 1
        host.files[f"jobs/{name}/list.sha256"] = (digest + "\n").encode()
        host.files[str(metadata.get("ids_path"))] = ("\n".join(sorted(candidate_ids)) + "\n").encode()
        return 0, "STARTED\n", ""

    def _stop(self, host: FakeHost, metadata: Mapping[str, object]) -> tuple[int, str, str]:
        name = str(metadata.get("name", ""))
        job = host.jobs.get(name)
        if job is None or job.state != "RUNNING":
            return 0, "NOT_RUNNING\n", ""
        job.state = "EXIT"
        job.exit_code = 143
        host.stops += 1
        host.files[f"jobs/{name}/EXIT"] = b"143\n"
        return 0, "STOPPED\n", ""


def json_output(value: object) -> str:
    """Canonical compact JSON for transport payloads."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
