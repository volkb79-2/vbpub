"""Bounded host-side providers for admission and local Docker leases."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import selectors
import signal
import stat
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .admission import InspectorAnswer, is_ambiguous_container_name
from .hostside import open_beneath

MAX_LEGACY_JOB_ENTRIES = 65536
MAX_DOCKER_OUTPUT_BYTES = 4 * 1024 * 1024
_DOCKER_ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C"}
_DOCKER_HOST = "unix:///var/run/docker.sock"
_ADMISSION_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_FULL_CONTAINER_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
_MISSING_OBJECT_RE = re.compile(
    r"(?:Error:\s*|Error response from daemon:\s*)?"
    r"No such (?:object|container):\s*([^\s]+)\Z",
    re.IGNORECASE,
)


class DockerInspectionError(RuntimeError):
    """Docker did not provide one complete, exact container inspection."""


def legacy_job_ids(
    directory: Path,
    *,
    root_opener: Callable[[str], int] | None = None,
    directory_scanner: Callable[[int], list[str]] | None = None,
    marker_checker: Callable[[int, str], bool] | None = None,
) -> tuple[str, ...]:
    """Charge every observed legacy entry without a safely opened regular EXIT.

    The configured absolute path is opened from ``/`` one component at a time.
    The two listings narrow rename races; they do not make this a writer
    protocol, so this remains a point-in-time observation.
    """

    configured = os.fspath(directory)
    if not isinstance(configured, str) or not configured.startswith("/"):
        raise ValueError("legacy jobs directory must be absolute")
    opener = _open_directory_from_root if root_opener is None else root_opener
    scanner = _directory_names if directory_scanner is None else directory_scanner
    marker = _has_regular_exit_marker if marker_checker is None else marker_checker

    root_fd = opener(configured)
    try:
        first_names = scanner(root_fd)
        first_set = set(first_names)
        live_names: list[str] = []
        for name in first_names:
            if not isinstance(name, str):
                raise OSError("legacy jobs directory returned a non-text entry name")
            if not marker(root_fd, name):
                live_names.append(name)

        second_names = scanner(root_fd)
        for name in second_names:
            if not isinstance(name, str):
                raise OSError("legacy jobs directory returned a non-text entry name")
            if name not in first_set and not marker(root_fd, name):
                live_names.append(name)

        reopened_fd = opener(configured)
        try:
            pinned = os.fstat(root_fd)
            current = os.fstat(reopened_fd)
            if (pinned.st_dev, pinned.st_ino) != (current.st_dev, current.st_ino):
                raise OSError("legacy jobs directory changed during admission scan")
        finally:
            os.close(reopened_fd)

        result: list[str] = []
        seen_ids: set[str] = set()
        for name in live_names:
            legacy_id = _legacy_job_id(name)
            if legacy_id in seen_ids:
                raise OSError("legacy jobs directory produced a duplicate admission id")
            seen_ids.add(legacy_id)
            result.append(legacy_id)
        return tuple(result)
    finally:
        os.close(root_fd)


def _open_directory_from_root(path: str) -> int:
    """Open an absolute, resolved directory path without following components."""

    if os.path.normpath(path) != path:
        raise ValueError("legacy jobs directory path must be fully resolved")
    components = path.split("/")[1:]
    if any(component in {"", ".", ".."} for component in components):
        raise ValueError("legacy jobs directory path must be fully resolved")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    current_fd = os.open("/", flags)
    try:
        for component in components:
            next_fd = os.open(
                component,
                flags | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
            os.close(current_fd)
            current_fd = next_fd
        result, current_fd = current_fd, -1
        return result
    finally:
        if current_fd >= 0:
            os.close(current_fd)


def _directory_names(root_fd: int) -> list[str]:
    names: list[str] = []
    with os.scandir(root_fd) as entries:
        for entry in entries:
            name = entry.name
            if not isinstance(name, str):
                raise OSError("legacy jobs directory returned a non-text entry name")
            names.append(name)
            if len(names) > MAX_LEGACY_JOB_ENTRIES:
                raise OSError(
                    f"legacy jobs directory exceeds MAX_LEGACY_JOB_ENTRIES ({MAX_LEGACY_JOB_ENTRIES})"
                )
    return sorted(names)


def _legacy_job_id(name: str) -> str:
    digest = hashlib.sha256(os.fsencode(name)).hexdigest()[:40]
    return f"legacy-{digest}"


def _has_regular_exit_marker(
    root_fd: int,
    name: str,
    *,
    marker_opener: Callable[..., int] | None = None,
) -> bool:
    try:
        entry_fd = open_beneath(root_fd, name, directory=True)
    except Exception:
        # Anything that cannot be safely opened remains charged.
        return False
    try:
        open_marker = os.open if marker_opener is None else marker_opener
        try:
            marker_fd = open_marker(
                "EXIT",
                os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=entry_fd,
            )
        except Exception:
            return False
        try:
            return stat.S_ISREG(os.fstat(marker_fd).st_mode)
        finally:
            os.close(marker_fd)
    finally:
        os.close(entry_fd)


def docker_object_inspector(
    timeout_s: float,
    *,
    runner: Callable[..., Any] | None = None,
    docker_binary: str = "/usr/bin/docker",
) -> Callable[[str], InspectorAnswer]:
    """Build an exact-name/full-ID inspector for admission lease decisions."""

    timeout = _positive_timeout(timeout_s)

    def inspect(target: str) -> InspectorAnswer:
        try:
            observed = _inspect_one(target, timeout, runner, docker_binary=docker_binary)
            if observed is None:
                return InspectorAnswer.NO_SUCH_OBJECT
            container_id, _raw_name, normalized_name, _labels = _container_details(observed)
            if target == container_id or target == normalized_name:
                return InspectorAnswer.PRESENT
        except Exception:
            return InspectorAnswer.ERROR
        return InspectorAnswer.ERROR

    return inspect


def docker_container_identity_inspector(
    timeout_s: float,
    *,
    runner: Callable[..., Any] | None = None,
    docker_binary: str = "/usr/bin/docker",
) -> Callable[[str], Mapping[str, object] | None]:
    """Build an inspector that returns name, full ID and labels for one full ID."""

    timeout = _positive_timeout(timeout_s)

    def inspect(container_id: str) -> Mapping[str, object] | None:
        try:
            observed = _inspect_one(
                container_id, timeout, runner, docker_binary=docker_binary, full_id_only=True,
            )
            if observed is None:
                return None
            observed_id, raw_name, _normalized_name, labels = _container_details(observed)
            if observed_id != container_id:
                raise DockerInspectionError("Docker returned a different container ID")
            return {"id": observed_id, "name": raw_name, "labels": labels}
        except Exception as exc:
            raise DockerInspectionError(f"container identity inspection failed: {exc}") from exc

    return inspect


def _positive_timeout(timeout_s: float) -> float:
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
        raise ValueError("Docker query timeout must be a finite positive number")
    try:
        timeout = float(timeout_s)
    except OverflowError as exc:
        raise ValueError("Docker query timeout must be a finite positive number") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Docker query timeout must be a finite positive number")
    return timeout


def _inspect_one(
    target: str,
    timeout_s: float,
    runner: Callable[..., Any] | None,
    *,
    docker_binary: str,
    full_id_only: bool = False,
) -> dict[str, object] | None:
    _validate_target(target, full_id_only=full_id_only)
    if (
        not isinstance(docker_binary, str)
        or not os.path.isabs(docker_binary)
        or docker_binary.startswith("-")
    ):
        raise DockerInspectionError("Docker executable is invalid")
    argv = [docker_binary, "--host", _DOCKER_HOST, "inspect", "--type=container", target]
    try:
        if runner is None:
            result = _run_bounded(argv, timeout_s)
        else:
            result = runner(
                argv,
                check=False,
                capture_output=True,
                text=False,
                timeout=timeout_s,
                shell=False,
                env=dict(_DOCKER_ENV),
                cwd="/",
                start_new_session=True,
            )
    except Exception as exc:
        raise DockerInspectionError(f"Docker inspect command failed: {exc}") from exc
    try:
        returncode = result.returncode
        stdout_bytes = result.stdout
        stderr_bytes = result.stderr
    except Exception as exc:
        raise DockerInspectionError("Docker inspect runner returned an invalid result") from exc
    if (
        type(returncode) is not int
        or not isinstance(stdout_bytes, bytes)
        or not isinstance(stderr_bytes, bytes)
    ):
        raise DockerInspectionError("Docker inspect runner returned an invalid result")
    if len(stdout_bytes) > MAX_DOCKER_OUTPUT_BYTES or len(stderr_bytes) > MAX_DOCKER_OUTPUT_BYTES:
        raise DockerInspectionError("Docker inspect output exceeded MAX_DOCKER_OUTPUT_BYTES")
    try:
        stdout = stdout_bytes.decode("utf-8", errors="strict")
        stderr = stderr_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise DockerInspectionError("Docker inspect output is not strict UTF-8") from exc
    if returncode != 0:
        if not stdout.strip() and _is_explicit_missing(stderr, target):
            return None
        raise DockerInspectionError(f"Docker inspect exited with status {returncode}")
    if stderr:
        raise DockerInspectionError("Docker inspect returned unexpected stderr")
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise DockerInspectionError("Docker inspect returned unparseable JSON") from exc
    if isinstance(payload, list):
        if len(payload) != 1:
            raise DockerInspectionError("Docker inspect did not return exactly one object")
        payload = payload[0]
    if not isinstance(payload, dict):
        raise DockerInspectionError("Docker inspect returned an invalid object")
    return payload


def _run_bounded(
    argv: list[str],
    timeout_s: float,
    *,
    popen_factory: Callable[..., Any] | None = None,
    selector_factory: Callable[[], Any] | None = None,
    monotonic: Callable[[], float] | None = None,
    kill_group: Callable[[int, int], None] | None = None,
    output_limit: int | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Collect Docker output, observe exit without reaping, then clean its group.

    The deadline starts before synchronous process creation, which cannot be
    cancelled; time spent there consumes the available query time. A pidfd
    selector wait observes exit without reaping the group leader, keeping its
    PID pinned until SIGKILL has been attempted for every result path.
    """

    spawn = subprocess.Popen if popen_factory is None else popen_factory
    selector_type = selectors.DefaultSelector if selector_factory is None else selector_factory
    clock = time.monotonic if monotonic is None else monotonic
    group_kill = os.killpg if kill_group is None else kill_group
    limit = MAX_DOCKER_OUTPUT_BYTES if output_limit is None else output_limit
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("Docker output limit must be a positive integer")

    deadline = clock() + timeout_s
    process: Any = None
    selector: Any | None = None
    pidfd: int | None = None
    stdout = bytearray()
    stderr = bytearray()
    termination_attempted = False

    def terminate_group_and_reap() -> int:
        nonlocal termination_attempted
        termination_attempted = True
        return _kill_process_group_and_reap(process, deadline, clock, group_kill)

    try:
        process = spawn(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd="/",
            env=dict(_DOCKER_ENV),
            shell=False,
            close_fds=True,
            start_new_session=True,
            bufsize=0,
        )
        if clock() >= deadline:
            raise DockerInspectionError("Docker inspect exceeded query_timeout_s during spawn")

        selector = selector_type()
        streams = (("stdout", process.stdout, stdout), ("stderr", process.stderr, stderr))
        for stream_name, stream, buffer in streams:
            if stream is None:
                raise DockerInspectionError("Docker inspect process did not provide output pipes")
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, (stream_name, buffer))

        while selector.get_map():
            remaining = deadline - clock()
            if remaining <= 0:
                raise DockerInspectionError("Docker inspect exceeded query_timeout_s")
            ready = selector.select(remaining)
            if not ready:
                continue
            for key, _events in ready:
                buffer = key.data[1]
                room = limit - len(buffer)
                try:
                    chunk = os.read(key.fileobj.fileno(), min(65536, max(1, room)))
                except OSError as exc:
                    raise DockerInspectionError("Docker inspect output pipe failed") from exc
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if len(chunk) > room:
                    raise DockerInspectionError("Docker inspect output exceeded MAX_DOCKER_OUTPUT_BYTES")
                buffer.extend(chunk)

        remaining = deadline - clock()
        if remaining <= 0:
            raise DockerInspectionError("Docker inspect exceeded query_timeout_s while observing child exit")
        try:
            pidfd = os.pidfd_open(process.pid)
        except OSError as exc:
            raise DockerInspectionError("Docker inspect could not open a child pidfd") from exc
        remaining = deadline - clock()
        if remaining <= 0:
            raise DockerInspectionError("Docker inspect exceeded query_timeout_s while observing child exit")
        selector.register(pidfd, selectors.EVENT_READ, ("pidfd", None))
        ready = selector.select(remaining)
        if not any(key.fileobj == pidfd for key, _events in ready):
            raise DockerInspectionError("Docker inspect exceeded query_timeout_s while observing child exit")
        selector.unregister(pidfd)
        if clock() >= deadline:
            raise DockerInspectionError("Docker inspect exceeded query_timeout_s while observing child exit")
        returncode = terminate_group_and_reap()
        if clock() >= deadline:
            raise DockerInspectionError("Docker inspect exceeded query_timeout_s while reaping")
        return subprocess.CompletedProcess(argv, returncode, bytes(stdout), bytes(stderr))
    except DockerInspectionError:
        if process is not None and not termination_attempted:
            terminate_group_and_reap()
        raise
    except Exception as exc:
        if process is not None and not termination_attempted:
            terminate_group_and_reap()
        raise DockerInspectionError(f"Docker inspect process failed: {exc}") from exc
    finally:
        if pidfd is not None:
            os.close(pidfd)
        if selector is not None:
            selector.close()
        if process is not None:
            for stream in (getattr(process, "stdout", None), getattr(process, "stderr", None)):
                if stream is not None:
                    stream.close()


def _kill_process_group_and_reap(
    process: Any,
    deadline: float,
    clock: Callable[[], float],
    kill_group: Callable[[int, int], None],
) -> int:
    """Signal the private group before making a deadline-bounded reap attempt."""

    try:
        kill_group(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        kill_error: Exception | None = None
    except Exception as exc:
        kill_error = exc
    else:
        kill_error = None
    remaining = max(0.0, deadline - clock())
    try:
        returncode = process.wait(timeout=remaining)
    except subprocess.TimeoutExpired as exc:
        raise DockerInspectionError("Docker inspect exceeded query_timeout_s while reaping child") from exc
    except Exception as exc:
        raise DockerInspectionError("Docker inspect child reaping failed") from exc
    if kill_error is not None:
        raise DockerInspectionError("could not kill Docker inspect process group") from kill_error
    return returncode


def _validate_target(target: str, *, full_id_only: bool) -> None:
    if not isinstance(target, str):
        raise DockerInspectionError("Docker target must be text")
    if full_id_only:
        if _FULL_CONTAINER_ID_RE.fullmatch(target) is None:
            raise DockerInspectionError("container identity target must be a full lowercase Docker ID")
        return
    if _FULL_CONTAINER_ID_RE.fullmatch(target) is not None:
        return
    if is_ambiguous_container_name(target):
        raise DockerInspectionError("Docker target is an ambiguous container ID prefix")
    if _ADMISSION_NAME_RE.fullmatch(target) is None:
        raise DockerInspectionError("Docker target is not an exact name or full container ID")


def _is_explicit_missing(stderr: str, target: str) -> bool:
    match = _MISSING_OBJECT_RE.fullmatch(stderr.strip())
    return match is not None and match.group(1) == target


def _container_details(
    value: Mapping[str, object],
) -> tuple[str, str, str, dict[str, str]]:
    container_id = value.get("Id")
    raw_name = value.get("Name")
    config = value.get("Config")
    if not isinstance(container_id, str) or _FULL_CONTAINER_ID_RE.fullmatch(container_id) is None:
        raise DockerInspectionError("Docker inspect object has no full lowercase ID")
    if not isinstance(raw_name, str) or not raw_name:
        raise DockerInspectionError("Docker inspect object has no name")
    normalized_name = raw_name[1:] if raw_name.startswith("/") else raw_name
    if _ADMISSION_NAME_RE.fullmatch(normalized_name) is None:
        raise DockerInspectionError("Docker inspect object has an invalid name")
    if not isinstance(config, Mapping):
        raise DockerInspectionError("Docker inspect object has no configuration")
    raw_labels = config.get("Labels")
    if raw_labels is None:
        labels: dict[str, str] = {}
    elif isinstance(raw_labels, Mapping):
        if any(not isinstance(label, str) for label in raw_labels.values()):
            raise DockerInspectionError("Docker inspect object has invalid labels")
        labels = dict(raw_labels)
    else:
        raise DockerInspectionError("Docker inspect object has invalid labels")
    return container_id, raw_name, normalized_name, labels
