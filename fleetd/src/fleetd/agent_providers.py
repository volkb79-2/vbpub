"""Bounded host-side providers for admission and local Docker leases."""

from __future__ import annotations

import json
import math
import os
import re
import stat
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .admission import InspectorAnswer
from .hostside import open_beneath

_ADMISSION_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_FULL_CONTAINER_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
_MISSING_OBJECT_RE = re.compile(
    r"(?:Error:\s*|Error response from daemon:\s*)?"
    r"No such (?:object|container):\s*([^\s]+)\Z",
    re.IGNORECASE,
)


class DockerInspectionError(RuntimeError):
    """Docker did not provide one complete, exact container inspection."""


def legacy_job_ids(directory: Path) -> tuple[str, ...]:
    """List every legacy entry without a regular EXIT marker, without following links.

    The returned IDs are stable for a given sorted directory listing and always
    satisfy AdmissionBook's identifier contract, including for unexpected
    names and non-directory entries. The provider accounts for every entry;
    only a safely opened regular EXIT file marks it complete.
    """

    root = Path(directory)
    if not root.is_absolute():
        raise ValueError("legacy jobs directory must be absolute")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    root_fd = os.open(root, flags)
    try:
        names = sorted(os.listdir(root_fd))
        live: list[str] = []
        for index, name in enumerate(names):
            if not isinstance(name, str):
                raise OSError("legacy jobs directory returned a non-text entry name")
            if not _has_regular_exit_marker(root_fd, name):
                legacy_id = f"legacy-job-{index}"
                if _ADMISSION_NAME_RE.fullmatch(legacy_id) is None:
                    raise OSError("legacy job entry count exceeds admission identifier limits")
                live.append(legacy_id)
        return tuple(live)
    finally:
        os.close(root_fd)


def _has_regular_exit_marker(root_fd: int, name: str) -> bool:
    try:
        entry_fd = open_beneath(root_fd, name, directory=True)
    except (OSError, ValueError):
        return False
    try:
        try:
            marker_fd = os.open(
                "EXIT",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                dir_fd=entry_fd,
            )
        except OSError:
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
    docker_binary: str = "docker",
) -> Callable[[str], InspectorAnswer]:
    """Build an exact-name/full-ID inspector for admission lease decisions."""

    timeout = _positive_timeout(timeout_s)
    run = subprocess.run if runner is None else runner

    def inspect(target: str) -> InspectorAnswer:
        try:
            observed = _inspect_one(target, timeout, run, docker_binary=docker_binary)
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
    docker_binary: str = "docker",
) -> Callable[[str], Mapping[str, object] | None]:
    """Build an inspector that returns name, full ID and labels for one full ID."""

    timeout = _positive_timeout(timeout_s)
    run = subprocess.run if runner is None else runner

    def inspect(container_id: str) -> Mapping[str, object] | None:
        try:
            observed = _inspect_one(
                container_id, timeout, run, docker_binary=docker_binary, full_id_only=True,
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
    runner: Callable[..., Any],
    *,
    docker_binary: str,
    full_id_only: bool = False,
) -> dict[str, object] | None:
    _validate_target(target, full_id_only=full_id_only)
    if not isinstance(docker_binary, str) or not docker_binary or docker_binary.startswith("-"):
        raise DockerInspectionError("Docker executable is invalid")
    argv = [docker_binary, "inspect", "--type=container", target]
    try:
        result = runner(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            shell=False,
        )
    except Exception as exc:
        raise DockerInspectionError(f"Docker inspect command failed: {exc}") from exc
    try:
        returncode = result.returncode
        stdout = result.stdout
        stderr = result.stderr
    except Exception as exc:
        raise DockerInspectionError("Docker inspect runner returned an invalid result") from exc
    if type(returncode) is not int or not isinstance(stdout, str) or not isinstance(stderr, str):
        raise DockerInspectionError("Docker inspect runner returned an invalid result")
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


def _validate_target(target: str, *, full_id_only: bool) -> None:
    if not isinstance(target, str):
        raise DockerInspectionError("Docker target must be text")
    if full_id_only:
        if _FULL_CONTAINER_ID_RE.fullmatch(target) is None:
            raise DockerInspectionError("container identity target must be a full lowercase Docker ID")
        return
    if (
        _FULL_CONTAINER_ID_RE.fullmatch(target) is None
        and _ADMISSION_NAME_RE.fullmatch(target) is None
    ):
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
