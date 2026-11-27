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
_SHARD_EVENT_RE = re.compile(r"(.+)\.shard[1-9][0-9]*of[1-9][0-9]*\Z")
_PSI_LIMIT = 25.0
_PSI_WAIT_S = 900
_PSI_POLL_S = 30
_MEMORY_FLOOR_MIB = 1024
_MEMORY_WAIT_S = 900
_MEMORY_POLL_S = 10
_MEMORY_SETTLE_S = 20
_ARTIFACT_MAX_BYTES = 50 * 1024 * 1024
_STORE_MAX_BYTES = 512 * 1024 * 1024
_RUN_GATE_VERDICT_CODES = {
    "PASS": 0,
    "FAIL": 1,
    "ERROR": 2,
    "NOT_RUN": 3,
    "BUDGET_EXCEEDED": 4,
}


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

    def run_to_fd(
        self, argv: Sequence[str], *, cwd_fd: int, env: Mapping[str, str], output_fd: int,
    ) -> CommandResult:
        process = self._popen(
            list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=dict(env), close_fds=True, pass_fds=(cwd_fd, output_fd),
            preexec_fn=self._preexec(cwd_fd),
        )
        stdout, stderr = process.communicate()
        return CommandResult(process.returncode, stdout or b"", stderr or b"")

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

    def remove_stopped(self, container_id: str) -> None:
        if _CONTAINER_ID_RE.fullmatch(container_id) is None:
            raise AdmissionError(ExitCode.REFUSAL, "container removal requires a full Docker id")
        result = self.command("rm", container_id)
        if result.returncode == 0:
            return
        detail = result.stderr.decode("utf-8", "replace").strip()
        if detail == f"Error: No such container: {container_id}":
            return
        raise AdmissionError(ExitCode.QUEUED_WITHDRAWN, f"Docker could not remove stopped container {container_id}: {detail}")


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
    pending: list[tuple[str, int]] = [(relative, first_fd)]
    special_reason: str | None = None
    try:
        while pending:
            directory_rel, directory_fd = pending.pop()
            try:
                names = sorted(os.listdir(directory_fd))
                for name in names:
                    child_rel = f"{directory_rel}/{name}"
                    info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        child_fd = os.open(
                            name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd,
                        )
                        entries.append((child_rel, True, 0))
                        pending.append((child_rel, child_fd))
                    elif stat.S_ISREG(info.st_mode):
                        entries.append((child_rel, False, info.st_size))
                    elif reject_special:
                        special_reason = "symlink" if stat.S_ISLNK(info.st_mode) else "special-file"
                        break
            finally:
                os.close(directory_fd)
            if special_reason:
                break
        for _relative, directory_fd in pending:
            _close(directory_fd)
        pending.clear()
        return ([], special_reason) if special_reason else (entries, None)
    except BaseException:
        for _relative, directory_fd in pending:
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


def _archive_artifacts(root_fd: int, output_fd: int) -> Mapping[str, object]:
    entries: list[tuple[str, bool, int]] = []
    for root in (".assay", ".run-gate"):
        found, reason = _tree_entries(root_fd, root, reject_special=False)
        if reason not in {None, "absent"}:
            raise OSError(f"cannot inspect artifact tree {root}: {reason}")
        for relative, is_directory, size in found:
            if is_directory or (_artifact_filename(relative.rsplit("/", 1)[-1]) and size < _ARTIFACT_MAX_BYTES):
                entries.append((relative, is_directory, size))
    _write_archive(root_fd, output_fd, entries)
    return {"files": sum(not is_directory for _path, is_directory, _size in entries)}


def _archive_store(root_fd: int, output_fd: int, relative: str) -> Mapping[str, object]:
    if (
        _STORE_PATH_RE.fullmatch(relative) is None
        or any(part in {".", "..", ".git"} for part in relative.split("/"))
    ):
        return {"status": "failed", "reason": "invalid-path", "log": f"STORE-COLLECT-FAILED {relative} invalid-path"}
    try:
        entries, special = _tree_entries(root_fd, relative, reject_special=True)
    except NotADirectoryError:
        return {"status": "failed", "reason": "not-a-directory",
                "log": f"STORE-COLLECT-FAILED {relative} not-a-directory"}
    except OSError as exc:
        reason = "symlink" if exc.errno == errno.ELOOP else "unreadable"
        return {"status": "failed", "reason": reason,
                "log": f"STORE-COLLECT-FAILED {relative} {reason}"}
    if special == "absent":
        return {"status": "absent", "log": f"STORE-ABSENT {relative}"}
    if special:
        return {"status": "failed", "reason": special,
                "log": f"STORE-COLLECT-FAILED {relative} {special}"}
    total = sum(size for _path, is_directory, size in entries if not is_directory)
    if total > _STORE_MAX_BYTES:
        return {"status": "too-large", "bytes": total,
                "log": f"STORE-TOO-LARGE {relative} {total}"}
    try:
        _write_archive(root_fd, output_fd, entries)
    except (OSError, tarfile.TarError, ValueError):
        return {"status": "failed", "reason": "pack-failed",
                "log": f"STORE-COLLECT-FAILED {relative} pack-failed"}
    return {"status": "collected", "bytes": total, "log": f"STORE-COLLECTED {relative}"}


def _verify_archive_fd(fd: int, *, kind: str, store_path: str | None = None) -> int:
    """Verify a just-written archive's complete member set and return its size."""

    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise AdmissionError(ExitCode.STORE_COLLECTION_FAILED, "collected archive is not a regular file")
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(fd), "rb") as stream:
            with tarfile.open(fileobj=stream, mode="r:gz") as archive:
                members = archive.getmembers()
    except (OSError, EOFError, tarfile.TarError) as exc:
        raise AdmissionError(ExitCode.STORE_COLLECTION_FAILED, f"collected {kind} archive is unreadable: {exc}") from exc
    finally:
        os.lseek(fd, 0, os.SEEK_SET)
    seen: set[str] = set()
    total = 0
    for member in members:
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
        if kind == "store":
            if store_path is None or not (name == store_path or name.startswith(store_path + "/")):
                raise AdmissionError(ExitCode.STORE_COLLECTION_FAILED, "store archive contains an unexpected path")
            total += member.size if member.isfile() else 0
            if total > _STORE_MAX_BYTES:
                raise AdmissionError(ExitCode.STORE_COLLECTION_FAILED, "store archive exceeds its uncompressed limit")
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
        self.process_signals = process_signals or SystemProcessSignals()
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
        with _job_state_lock(self.job_dir):
            state = self._state()
            if updates.get("state") == "running" and state.get("state") == "stopping":
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
        state = self._save_state(lane_pid=process.pid, state="running", lane=lane, event_file=str(self.event_path), container={
            "id": None, "name": facts.get("container_name"), "image": facts.get("image"),
        })
        if state.get("cancel_requested") is True:
            try:
                self.process_signals.terminate_group(process.pid)
            except ProcessLookupError:
                self._save_state(stop_signal="process-already-exited")
            except OSError as exc:
                self._save_state(state="stopping", reason=f"TERM failed: {exc}", stop_signal="failed")
            else:
                self._save_state(stop_signal="sent")
        if memory_lock_fd is not None:
            self.waiter(_MEMORY_SETTLE_S)
            fcntl.flock(memory_lock_fd, fcntl.LOCK_UN)
            os.close(memory_lock_fd)
        status = process.wait()
        state = self._state()
        while state.get("cancel_requested") is True and state.get("stop_signal") not in {
            "sent", "process-already-exited",
        }:
            self._save_state(state="stopping", reason="TERM was not confirmed; waiting for a stop retry")
            self.waiter(1)
            state = self._state()
        self._save_state(state="collecting", lane_process_status=status, exit_code=status)
        self._log(f"lane exited rc={status}")
        return status

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
        kind: str, store_path: str | None = None,
    ) -> tuple[str, int, Mapping[str, object]]:
        assert self.runner is not None
        run_to_fd = getattr(self.runner, "run_to_fd", None)
        if not callable(run_to_fd):
            raise AdmissionError(ExitCode.RUNNER_FAILURE, "runner cannot stream a collected archive to the host")
        temporary_name = f".{destination_name}.part"
        temporary_path, output_fd = self._open_job_output(temporary_name)
        try:
            answer = run_to_fd(
                [sys.executable, "-m", "fleetd.executor_worker", helper_command, *arguments,
                 "--output-fd", str(output_fd)],
                cwd_fd=root_fd, env=self._environment(), output_fd=output_fd,
            )
            if answer.returncode != 0:
                detail = answer.stderr.decode("utf-8", "replace").strip()
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
            size = _verify_archive_fd(output_fd, kind=kind, store_path=store_path)
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

    def _archive_saved(self, name: str, *, kind: str, store_path: str | None = None) -> tuple[str, int]:
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

    def _saved_collection(self, store_rel: str | None) -> Mapping[str, object] | None:
        state = self._state()
        collection = state.get("collection")
        if not isinstance(collection, Mapping) or collection.get("store_rel") != store_rel:
            return None
        try:
            artifact_digest, artifact_size = self._archive_saved("artifacts.tgz", kind="artifacts")
        except AdmissionError:
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
            except AdmissionError:
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
        if store_rel is not None and (
            not isinstance(store_rel, str) or _STORE_PATH_RE.fullmatch(store_rel) is None
            or any(part in {"", ".", "..", ".git"} for part in store_rel.split("/"))
        ):
            raise AdmissionError(ExitCode.REFUSAL, "stores.collect is not a safe relative path")
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
        if store_rel is not None:
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
                    "archive-store", "assay-store.tgz", ("--relative", store_rel),
                    root_fd=self.project_fd, kind="store", store_path=store_rel,
                )
                log_line = details.get("log")
                if isinstance(log_line, str):
                    self._log(log_line)
                if details.get("status") == "collected":
                    os.replace(store_part, store_path)
                    _fsync_dir(self.job_dir)
                    self._write_checksum(store_digest, "assay-store.tgz")
                    store_details = {
                        "status": "collected", "sha256": store_digest, "size": store_size,
                    }
                else:
                    try:
                        store_part.unlink()
                    except FileNotFoundError:
                        pass
                    store_details = {
                        "status": str(details.get("status", "failed")),
                        "reason": details.get("reason"),
                    }
            except AdmissionError as exc:
                self._log(f"STORE-COLLECT-FAILED {store_rel} pack-failed {exc.reason[:160]}")
                try:
                    store_part.unlink()
                except FileNotFoundError:
                    pass
                store_details = {"status": "failed", "reason": "pack-failed"}
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

    def _read_events(self) -> list[Mapping[str, object]]:
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

        try:
            event_fd = open_beneath(parent_fd, self.event_rel)
            info = os.fstat(event_fd)
            assert self.runner_identity is not None
            if (
                not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != self.runner_identity.uid or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "Run-Gate event file has unsafe ownership or links")
            events: list[Mapping[str, object]] = []
            with os.fdopen(os.dup(event_fd), "rb") as stream:
                line_number = 0
                while True:
                    line = stream.readline(65537)
                    if not line:
                        break
                    line_number += 1
                    if len(line) > 65536 or not line.endswith(b"\n"):
                        raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"Run-Gate event line {line_number} is incomplete or too large")
                    try:
                        document = json.loads(line.decode("utf-8"), object_pairs_hook=no_duplicate_keys)
                    except (UnicodeDecodeError, ValueError) as exc:
                        raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"Run-Gate event line {line_number} is unreadable") from exc
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
                    events.append(document)
            return events
        except FileNotFoundError as exc:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "Run-Gate event file is missing") from exc
        except OSError as exc:
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"Run-Gate event file is unreadable: {exc}") from exc
        finally:
            _close(event_fd, parent_fd)

    def _wait_for_lane_containers(self) -> list[Mapping[str, object]]:
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

    def _container_mode(self, event_lane: str) -> tuple[str, str | None]:
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
            ):
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, f"container {container_id} labels do not bind this job")
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
            config = answer.get("Config")
            labels = config.get("Labels") if isinstance(config, Mapping) else None
            if isinstance(labels, Mapping):
                binding["labels"] = {
                    key: labels.get(key) for key in sorted(labels) if isinstance(key, str) and key.startswith("run-gate.")
                }
        return binding

    def _inspect_container_binding(
        self, event: Mapping[str, object], *, expected_sha: str,
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
            persistent_name=persistent_name, expected_sha=expected_sha,
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
        elif reasons:
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
            "verdict": final_verdict,
            "exit_code": final_exit,
            "binding": {"status": "refused" if reasons else "verified", "reasons": reasons},
        }
        _atomic_json(self.job_dir / "result.json", result)
        result_fd = os.open(self.job_dir / "result.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
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
        bindings: list[Mapping[str, object]] = []
        for event in events:
            if event.get("event") != "lane-started":
                continue
            lane = event.get("lane")
            if not isinstance(lane, str):
                raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "lane-started event has no lane")
            mode, persistent_name = self._container_mode(lane)
            container_id = event.get("container_id")
            inspected = self._inspect_container_binding(event, expected_sha=expected_sha)
            if inspected is None:
                status = "no-container" if container_id is None and mode == "host" else "missing-object"
                bindings.append({
                    "id": container_id, "lane": lane, "mode": mode,
                    "name": persistent_name, "status": status,
                })
            else:
                bindings.append({**inspected, "status": "verified"})
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
            "verdict": verdict,
            "exit_code": exit_code,
            "binding": {"status": "not-run", "reasons": [reason]},
        }
        _atomic_json(self.job_dir / "result.json", document)
        fd = os.open(self.job_dir / "result.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
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

    def _finish_unstarted(self, exit_code: int, reason: str) -> int:
        try:
            current = self._state()
        except AdmissionError:
            return int(exit_code)
        if current.get("lane_pid") is not None and current.get("lane_process_status") is None:
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
        try:
            if self.checkout_fd >= 0:
                self._collect()
            if self.checkout_rel and self.runner is not None:
                self._remove_checkout()
            verdict = "CANCELLED" if exit_code == ExitCode.CANCELLED else "NOT_RUN"
            terminal = self._simple_result(verdict, int(exit_code), reason)
            final_state = "cancelled" if verdict == "CANCELLED" else "failed"
            self._save_state(
                state=final_state, verdict=verdict, exit_code=exit_code, reason=reason,
                result_sha256=terminal["result_sha256"], result_size=terminal["result_size"],
                lane=terminal["lane"], api=terminal["api"], collected_at=terminal["collected_at"],
                ended_utc=self.clock(),
            )
            try:
                self._complete_admission(terminal)
            except AdmissionError as exc:
                self._save_state(
                    state="stopping", exit_code=ExitCode.QUEUED_WITHDRAWN,
                    reason=f"terminal record persistence deferred: {exc.reason}", retry_required=True,
                )
                return int(ExitCode.QUEUED_WITHDRAWN)
            return int(exit_code)
        except (AdmissionError, OSError, ValueError, UnicodeError) as exc:
            detail = exc.reason if isinstance(exc, AdmissionError) else str(exc)
            self._save_state(
                state="stopping", exit_code=ExitCode.QUEUED_WITHDRAWN,
                reason=f"pre-lane finalization deferred: {detail}", retry_required=True,
            )
            return int(ExitCode.QUEUED_WITHDRAWN)

    def run(self) -> int:
        """Run checkout and lane prerequisites, then return the Run-Gate status."""

        if not self._await_scope_confirmation():
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
            self._cancel_checkpoint()
            self._wait_for_psi()
            self._cancel_checkpoint()
            self._seed_stores()
            self._cancel_checkpoint()
            memory_lock_fd = self._memory_admission()
            status = self._run_lane(binary, mode, facts, memory_lock_fd)
            events = self._wait_for_lane_containers()
            request_sha = self.request.get("sha")
            if not isinstance(request_sha, str):
                raise AdmissionError(ExitCode.REFUSAL, "request sha is invalid during cleanup")
            containers = self._bind_container_events(events, expected_sha=request_sha)
            self._save_state(container_bindings=containers)
            self._collect()
            state = self._state()
            cancelled = state.get("cancel_requested") is True
            terminal = self._bind_verdict(events, status, containers, cancelled=cancelled)
            self._save_state(**terminal)
            self._teardown_containers(
                [container for container in containers if container.get("status") == "verified"],
                expected_sha=request_sha,
            )
            self._remove_checkout()
            state = self._state()
            if state.get("cancel_requested") is True and terminal.get("verdict") != "CANCELLED":
                terminal = self._bind_verdict(events, status, containers, cancelled=True)
            final_state = "cancelled" if terminal.get("verdict") == "CANCELLED" else (
                "done" if terminal.get("verdict") == "PASS" else "failed"
            )
            self._save_state(
                state=final_state, verdict=terminal.get("verdict"), exit_code=terminal.get("exit_code"),
                result_sha256=terminal.get("result_sha256"), result_size=terminal.get("result_size"),
                lane=terminal.get("lane"), api=terminal.get("api"), collected_at=terminal.get("collected_at"),
                reason=("; ".join(terminal.get("binding_reasons", []))
                        if isinstance(terminal.get("binding_reasons"), list) and terminal.get("binding_reasons")
                        else state.get("cancel_reason") if final_state == "cancelled" else None),
                ended_utc=self.clock(),
            )
            try:
                self._complete_admission({
                    field: terminal[field] for field in (
                        "verdict", "exit_code", "lane", "api", "collected_at", "result_sha256", "result_size",
                    )
                })
            except AdmissionError as exc:
                self._save_state(
                    state="stopping", exit_code=ExitCode.QUEUED_WITHDRAWN,
                    reason=f"terminal record persistence deferred: {exc.reason}", retry_required=True,
                )
                return int(ExitCode.QUEUED_WITHDRAWN)
            return int(terminal["exit_code"])
        except AdmissionError as exc:
            try:
                current = self._state()
            except AdmissionError:
                current = {}
            if current.get("lane_pid") is not None and current.get("lane_process_status") is None:
                self._log(f"executor cleanup deferred exit={exc.exit_code}: {exc.reason}")
                self._save_state(
                    state="stopping", exit_code=ExitCode.QUEUED_WITHDRAWN, reason=exc.reason,
                    retry_required=True,
                )
                return int(ExitCode.QUEUED_WITHDRAWN)
            if current.get("lane_process_status") is not None:
                self._log(f"executor cleanup deferred exit={exc.exit_code}: {exc.reason}")
                self._save_state(
                    state="stopping", exit_code=ExitCode.QUEUED_WITHDRAWN, reason=exc.reason,
                    retry_required=True,
                )
                return int(ExitCode.QUEUED_WITHDRAWN)
            self._log(f"executor stopped exit={exc.exit_code}: {exc.reason}")
            return self._finish_unstarted(int(exc.exit_code), exc.reason)
        except Exception as exc:
            self._log(f"executor stopped exit={ExitCode.RUNNER_FAILURE}: {exc}")
            try:
                current = self._state()
            except AdmissionError:
                current = {}
            if current.get("lane_pid") is not None or current.get("lane_process_status") is not None:
                self._save_state(
                    state="stopping", exit_code=ExitCode.QUEUED_WITHDRAWN,
                    reason=f"finalization deferred: {exc}", retry_required=True,
                )
                return int(ExitCode.QUEUED_WITHDRAWN)
            return self._finish_unstarted(int(ExitCode.RUNNER_FAILURE), str(exc))
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


def _remove_tree_beneath(root_fd: int, relative: str) -> None:
    parent_fd, name = _open_parent(root_fd, relative)
    try:
        try:
            _remove_tree_at(parent_fd, name)
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
    for name in ("prepare", "prepare-event", "write-file", "remove-tree", "archive-artifacts", "archive-store"):
        command = commands.add_parser(name)
        command.add_argument("--relative", required=name in {"write-file", "remove-tree", "archive-store"})
        command.add_argument("--output-fd", type=int, required=name in {"archive-artifacts", "archive-store"})
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
