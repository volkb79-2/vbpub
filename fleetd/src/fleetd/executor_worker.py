"""Scoped host-job worker and safe runner-user filesystem helpers.

The service process remains root.  This module owns a job after the transient
systemd scope starts; every command that reads or writes the checkout runs in
a child that has the configured runner uid, gid, supplementary groups, and
checkout directory fd.
"""

from __future__ import annotations

import argparse
import datetime as dt
import errno
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
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Protocol, Sequence

from .admission import AdmissionBook, AdmissionError, AdmissionHandle, ExitCode
from .agent_admission import AgentAdmission
from .agent_policy import AgentPolicy, load_agent_policy
from .executor import (
    ProcessSignals,
    RunnerIdentity,
    SystemProcessSignals,
    _atomic_json,
    _job_state_lock,
    _job_id,
    _resolve_system_user,
    _EXECUTOR_JSON_MAX,
)
from .agent_providers import (
    DockerInspectionError,
    MAX_RUNNER_QUERY_OUTPUT_BYTES as _MAX_RUNNER_QUERY_OUTPUT_BYTES,
    run_bounded_command,
    run_docker_command,
)
from .hostside import open_beneath

_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_CONTAINER_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
_RG_VERSION_RE = re.compile(
    r"(\d+(?:\.\d+)*)(?:(a|b|rc)(\d+))?(?:\.post(\d+))?(?:\.dev(\d+))?(?:\+[A-Za-z0-9.]+)?\Z",
)
_RG_VERSION_FLOOR = "25.0.1"
_RG_FALLBACK = "/home/vscode/.venvs/gate-tools2502/bin/run-gate"
_STORE_PATH_RE = re.compile(r"[A-Za-z0-9._@+=,:-]+(?:/[A-Za-z0-9._@+=,:-]+)*\Z")
_IMAGE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,254}\Z")
_CONTAINER_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SYSTEMD_SLICE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@:-]*\.slice\Z")
_SHARD_EVENT_RE = re.compile(r"(.+)\.shard[1-9][0-9]*of[1-9][0-9]*\Z")
_RELEASE_TAG_RE = re.compile(r"[^/\s]+-v[0-9][^\s]*\Z")
_PSI_LIMIT = 25.0
_PSI_WAIT_S = 900
_PSI_POLL_S = 30
_MEMORY_FLOOR_MIB = 1024
_MEMORY_WAIT_S = 900
_MEMORY_POLL_S = 10
_MEMORY_SETTLE_S = 20
_ARTIFACT_MAX_BYTES = 50 * 1024 * 1024
_STORE_MAX_BYTES = 512 * 1024 * 1024
MAX_STATE_RECORD_BYTES = _EXECUTOR_JSON_MAX
MAX_EVENT_LINE_BYTES = 4096
MAX_EVENT_FILE_BYTES = 512 * 1024
MAX_EVENT_COUNT = 128
MAX_EVENT_OBSERVATIONS_BYTES = 256 * 1024
MAX_DOCKER_JOB_CONTAINER_IDS = 4096
MAX_RUNNER_QUERY_OUTPUT_BYTES = _MAX_RUNNER_QUERY_OUTPUT_BYTES
MAX_DIRECTORY_ENTRIES = 4096
MAX_TREE_ENTRIES = 16384
MAX_TREE_DEPTH = 128
MAX_ARCHIVE_MEMBERS = 16384
MAX_ARCHIVE_FILE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_STORE_PATHS = 128
_RUN_GATE_VERDICT_CODES = {
    "PASS": 0,
    "FAIL": 1,
    "ERROR": 2,
    "NOT_RUN": 3,
    "BUDGET_EXCEEDED": 4,
}
_DOCKER_SELECTOR_ENV = frozenset({
    "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH",
})
_GATE_TIER_ENV = "CGROUP_PARENT_DEV_GATES"
_SYSTEMD_ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C"}
_HELPER_BOOTSTRAP = (
    "import sys; root = sys.argv.pop(1); sys.path.insert(0, root); "
    "from fleetd.executor_worker import main; raise SystemExit(main())"
)

_PERMANENT_LIMIT_MARKERS = (
    "MAX_STATE_RECORD_BYTES", "MAX_EVENT_LINE_BYTES", "MAX_EVENT_FILE_BYTES", "MAX_EVENT_COUNT",
    "MAX_EVENT_OBSERVATIONS_BYTES", "MAX_DOCKER_JOB_CONTAINER_IDS", "MAX_DIRECTORY_ENTRIES",
    "MAX_TREE_ENTRIES", "MAX_TREE_DEPTH", "MAX_ARCHIVE_MEMBERS", "MAX_ARCHIVE_FILE_BYTES",
    "MAX_ARCHIVE_UNCOMPRESSED_BYTES", "MAX_STORE_PATHS",
)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


class RunnerProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...

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

    def run_to_fd(
        self, argv: Sequence[str], *, cwd_fd: int, env: Mapping[str, str], output_fd: int,
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
        bounded_runner: Callable[..., Any] = run_bounded_command,
    ) -> None:
        self.identity = identity
        self._popen = popen
        self._bounded_runner = bounded_runner

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
        try:
            result = self._bounded_runner(
                list(argv), timeout, environment=dict(env), cwd="/", stdin=subprocess.DEVNULL,
                pass_fds=(cwd_fd,), preexec_fn=self._preexec(cwd_fd),
                output_limit=MAX_RUNNER_QUERY_OUTPUT_BYTES, popen_factory=self._popen,
            )
        except (DockerInspectionError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"runner query failed: {exc}") from exc
        return CommandResult(result.returncode, result.stdout or b"", result.stderr or b"")

    def run_with_input(
        self, argv: Sequence[str], *, cwd_fd: int, env: Mapping[str, str], source_fd: int,
        timeout: float | None = None,
    ) -> CommandResult:
        try:
            result = self._bounded_runner(
                list(argv), timeout, environment=dict(env), cwd="/", stdin=source_fd,
                pass_fds=tuple(dict.fromkeys((cwd_fd, source_fd))),
                preexec_fn=self._preexec(cwd_fd), output_limit=MAX_RUNNER_QUERY_OUTPUT_BYTES,
                popen_factory=self._popen,
            )
        except (DockerInspectionError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"runner input query failed: {exc}") from exc
        return CommandResult(result.returncode, result.stdout or b"", result.stderr or b"")

    def run_to_fd(
        self, argv: Sequence[str], *, cwd_fd: int, env: Mapping[str, str], output_fd: int,
    ) -> CommandResult:
        try:
            result = self._bounded_runner(
                list(argv), None, environment=dict(env), cwd="/", stdin=subprocess.DEVNULL,
                pass_fds=(cwd_fd, output_fd), preexec_fn=self._preexec(cwd_fd),
                output_limit=MAX_RUNNER_QUERY_OUTPUT_BYTES, popen_factory=self._popen,
            )
        except (DockerInspectionError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"runner archive command failed: {exc}") from exc
        return CommandResult(result.returncode, result.stdout or b"", result.stderr or b"")

    def start(
        self, argv: Sequence[str], *, cwd_fd: int, env: Mapping[str, str], output_fd: int,
    ) -> RunnerProcess:
        return self._popen(
            list(argv), stdin=subprocess.DEVNULL, stdout=output_fd, stderr=subprocess.STDOUT,
            env=dict(env), close_fds=True, pass_fds=(cwd_fd, output_fd),
            preexec_fn=self._preexec(cwd_fd), start_new_session=True,
        )


def _systemd_slice_loaded(unit: str, timeout_s: float) -> bool:
    """Check the configured host slice without an unbounded systemctl call."""

    answer = run_bounded_command(
        ["/usr/bin/systemctl", "show", "--property=LoadState", "--value", unit],
        timeout_s,
        environment=_SYSTEMD_ENV,
        cwd="/",
        stdin=subprocess.DEVNULL,
        pass_fds=(),
        output_limit=MAX_RUNNER_QUERY_OUTPUT_BYTES,
    )
    try:
        load_state = (answer.stdout or b"").decode("ascii").strip()
    except UnicodeDecodeError:
        return False
    return answer.returncode == 0 and load_state == "loaded"


def _require_gate_slice(
    value: str | None,
    timeout_s: float,
    unit_loaded: Callable[[str], bool] | None,
) -> str:
    if not isinstance(value, str) or not value:
        raise AdmissionError(
            ExitCode.PREREQUISITE_MISSING,
            f"{_GATE_TIER_ENV} is required for ephemeral Docker work",
        )
    if _SYSTEMD_SLICE_RE.fullmatch(value) is None:
        raise AdmissionError(
            ExitCode.PREREQUISITE_MISSING,
            f"{_GATE_TIER_ENV} must name a valid systemd slice",
        )
    try:
        loaded = _systemd_slice_loaded(value, timeout_s) if unit_loaded is None else unit_loaded(value)
    except Exception as exc:
        raise AdmissionError(
            ExitCode.PREREQUISITE_MISSING,
            f"{_GATE_TIER_ENV} could not verify slice {value!r} as loaded: {exc}",
        ) from exc
    if loaded is not True:
        raise AdmissionError(
            ExitCode.PREREQUISITE_MISSING,
            f"{_GATE_TIER_ENV} slice {value!r} is not a loaded systemd unit",
        )
    return value


class DockerClient:
    """Small argv-only Docker inspection adapter used by the executor."""

    def __init__(
        self,
        timeout_s: float,
        *,
        run: Callable[..., Any] | None = None,
        cgroup_parent: str | None = None,
        unit_loaded: Callable[[str], bool] | None = None,
    ) -> None:
        self.timeout_s = timeout_s
        self._run = run
        self.cgroup_parent = cgroup_parent
        self._unit_loaded = unit_loaded

    def command(self, *argv: str) -> CommandResult:
        try:
            result = run_docker_command(list(argv), self.timeout_s, runner=self._run)
        except (DockerInspectionError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
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

    def check_passwd(
        self, image: str, username_or_uid: str, job_id: str, *, expected_gid: int | None = None,
    ) -> None:
        cgroup_parent = _require_gate_slice(
            self.cgroup_parent, self.timeout_s, self._unit_loaded,
        )
        safe = re.sub(r"[^A-Za-z0-9_.-]", "-", job_id)[:100]
        result = self.command(
            "run", "--rm", "--pull=never", "--network", "none", "--cgroup-parent", cgroup_parent,
            "--name", f"fleetd-uid-{safe}", "--entrypoint", "getent", image, "passwd", username_or_uid,
        )
        if result.returncode != 0:
            raise AdmissionError(
                ExitCode.PREREQUISITE_MISSING,
                f"image {image!r} has no passwd entry for lane user {username_or_uid!r}",
            )
        if expected_gid is not None:
            try:
                fields = result.stdout.decode("utf-8").strip().split(":")
                uid, gid = int(fields[2]), int(fields[3])
            except (UnicodeDecodeError, IndexError, ValueError) as exc:
                raise AdmissionError(
                    ExitCode.PREREQUISITE_MISSING,
                    f"image {image!r} returned an unreadable passwd entry for uid {username_or_uid!r}",
                ) from exc
            if uid != int(username_or_uid) or gid != expected_gid:
                raise AdmissionError(
                    ExitCode.PREREQUISITE_MISSING,
                    f"image {image!r} passwd identity for uid {username_or_uid!r} differs from runner uid/gid",
                )

    def inspect(self, target: str) -> Mapping[str, object] | None:
        result = self.command("inspect", target)
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace").strip()
            if _docker_missing_object(detail, target):
                return None
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker inspect {target!r} failed: {detail}")
        try:
            value = json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker inspect {target!r} returned invalid JSON") from exc
        if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker inspect {target!r} returned an invalid object")
        return value[0]

    def remove_stopped(self, container_id: str) -> None:
        if _CONTAINER_ID_RE.fullmatch(container_id) is None:
            raise AdmissionError(ExitCode.REFUSAL, "container removal requires a full Docker id")
        result = self.command("rm", container_id)
        if result.returncode == 0:
            return
        detail = result.stderr.decode("utf-8", "replace").strip()
        if _docker_missing_object(detail, container_id, container=True):
            return
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker could not remove stopped container {container_id}: {detail}")

    def job_containers(self, job_id: str) -> tuple[str, ...]:
        if _CONTAINER_NAME_RE.fullmatch(job_id) is None:
            raise AdmissionError(ExitCode.REFUSAL, "Docker job-container query requires a safe job id")
        result = self.command(
            "ps", "-a", "--no-trunc", "--quiet", "--filter", f"label=run-gate.job={job_id}",
        )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace").strip()
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker job-container query failed: {detail}")
        try:
            lines = result.stdout.decode("ascii").splitlines()
        except UnicodeDecodeError as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "Docker job-container query returned non-ASCII ids") from exc
        ids_list: list[str] = []
        for line in lines:
            candidate = line.strip()
            if not candidate:
                continue
            if len(ids_list) >= MAX_DOCKER_JOB_CONTAINER_IDS:
                raise AdmissionError(
                    ExitCode.IDENTITY_OR_TRANSPORT,
                    f"MAX_DOCKER_JOB_CONTAINER_IDS exceeded ({MAX_DOCKER_JOB_CONTAINER_IDS})",
                )
            ids_list.append(candidate)
        ids = tuple(ids_list)
        if any(_CONTAINER_ID_RE.fullmatch(container_id) is None for container_id in ids):
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "Docker job-container query returned a non-full id")
        if len(ids) != len(set(ids)):
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "Docker job-container query returned duplicate ids")
        return ids


def _docker_missing_object(detail: str, target: str, *, container: bool = False) -> bool:
    normalized = " ".join(detail.split()).casefold()
    normalized_target = target.casefold()
    accepted = {
        f"error: no such object: {normalized_target}",
        f"error response from daemon: no such object: {normalized_target}",
    }
    if container:
        accepted.update({
            f"error: no such container: {normalized_target}",
            f"error response from daemon: no such container: {normalized_target}",
        })
    return normalized in accepted


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


def _bounded_directory_entries(directory_fd: int) -> list[str]:
    """Read at most the named entry cap before sorting a directory listing."""

    names: list[str] = []
    with os.scandir(directory_fd) as entries:
        for entry in entries:
            if len(names) >= MAX_DIRECTORY_ENTRIES:
                raise OSError(f"MAX_DIRECTORY_ENTRIES exceeded ({MAX_DIRECTORY_ENTRIES})")
            names.append(entry.name)
    return sorted(names)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _tree_entries(
    root_fd: int, relative: str, *, reject_special: bool,
) -> tuple[list[tuple[str, bool, int]], str | None]:
    """Return regular files/directories under a root fd without following links."""

    try:
        first_fd = open_beneath(root_fd, relative, directory=True)
    except FileNotFoundError:
        return [], "absent"
    entries: list[tuple[str, bool, int]] = [(relative, True, 0)]
    pending: list[tuple[str, int, int]] = [(relative, first_fd, 0)]
    special_reason: str | None = None
    visited_entries = 1
    try:
        while pending:
            directory_rel, directory_fd, depth = pending.pop()
            try:
                names = _bounded_directory_entries(directory_fd)
                for name in names:
                    if visited_entries >= MAX_TREE_ENTRIES:
                        raise OSError(f"MAX_TREE_ENTRIES exceeded ({MAX_TREE_ENTRIES})")
                    visited_entries += 1
                    child_rel = f"{directory_rel}/{name}"
                    info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        if depth >= MAX_TREE_DEPTH:
                            raise OSError(f"MAX_TREE_DEPTH exceeded ({MAX_TREE_DEPTH})")
                        child_fd = os.open(
                            name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd,
                        )
                        entries.append((child_rel, True, 0))
                        pending.append((child_rel, child_fd, depth + 1))
                    elif stat.S_ISREG(info.st_mode):
                        entries.append((child_rel, False, info.st_size))
                    elif reject_special:
                        special_reason = "symlink" if stat.S_ISLNK(info.st_mode) else "special-file"
                        break
            finally:
                os.close(directory_fd)
            if special_reason:
                break
        for _relative, directory_fd, _depth in pending:
            _close(directory_fd)
        pending.clear()
        return ([], special_reason) if special_reason else (entries, None)
    except BaseException:
        for _relative, directory_fd, _depth in pending:
            _close(directory_fd)
        raise


def _artifact_filename(name: str) -> bool:
    return (
        (name.startswith("verdict") and name.endswith(".json"))
        or ("progress" in name and name.endswith(".jsonl"))
        or (name.startswith("coverage") and name.endswith(".json"))
        or name.endswith((".log", ".xml"))
    )


def _write_archive(
    root_fd: int, output_fd: int, entries: Sequence[tuple[str, bool, int]],
) -> None:
    """Write a gzip tar from fd-opened regular files and directories only."""

    if len(entries) > MAX_ARCHIVE_MEMBERS:
        raise OSError(f"MAX_ARCHIVE_MEMBERS exceeded ({MAX_ARCHIVE_MEMBERS})")
    if sum(size for _path, is_directory, size in entries if not is_directory) > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
        raise OSError(f"MAX_ARCHIVE_UNCOMPRESSED_BYTES exceeded ({MAX_ARCHIVE_UNCOMPRESSED_BYTES})")
    output = os.fdopen(os.dup(output_fd), "wb")
    try:
        with tarfile.open(fileobj=output, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
            for relative, is_directory, expected_size in sorted(entries):
                member = tarfile.TarInfo(relative + ("/" if is_directory else ""))
                member.mtime = 0
                member.uid = member.gid = 0
                member.uname = member.gname = ""
                if is_directory:
                    member.type = tarfile.DIRTYPE
                    member.mode = 0o755
                    member.size = 0
                    archive.addfile(member)
                    continue
                source_fd = open_beneath(root_fd, relative)
                try:
                    source_info = os.fstat(source_fd)
                    if not stat.S_ISREG(source_info.st_mode) or source_info.st_size != expected_size:
                        raise OSError(f"archive source changed while collecting: {relative}")
                    member.mode = 0o644
                    member.size = source_info.st_size
                    with os.fdopen(os.dup(source_fd), "rb") as source:
                        archive.addfile(member, source)
                finally:
                    os.close(source_fd)
        os.fsync(output_fd)
    finally:
        output.close()
    if os.fstat(output_fd).st_size > MAX_ARCHIVE_FILE_BYTES:
        raise OSError(f"MAX_ARCHIVE_FILE_BYTES exceeded ({MAX_ARCHIVE_FILE_BYTES})")


def _archive_artifacts(root_fd: int, output_fd: int) -> Mapping[str, object]:
    entries: list[tuple[str, bool, int]] = []
    for root in (".assay", ".run-gate"):
        found, reason = _tree_entries(root_fd, root, reject_special=False)
        if reason not in {None, "absent"}:
            raise OSError(f"cannot inspect artifact tree {root}: {reason}")
        if len(entries) + len(found) > MAX_TREE_ENTRIES:
            raise OSError(f"MAX_TREE_ENTRIES exceeded ({MAX_TREE_ENTRIES})")
        for relative, is_directory, size in found:
            if is_directory or (_artifact_filename(relative.rsplit("/", 1)[-1]) and size < _ARTIFACT_MAX_BYTES):
                entries.append((relative, is_directory, size))
    _write_archive(root_fd, output_fd, entries)
    return {"files": sum(not is_directory for _path, is_directory, _size in entries)}


def _archive_store(root_fd: int, output_fd: int, relatives: Sequence[str]) -> Mapping[str, object]:
    if len(relatives) > MAX_STORE_PATHS:
        raise OSError(f"MAX_STORE_PATHS exceeded ({MAX_STORE_PATHS})")
    entries: list[tuple[str, bool, int]] = []
    stores: list[dict[str, object]] = []
    logs: list[str] = []
    total = 0
    total_entries = 0
    for relative in relatives:
        if (
            _STORE_PATH_RE.fullmatch(relative) is None
            or any(part in {".", "..", ".git"} for part in relative.split("/"))
        ):
            status, reason = "failed", "invalid-path"
            found: list[tuple[str, bool, int]] = []
        else:
            try:
                found, special = _tree_entries(root_fd, relative, reject_special=True)
            except NotADirectoryError:
                found, special = [], "not-a-directory"
            except OSError as exc:
                if any(marker in str(exc) for marker in (
                    "MAX_DIRECTORY_ENTRIES", "MAX_TREE_ENTRIES", "MAX_TREE_DEPTH",
                )):
                    raise
                found, special = [], "symlink" if exc.errno == errno.ELOOP else "unreadable"
            if special == "absent":
                status, reason = "absent", None
            elif special:
                status, reason = "failed", special
            else:
                if total_entries + len(found) > MAX_TREE_ENTRIES:
                    raise OSError(f"MAX_TREE_ENTRIES exceeded ({MAX_TREE_ENTRIES})")
                total_entries += len(found)
                size = sum(item_size for _path, is_directory, item_size in found if not is_directory)
                if size > _STORE_MAX_BYTES or total + size > _STORE_MAX_BYTES:
                    status, reason = "too-large", str(size)
                    found = []
                else:
                    status, reason = "collected", None
                    total += size
        stores.append({"path": relative, "status": status, "reason": reason})
        if status == "collected":
            entries.extend(found)
            logs.append(f"STORE-COLLECTED {relative}")
        elif status == "absent":
            logs.append(f"STORE-ABSENT {relative}")
        else:
            logs.append(f"STORE-COLLECT-FAILED {relative} {reason}")
    try:
        _write_archive(root_fd, output_fd, entries)
    except (OSError, tarfile.TarError, ValueError) as exc:
        if any(marker in str(exc) for marker in _PERMANENT_LIMIT_MARKERS):
            raise
        for item in stores:
            if item["status"] == "collected":
                item.update(status="failed", reason="pack-failed")
        logs = [
            f"STORE-COLLECT-FAILED {item['path']} pack-failed"
            if item["reason"] == "pack-failed" else log_line
            for item, log_line in zip(stores, logs)
        ]
        return {"status": "failed", "reason": "pack-failed", "stores": stores, "logs": logs}
    successful = any(item["status"] == "collected" for item in stores)
    return {
        "status": "collected" if successful else "none",
        "bytes": total,
        "stores": stores,
        "logs": logs,
    }


def _verify_archive_fd(
    fd: int, *, kind: str, store_path: str | Sequence[str] | None = None,
) -> int:
    """Verify a just-written archive's complete member set and return its size."""

    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise AdmissionError(ExitCode.STORE_COLLECTION_FAILED, "collected archive is not a regular file")
    if info.st_size > MAX_ARCHIVE_FILE_BYTES:
        raise AdmissionError(
            ExitCode.STORE_COLLECTION_FAILED,
            f"MAX_ARCHIVE_FILE_BYTES exceeded ({MAX_ARCHIVE_FILE_BYTES})",
        )
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(fd), "rb") as stream:
            with tarfile.open(fileobj=stream, mode="r|gz") as archive:
                seen: set[str] = set()
                total = 0
                store_paths = (store_path,) if isinstance(store_path, str) else tuple(store_path or ())
                for member_count, member in enumerate(archive, start=1):
                    if member_count > MAX_ARCHIVE_MEMBERS:
                        raise AdmissionError(
                            ExitCode.STORE_COLLECTION_FAILED,
                            f"MAX_ARCHIVE_MEMBERS exceeded ({MAX_ARCHIVE_MEMBERS})",
                        )
                    name = member.name.rstrip("/")
                    parts = name.split("/")
                    if (
                        not name or name.startswith("/") or "\\" in name
                        or any(part in {"", ".", ".."} for part in parts)
                        or name in seen or not (member.isdir() or member.isfile())
                    ):
                        raise AdmissionError(ExitCode.STORE_COLLECTION_FAILED, f"collected {kind} archive has an unsafe member")
                    seen.add(name)
                    if kind == "artifacts" and not (
                        name in {".assay", ".run-gate"} and member.isdir()
                        or name.startswith(".assay/") or name.startswith(".run-gate/")
                    ):
                        raise AdmissionError(ExitCode.STORE_COLLECTION_FAILED, "artifact archive contains an unexpected path")
                    if kind == "artifacts" and member.isfile() and member.size >= _ARTIFACT_MAX_BYTES:
                        raise AdmissionError(ExitCode.STORE_COLLECTION_FAILED, "artifact archive contains an oversized file")
                    if member.isfile():
                        total += member.size
                        if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                            raise AdmissionError(
                                ExitCode.STORE_COLLECTION_FAILED,
                                f"MAX_ARCHIVE_UNCOMPRESSED_BYTES exceeded ({MAX_ARCHIVE_UNCOMPRESSED_BYTES})",
                            )
                    if kind == "store":
                        if not any(name == path or name.startswith(path + "/") for path in store_paths):
                            raise AdmissionError(ExitCode.STORE_COLLECTION_FAILED, "store archive contains an unexpected path")
                        if total > _STORE_MAX_BYTES:
                            raise AdmissionError(ExitCode.STORE_COLLECTION_FAILED, "store archive exceeds its uncompressed limit")
    except (OSError, EOFError, tarfile.TarError) as exc:
        raise AdmissionError(ExitCode.STORE_COLLECTION_FAILED, f"collected {kind} archive is unreadable: {exc}") from exc
    finally:
        os.lseek(fd, 0, os.SEEK_SET)
    return info.st_size


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
        process_signals: ProcessSignals | None = None,
        scope_process_reader: Callable[[str], Sequence[int]] | None = None,
        thrashguard_client: object | None = None,
        gate_cgroup_parent: str | None = None,
        gate_unit_loaded: Callable[[str], bool] | None = None,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.jobs_dir = self.state_dir / "jobs"
        self.job_id = _job_id(job_id)
        self.job_dir = self.jobs_dir / self.job_id
        self.policy = policy
        self.lock_fd = lock_fd
        self.user_resolver = user_resolver
        self.runner_factory = runner_factory
        self.gate_cgroup_parent = os.environ.get(_GATE_TIER_ENV) if gate_cgroup_parent is None else gate_cgroup_parent
        self.gate_unit_loaded = gate_unit_loaded
        self.docker = docker or DockerClient(
            policy.query_timeout_s, cgroup_parent=self.gate_cgroup_parent, unit_loaded=gate_unit_loaded,
        )
        self.pressure_reader = pressure_reader or _read_text_file
        self.meminfo_reader = meminfo_reader or _read_meminfo
        self.waiter = waiter
        self.clock = clock or _utc_now
        self.run_gate_finder = run_gate_finder
        self.process_signals = process_signals or SystemProcessSignals()
        self.scope_process_reader = scope_process_reader or _scope_processes
        self.thrashguard_client = thrashguard_client
        self.request: dict[str, object] = {}
        self.request_digest = ""
        self.group = ""
        self.runner_identity: RunnerIdentity | None = None
        self.runner: RunnerCommands | None = None
        self.repo_root = Path()
        self.repo_root_fd = -1
        self.checkout_rel = ""
        self.checkout_fd = -1
        self.project_fd = -1
        self.event_rel = ""
        self.event_path = Path()
        self.runner_process: RunnerProcess | None = None
        self.event_cursor = 0
        self.event_offset = 0
        self.event_bytes_read = 0
        self.event_pending = bytearray()
        self.event_records: list[Mapping[str, object]] = []
        self.event_file_identity: tuple[int, int] | None = None
        self.event_observations: list[Mapping[str, object]] = []
        self.event_observation_bytes = 0
        self.lane_error: str | None = None
        self.permanent_defect: str | None = None
        self.terminal_completed = False
        self.interruption_settled = False
        self.memory_lock_fd = -1

    def _state(self) -> dict[str, object]:
        raw = _read_regular(self.job_dir / "state.json", limit=MAX_STATE_RECORD_BYTES)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"executor state is unreadable: {exc}") from exc
        if not isinstance(value, dict) or value.get("schema") != "fleet-host-job-state/1" or value.get("job_id") != self.job_id:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "executor state identity is invalid")
        return value

    def _save_state(self, **updates: object) -> dict[str, object]:
        with _job_state_lock(self.job_dir):
            state = self._state()
            if (
                updates.get("state") not in {None, "done", "failed", "cancelled"}
                and state.get("state") == "stopping"
            ):
                updates = {key: value for key, value in updates.items() if key != "state"}
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
        self.request_digest = digest

    def _environment(self) -> dict[str, str]:
        assert self.runner_identity is not None
        for key in _DOCKER_SELECTOR_ENV:
            if key in self.policy.env_values:
                raise AdmissionError(
                    ExitCode.REFUSAL,
                    f"agent.toml.env_values.{key} cannot select the host Docker daemon or configuration",
                )
        extra = self.request.get("env")
        if isinstance(extra, Mapping):
            for key in _DOCKER_SELECTOR_ENV:
                if key in extra:
                    raise AdmissionError(
                        ExitCode.REFUSAL,
                        f"request env {key} cannot select the host Docker daemon or configuration",
                    )
        if _GATE_TIER_ENV in self.policy.env_values:
            raise AdmissionError(
                ExitCode.REFUSAL,
                f"agent.toml.env_values.{_GATE_TIER_ENV} cannot select the host gate cgroup tier",
            )
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
            "DOCKER_HOST": "unix:///var/run/docker.sock",
        }
        if isinstance(extra, Mapping):
            if _GATE_TIER_ENV in extra:
                raise AdmissionError(
                    ExitCode.REFUSAL,
                    f"request env {_GATE_TIER_ENV} cannot select the host gate cgroup tier",
                )
            for key, value in extra.items():
                if isinstance(key, str) and isinstance(value, str):
                    env[key] = value
        return env

    def _require_gate_tier(self) -> str:
        return _require_gate_slice(
            self.gate_cgroup_parent, self.policy.query_timeout_s, self.gate_unit_loaded,
        )

    def _lane_runner_environment(self, mode: str, facts: Mapping[str, object]) -> dict[str, str]:
        env = self._environment()
        if mode == "ephemeral":
            env[_GATE_TIER_ENV] = self._require_gate_tier()
            env["RUN_GATE_TESTER_UNIFIED_IMAGE"] = str(facts["image"])
        return env

    def _run(self, argv: Sequence[str], *, cwd_fd: int | None = None, timeout: float | None = None) -> CommandResult:
        if self.runner is None:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, "runner command interface is unavailable")
        result = self.runner.run(
            argv, cwd_fd=self.repo_root_fd if cwd_fd is None else cwd_fd,
            env=self._environment(), timeout=timeout,
        )
        return result

    def _run_query(self, argv: Sequence[str], *, cwd_fd: int | None = None) -> CommandResult:
        """Run one state inspection under the host's single-command deadline."""

        return self._run(argv, cwd_fd=cwd_fd, timeout=self.policy.query_timeout_s)

    def _run_checked(
        self, argv: Sequence[str], *, cwd_fd: int | None = None, exit_code: int = ExitCode.RUNNER_FAILURE,
        detail: str, timeout: float | None = None,
    ) -> CommandResult:
        result = self._run(argv, cwd_fd=cwd_fd, timeout=timeout)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", "replace").strip()
            raise AdmissionError(exit_code, f"{detail}{': ' + stderr if stderr else f' (exit {result.returncode})'}")
        return result

    def _helper(self, mode: str, *args: str, cwd_fd: int | None = None) -> CommandResult:
        try:
            return self._run_checked(
                self._helper_argv(mode, *args),
                cwd_fd=self.repo_root_fd if cwd_fd is None else cwd_fd,
                detail=f"runner-user helper {mode!r} failed",
            )
        except AdmissionError as exc:
            marker = next((value for value in _PERMANENT_LIMIT_MARKERS if value in exc.reason), None)
            if marker is None:
                raise
            self.permanent_defect = f"{marker} exceeded"
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, self.permanent_defect) from exc

    @staticmethod
    def _helper_argv(mode: str, *args: str) -> list[str]:
        """Launch a helper with only the packaged source root on its project import path."""

        package_root = str(Path(__file__).resolve().parent.parent)
        return [
            sys.executable, "-I", "-P", "-c", _HELPER_BOOTSTRAP,
            package_root, mode, *args,
        ]

    def _cancel_checkpoint(self) -> None:
        state = self._state()
        if state.get("cancel_requested") is True and state.get("lane_pid") is None:
            raise AdmissionError(ExitCode.CANCELLED, str(state.get("cancel_reason") or "cancelled before lane start"))

    def _await_scope_confirmation(self) -> bool:
        """Do not consume the request or charge until the parent confirms scope start."""

        gate_fd = -1
        try:
            gate_fd = os.open(self.job_dir / "start.lock", os.O_RDWR | os.O_NOFOLLOW)
            fcntl.flock(gate_fd, fcntl.LOCK_EX)
            return self._state().get("start_confirmed") is True
        except (AdmissionError, OSError):
            return False
        finally:
            if gate_fd >= 0:
                try:
                    fcntl.flock(gate_fd, fcntl.LOCK_UN)
                finally:
                    os.close(gate_fd)

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
        self._helper("prepare", "--relative", ".fleetd-worktrees")
        checkout_record = {"schema": "fleet-host-checkout/1", "repo": repo, "relative": self.checkout_rel}
        _atomic_json(self.job_dir / "checkout.json", checkout_record)

        seeds = self._host_checkout_candidates()
        request_base = self._validated_request_base()
        seeded_sha = False
        if sha in seeds and self._reset_checkout(sha):
            self._copy_seed_refs(sha)
            seeded_sha = self._checkout_commit_resolves(sha)
        if not seeded_sha:
            self._fetch_uploaded_bundle(seeds, required_base=request_base)
        self._checkout_requested_sha(sha)

        required_base = self._required_checkout_base(request_base)
        if required_base is not None and not self._checkout_commit_resolves(required_base):
            self._fetch_uploaded_bundle(seeds, required_base=required_base)
            self._checkout_requested_sha(sha)
        self._synchronize_job_refs()
        actual = self._checkout_requested_sha(sha)
        if required_base is not None and not self._checkout_commit_resolves(required_base):
            self._log(f"base {required_base} missing on the host after bundle resolution")
            raise AdmissionError(ExitCode.MISSING_BASE, f"base {required_base} missing on the host")
        self._log(f"checkout {self.repo_root / self.checkout_rel} at {actual}")
        self._save_state(checkout={"ready": True, "sha": actual})

    def _validated_request_base(self) -> str | None:
        base = self.request.get("base")
        if base is None:
            return None
        if not isinstance(base, str) or not base or base.startswith("-") or "\0" in base:
            raise AdmissionError(ExitCode.MISSING_BASE, "request base is invalid")
        return base

    def _required_checkout_base(self, request_base: str | None) -> str | None:
        if request_base is not None:
            return request_base
        project = self.request.get("project")
        lane_name = self.request.get("lane")
        if not isinstance(project, str) or not isinstance(lane_name, str) or self.checkout_fd < 0:
            return None
        try:
            lane, _environment = _lane_environment(self.checkout_fd, project, lane_name)
        except AdmissionError:
            # Checkout validity is proved here; the prerequisites path reports
            # the original lane/configuration refusal before a lane can start.
            return None
        base = lane.get("base")
        if base is None:
            return None
        if not isinstance(base, str) or not base or base.startswith("-") or "\0" in base:
            raise AdmissionError(ExitCode.PREREQUISITE_MISSING, "Run-Gate lane declares an invalid comparison base")
        if base in {"main", "origin/main"}:
            raise AdmissionError(
                ExitCode.MISSING_BASE,
                f"lane base {base!r} requires an explicit request base; the host has no authenticated current main",
            )
        return base

    def _checkout_commit_resolves(self, commit: str) -> bool:
        answer = self._run_query([
            "git", "-C", str(self.repo_root / self.checkout_rel), "cat-file", "-e", f"{commit}^{{commit}}",
        ], cwd_fd=self.repo_root_fd)
        return answer.returncode == 0

    def _checkout_requested_sha(self, sha: str) -> str:
        self._run_checked(
            ["git", "-C", str(self.repo_root / self.checkout_rel), "checkout", "--quiet", "--detach", sha],
            cwd_fd=self.repo_root_fd, exit_code=ExitCode.CHECKOUT_FAILURE, detail="cannot check out requested commit",
        )
        actual = self._run_checked(
            ["git", "-C", str(self.repo_root / self.checkout_rel), "rev-parse", "HEAD"],
            cwd_fd=self.repo_root_fd, exit_code=ExitCode.CHECKOUT_FAILURE, detail="cannot read host checkout commit",
            timeout=self.policy.query_timeout_s,
        ).stdout.decode("ascii", "strict").strip()
        if actual != sha:
            raise AdmissionError(ExitCode.CHECKOUT_FAILURE, f"host checkout commit {actual!r} does not match request sha")
        return actual

    def _host_checkout_candidates(self) -> list[str]:
        """Return only real, full-commit directories that can seed a local clone."""

        candidates: list[str] = []
        try:
            names = _bounded_directory_entries(self.repo_root_fd)
        except OSError as exc:
            raise AdmissionError(ExitCode.CHECKOUT_FAILURE, str(exc)) from exc
        for name in names:
            if _COMMIT_RE.fullmatch(name) is None:
                continue
            try:
                fd = open_beneath(self.repo_root_fd, name, directory=True)
            except (OSError, ValueError):
                continue
            else:
                os.close(fd)
                candidates.append(name)
        return candidates

    def _reset_checkout(self, seed_rel: str | None) -> bool:
        if self.project_fd >= 0 or self.checkout_fd >= 0:
            _close(self.project_fd, self.checkout_fd)
            self.project_fd = self.checkout_fd = -1
        self._helper("remove-tree", "--relative", self.checkout_rel)
        if seed_rel is None:
            result = self._run([
                "git", "init", "--quiet", "--initial-branch=main",
                str(self.repo_root / self.checkout_rel),
            ], cwd_fd=self.repo_root_fd)
        else:
            seed_fd = -1
            try:
                seed_fd = open_beneath(self.repo_root_fd, seed_rel, directory=True)
            except (OSError, ValueError):
                return False
            finally:
                _close(seed_fd)
            result = self._run([
                "git", "clone", "--quiet", "--no-checkout",
                str(self.repo_root / seed_rel), str(self.repo_root / self.checkout_rel),
            ], cwd_fd=self.repo_root_fd)
        if result.returncode != 0:
            self._helper("remove-tree", "--relative", self.checkout_rel)
            return False
        try:
            self.checkout_fd = open_beneath(self.repo_root_fd, self.checkout_rel, directory=True)
        except (OSError, ValueError) as exc:
            raise AdmissionError(ExitCode.REFUSAL, f"host checkout path is unsafe: {exc}") from exc
        return True

    def _copy_seed_refs(self, seed_rel: str) -> None:
        seed_path = str(self.repo_root / seed_rel)
        listed = self._run_query([
            "git", "-C", seed_path, "for-each-ref", "--format=%(refname)", "refs/tags",
        ], cwd_fd=self.repo_root_fd)
        if listed.returncode != 0:
            self._log(f"release tag listing from seed {seed_rel} failed; retaining no additional tags")
            return
        try:
            refs = listed.stdout.decode("ascii", "strict").splitlines()
        except UnicodeDecodeError:
            self._log(f"release tag listing from seed {seed_rel} was unreadable; retaining no additional tags")
            return
        for ref in refs:
            if not ref.startswith("refs/tags/") or _RELEASE_TAG_RE.fullmatch(ref[len("refs/tags/"):]) is None:
                continue
            result = self._run([
                "git", "-C", str(self.repo_root / self.checkout_rel), "fetch", "--quiet",
                seed_path, f"+{ref}:{ref}",
            ], cwd_fd=self.repo_root_fd)
            if result.returncode != 0:
                self._log(f"seed ref {ref} unavailable from {seed_rel}")

    def _synchronize_job_refs(self) -> None:
        assert self.checkout_fd >= 0
        base = self.request.get("base")
        if isinstance(base, str):
            self._run_checked(
                ["git", "-C", str(self.repo_root / self.checkout_rel), "update-ref", "refs/remote-gate/base", base],
                cwd_fd=self.repo_root_fd, exit_code=ExitCode.MISSING_BASE,
                detail=f"cannot install requested base {base}",
            )
        else:
            self._run([
                "git", "-C", str(self.repo_root / self.checkout_rel), "update-ref", "-d", "refs/remote-gate/base",
            ], cwd_fd=self.repo_root_fd)

        for ref in ("refs/remotes/origin/main", "refs/heads/main"):
            self._run([
                "git", "-C", str(self.repo_root / self.checkout_rel), "update-ref", "-d", ref,
            ], cwd_fd=self.repo_root_fd)

    def _uploaded_objects(self, name: str) -> list[tuple[str, int]]:
        root_fd = os.open(self.state_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        uploads_fd = group_fd = name_fd = -1
        try:
            uploads_fd = open_beneath(root_fd, "uploads", directory=True)
            group_fd = open_beneath(uploads_fd, self.group, directory=True)
            name_fd = open_beneath(group_fd, name, directory=True)
            result: list[tuple[str, int]] = []
            try:
                digests = _bounded_directory_entries(name_fd)
            except OSError as exc:
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, str(exc)) from exc
            for digest in digests:
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
        except AdmissionError:
            raise
        except (OSError, ValueError) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot inspect host upload objects: {exc}") from exc
        finally:
            _close(name_fd, group_fd, uploads_fd, root_fd)

    def _fetch_uploaded_bundle(
        self, seeds: Sequence[str] = (), *, required_base: str | None = None,
    ) -> None:
        objects = self._uploaded_objects("in.bundle")
        if not objects:
            raise AdmissionError(ExitCode.CHECKOUT_FAILURE, "no verified in.bundle upload contains the requested commit")
        assert self.runner is not None
        request_sha = self.request.get("sha")
        if not isinstance(request_sha, str) or _COMMIT_RE.fullmatch(request_sha) is None:
            raise AdmissionError(ExitCode.REFUSAL, "job request sha is invalid during bundle resolution")
        for digest, size in objects:
            root_fd = os.open(self.state_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            source_fd = -1
            uploads_fd = group_fd = name_fd = -1
            target_rel = f".fleetd-worktrees/.{self.job_id}.bundle-{digest}"
            try:
                uploads_fd = open_beneath(root_fd, "uploads", directory=True)
                group_fd = open_beneath(uploads_fd, self.group, directory=True)
                name_fd = open_beneath(group_fd, "in.bundle", directory=True)
                source_fd = open_beneath(name_fd, digest)
                copied = self.runner.run_with_input(
                    self._helper_argv(
                        "write-file", "--relative", target_rel, "--sha256", digest,
                        "--bytes", str(size),
                    ),
                    cwd_fd=self.repo_root_fd, env=self._environment(), source_fd=source_fd,
                )
                if copied.returncode != 0:
                    raise AdmissionError(ExitCode.RUNNER_FAILURE, "runner could not stage the verified bundle")
            except OSError as exc:
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot open verified bundle upload: {exc}") from exc
            finally:
                _close(source_fd, name_fd, group_fd, uploads_fd, root_fd)
            bundle = str(self.repo_root / target_rel)
            try:
                for seed_rel in (*seeds, None):
                    if not self._reset_checkout(seed_rel):
                        continue
                    verified = self._run_query([
                        "git", "-C", str(self.repo_root / self.checkout_rel), "bundle", "verify", bundle,
                    ], cwd_fd=self.repo_root_fd)
                    if verified.returncode != 0:
                        continue
                    fetched = self._run([
                        "git", "-C", str(self.repo_root / self.checkout_rel), "fetch", "--quiet", bundle, "HEAD",
                    ], cwd_fd=self.repo_root_fd)
                    if fetched.returncode != 0:
                        continue
                    for ref in ("refs/remote-gate/base",):
                        self._run([
                            "git", "-C", str(self.repo_root / self.checkout_rel), "fetch", "--quiet",
                            bundle, f"+{ref}:{ref}",
                        ], cwd_fd=self.repo_root_fd)
                    tags = self._run_query(["git", "ls-remote", bundle, "refs/tags/*"], cwd_fd=self.repo_root_fd)
                    if tags.returncode == 0:
                        for line in tags.stdout.decode("ascii", "replace").splitlines():
                            fields = line.split("\t", 1)
                            if (
                                len(fields) != 2
                                or not fields[1].startswith("refs/tags/")
                                or fields[1].endswith("^{}")
                                or _RELEASE_TAG_RE.fullmatch(fields[1][len("refs/tags/"):]) is None
                            ):
                                continue
                            exists = self._run_query([
                                "git", "-C", str(self.repo_root / self.checkout_rel),
                                "show-ref", "--verify", "--quiet", fields[1],
                            ], cwd_fd=self.repo_root_fd)
                            if exists.returncode == 0:
                                continue
                            self._run([
                                "git", "-C", str(self.repo_root / self.checkout_rel), "fetch", "--quiet",
                                bundle, f"+{fields[1]}:{fields[1]}",
                            ], cwd_fd=self.repo_root_fd)
                    else:
                        self._log("release tag listing from bundle failed; retaining seed tags")
                    resolved = self._run_query([
                        "git", "-C", str(self.repo_root / self.checkout_rel), "cat-file", "-e",
                        f"{request_sha}^{{commit}}",
                    ], cwd_fd=self.repo_root_fd)
                    if resolved.returncode == 0 and (
                        required_base is None or self._checkout_commit_resolves(required_base)
                    ):
                        return
            finally:
                self._helper("remove-tree", "--relative", target_rel, cwd_fd=self.repo_root_fd)
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
            self._require_gate_tier()
        project_rel = "" if project == "." else project
        project_fd = os.dup(self.checkout_fd) if not project_rel else open_beneath(self.checkout_fd, project_rel, directory=True)
        try:
            config_rel = "run-gate.toml" if project == "." else f"{project}/run-gate.toml"
            config = _parse_toml_fd(self.checkout_fd, config_rel) or {}
            admission = config.get("admission")
            if isinstance(admission, Mapping) and admission.get("enabled") is True:
                try:
                    answer = self.runner.run(
                        [binary, "admission", "show"], cwd_fd=project_fd, env=self._environment(),
                        timeout=self.policy.query_timeout_s,
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    raise AdmissionError(
                        ExitCode.PREREQUISITE_MISSING, f"run-gate admission show failed: {exc}",
                    ) from exc
                output = answer.stdout.decode("utf-8", "replace")
                if answer.returncode != 0:
                    raise AdmissionError(
                        ExitCode.PREREQUISITE_MISSING,
                        f"run-gate admission show failed (rc {answer.returncode}): {output[:200]}",
                    )
                enabled = re.search(r"(?m)^.*admission: enabled=(true|false).*$", output)
                if enabled is None:
                    raise AdmissionError(
                        ExitCode.PREREQUISITE_MISSING,
                        f"run-gate admission show printed no enabled=true|false statement: {output[:200]}",
                    )
                if enabled.group(1) == "true" and re.search(r"(?m)^.*max_concurrent=[0-9]+.*$", output) is None:
                    raise AdmissionError(
                        ExitCode.PREREQUISITE_MISSING,
                        "run-gate admission is enabled by the repo but this host publishes no gates cap",
                    )
        finally:
            os.close(project_fd)
        if mode == "ephemeral":
            image = environment.get("image")
            image_env = environment.get("image_env")
            if isinstance(image_env, str):
                image = self._environment().get(image_env, image)
            if not isinstance(image, str) or _IMAGE_RE.fullmatch(image) is None:
                raise AdmissionError(ExitCode.PREREQUISITE_MISSING, "ephemeral Run-Gate environment has no resolved image")
            image_info = self.docker.image(image)
            self._check_lane_user(image, image_info, environment)
            self._check_declared_base(lane)
            return binary, mode, {"image": image, "lane": lane, "environment": environment,
                                 "run_gate_version": version, "runner_name": None}
        if mode == "exec":
            container_name = _exec_container_name(self.checkout_fd, str(lane["environment"]), environment)
            if _CONTAINER_NAME_RE.fullmatch(container_name) is None:
                raise AdmissionError(ExitCode.PREREQUISITE_MISSING, "exec-mode persistent runner name is invalid")
            existing = self.docker.inspect(container_name)
            container_state = existing.get("State") if isinstance(existing, Mapping) else None
            if existing is None or not isinstance(container_state, Mapping) or container_state.get("Running") is not True:
                raise AdmissionError(ExitCode.PREREQUISITE_MISSING, f"persistent runner {container_name!r} is not running")
            self._check_declared_base(lane)
            return binary, mode, {"container_name": container_name, "lane": lane, "environment": environment,
                                 "run_gate_version": version, "runner_name": container_name}
        self._check_declared_base(lane)
        return binary, mode, {"lane": lane, "environment": environment, "run_gate_version": version,
                             "runner_name": None}

    def _check_declared_base(self, lane: Mapping[str, object]) -> None:
        declared = lane.get("base")
        request_base = self.request.get("base")
        if request_base is not None and not isinstance(request_base, str):
            raise AdmissionError(ExitCode.MISSING_BASE, "request base is invalid")
        if isinstance(declared, str) and declared in {"main", "origin/main"} and request_base is None:
            raise AdmissionError(
                ExitCode.MISSING_BASE,
                f"lane base {declared!r} requires an explicit request base; the host has no authenticated current main",
            )
        base = request_base if request_base is not None else declared
        if base is None:
            return
        if not isinstance(base, str) or not base or base.startswith("-") or "\0" in base:
            raise AdmissionError(ExitCode.PREREQUISITE_MISSING, "Run-Gate lane declares an invalid comparison base")
        result = self._run_query([
            "git", "-C", str(self.repo_root / self.checkout_rel), "cat-file", "-e", f"{base}^{{commit}}",
        ], cwd_fd=self.repo_root_fd)
        if result.returncode != 0:
            raise AdmissionError(ExitCode.MISSING_BASE, f"Run-Gate lane base {base!r} is missing from the host checkout")

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
        if user == "invoker":
            assert self.runner_identity is not None
            if self.runner_identity.uid == 0:
                raise AdmissionError(ExitCode.PREREQUISITE_MISSING, "lane user invoker resolves to uid 0")
            self.docker.check_passwd(
                image, str(self.runner_identity.uid), self.job_id,
                expected_gid=self.runner_identity.gid,
            )
            return
        if user in {"", "0", "root"}:
            return
        if user.isdigit():
            if int(user) == 0:
                return
            gid = None
            if isinstance(declared, str) and ":" in declared:
                try:
                    gid = int(declared.split(":", 1)[1])
                except ValueError as exc:
                    raise AdmissionError(ExitCode.PREREQUISITE_MISSING, "lane user gid is invalid") from exc
            self.docker.check_passwd(image, user, self.job_id, expected_gid=gid)
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
            self._cancel_checkpoint()

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
                    self._cancel_checkpoint()
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
                self._cancel_checkpoint()
        except BaseException:
            os.close(fd)
            raise

    def _save_post_start_retry(self, reason: str) -> None:
        """Keep a post-start failure visible without transferring ownership."""

        try:
            current = self._state()
            previous = current.get("reason")
            detail = f"post-start retry required: {reason}"[:1000]
            if isinstance(previous, str) and previous:
                detail = previous[:1000] if reason in previous else f"{previous}; {reason}"[:1000]
            self._save_state(
                state="stopping", exit_code=ExitCode.QUEUED_WITHDRAWN,
                reason=detail, retry_required=True,
            )
        except Exception:
            # A later retry may succeed; the worker and admission lock remain
            # the live owner while this record is unavailable.
            pass

    def _finalize_lane_with_retry(self, status: int) -> int:
        """Retry unexpected finalization failures while retaining the charge."""

        while True:
            try:
                return self._finalize_lane(status)
            except Exception as exc:
                self._save_post_start_retry(f"finalization deferred: {exc}")
                self.waiter(1)

    def _resume_lane_after_worker_error(self, reason: str) -> int:
        """Recover the worker-owned lane wait after an unexpected exception."""

        self.lane_error = self.lane_error or f"worker-post-start-error: {reason}"
        self._save_post_start_retry(reason)
        while True:
            try:
                state = self._state()
            except Exception as exc:
                self._save_post_start_retry(f"cannot read lane state: {exc}")
                self.waiter(1)
                continue
            status = state.get("lane_process_status")
            if type(status) is int:
                return self._finalize_lane_with_retry(status)
            process = self.runner_process
            if process is None:
                # Start was marked as possibly live before Popen returned. With
                # no owned process handle there is no safe stop or release.
                self.waiter(1)
                continue
            try:
                status = process.poll()
            except Exception as exc:
                self._save_post_start_retry(f"lane status query deferred: {exc}")
                self.waiter(1)
                continue
            if status is not None:
                self.runner_process = None
                self._record_lane_exit(status)
                return self._finalize_lane_with_retry(status)
            try:
                self._follow_lane_events(final=False)
            except Exception as exc:
                self._save_post_start_retry(f"lane event query deferred: {exc}")
            try:
                self._request_worker_stop(process)
            except Exception as exc:
                self._save_post_start_retry(f"lane stop request deferred: {exc}")
            self.waiter(1)

    def _settle_worker_interruption(self, interrupted: BaseException) -> None:
        """Keep retrying safe completion until ownership can be transferred."""

        reason = f"worker interrupted: {type(interrupted).__name__}: {interrupted}"
        while True:
            try:
                current = self._state()
                if (
                    current.get("lane_pid") is not None
                    or current.get("lane_process_status") is not None
                    or current.get("lane_may_be_live") is True
                ):
                    self._save_post_start_retry(reason)
                    status = current.get("lane_process_status")
                    if type(status) is int:
                        self._finalize_lane_with_retry(status)
                    else:
                        self._resume_lane_after_worker_error(reason)
                else:
                    self._finish_unstarted(
                        int(ExitCode.RUNNER_FAILURE),
                        f"worker interrupted before lane start: {type(interrupted).__name__}: {interrupted}",
                    )
                return
            except BaseException as cleanup_error:
                try:
                    self._save_post_start_retry(f"{reason}; cleanup retry: {cleanup_error}")
                except BaseException:
                    pass
                try:
                    self.waiter(1)
                except BaseException:
                    pass

    def _settle_interruption_before_run(self, interrupted: BaseException) -> None:
        """Wait for the parent start gate before resolving an early interruption."""

        while True:
            try:
                confirmed = self._await_scope_confirmation()
            except BaseException:
                try:
                    self.waiter(1)
                except BaseException:
                    pass
                continue
            if not confirmed:
                self.interruption_settled = True
                raise interrupted
            try:
                self._finish_unstarted(
                    int(ExitCode.RUNNER_FAILURE),
                    f"worker interrupted before lane start: {type(interrupted).__name__}: {interrupted}",
                )
            except BaseException:
                try:
                    self.waiter(1)
                except BaseException:
                    pass
                continue
            self.interruption_settled = True
            return

    def _handle_run_error(self, exit_code: int, reason: str) -> int:
        """Recover an ordinary worker error without losing an interruption."""

        try:
            self._log(f"executor stopped exit={exit_code}: {reason}")
            try:
                current = self._state()
            except AdmissionError:
                current = {}
            if (
                current.get("lane_pid") is not None
                or current.get("lane_process_status") is not None
                or current.get("lane_may_be_live") is True
            ):
                self._save_post_start_retry(reason)
                status = current.get("lane_process_status")
                if type(status) is int:
                    return self._finalize_lane_with_retry(status)
                return self._resume_lane_after_worker_error(reason)
            return self._finish_unstarted(exit_code, reason)
        except BaseException as interrupted:
            self._settle_worker_interruption(interrupted)
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
        tracked = self._run_query([
            "git", "-C", str(self.repo_root / self.checkout_rel), "ls-files", "--", target,
        ], cwd_fd=self.repo_root_fd)
        if tracked.returncode != 0:
            raise AdmissionError(ExitCode.STORE_SEED_FAILED, f"STORE-SEED-FAILED {target} tracked-check")
        if tracked.stdout:
            raise AdmissionError(ExitCode.STORE_SEED_FAILED, f"STORE-SEED-FAILED {target} tracked-files")
        source_fd = self._open_upload_object(upload_name, str(digest))
        try:
            result = self.runner.run_with_input(
                self._helper_argv(
                    "extract-seed", "--relative", target, "--sha256", str(digest),
                    "--bytes", str(size), "--max-bytes", str(_STORE_MAX_BYTES),
                ),
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
            try:
                names = _bounded_directory_entries(group_fd)
            except OSError as exc:
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, str(exc)) from exc
            for name in names:
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

    def _run_lane(self, binary: str, mode: str, facts: Mapping[str, object], memory_lock_fd: int | None) -> int:
        assert self.runner is not None and self.checkout_fd >= 0
        project = self.request.get("project")
        lane = self.request.get("lane")
        if not isinstance(project, str) or not isinstance(lane, str):
            raise AdmissionError(ExitCode.REFUSAL, "request project or lane is invalid")
        project_rel = "" if project == "." else project
        self.project_fd = os.dup(self.checkout_fd) if not project_rel else open_beneath(self.checkout_fd, project_rel, directory=True)
        repo_key = self.request.get("repo")
        if not isinstance(repo_key, str):
            raise AdmissionError(ExitCode.REFUSAL, "request repo is invalid")
        self.event_rel = f".fleetd-events/{repo_key}/{self.job_id}.jsonl"
        event_parent_fd = _open_absolute_directory(self.repo_root.parent)
        try:
            self._helper("prepare-event", "--relative", self.event_rel, cwd_fd=event_parent_fd)
        finally:
            os.close(event_parent_fd)
        self.event_path = self.repo_root.parent / self.event_rel
        argv = [binary, lane]
        extra = self.request.get("extra_argv")
        if not isinstance(extra, list) or any(not isinstance(item, str) for item in extra):
            raise AdmissionError(ExitCode.REFUSAL, "request extra_argv is invalid")
        argv.extend(extra)
        base = self.request.get("base")
        if isinstance(base, str):
            argv.extend(("--base", base))
        argv.extend(("--event-file", str(self.event_path), "--job-id", self.job_id))
        env = self._lane_runner_environment(mode, facts)
        log_fd = os.open(self.job_dir / "lane.log", os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            runner_name = facts.get("runner_name")
            self._save_state(
                state="lane-starting", lane_may_be_live=True, lane=lane, lane_mode=mode,
                runner_name=runner_name, lane_status_owner="worker", event_file=str(self.event_path),
                container={"id": None, "name": runner_name, "image": facts.get("image")},
            )
            try:
                self._cancel_checkpoint()
            except AdmissionError as exc:
                if exc.exit_code == ExitCode.CANCELLED:
                    # This worker knows start() has not been called. Clear the
                    # conservative marker before taking the never-started path.
                    self._retry_state_save(lane_may_be_live=False, lane_start_attempted=False)
                raise
            process = self.runner.start(argv, cwd_fd=self.project_fd, env=env, output_fd=log_fd)
        finally:
            os.close(log_fd)
        self.runner_process = process
        self._retry_state_save(lane_pid=process.pid, state="running", lane_status_owner="worker")
        status: int | None = None
        if memory_lock_fd is not None:
            try:
                self.waiter(_MEMORY_SETTLE_S)
                try:
                    status = process.poll()
                except Exception:
                    status = None
                if status is not None:
                    self.runner_process = None
                    self._record_lane_exit(status)
                try:
                    self._cancel_checkpoint()
                except AdmissionError as exc:
                    if exc.exit_code != ExitCode.CANCELLED:
                        raise
                    # The lane has started.  The worker owns its process and
                    # must stop it through the guard/TERM path before waiting
                    # for exit and collection.
                if status is None and self._state().get("cancel_requested") is True:
                    self._request_worker_stop(process)
            finally:
                fcntl.flock(memory_lock_fd, fcntl.LOCK_UN)
                os.close(memory_lock_fd)
                self.memory_lock_fd = -1
        while status is None:
            self._follow_lane_events(final=False)
            try:
                status = process.poll()
            except Exception:
                status = None
            if status is not None:
                self.runner_process = None
                self._record_lane_exit(status)
                break
            try:
                state = self._state()
            except AdmissionError:
                state = {}
            if state.get("cancel_requested") is True:
                try:
                    self._request_worker_stop(process)
                except AdmissionError:
                    pass
            self.waiter(1)
            try:
                status = process.poll()
            except Exception:
                status = None
            if status is not None:
                self.runner_process = None
                self._record_lane_exit(status)
            try:
                cancel_after_wait = self._state().get("cancel_requested") is True
            except AdmissionError:
                cancel_after_wait = False
            if status is not None:
                break
            if cancel_after_wait:
                try:
                    self._request_worker_stop(process)
                except AdmissionError:
                    pass
        try:
            self._follow_lane_events(final=True)
        except AdmissionError as exc:
            self.lane_error = self.lane_error or exc.reason
        self._log(f"lane exited rc={status}")
        return status

    def _record_lane_exit(self, status: int) -> None:
        """Persist the worker-owned child status before any other lane work."""

        self._retry_state_save(
            state="collecting", lane_process_status=status, exit_code=status,
            lane_may_be_live=False, lane_status_owner="worker",
        )

    def _retry_state_save(self, **updates: object) -> dict[str, object]:
        while True:
            try:
                return self._save_state(**updates)
            except AdmissionError as exc:
                if (
                    "executor JSON record exceeds" in exc.reason
                    or "state.json exceeds" in exc.reason
                ):
                    self.permanent_defect = "MAX_STATE_RECORD_BYTES exceeded"
                    raise AdmissionError(
                        ExitCode.IDENTITY_OR_TRANSPORT, self.permanent_defect,
                    ) from exc
                self.waiter(1)
                if self.runner_process is not None and not {
                    "stop_signal", "cancel_refused", "guard_deregistered",
                }.intersection(updates):
                    try:
                        current = self._state()
                    except AdmissionError:
                        continue
                    if current.get("cancel_requested") is True:
                        self._request_worker_stop(self.runner_process)
            except OSError:
                self.waiter(1)
                if self.runner_process is not None and not {
                    "stop_signal", "cancel_refused", "guard_deregistered",
                }.intersection(updates):
                    try:
                        current = self._state()
                    except AdmissionError:
                        continue
                    if current.get("cancel_requested") is True:
                        self._request_worker_stop(self.runner_process)

    def _request_worker_stop(self, process: RunnerProcess) -> None:
        poll = getattr(process, "poll", None)
        if callable(poll):
            try:
                status = poll()
            except Exception:
                status = None
            if status is not None:
                self.runner_process = None
                self._record_lane_exit(status)
                return
        state = self._state()
        if state.get("stop_signal") in {"sent", "process-already-exited"}:
            return
        if state.get("lane_mode") == "exec":
            reason = "exec-cancel-cannot-prove-runner-process-stopped"
            self.lane_error = self.lane_error or reason
            self._retry_state_save(state="stopping", reason=reason, cancel_refused=True)
            return
        guard = state.get("guard")
        present = guard.get("present") if isinstance(guard, Mapping) else None
        if present is True:
            action = getattr(self.thrashguard_client, "deregister", None)
            if not callable(action):
                self.lane_error = self.lane_error or "thrashguard-deregister-unavailable"
                self._retry_state_save(
                    state="stopping", reason=self.lane_error, cancel_refused=True,
                )
                return
            try:
                action(self.job_id)
            except Exception as exc:
                self._retry_state_save(
                    state="stopping", reason=f"thrashguard-deregister-failed: {exc}",
                    cancel_refused=True,
                )
                return
            self._retry_state_save(guard_deregistered=True, cancel_refused=False)
        elif present is not False:
            self.lane_error = self.lane_error or "thrashguard-presence-unknown"
            self._retry_state_save(state="stopping", reason=self.lane_error, cancel_refused=True)
            return
        try:
            self.process_signals.terminate_group(process.pid)
        except ProcessLookupError:
            self._retry_state_save(stop_signal="process-already-exited")
        except OSError as exc:
            self._retry_state_save(state="stopping", reason=f"TERM failed: {exc}", stop_signal="failed")
        else:
            self._retry_state_save(stop_signal="sent", cancel_refused=False)

    def _follow_lane_events(self, *, final: bool) -> None:
        try:
            events = self._read_events(final=final)
        except AdmissionError as exc:
            if exc.exit_code != ExitCode.QUEUED_WITHDRAWN:
                self.lane_error = self.lane_error or exc.reason
                if self.runner_process is not None:
                    self._request_worker_stop(self.runner_process)
                events = list(self.event_records)
            else:
                return
        for index in range(self.event_cursor, len(events)):
            event = events[index]
            if event.get("event") == "lane-started":
                container_id = event.get("container_id")
                lane = event.get("lane")
                if not isinstance(lane, str):
                    self.lane_error = self.lane_error or "lane-started event has no lane"
                    continue
                try:
                    binding = self._inspect_container_binding(event, expected_sha=str(self.request.get("sha", "")),
                                                              require_stopped=False)
                    if isinstance(container_id, str) and binding is None:
                        raise AdmissionError(
                            ExitCode.IDENTITY_OR_TRANSPORT,
                            f"lane-started container {container_id} was gone at observation",
                        )
                    observation: Mapping[str, object] = {
                        "index": index, "event": dict(event),
                        "binding": dict(binding) if binding is not None else {
                            "id": None, "lane": lane, "mode": "host", "status": "no-container",
                        },
                    }
                except AdmissionError as exc:
                    if exc.exit_code == ExitCode.QUEUED_WITHDRAWN:
                        break
                    observation = {"index": index, "event": dict(event), "error": exc.reason}
                    self.lane_error = self.lane_error or exc.reason
                observation_size = len(json.dumps(
                    observation, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                ).encode("utf-8")) + 1
                if self.event_observation_bytes + observation_size > MAX_EVENT_OBSERVATIONS_BYTES:
                    self.lane_error = self.lane_error or "MAX_EVENT_OBSERVATIONS_BYTES exceeded"
                    self.event_cursor = index + 1
                    if self.runner_process is not None:
                        self._request_worker_stop(self.runner_process)
                    break
                self.event_observations.append(observation)
                self.event_observation_bytes += observation_size
                try:
                    self._retry_state_save(lane_event_observations=list(self.event_observations))
                except AdmissionError as exc:
                    if self.permanent_defect is not None:
                        self.lane_error = self.lane_error or self.permanent_defect
                        if self.runner_process is not None:
                            self._request_worker_stop(self.runner_process)
                        self.event_cursor = index + 1
                        break
                    raise exc
            self.event_cursor = index + 1

    @staticmethod
    def _hash_fd(fd: int) -> tuple[str, int]:
        hasher = hashlib.sha256()
        size = 0
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
            size += len(chunk)
        os.lseek(fd, 0, os.SEEK_SET)
        return hasher.hexdigest(), size

    def _open_job_output(self, name: str) -> tuple[Path, int]:
        if "/" in name or name in {"", ".", ".."}:
            raise AdmissionError(ExitCode.RUNNER_FAILURE, "invalid executor output name")
        path = self.job_dir / name
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        except OSError as exc:
            raise AdmissionError(ExitCode.STORE_COLLECTION_FAILED, f"cannot create {name}: {exc}") from exc
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise AdmissionError(ExitCode.STORE_COLLECTION_FAILED, f"{name} is not a regular file")
        return path, fd

    def _write_archive_from_runner(
        self, helper_command: str, destination_name: str, arguments: Sequence[str], *, root_fd: int,
        kind: str, store_path: str | Sequence[str] | None = None,
    ) -> tuple[str, int, Mapping[str, object]]:
        assert self.runner is not None
        run_to_fd = getattr(self.runner, "run_to_fd", None)
        if not callable(run_to_fd):
            raise AdmissionError(ExitCode.RUNNER_FAILURE, "runner cannot stream a collected archive to the host")
        temporary_name = f".{destination_name}.part"
        temporary_path, output_fd = self._open_job_output(temporary_name)
        try:
            answer = run_to_fd(
                self._helper_argv(helper_command, *arguments, "--output-fd", str(output_fd)),
                cwd_fd=root_fd, env=self._environment(), output_fd=output_fd,
            )
            if answer.returncode != 0:
                detail = answer.stderr.decode("utf-8", "replace").strip()
                marker = next((value for value in _PERMANENT_LIMIT_MARKERS if value in detail), None)
                if marker is not None:
                    self.permanent_defect = f"{marker} exceeded"
                    self.lane_error = self.lane_error or self.permanent_defect
                    raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, self.permanent_defect)
                raise AdmissionError(
                    ExitCode.STORE_COLLECTION_FAILED,
                    f"runner could not collect {kind} archive{': ' + detail if detail else ''}",
                )
            try:
                document = json.loads(answer.stdout.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AdmissionError(ExitCode.STORE_COLLECTION_FAILED, f"runner returned invalid {kind} collection details") from exc
            if not isinstance(document, dict):
                raise AdmissionError(ExitCode.STORE_COLLECTION_FAILED, f"runner returned invalid {kind} collection details")
            if kind == "store" and document.get("status") != "collected":
                return "", 0, document
            try:
                size = _verify_archive_fd(output_fd, kind=kind, store_path=store_path)
            except AdmissionError as exc:
                marker = next((value for value in _PERMANENT_LIMIT_MARKERS if value in exc.reason), None)
                if marker is None:
                    raise
                self.permanent_defect = f"{marker} exceeded"
                self.lane_error = self.lane_error or self.permanent_defect
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, self.permanent_defect) from exc
            digest, hashed_size = self._hash_fd(output_fd)
            if size != hashed_size:
                raise AdmissionError(ExitCode.STORE_COLLECTION_FAILED, f"{kind} archive size changed during verification")
            os.fsync(output_fd)
            return digest, size, document
        except BaseException:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            raise
        finally:
            os.close(output_fd)

    def _archive_saved(
        self, name: str, *, kind: str, store_path: str | Sequence[str] | None = None,
    ) -> tuple[str, int]:
        path = self.job_dir / name
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError as exc:
            raise AdmissionError(ExitCode.STORE_COLLECTION_FAILED, f"cannot reopen {name}: {exc}") from exc
        try:
            size = _verify_archive_fd(fd, kind=kind, store_path=store_path)
            digest, hashed_size = self._hash_fd(fd)
            if size != hashed_size:
                raise AdmissionError(ExitCode.STORE_COLLECTION_FAILED, f"{name} changed during verification")
            return digest, size
        finally:
            os.close(fd)

    def _write_checksum(self, digest: str, name: str) -> None:
        temporary = self.job_dir / f".{name}.sha256.part"
        destination = self.job_dir / f"{name}.sha256"
        fd = -1
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            view = memoryview(f"{digest}  {name}\n".encode("ascii"))
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("checksum write made no progress")
                view = view[written:]
            os.fsync(fd)
            os.close(fd)
            fd = -1
            os.replace(temporary, destination)
            _fsync_dir(self.job_dir)
        except OSError as exc:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise AdmissionError(ExitCode.STORE_COLLECTION_FAILED, f"cannot persist {name} checksum: {exc}") from exc
        finally:
            _close(fd)

    def _saved_collection(self, store_rel: str | list[str] | None) -> Mapping[str, object] | None:
        state = self._state()
        collection = state.get("collection")
        if not isinstance(collection, Mapping) or collection.get("store_rel") != store_rel:
            return None
        try:
            artifact_digest, artifact_size = self._archive_saved("artifacts.tgz", kind="artifacts")
        except AdmissionError as exc:
            marker = next((value for value in _PERMANENT_LIMIT_MARKERS if value in exc.reason), None)
            if marker is not None:
                self.permanent_defect = f"{marker} exceeded"
                self.lane_error = self.lane_error or self.permanent_defect
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, self.permanent_defect) from exc
            return None
        if collection.get("artifacts_sha256") != artifact_digest or collection.get("artifacts_size") != artifact_size:
            return None
        store = collection.get("store")
        if isinstance(store, Mapping) and store.get("status") == "collected":
            if store_rel is None:
                return None
            try:
                store_digest, store_size = self._archive_saved(
                    "assay-store.tgz", kind="store", store_path=store_rel,
                )
                checksum = _read_regular(self.job_dir / "assay-store.tgz.sha256", limit=256)
            except AdmissionError as exc:
                marker = next((value for value in _PERMANENT_LIMIT_MARKERS if value in exc.reason), None)
                if marker is not None:
                    self.permanent_defect = f"{marker} exceeded"
                    self.lane_error = self.lane_error or self.permanent_defect
                    raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, self.permanent_defect) from exc
                return None
            if (
                store.get("sha256") != store_digest or store.get("size") != store_size
                or checksum != f"{store_digest}  assay-store.tgz\n".encode("ascii")
            ):
                return None
        return collection

    def _collect(self) -> Mapping[str, object]:
        """Collect and verify artifacts and the optional carry while checkout is live."""

        if self.checkout_fd < 0:
            raise AdmissionError(ExitCode.STORE_COLLECTION_FAILED, "cannot collect without a verified checkout")
        if self.project_fd < 0:
            project = self.request.get("project")
            if not isinstance(project, str):
                raise AdmissionError(ExitCode.REFUSAL, "request project is invalid during collection")
            self.project_fd = os.dup(self.checkout_fd) if project == "." else open_beneath(self.checkout_fd, project, directory=True)
        stores = self.request.get("stores")
        store_rel = stores.get("collect") if isinstance(stores, Mapping) else None
        if store_rel is None:
            store_paths: list[str] = []
        elif isinstance(store_rel, str):
            store_paths = [store_rel]
        elif isinstance(store_rel, list) and all(isinstance(item, str) for item in store_rel):
            store_paths = list(store_rel)
        else:
            raise AdmissionError(ExitCode.REFUSAL, "stores.collect is not a path or a list of paths")
        if len(store_paths) > MAX_STORE_PATHS:
            self.permanent_defect = "MAX_STORE_PATHS exceeded"
            self.lane_error = self.lane_error or self.permanent_defect
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, self.permanent_defect)
        if (store_rel is not None and not store_paths) or any(
            _STORE_PATH_RE.fullmatch(path) is None
            or any(part in {"", ".", "..", ".git"} for part in path.split("/"))
            for path in store_paths
        ) or len(store_paths) != len(set(store_paths)):
            raise AdmissionError(ExitCode.REFUSAL, "stores.collect contains an unsafe or duplicate relative path")
        saved = self._saved_collection(store_rel)
        if saved is not None:
            return saved

        artifact_digest, artifact_size, artifact_details = self._write_archive_from_runner(
            "archive-artifacts", "artifacts.tgz", (), root_fd=self.project_fd, kind="artifacts",
        )
        artifact_part = self.job_dir / ".artifacts.tgz.part"
        artifact_path = self.job_dir / "artifacts.tgz"
        os.replace(artifact_part, artifact_path)
        _fsync_dir(self.job_dir)

        store_details: dict[str, object] = {"status": "none"}
        if store_paths:
            store_part = self.job_dir / ".assay-store.tgz.part"
            store_path = self.job_dir / "assay-store.tgz"
            checksum_path = self.job_dir / "assay-store.tgz.sha256"
            for stale in (store_part, store_path, checksum_path, self.job_dir / ".assay-store.tgz.sha256.part"):
                try:
                    stale.unlink()
                except FileNotFoundError:
                    pass
            try:
                store_digest, store_size, details = self._write_archive_from_runner(
                    "archive-store", "assay-store.tgz",
                    tuple(argument for path in store_paths for argument in ("--relative", path)),
                    root_fd=self.checkout_fd, kind="store", store_path=store_paths,
                )
                logs = details.get("logs")
                if isinstance(logs, list):
                    for log_line in logs:
                        if isinstance(log_line, str):
                            self._log(log_line)
                elif isinstance(details.get("log"), str):
                    self._log(str(details["log"]))
                if details.get("status") == "collected":
                    os.replace(store_part, store_path)
                    _fsync_dir(self.job_dir)
                    self._write_checksum(store_digest, "assay-store.tgz")
                    store_details = {
                        "status": "collected", "sha256": store_digest, "size": store_size,
                        "stores": details.get("stores"),
                    }
                else:
                    try:
                        store_part.unlink()
                    except FileNotFoundError:
                        pass
                    store_details = {
                        "status": str(details.get("status", "failed")),
                        "reason": details.get("reason"),
                        "stores": details.get("stores"),
                    }
            except AdmissionError:
                try:
                    store_part.unlink()
                except FileNotFoundError:
                    pass
                raise
        collection: dict[str, object] = {
            "artifacts_sha256": artifact_digest,
            "artifacts_size": artifact_size,
            "artifact_files": artifact_details.get("files"),
            "store_rel": store_rel,
            "store": store_details,
            "collected_at": self.clock(),
        }
        self._save_state(state="collecting", collection=collection)
        return collection

    def _read_events(self, *, final: bool = True) -> list[Mapping[str, object]]:
        if not self.event_rel or not self.repo_root.is_absolute():
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "host-owned Run-Gate event file was not established")
        parent_fd = _open_absolute_directory(self.repo_root.parent)
        event_fd = -1

        def no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
            document: dict[str, object] = {}
            for key, value in pairs:
                if key in document:
                    raise ValueError(f"duplicate Run-Gate event field {key!r}")
                document[key] = value
            return document

        def parse_line(line: bytes) -> Mapping[str, object]:
            line_number = len(self.event_records) + 1
            if len(line) + 1 > MAX_EVENT_LINE_BYTES:
                raise AdmissionError(
                    ExitCode.IDENTITY_OR_TRANSPORT,
                    f"MAX_EVENT_LINE_BYTES exceeded at Run-Gate event line {line_number}",
                )
            try:
                document = json.loads(line.decode("utf-8"), object_pairs_hook=no_duplicate_keys)
            except (UnicodeDecodeError, ValueError) as exc:
                raise AdmissionError(
                    ExitCode.IDENTITY_OR_TRANSPORT, f"Run-Gate event line {line_number} is unreadable",
                ) from exc
            if (
                not isinstance(document, dict)
                or document.get("api") != "fleet-host-agent/1"
                or document.get("job_id") != self.job_id
                or not isinstance(document.get("event"), str)
                or document.get("event") not in {"lane-started", "lane-verdict"}
                or not isinstance(document.get("lane"), str) or not document.get("lane")
            ):
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"Run-Gate event line {line_number} has invalid identity")
            if document["event"] == "lane-started":
                required = frozenset({"api", "event", "job_id", "lane", "container_id", "image_id", "adopted"})
                keys = frozenset(document)
                if keys not in {required, required | {"reason"}}:
                    raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"Run-Gate event line {line_number} has an unexpected lane-started shape")
                if (
                    (document["container_id"] is not None and not isinstance(document["container_id"], str))
                    or (document["image_id"] is not None and not isinstance(document["image_id"], str))
                    or type(document["adopted"]) is not bool
                    or ("reason" in document and not isinstance(document["reason"], str))
                ):
                    raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"Run-Gate event line {line_number} has invalid lane-started fields")
            else:
                required = frozenset({"api", "event", "job_id", "lane", "verdict", "exit_code", "lane_exit_code"})
                if frozenset(document) != required:
                    raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"Run-Gate event line {line_number} has an unexpected lane-verdict shape")
                if (
                    not isinstance(document["verdict"], str)
                    or document["verdict"] not in _RUN_GATE_VERDICT_CODES
                    or type(document["exit_code"]) is not int
                    or (document["lane_exit_code"] is not None and type(document["lane_exit_code"]) is not int)
                ):
                    raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"Run-Gate event line {line_number} has invalid lane-verdict fields")
            if len(self.event_records) >= MAX_EVENT_COUNT:
                raise AdmissionError(
                    ExitCode.IDENTITY_OR_TRANSPORT,
                    f"MAX_EVENT_COUNT exceeded ({MAX_EVENT_COUNT})",
                )
            return document

        try:
            event_fd = open_beneath(parent_fd, self.event_rel)
            info = os.fstat(event_fd)
            assert self.runner_identity is not None
            if (
                not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != self.runner_identity.uid or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "Run-Gate event file has unsafe ownership or links")
            if info.st_size > MAX_EVENT_FILE_BYTES:
                raise AdmissionError(
                    ExitCode.IDENTITY_OR_TRANSPORT,
                    f"MAX_EVENT_FILE_BYTES exceeded ({MAX_EVENT_FILE_BYTES})",
                )
            identity = (info.st_dev, info.st_ino)
            if self.event_file_identity is not None and identity != self.event_file_identity:
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "Run-Gate event file identity changed while following")
            self.event_file_identity = identity
            if info.st_size < self.event_offset:
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "Run-Gate event file shrank while following")
            os.lseek(event_fd, self.event_offset, os.SEEK_SET)
            remaining = info.st_size - self.event_offset
            while remaining:
                chunk = os.read(event_fd, min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                self.event_offset += len(chunk)
                self.event_bytes_read += len(chunk)
                self.event_pending.extend(chunk)
                while True:
                    newline = self.event_pending.find(b"\n")
                    if newline < 0:
                        break
                    line = bytes(self.event_pending[:newline])
                    del self.event_pending[:newline + 1]
                    self.event_records.append(parse_line(line))
                if len(self.event_pending) >= MAX_EVENT_LINE_BYTES:
                    raise AdmissionError(
                        ExitCode.IDENTITY_OR_TRANSPORT,
                        f"MAX_EVENT_LINE_BYTES exceeded before newline ({MAX_EVENT_LINE_BYTES})",
                    )
            if final and self.event_pending:
                raise AdmissionError(
                    ExitCode.IDENTITY_OR_TRANSPORT,
                    f"Run-Gate event line {len(self.event_records) + 1} is incomplete",
                )
            return list(self.event_records)
        except FileNotFoundError as exc:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "Run-Gate event file is missing") from exc
        except OSError as exc:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"Run-Gate event file is unreadable: {exc}") from exc
        finally:
            _close(event_fd, parent_fd)

    def _wait_for_lane_containers(self) -> list[Mapping[str, object]]:
        self._follow_lane_events(final=True)
        events = self._read_events()
        started = [event for event in events if event.get("event") == "lane-started"]
        inspect = getattr(self.docker, "inspect", None)
        if started and not callable(inspect):
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "Docker inspect provider is unavailable for lane containers")
        container_ids: list[tuple[str, str]] = []
        for event in started:
            lane = event.get("lane")
            if not isinstance(lane, str):
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "lane-started event has no lane")
            mode, _persistent_name = self._container_mode(lane)
            container_id = event.get("container_id")
            if container_id is None:
                if mode in {"ephemeral", "exec"}:
                    raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"container lane {lane!r} has no full container id")
                continue
            if not isinstance(container_id, str) or _CONTAINER_ID_RE.fullmatch(container_id) is None:
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "Run-Gate reported a non-full container id")
            if mode == "host":
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"host-mode lane {lane!r} reported a container")
            container_ids.append((container_id, mode))
        while container_ids:
            still_running: list[tuple[str, str]] = []
            for container_id, mode in sorted(set(container_ids)):
                answer = inspect(container_id)
                if answer is None:
                    continue
                if not isinstance(answer, Mapping) or answer.get("Id") != container_id:
                    raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"Docker inspect did not confirm full id {container_id}")
                state = answer.get("State")
                if not isinstance(state, Mapping) or type(state.get("Running")) is not bool:
                    raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker state for {container_id} is unreadable")
                if state.get("Running") is True:
                    if mode != "exec":
                        still_running.append((container_id, mode))
            if still_running:
                self._save_state(
                    state="stopping", live_containers=[container_id for container_id, _mode in still_running],
                    retry_required=True,
                )
                self.waiter(1)
                container_ids = still_running
            else:
                container_ids = []
        self._save_state(live_containers=[], retry_required=False)
        return events

    def _ensure_scope_is_only_worker(self) -> None:
        state = self._state()
        unit = state.get("scope")
        if not isinstance(unit, str) or not unit.endswith(".scope"):
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "executor scope identity is unavailable")
        try:
            pids = tuple(sorted(set(self.scope_process_reader(unit))))
        except Exception as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"cannot inspect executor scope processes: {exc}") from exc
        if pids != (os.getpid(),):
            raise AdmissionError(
                ExitCode.QUEUED_WITHDRAWN,
                f"executor scope {unit} contains processes {pids!r}, expected only worker {os.getpid()}",
            )

    def _ensure_job_containers_stopped(self, *, remove: bool) -> None:
        query = getattr(self.docker, "job_containers", None)
        inspect = getattr(self.docker, "inspect", None)
        remove_exact = getattr(self.docker, "remove_stopped", None)
        if not callable(query) or not callable(inspect) or (remove and not callable(remove_exact)):
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "Docker job-container teardown provider is unavailable")
        while True:
            try:
                container_ids = query(self.job_id)
            except AdmissionError:
                raise
            except Exception as exc:
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker job-container query failed: {exc}") from exc
            if not isinstance(container_ids, Sequence) or isinstance(container_ids, (str, bytes)):
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "Docker job-container query returned an invalid result")
            running = False
            for container_id in container_ids:
                if not isinstance(container_id, str) or _CONTAINER_ID_RE.fullmatch(container_id) is None:
                    raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "Docker job-container query returned a non-full id")
                try:
                    answer = inspect(container_id)
                except AdmissionError:
                    raise
                except Exception as exc:
                    raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker inspect {container_id} failed: {exc}") from exc
                if answer is None:
                    continue
                if not isinstance(answer, Mapping) or answer.get("Id") != container_id:
                    raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker inspect did not confirm exact id {container_id}")
                config = answer.get("Config")
                labels = config.get("Labels") if isinstance(config, Mapping) else None
                if not isinstance(labels, Mapping) or labels.get("run-gate.job") != self.job_id:
                    raise AdmissionError(
                        ExitCode.IDENTITY_OR_TRANSPORT,
                        f"Docker container {container_id} does not carry run-gate.job={self.job_id}",
                    )
                state = answer.get("State")
                if not isinstance(state, Mapping) or type(state.get("Running")) is not bool:
                    raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker state for {container_id} is unreadable")
                if state.get("Running") is True:
                    running = True
                    continue
                if remove:
                    try:
                        remove_exact(container_id)
                    except AdmissionError:
                        raise
                    except Exception as exc:
                        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker remove {container_id} failed: {exc}") from exc
            if running:
                self._retry_state_save(
                    state="stopping", live_containers=list(container_ids), retry_required=True,
                )
                self.waiter(1)
                continue
            if not remove or not container_ids:
                self._retry_state_save(live_containers=[], retry_required=False)
                return
            # Confirm that exact-label enumeration is empty after removals.
            self.waiter(1)

    def _container_mode(self, event_lane: str) -> tuple[str, str | None]:
        if event_lane == self.request.get("lane"):
            state = self._state()
            captured_mode = state.get("lane_mode")
            captured_name = state.get("runner_name")
            if captured_mode in {"host", "exec", "ephemeral"}:
                return str(captured_mode), captured_name if isinstance(captured_name, str) else None
        lane_name = event_lane
        shard = _SHARD_EVENT_RE.fullmatch(event_lane)
        if shard is not None:
            lane_name = shard.group(1)
        project = self.request.get("project")
        if not isinstance(project, str):
            raise AdmissionError(ExitCode.REFUSAL, "request project is invalid during verdict binding")
        lane, environment = _lane_environment(self.checkout_fd, project, lane_name)
        mode = environment.get("mode")
        if mode not in {"host", "exec", "ephemeral"}:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"lane {event_lane!r} has no resolved execution mode")
        if mode == "exec":
            environment_name = lane.get("environment")
            if not isinstance(environment_name, str):
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"lane {event_lane!r} has no environment name")
            return "exec", _exec_container_name(self.checkout_fd, environment_name, environment)
        return str(mode), None

    def _verify_container_observation(
        self, answer: Mapping[str, object], *, container_id: str, event_lane: str,
        mode: str, persistent_name: str | None, expected_sha: str, require_stopped: bool = True,
    ) -> Mapping[str, object]:
        if answer.get("Id") != container_id or _CONTAINER_ID_RE.fullmatch(container_id) is None:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"Docker did not report exact container id {container_id}")
        mounts = answer.get("Mounts")
        checkout_source = str(self.repo_root / self.checkout_rel)
        if not isinstance(mounts, list) or not any(
            isinstance(mount, Mapping)
            and mount.get("Type") == "bind"
            and mount.get("Source") == checkout_source
            and isinstance(mount.get("Destination"), str)
            and str(mount.get("Destination")).startswith("/")
            for mount in mounts
        ):
            raise AdmissionError(
                ExitCode.IDENTITY_OR_TRANSPORT,
                f"container {container_id} does not mount this job's checkout",
            )
        if mode == "exec":
            if not isinstance(persistent_name, str) or not persistent_name:
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "exec-mode lane has no persistent runner name")
            actual_name = answer.get("Name")
            if not isinstance(actual_name, str) or actual_name.removeprefix("/") != persistent_name:
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "exec-mode container does not match the persistent runner name")
            state = answer.get("State")
            if not isinstance(state, Mapping) or state.get("Running") is not True:
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "exec-mode persistent runner is not running")
        elif mode == "ephemeral":
            config = answer.get("Config")
            labels = config.get("Labels") if isinstance(config, Mapping) else None
            required = {
                "run-gate.repo", "run-gate.project", "run-gate.lane", "run-gate.commit",
                "run-gate.name-schema", "run-gate.started", "run-gate.job",
            }
            if not isinstance(labels, Mapping) or any(
                not isinstance(labels.get(key), str) or not labels.get(key) for key in required
            ):
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"container {container_id} has incomplete run-gate labels")
            if (
                labels.get("run-gate.lane") != event_lane
                or labels.get("run-gate.commit") != expected_sha
                or labels.get("run-gate.job") != self.job_id
                or labels.get("run-gate.repo") != self.request.get("repo")
                or labels.get("run-gate.project") != self.request.get("project")
            ):
                raise AdmissionError(
                    ExitCode.IDENTITY_OR_TRANSPORT,
                    f"container {container_id} run-gate labels do not bind repo, project, lane, commit, and job",
                )
            state = answer.get("State")
            if require_stopped and (not isinstance(state, Mapping) or state.get("Running") is not False):
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Run-Gate container {container_id} is not confirmed stopped")
        else:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"host-mode lane {event_lane!r} emitted a container id")
        binding: dict[str, object] = {
            "id": container_id,
            "lane": event_lane,
            "mode": mode,
            "name": persistent_name,
            "checkout_mount": checkout_source,
            "observed_name": answer.get("Name"),
        }
        if mode == "ephemeral":
            config = answer["Config"]
            labels = config["Labels"]
            binding["labels"] = {
                key: labels.get(key) for key in sorted(labels) if isinstance(key, str) and key.startswith("run-gate.")
            }
        return binding

    def _inspect_container_binding(
        self, event: Mapping[str, object], *, expected_sha: str, require_stopped: bool = True,
    ) -> Mapping[str, object] | None:
        container_id = event.get("container_id")
        if container_id is None:
            mode, _name = self._container_mode(str(event.get("lane", "")))
            if mode == "ephemeral" or mode == "exec":
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "container lane has no full lane-started id")
            return None
        if not isinstance(container_id, str) or _CONTAINER_ID_RE.fullmatch(container_id) is None:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "lane-started event has an invalid container id")
        lane = event.get("lane")
        if not isinstance(lane, str):
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "lane-started event has no lane")
        mode, persistent_name = self._container_mode(lane)
        inspect = getattr(self.docker, "inspect", None)
        if not callable(inspect):
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "Docker inspect provider is unavailable for verdict binding")
        try:
            answer = inspect(container_id)
        except AdmissionError:
            raise
        except Exception as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker inspect {container_id} failed: {exc}") from exc
        if answer is None:
            return None
        if not isinstance(answer, Mapping):
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker inspect {container_id} returned an invalid object")
        if mode == "exec":
            assert persistent_name is not None
            try:
                named_answer = inspect(persistent_name)
            except AdmissionError:
                raise
            except Exception as exc:
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker inspect runner {persistent_name!r} failed: {exc}") from exc
            if not isinstance(named_answer, Mapping) or named_answer.get("Id") != container_id:
                raise AdmissionError(
                    ExitCode.IDENTITY_OR_TRANSPORT,
                    f"persistent runner {persistent_name!r} does not match event container {container_id}",
                )
        return self._verify_container_observation(
            answer, container_id=container_id, event_lane=lane, mode=mode,
            persistent_name=persistent_name, expected_sha=expected_sha, require_stopped=require_stopped,
        )

    def _teardown_containers(self, containers: Sequence[Mapping[str, object]], *, expected_sha: str) -> None:
        inspect = getattr(self.docker, "inspect", None)
        remove = getattr(self.docker, "remove_stopped", None)
        for container in containers:
            if container.get("mode") == "exec":
                continue
            container_id = container.get("id")
            lane = container.get("lane")
            if not isinstance(container_id, str) or not isinstance(lane, str):
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "verified container record is incomplete")
            if not callable(inspect) or not callable(remove):
                raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "Docker teardown provider is unavailable")
            while True:
                try:
                    answer = inspect(container_id)
                except AdmissionError:
                    raise
                except Exception as exc:
                    raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker inspect {container_id} failed: {exc}") from exc
                if answer is None:
                    break
                if not isinstance(answer, Mapping):
                    raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker inspect {container_id} returned an invalid object")
                self._verify_container_observation(
                    answer, container_id=container_id, event_lane=lane,
                    mode=str(container.get("mode")),
                    persistent_name=container.get("name") if isinstance(container.get("name"), str) else None,
                    expected_sha=expected_sha, require_stopped=False,
                )
                state = answer.get("State")
                if not isinstance(state, Mapping) or type(state.get("Running")) is not bool:
                    raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker state for {container_id} is unreadable")
                if state.get("Running") is False:
                    break
                self._save_state(state="stopping", live_containers=[container_id], retry_required=True)
                self.waiter(1)
            if answer is not None:
                remove(container_id)
        self._save_state(live_containers=[], retry_required=False)

    def _host_checkout_line(self) -> tuple[str, str]:
        request_sha = self.request.get("sha")
        state = self._state()
        checkout = state.get("checkout")
        state_sha = checkout.get("sha") if isinstance(checkout, Mapping) else None
        if (
            not isinstance(request_sha, str) or _COMMIT_RE.fullmatch(request_sha) is None
            or not isinstance(state_sha, str) or _COMMIT_RE.fullmatch(state_sha) is None
            or state_sha != request_sha
        ):
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "request sha and host checkout state do not agree")
        raw = _read_regular(self.job_dir / "job.log", limit=1024 * 1024)
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "host checkout log is unreadable") from exc
        expected = str(self.repo_root / self.checkout_rel)
        pattern = re.compile(rf"^[^ ]+ checkout {re.escape(expected)} at ([0-9a-f]{{40}}|[0-9a-f]{{64}})$")
        matches = [(line, pattern.fullmatch(line)) for line in lines]
        matches = [(line, match) for line, match in matches if match is not None]
        if len(matches) != 1 or matches[0][1].group(1) != request_sha:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "host checkout line is missing, duplicated, or differs from request sha")
        return matches[0][0], request_sha

    def _last_lane_summary(self) -> tuple[str, str, int | None]:
        lane = self.request.get("lane")
        if not isinstance(lane, str):
            raise AdmissionError(ExitCode.REFUSAL, "request lane is invalid during verdict binding")
        path = self.job_dir / "lane.log"
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError as exc:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"lane log is unavailable: {exc}") from exc
        prefix = f"run-gate: lane '{lane}' verdict ".encode("utf-8")
        last: bytes | None = None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "lane log is not a regular file")
            with os.fdopen(os.dup(fd), "rb") as stream:
                while True:
                    line = stream.readline(65537)
                    if not line:
                        break
                    if len(line) > 65536:
                        if line.startswith(prefix):
                            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "anchored Run-Gate summary is too large")
                        while line and not line.endswith(b"\n"):
                            line = stream.readline(65537)
                        continue
                    if not line.endswith(b"\n"):
                        if line.startswith(prefix):
                            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "anchored Run-Gate summary is incomplete")
                        continue
                    candidate = line[:-1].removesuffix(b"\r")
                    if candidate.startswith(prefix):
                        last = candidate
        finally:
            os.close(fd)
        if last is None:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "exact-lane anchored Run-Gate summary is missing")
        try:
            text = last[len(prefix):].decode("ascii")
        except UnicodeDecodeError as exc:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "anchored Run-Gate summary is unreadable") from exc
        fields = [field.strip() for field in text.split(";")]
        verdict = fields[0] if fields else ""
        if verdict not in _RUN_GATE_VERDICT_CODES:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "anchored Run-Gate summary has an unknown verdict")
        code_fields = [field for field in fields[1:] if field.startswith("exit_code")]
        if len(code_fields) > 1:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "anchored Run-Gate summary repeats exit_code")
        lane_exit_code: int | None = None
        if code_fields:
            match = re.fullmatch(r"exit_code (-?[0-9]+|null)", code_fields[0])
            if match is None:
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "anchored Run-Gate summary has an invalid exit_code")
            if match.group(1) != "null":
                lane_exit_code = int(match.group(1))
        return last.decode("ascii"), verdict, lane_exit_code

    def _bind_verdict(
        self, events: Sequence[Mapping[str, object]], process_status: int,
        containers: Sequence[Mapping[str, object]], *, cancelled: bool = False,
    ) -> Mapping[str, object]:
        """Create the host result from checkout, Run-Gate, and inspected-container facts."""

        reasons: list[str] = []
        for container in containers:
            if container.get("status") == "missing-object":
                reasons.append(f"Run-Gate container {container.get('id')} disappeared before binding")
            elif container.get("status") == "inspection-error":
                reasons.append(str(container.get("reason") or "Run-Gate container inspection was not confirmed"))
        checkout_line: str | None = None
        checkout_sha: str | None = None
        try:
            checkout_line, checkout_sha = self._host_checkout_line()
        except AdmissionError as exc:
            reasons.append(exc.reason)
        lane = self.request.get("lane")
        if not isinstance(lane, str):
            raise AdmissionError(ExitCode.REFUSAL, "request lane is invalid")
        top_mode, _top_runner = self._container_mode(lane)
        started_events = [event for event in events if event.get("event") == "lane-started"]
        if top_mode in {"ephemeral", "exec"} and not any(event.get("lane") == lane for event in started_events):
            reasons.append(f"container lane {lane!r} has no lane-started event")
        verdict_events = [event for event in events if event.get("event") == "lane-verdict"]
        verdict_event = verdict_events[0] if len(verdict_events) == 1 else None
        if len(verdict_events) != 1:
            reasons.append(f"expected exactly one lane-verdict event, got {len(verdict_events)}")
        if verdict_event is not None and verdict_event.get("lane") != lane:
            reasons.append("lane-verdict event names a different lane")
        summary_line: str | None = None
        summary_verdict: str | None = None
        summary_exit: int | None = None
        try:
            summary_line, summary_verdict, summary_exit = self._last_lane_summary()
        except AdmissionError as exc:
            reasons.append(exc.reason)
        if verdict_event is not None:
            event_verdict = verdict_event.get("verdict")
            event_status = verdict_event.get("exit_code")
            event_lane_status = verdict_event.get("lane_exit_code")
            if not isinstance(event_verdict, str) or event_verdict not in _RUN_GATE_VERDICT_CODES:
                reasons.append("lane-verdict event has an unknown verdict")
            if type(event_status) is not int:
                reasons.append("lane-verdict event has no integer process status")
            if "lane_exit_code" not in verdict_event or (
                event_lane_status is not None and type(event_lane_status) is not int
            ):
                reasons.append("lane-verdict event has no valid lane_exit_code")
            if type(event_status) is int and event_status != process_status:
                reasons.append("lane-verdict event differs from the Run-Gate process status")
            if summary_verdict is not None and event_verdict != summary_verdict:
                reasons.append("lane-verdict event differs from the anchored summary")
            if summary_line is not None and event_lane_status != summary_exit:
                reasons.append("lane-verdict event lane_exit_code differs from the anchored summary")
            if isinstance(event_verdict, str) and event_verdict in _RUN_GATE_VERDICT_CODES \
                    and process_status != _RUN_GATE_VERDICT_CODES[event_verdict]:
                reasons.append("Run-Gate process status is outside its closed verdict table")
        if cancelled:
            final_verdict = "CANCELLED"
            final_exit = int(ExitCode.CANCELLED)
        elif reasons or self.lane_error is not None:
            if self.lane_error is not None:
                reasons.append(self.lane_error)
            final_verdict = "ERROR"
            final_exit = int(ExitCode.IDENTITY_OR_TRANSPORT)
        else:
            assert verdict_event is not None
            final_verdict = str(verdict_event["verdict"])
            final_exit = process_status
        result: dict[str, object] = {
            "schema": "fleet-host-result/1",
            "api": "fleet-host-agent/1",
            "job_id": self.job_id,
            "repo": self.request.get("repo"),
            "lane": lane,
            "request_sha": self.request.get("sha"),
            "checkout_sha": checkout_sha,
            "checkout_line": checkout_line,
            "process_status": process_status,
            "anchored_summary": summary_line,
            "lane_verdict_event": dict(verdict_event) if verdict_event is not None else None,
            "containers": [dict(container) for container in containers],
            "collection": self._state().get("collection"),
            "verdict": final_verdict,
            "exit_code": final_exit,
            "binding": {"status": "refused" if reasons else "verified", "reasons": reasons},
        }
        _atomic_json(self.job_dir / "result-pending.json", result)
        result_fd = os.open(self.job_dir / "result-pending.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            result_sha, result_size = self._hash_fd(result_fd)
        finally:
            os.close(result_fd)
        current = self._state()
        collection = current.get("collection")
        collected_at = collection.get("collected_at") if isinstance(collection, Mapping) else None
        return {
            "verdict": final_verdict,
            "exit_code": final_exit,
            "result_sha256": result_sha,
            "result_size": result_size,
            "lane": lane,
            "api": "fleet-host-agent/1",
            "collected_at": collected_at if isinstance(collected_at, str) else self.clock(),
            "binding_reasons": reasons,
        }

    def _bind_container_events(
        self, events: Sequence[Mapping[str, object]], *, expected_sha: str,
    ) -> list[Mapping[str, object]]:
        state = self._state()
        raw_observations = state.get("lane_event_observations")
        observations = {
            value.get("index"): value for value in raw_observations
            if isinstance(raw_observations, list) and isinstance(value, Mapping)
        } if isinstance(raw_observations, list) else {}
        bindings: list[Mapping[str, object]] = []
        for index, event in enumerate(events):
            if event.get("event") != "lane-started":
                continue
            lane = event.get("lane")
            if not isinstance(lane, str):
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "lane-started event has no lane")
            mode, persistent_name = self._container_mode(lane)
            container_id = event.get("container_id")
            observed = observations.get(index)
            if isinstance(observed, Mapping) and isinstance(observed.get("error"), str):
                bindings.append({
                    "id": container_id, "lane": lane, "mode": mode, "name": persistent_name,
                    "status": "inspection-error", "reason": observed["error"],
                })
                continue
            inspected = observed.get("binding") if isinstance(observed, Mapping) else None
            if not isinstance(inspected, Mapping):
                self.lane_error = self.lane_error or f"lane-started event {index} was not durably observed"
                bindings.append({
                    "id": container_id, "lane": lane, "mode": mode, "name": persistent_name,
                    "status": "inspection-error", "reason": self.lane_error,
                })
                continue
            if inspected.get("status") == "no-container":
                status = "no-container" if container_id is None and mode == "host" else "missing-object"
                bindings.append({
                    "id": container_id, "lane": lane, "mode": mode,
                    "name": persistent_name, "status": status,
                })
            else:
                bindings.append({**dict(inspected), "status": "verified"})
        return bindings

    def _remove_checkout(self) -> None:
        if not self.checkout_rel:
            return
        _close(self.project_fd, self.checkout_fd)
        self.project_fd = self.checkout_fd = -1
        self._helper("remove-tree", "--relative", self.checkout_rel, cwd_fd=self.repo_root_fd)

    def _simple_result(
        self, verdict: str, exit_code: int, reason: str,
    ) -> Mapping[str, object]:
        state = self._state()
        checkout = state.get("checkout")
        checkout_sha = checkout.get("sha") if isinstance(checkout, Mapping) else None
        collection = state.get("collection")
        collected_at = collection.get("collected_at") if isinstance(collection, Mapping) else None
        if not isinstance(collected_at, str):
            collected_at = self.clock()
        document: dict[str, object] = {
            "schema": "fleet-host-result/1",
            "api": "fleet-host-agent/1",
            "job_id": self.job_id,
            "repo": self.request.get("repo"),
            "lane": self.request.get("lane"),
            "request_sha": self.request.get("sha"),
            "checkout_sha": checkout_sha,
            "checkout_line": None,
            "process_status": None,
            "anchored_summary": None,
            "lane_verdict_event": None,
            "containers": [],
            "collection": collection,
            "verdict": verdict,
            "exit_code": exit_code,
            "binding": {"status": "not-run", "reasons": [reason]},
        }
        _atomic_json(self.job_dir / "result-pending.json", document)
        fd = os.open(self.job_dir / "result-pending.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            result_sha, result_size = self._hash_fd(fd)
        finally:
            os.close(fd)
        lane = self.request.get("lane")
        if not isinstance(lane, str) or not lane:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "cannot persist a result without the request lane")
        return {
            "verdict": verdict,
            "exit_code": exit_code,
            "result_sha256": result_sha,
            "result_size": result_size,
            "lane": lane,
            "api": "fleet-host-agent/1",
            "collected_at": collected_at,
        }

    def _persist_pending_terminal(self, terminal: Mapping[str, object]) -> None:
        fields = (
            "verdict", "exit_code", "lane", "api", "collected_at", "result_sha256", "result_size",
        )
        record = {field: terminal[field] for field in fields}
        _atomic_json(self.job_dir / "terminal-pending.json", record)

    def _rewrite_pending_terminal_as_error(self, reason: str) -> Mapping[str, object]:
        """Bind a permanent cleanup limit into the result after stop proofs."""

        path = self.job_dir / "result-pending.json"
        try:
            value = json.loads(_read_regular(path, limit=MAX_STATE_RECORD_BYTES).decode("utf-8"))
        except (AdmissionError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"pending result cannot record {reason}: {exc}") from exc
        if not isinstance(value, dict):
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "pending result cannot record permanent cleanup failure")
        binding = value.get("binding")
        binding_record = dict(binding) if isinstance(binding, Mapping) else {}
        reasons = binding_record.get("reasons")
        reason_list = list(reasons) if isinstance(reasons, list) else []
        if reason not in reason_list:
            reason_list.append(reason)
        binding_record.update(status="refused", reasons=reason_list)
        value.update(verdict="ERROR", exit_code=int(ExitCode.IDENTITY_OR_TRANSPORT), binding=binding_record)
        _atomic_json(path, value)
        result_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            result_sha, result_size = self._hash_fd(result_fd)
        finally:
            os.close(result_fd)
        lane = value.get("lane")
        if not isinstance(lane, str) or not lane:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "pending result has no lane for permanent cleanup failure")
        collection = value.get("collection")
        collected_at = collection.get("collected_at") if isinstance(collection, Mapping) else None
        terminal = {
            "verdict": "ERROR", "exit_code": int(ExitCode.IDENTITY_OR_TRANSPORT),
            "lane": lane, "api": "fleet-host-agent/1",
            "collected_at": collected_at if isinstance(collected_at, str) else self.clock(),
            "result_sha256": result_sha, "result_size": result_size,
        }
        self._persist_pending_terminal(terminal)
        return terminal

    def _read_pending_terminal(self) -> Mapping[str, object]:
        raw = _read_regular(self.job_dir / "terminal-pending.json", limit=65536)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"pending terminal result is unreadable: {exc}") from exc
        required = {
            "verdict", "exit_code", "lane", "api", "collected_at", "result_sha256", "result_size",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "pending terminal result has an invalid closed shape")
        return value

    def _publish_terminal(self, terminal: Mapping[str, object], *, final_state: str, reason: str | None) -> None:
        if not self.terminal_completed:
            while True:
                try:
                    self._complete_admission(terminal)
                    self.terminal_completed = True
                    break
                except AdmissionError as exc:
                    try:
                        self._save_state(
                            state="stopping", reason=f"terminal record persistence deferred: {exc.reason}",
                            retry_required=True,
                        )
                    except (AdmissionError, OSError):
                        pass
                    self.waiter(1)
        pending = self.job_dir / "result-pending.json"
        result = self.job_dir / "result.json"
        if pending.exists():
            os.replace(pending, result)
            _fsync_dir(self.job_dir)
        elif not result.is_file():
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "durable terminal record has no result artifact")
        try:
            (self.job_dir / "terminal-pending.json").unlink()
        except FileNotFoundError:
            pass
        self._retry_state_save(
            state=final_state, verdict=terminal.get("verdict"), exit_code=terminal.get("exit_code"),
            result_sha256=terminal.get("result_sha256"), result_size=terminal.get("result_size"),
            lane=terminal.get("lane"), api=terminal.get("api"), collected_at=terminal.get("collected_at"),
            reason=reason, ended_utc=self.clock(), retry_required=False,
        )

    def _complete_admission(self, terminal_details: Mapping[str, object]) -> None:
        if not self.group or not self.request_digest or self.lock_fd < 0:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, "executor has no inherited live admission lock")
        try:
            os.fstat(self.lock_fd)
        except OSError as exc:
            raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"inherited admission lock is unavailable: {exc}") from exc
        book = AdmissionBook(self.state_dir)
        handle = AdmissionHandle(book, {
            "job_id": self.job_id,
            "group": self.group,
            "digest": self.request_digest,
        }, self.lock_fd)
        book.complete(handle, terminal_details=terminal_details)
        self.lock_fd = -1

    def _validate_run_gate_after_run(self) -> None:
        project = self.request.get("project")
        lane = self.request.get("lane")
        if not isinstance(project, str) or not isinstance(lane, str):
            self.lane_error = self.lane_error or "run-gate-request-identity-invalid"
            return
        try:
            _lane, environment = _lane_environment(self.checkout_fd, project, lane)
            state = self._state()
            if environment.get("mode") != state.get("lane_mode"):
                self.lane_error = self.lane_error or "run-gate-environment-changed-during-lane"
        except AdmissionError as exc:
            self.lane_error = self.lane_error or f"run-gate-config-invalid-after-lane: {exc.reason}"
        if self.project_fd >= 0:
            try:
                info = os.stat(".assay", dir_fd=self.project_fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            except OSError as exc:
                self.lane_error = self.lane_error or f"assay-directory-unreadable-after-lane: {exc}"
            else:
                if stat.S_ISLNK(info.st_mode):
                    self.lane_error = self.lane_error or "assay-directory-symlink-after-lane"

    def _finalize_lane(self, process_status: int) -> int:
        while True:
            try:
                pending_path = self.job_dir / "terminal-pending.json"
                if pending_path.is_file():
                    terminal = self._read_pending_terminal()
                else:
                    try:
                        self._follow_lane_events(final=True)
                        events = self._read_events()
                    except AdmissionError as exc:
                        if exc.exit_code == ExitCode.QUEUED_WITHDRAWN:
                            raise
                        self.lane_error = self.lane_error or exc.reason
                        events = []
                    if self.event_cursor < len(events) and self.lane_error is None:
                        self.waiter(1)
                        continue
                    state = self._state()
                    mode = state.get("lane_mode")
                    verdict_events = [event for event in events if event.get("event") == "lane-verdict"]
                    if mode == "exec" and state.get("cancel_requested") is True:
                        reason = "exec-cancel-cannot-prove-runner-process-stopped"
                        self._save_state(state="stopping", reason=reason, retry_required=True)
                        self.waiter(1)
                        continue
                    if state.get("cancel_requested") is True:
                        if state.get("stop_signal") == "failed":
                            reason = str(state.get("reason") or "TERM failed; cancellation finalization is refused")
                            self._save_state(state="stopping", reason=reason, retry_required=True)
                            self.waiter(1)
                            continue
                        guard = state.get("guard")
                        present = guard.get("present") if isinstance(guard, Mapping) else None
                        if present is True and state.get("guard_deregistered") is not True:
                            action = getattr(self.thrashguard_client, "deregister", None)
                            if not callable(action):
                                reason = "thrashguard-deregister-unavailable"
                                self._save_state(state="stopping", reason=reason, retry_required=True)
                                self.waiter(1)
                                continue
                            try:
                                action(self.job_id)
                                self._retry_state_save(guard_deregistered=True, cancel_refused=False)
                            except Exception as exc:
                                self._save_state(
                                    state="stopping", reason=f"thrashguard-deregister-failed: {exc}",
                                    retry_required=True,
                                )
                                self.waiter(1)
                                continue
                        elif present is not False:
                            reason = "thrashguard-presence-unknown"
                            self._save_state(state="stopping", reason=reason, retry_required=True)
                            self.waiter(1)
                            continue
                    if mode == "exec" and (
                        len(verdict_events) != 1
                        or verdict_events[0].get("lane") != self.request.get("lane")
                    ):
                        reason = "exec-end-without-run-gate-verdict-cannot-prove-runner-process-stopped"
                        self._save_state(state="stopping", reason=reason, retry_required=True)
                        self.waiter(1)
                        continue
                    self._validate_run_gate_after_run()
                    request_sha = self.request.get("sha")
                    if not isinstance(request_sha, str):
                        raise AdmissionError(ExitCode.REFUSAL, "request sha is invalid during cleanup")
                    self._ensure_scope_is_only_worker()
                    self._ensure_job_containers_stopped(remove=False)
                    containers = self._bind_container_events(events, expected_sha=request_sha)
                    self._retry_state_save(container_bindings=containers)
                    try:
                        self._collect()
                    except AdmissionError:
                        if self.permanent_defect is None:
                            raise
                        self.lane_error = self.lane_error or self.permanent_defect
                        self._retry_state_save(
                            state="collecting",
                            collection={
                                "status": "failed", "reason": self.permanent_defect,
                                "collected_at": self.clock(),
                            },
                        )
                    state = self._state()
                    cancelled = state.get("cancel_requested") is True
                    terminal = self._bind_verdict(events, process_status, containers, cancelled=cancelled)
                    self._persist_pending_terminal(terminal)
                pending_result = json.loads(_read_regular(self.job_dir / "result-pending.json", limit=1024 * 1024).decode("utf-8"))
                containers = pending_result.get("containers", []) if isinstance(pending_result, Mapping) else []
                expected_sha = str(self.request.get("sha", ""))
                verified = [
                    value for value in containers
                    if isinstance(value, Mapping) and value.get("status") == "verified"
                ] if isinstance(containers, list) else []
                self._teardown_containers(verified, expected_sha=expected_sha)
                self._ensure_job_containers_stopped(remove=True)
                self._ensure_scope_is_only_worker()
                try:
                    self._remove_checkout()
                except AdmissionError:
                    if self.permanent_defect is None:
                        raise
                    terminal = self._rewrite_pending_terminal_as_error(self.permanent_defect)
                    self._publish_terminal(
                        terminal, final_state="failed", reason=self.permanent_defect,
                    )
                    return int(terminal["exit_code"])
                self._ensure_scope_is_only_worker()
                self._ensure_job_containers_stopped(remove=False)
                reason: str | None = None
                reasons = pending_result.get("binding", {}).get("reasons") if isinstance(pending_result, Mapping) else None
                if isinstance(reasons, list) and reasons:
                    reason = "; ".join(str(item) for item in reasons)
                elif terminal.get("verdict") == "CANCELLED":
                    current = self._state()
                    reason = str(current.get("cancel_reason") or "cancelled")
                final_state = "cancelled" if terminal.get("verdict") == "CANCELLED" else (
                    "done" if terminal.get("verdict") == "PASS" else "failed"
                )
                self._publish_terminal(terminal, final_state=final_state, reason=reason)
                return int(terminal["exit_code"])
            except AdmissionError as exc:
                if exc.exit_code in {
                    ExitCode.IDENTITY_OR_TRANSPORT, ExitCode.REFUSAL,
                    ExitCode.PREREQUISITE_MISSING, ExitCode.CHECKOUT_FAILURE, ExitCode.MISSING_BASE,
                } and self.lane_error is None:
                    self.lane_error = exc.reason
                try:
                    self._save_state(
                        state="stopping", exit_code=ExitCode.QUEUED_WITHDRAWN,
                        reason=f"lane finalization deferred: {exc.reason}", retry_required=True,
                    )
                except (AdmissionError, OSError):
                    pass
                self.waiter(1)
            except (OSError, ValueError, UnicodeError) as exc:
                if self.lane_error is None:
                    self.lane_error = f"lane finalization failed: {exc}"
                try:
                    self._save_state(
                        state="stopping", exit_code=ExitCode.QUEUED_WITHDRAWN,
                        reason=f"lane finalization deferred: {exc}", retry_required=True,
                    )
                except (AdmissionError, OSError):
                    pass
                self.waiter(1)

    def _finish_unstarted(self, exit_code: int, reason: str) -> int:
        try:
            current = self._state()
        except AdmissionError:
            return int(exit_code)
        if (
            current.get("lane_process_status") is None
            and (current.get("lane_pid") is not None or current.get("lane_may_be_live") is True)
        ):
            self._save_state(
                state="stopping", exit_code=ExitCode.QUEUED_WITHDRAWN,
                reason="lane process start could not be confirmed stopped", retry_required=True,
            )
            return int(ExitCode.QUEUED_WITHDRAWN)
        if not self.group or not self.request_digest or self.lock_fd < 0:
            self._save_state(
                state="stopping", exit_code=ExitCode.QUEUED_WITHDRAWN,
                reason=f"terminal completion requires the inherited admission lock and request identity: {reason}",
                retry_required=True,
            )
            return int(ExitCode.QUEUED_WITHDRAWN)
        permanent = exit_code in {ExitCode.IDENTITY_OR_TRANSPORT, ExitCode.REFUSAL} or (
            exit_code == ExitCode.PREREQUISITE_MISSING and "Run-Gate" in reason
        )
        verdict = "CANCELLED" if exit_code == ExitCode.CANCELLED else "ERROR" if permanent else "NOT_RUN"
        final_state = "cancelled" if verdict == "CANCELLED" else "failed"
        while True:
            try:
                pending_path = self.job_dir / "terminal-pending.json"
                if pending_path.is_file():
                    terminal = self._read_pending_terminal()
                else:
                    self._ensure_scope_is_only_worker()
                    self._ensure_job_containers_stopped(remove=False)
                    if self.checkout_fd >= 0 and self.runner is not None:
                        self._collect()
                    terminal = self._simple_result(verdict, int(exit_code), reason)
                    self._persist_pending_terminal(terminal)
                self._ensure_job_containers_stopped(remove=True)
                if self.checkout_rel and self.runner is not None:
                    self._remove_checkout()
                self._ensure_scope_is_only_worker()
                self._ensure_job_containers_stopped(remove=False)
                self._publish_terminal(terminal, final_state=final_state, reason=reason)
                return int(terminal["exit_code"])
            except (AdmissionError, OSError, ValueError, UnicodeError) as exc:
                detail = exc.reason if isinstance(exc, AdmissionError) else str(exc)
                try:
                    self._save_state(
                        state="stopping", exit_code=ExitCode.QUEUED_WITHDRAWN,
                        reason=f"pre-lane finalization deferred: {detail}", retry_required=True,
                    )
                except (AdmissionError, OSError):
                    pass
                self.waiter(1)

    def run(self) -> int:
        """Run checkout and lane prerequisites, then return the Run-Gate status."""

        try:
            confirmed = self._await_scope_confirmation()
        except BaseException as interrupted:
            self._settle_interruption_before_run(interrupted)
            raise
        if not confirmed:
            return int(ExitCode.RUNNER_FAILURE)
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
            self._cancel_checkpoint()
            if self.request.get("kind") != "lane":
                raise AdmissionError(ExitCode.REFUSAL, "kind=dev is outside the C3b-1 lane executor")
            binary, mode, facts = self._prerequisites()
            self._save_state(
                lane_mode=mode,
                runner_name=facts.get("runner_name"),
                captured_lane=self.request.get("lane"),
                captured_at=self.clock(),
            )
            self._cancel_checkpoint()
            self._wait_for_psi()
            self._cancel_checkpoint()
            self._seed_stores()
            self._cancel_checkpoint()
            memory_lock_fd = self._memory_admission()
            self.memory_lock_fd = memory_lock_fd if memory_lock_fd is not None else -1
            status = self._run_lane(binary, mode, facts, memory_lock_fd)
            return self._finalize_lane_with_retry(status)
        except AdmissionError as exc:
            return self._handle_run_error(int(exc.exit_code), exc.reason)
        except Exception as exc:
            return self._handle_run_error(int(ExitCode.RUNNER_FAILURE), str(exc))
        except BaseException as exc:
            self._settle_worker_interruption(exc)
            self.interruption_settled = True
            raise
        finally:
            if self.memory_lock_fd >= 0:
                try:
                    fcntl.flock(self.memory_lock_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                _close(self.memory_lock_fd)
            _close(self.project_fd, self.checkout_fd, self.repo_root_fd)


def _read_text_file(path: str) -> str:
    with open(path, "r", encoding="ascii") as stream:
        return stream.read()


def _scope_processes(unit: str) -> tuple[int, ...]:
    """Read process membership for this worker's unified cgroup scope."""

    cgroup = ""
    with open("/proc/self/cgroup", "r", encoding="ascii") as stream:
        for line in stream:
            hierarchy, _controllers, path = line.rstrip("\n").split(":", 2)
            if hierarchy == "0":
                cgroup = path
                break
    parts = PurePosixPath(cgroup)
    if not cgroup.startswith("/") or ".." in parts.parts or parts.name != unit:
        raise OSError(f"worker cgroup {cgroup!r} does not match scope {unit!r}")
    with open(Path("/sys/fs/cgroup") / cgroup.lstrip("/") / "cgroup.procs", "r", encoding="ascii") as stream:
        values = tuple(int(line) for line in stream if line.strip())
    if any(pid <= 0 for pid in values):
        raise OSError("scope cgroup.procs contains an invalid pid")
    return values


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
            created = False
            try:
                os.mkdir(part, mode=0o755, dir_fd=current)
                created = True
            except FileExistsError:
                pass
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            if created:
                os.fchmod(next_fd, 0o755)
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


def _remove_tree_at(
    parent_fd: int, name: str, *, visited: list[int] | None = None, depth: int = 0,
) -> None:
    """Remove one entry beneath parent_fd without following any links."""

    count = [0] if visited is None else visited
    if count[0] >= MAX_TREE_ENTRIES:
        raise OSError(f"MAX_TREE_ENTRIES exceeded ({MAX_TREE_ENTRIES})")
    count[0] += 1
    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        os.unlink(name, dir_fd=parent_fd)
        return
    if depth >= MAX_TREE_DEPTH:
        raise OSError(f"MAX_TREE_DEPTH exceeded ({MAX_TREE_DEPTH})")
    child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        for child in _bounded_directory_entries(child_fd):
            _remove_tree_at(child_fd, child, visited=count, depth=depth + 1)
    finally:
        os.close(child_fd)
    os.rmdir(name, dir_fd=parent_fd)


def _remove_tree_beneath(root_fd: int, relative: str) -> None:
    parent_fd, name = _open_parent(root_fd, relative)
    try:
        try:
            _remove_tree_at(parent_fd, name, visited=[0])
        except FileNotFoundError:
            return
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


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
    try:
        fd = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root_fd)
    except OSError as exc:
        raise ValueError(f"STORE-SEED-FAILED {relative} unpack-failed") from exc
    size = 0
    hasher = hashlib.sha256()
    try:
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
                if written <= 0:
                    raise OSError("seed archive write made no progress")
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
                members: list[tarfile.TarInfo] = []
                while True:
                    member = archive.next()
                    if member is None:
                        break
                    if len(members) >= MAX_ARCHIVE_MEMBERS:
                        raise ValueError(f"MAX_ARCHIVE_MEMBERS exceeded ({MAX_ARCHIVE_MEMBERS})")
                    members.append(member)
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
                    if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                        raise ValueError(
                            f"STORE-SEED-FAILED {relative} MAX_ARCHIVE_UNCOMPRESSED_BYTES exceeded "
                            f"({MAX_ARCHIVE_UNCOMPRESSED_BYTES})",
                        )
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
                created_parent = _mkdir_beneath(root_fd, parent_rel)
                os.close(created_parent)
                parent_fd, leaf = _open_parent(root_fd, name)
                member_fd = -1
                try:
                    member_fd = os.open(
                        leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o644, dir_fd=parent_fd,
                    )
                    os.fchmod(member_fd, 0o644)
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
        if any(marker in str(exc) for marker in _PERMANENT_LIMIT_MARKERS):
            raise ValueError(f"STORE-SEED-FAILED {relative} {exc}") from exc
        raise ValueError(f"STORE-SEED-FAILED {relative} unpack-failed") from exc
    finally:
        _close(fd)
        try:
            os.unlink(temporary, dir_fd=root_fd)
        except FileNotFoundError:
            pass


def _helper_main(argv: Sequence[str]) -> int:
    parser = _helper_parser()
    args = parser.parse_args(argv)
    cwd_fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if args.command == "prepare":
            fd = _mkdir_beneath(cwd_fd, args.relative)
            os.close(fd)
        elif args.command == "prepare-event":
            _prepare_event(cwd_fd, args.relative)
        elif args.command == "remove-tree":
            _remove_tree_beneath(cwd_fd, args.relative)
        elif args.command == "archive-artifacts":
            document = _archive_artifacts(cwd_fd, args.output_fd)
            print(json.dumps(document, sort_keys=True, separators=(",", ":")))
        elif args.command == "archive-store":
            document = _archive_store(cwd_fd, args.output_fd, args.relative)
            print(json.dumps(document, sort_keys=True, separators=(",", ":")))
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


def _helper_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleetd-executor-worker-helper")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "prepare-event", "write-file", "remove-tree", "archive-artifacts", "archive-store"):
        command = commands.add_parser(name)
        if name == "archive-store":
            command.add_argument("--relative", action="append", required=True)
        elif name in {"prepare", "prepare-event", "write-file", "remove-tree"}:
            command.add_argument("--relative", required=True)
        command.add_argument("--output-fd", type=int, required=name in {"archive-artifacts", "archive-store"})
        if name == "write-file":
            command.add_argument("--sha256", required=True)
            command.add_argument("--bytes", type=int, required=True)
    seed = commands.add_parser("extract-seed")
    seed.add_argument("--relative", required=True)
    seed.add_argument("--sha256", required=True)
    seed.add_argument("--bytes", type=int, required=True)
    seed.add_argument("--max-bytes", type=int, required=True)
    return parser


def _worker_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="fleetd-executor-worker")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--lock-fd", type=int, required=True)
    parser.add_argument("--handshake-fd", type=int, required=True)
    args = parser.parse_args(argv)
    worker: HostJobWorker | None = None
    try:
        policy = load_agent_policy(args.policy)
        if policy.executor_enabled is not True:
            raise AdmissionError(ExitCode.REFUSAL, "executor-disabled-by-agent-policy")
        lock_info = os.fstat(args.lock_fd)
        if not stat.S_ISREG(lock_info.st_mode):
            raise AdmissionError(ExitCode.RUNNER_FAILURE, "inherited admission lock is not a regular file")
        fcntl.flock(args.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        worker = HostJobWorker(args.state_dir, policy, args.job_id, args.lock_fd)
        handshake = f"{os.getpid()}\n".encode("ascii")
        written = os.write(args.handshake_fd, handshake)
        if written != len(handshake):
            raise AdmissionError(ExitCode.RUNNER_FAILURE, "worker start handshake was incomplete")
        os.close(args.handshake_fd)
        return worker.run()
    except (AdmissionError, OSError, ValueError) as exc:
        print(f"fleetd-executor-worker: {exc}", file=sys.stderr)
        return int(exc.exit_code) if isinstance(exc, AdmissionError) else int(ExitCode.RUNNER_FAILURE)
    except BaseException as exc:
        if worker is not None and not worker.interruption_settled:
            worker._settle_interruption_before_run(exc)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    helper_parser = _helper_parser()
    helper_commands = helper_parser._subparsers._group_actions[0].choices
    if args and args[0] in helper_commands:
        return _helper_main(args)
    if args and args[0] == "worker":
        return _worker_main(args[1:])
    print(
        "fleetd-executor-worker: expected worker or one of: "
        + ", ".join(sorted(helper_commands)),
        file=sys.stderr,
    )
    return int(ExitCode.REFUSAL)


if __name__ == "__main__":
    raise SystemExit(main())
