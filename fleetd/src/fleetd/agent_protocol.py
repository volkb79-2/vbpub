"""Framing and closed-set request validation for fleetd host-agent transports."""

from __future__ import annotations

import json
import math
import os
import re
import select
import struct
from pathlib import Path
from typing import Any, Mapping

from .admission import AdmissionError, ExitCode, JOB_ID_RE, MAX_JOB_ID_LENGTH, PRIORITIES
from .agent_policy import AgentIdentity, AgentPolicy

MAX_FRAME_BYTES = 64 * 1024
MAX_FETCH_BYTES = 8 * 1024 * 1024
ARTIFACT_NAMES = frozenset({
    "job.log", "lane.log", "lane-detail.log", "artifacts.tgz", "assay-store.tgz",
    "assay-store.tgz.sha256", "result.json",
})
_REPO_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,40}\Z")
_SHA40_RE = re.compile(r"[0-9a-f]{40}\Z")
_SHA64_RE = re.compile(r"[0-9a-f]{64}\Z")
_BASE_RE = re.compile(r"[0-9a-f]{1,64}\Z")
_LANE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SEED_NAME_RE = re.compile(r"assay-seed-[0-9]+\.tgz(?:\.sha256)?\Z")
_WORKTREE_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SEMVER_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z")


class FrameError(ValueError):
    """A malformed, oversized, truncated, or idle agent frame."""


def _read_exact(fd: int, size: int, idle_timeout_s: float) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        try:
            readable, _, _ = select.select([fd], [], [], idle_timeout_s)
        except (OSError, ValueError) as exc:
            raise FrameError(f"cannot wait for framed input: {exc}") from exc
        if not readable:
            raise FrameError("idle connection deadline expired")
        try:
            chunk = os.read(fd, remaining)
        except OSError as exc:
            raise FrameError(f"cannot read framed input: {exc}") from exc
        if not chunk:
            raise FrameError("truncated frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FrameError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _invalid_json_constant(value: str) -> None:
    raise FrameError(f"invalid JSON constant {value!r}")


def read_frame(stream: object, idle_timeout_s: float) -> dict[str, Any]:
    """Read one big-endian length-prefixed JSON object, with an idle timeout."""
    if isinstance(idle_timeout_s, bool) or not isinstance(idle_timeout_s, (int, float)):
        raise FrameError("idle timeout must be a positive number")
    timeout = float(idle_timeout_s)
    if not math.isfinite(timeout) or timeout <= 0:
        raise FrameError("idle timeout must be a positive number")
    try:
        fd = stream if type(stream) is int else stream.fileno()
    except (AttributeError, OSError, ValueError) as exc:
        raise FrameError("framed stream must expose a file descriptor") from exc
    if type(fd) is not int or fd < 0:
        raise FrameError("framed stream has an invalid file descriptor")
    header = _read_exact(fd, 4, timeout)
    length = struct.unpack(">I", header)[0]
    if length == 0 or length > MAX_FRAME_BYTES:
        raise FrameError(f"frame length {length} is outside 1..{MAX_FRAME_BYTES}")
    body = _read_exact(fd, length, timeout)
    try:
        decoded = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FrameError("frame body is not UTF-8") from exc
    try:
        value = json.loads(
            decoded, object_pairs_hook=_unique_object, parse_constant=_invalid_json_constant,
        )
    except (json.JSONDecodeError, FrameError) as exc:
        raise FrameError(f"frame body is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise FrameError("frame body must be a JSON object")
    return value


def _write_all(fd: int, content: bytes) -> None:
    remaining = memoryview(content)
    while remaining:
        try:
            count = os.write(fd, remaining)
        except OSError as exc:
            raise FrameError(f"cannot write framed response: {exc}") from exc
        if count <= 0:
            raise FrameError("framed response write made no progress")
        remaining = remaining[count:]


def write_frame(stream: object, value: Mapping[str, object]) -> None:
    """Write one capped JSON-object response frame."""
    try:
        fd = stream if type(stream) is int else stream.fileno()
        body = json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (AttributeError, OSError, ValueError, TypeError) as exc:
        raise FrameError(f"cannot encode framed response: {exc}") from exc
    if type(fd) is not int or fd < 0:
        raise FrameError("framed stream has an invalid file descriptor")
    if len(body) == 0 or len(body) > MAX_FRAME_BYTES:
        raise FrameError(f"response frame exceeds {MAX_FRAME_BYTES} bytes")
    _write_all(fd, struct.pack(">I", len(body)) + body)


def _refuse(reason: str) -> None:
    raise AdmissionError(ExitCode.REFUSAL, reason)


def _object(value: object, where: str, required: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        _refuse(f"{where} must be an object")
    extra = sorted(set(value) - required)
    missing = sorted(required - set(value))
    if extra:
        _refuse(f"{where} has unknown field(s): {', '.join(extra)}")
    if missing:
        _refuse(f"{where} is missing field(s): {', '.join(missing)}")
    return value


def _string(value: object, where: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value or len(value) > maximum:
        _refuse(f"{where} must be a non-empty string of at most {maximum} characters")
    return value


def _integer(value: object, where: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        bound = f"{minimum}..{maximum}" if maximum is not None else f"at least {minimum}"
        _refuse(f"{where} must be an integer {bound}")
    return value


def _number(value: object, where: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _refuse(f"{where} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        _refuse(f"{where} must be a finite number")
    if not math.isfinite(number) or number < 0 or (positive and number == 0):
        _refuse(f"{where} must be a finite {'positive ' if positive else 'non-negative '}number")
    return number


def validate_relative_path(value: object, where: str, *, allow_dot: bool = False) -> str:
    """Validate a portable relative path without empty, dot, or parent segments."""
    path = _string(value, where)
    if path.startswith("/") or "\\" in path or (path == "." and not allow_dot):
        _refuse(f"{where} must be a safe relative path")
    if path == ".":
        return path
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        _refuse(f"{where} must not contain empty, dot, or parent path components")
    return path


def _inside(root: Path, relative: str, where: str) -> Path:
    """Resolve existing symlinks and refuse any path that escapes ``root``."""
    try:
        resolved_root = root.resolve(strict=False)
        candidate = (root / relative).resolve(strict=False)
        candidate.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError):
        _refuse(f"{where} escapes its configured checkout root")
    return candidate


def _repo(value: object, policy: AgentPolicy) -> Path:
    repo = _string(value, "repo", maximum=41)
    if (
        _REPO_NAME_RE.fullmatch(repo) is None
        or repo.endswith(".git")
        or _SHA40_RE.fullmatch(repo)
        or _SHA64_RE.fullmatch(repo)
    ):
        _refuse("repo is not a valid repository name")
    root = policy.repo_roots.get(repo)
    if root is None:
        _refuse(f"repo {repo!r} is not on the host allow-list")
    return root


def _job_id(value: object, identity: AgentIdentity, where: str) -> str:
    job_id = _string(value, where, maximum=MAX_JOB_ID_LENGTH)
    if JOB_ID_RE.fullmatch(job_id) is None or not job_id.startswith(identity.group + "-"):
        _refuse(f"{where} is invalid or not prefixed by the authenticated group")
    return job_id


def _sha256(value: object, where: str) -> str:
    digest = _string(value, where, maximum=64)
    if _SHA64_RE.fullmatch(digest) is None:
        _refuse(f"{where} must be a lowercase SHA-256 digest")
    return digest


def _file_descriptor(value: object, where: str) -> dict[str, object]:
    item = _object(value, where, {"path", "sha256", "bytes"})
    relative = validate_relative_path(item["path"], f"{where}.path")
    return {
        "path": relative,
        "sha256": _sha256(item["sha256"], f"{where}.sha256"),
        "bytes": _integer(item["bytes"], f"{where}.bytes", minimum=1),
    }


def _check_argv(
    value: object,
    where: str,
    allowed: frozenset[tuple[str, ...]],
    *,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        _refuse(f"{where} must be an argv array")
    argv = tuple(_string(item, f"{where}[{index}]") for index, item in enumerate(value))
    if not argv and not allow_empty:
        _refuse(f"{where} must not be empty")
    if argv and argv not in allowed:
        _refuse(f"{where} is not on the host allow-list")
    return argv


def _check_env(value: object, policy: AgentPolicy) -> dict[str, str]:
    if not isinstance(value, dict):
        _refuse("env must be an object")
    result: dict[str, str] = {}
    for raw_name, raw_value in value.items():
        name = _string(raw_name, "env key", maximum=128)
        allowed = policy.env_values.get(name)
        if allowed is None:
            _refuse(f"env name {name!r} is not on the host allow-list")
        env_value = _string(raw_value, f"env.{name}")
        if env_value not in allowed:
            _refuse(f"env value for {name!r} is not on the host allow-list")
        result[name] = env_value
    return result


def _sha(value: object, where: str, *, full: bool) -> str:
    digest = _string(value, where, maximum=64)
    valid = _SHA40_RE.fullmatch(digest) or _SHA64_RE.fullmatch(digest) if full else _BASE_RE.fullmatch(digest)
    if valid is None:
        _refuse(f"{where} must be {'a full' if full else 'a'} hexadecimal commit id")
    return digest


def validate_request(
    value: object,
    identity: AgentIdentity,
    policy: AgentPolicy,
    *,
    created_candidate_paths: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Validate one complete request before any verb takes a queue lock or writes."""
    if not isinstance(value, dict):
        _refuse("request must be a JSON object")
    verb = value.get("verb")
    if not isinstance(verb, str):
        _refuse("request.verb must be a string")
    fields: dict[str, set[str]] = {
        "probe": {"verb"}, "facts": {"verb"}, "have": {"verb", "repo"},
        "put": {"verb", "name", "sha256", "bytes"},
        "submit": {
            "verb", "job_id", "repo", "sha", "base", "project", "lane", "shard",
            "resources", "priority", "requested_by", "stores", "extra_argv", "env", "dev", "kind",
        },
        "status": {"verb", "job_id"}, "progress": {"verb", "job_id", "offset"},
        "cancel": {"verb", "job_id", "reason"},
        "fetch": {"verb", "job_id", "artifact", "offset", "length"},
        "lease": {"verb", "lease_id", "container_name", "footprint", "planned_cores", "priority"},
    }
    if verb not in fields:
        _refuse(f"unsupported agent verb {verb!r}")
    request = _object(value, "request", fields[verb])

    if verb in {"probe", "facts"}:
        return request
    if verb == "have":
        _repo(request["repo"], policy)
        return request
    if verb == "put":
        name = _string(request["name"], "put.name", maximum=128)
        if name not in {"in.bundle", "candidates", "dev.tracked.tar", "dev.untracked.tar"} and _SEED_NAME_RE.fullmatch(name) is None:
            _refuse(f"put name {name!r} is not in the closed artifact set")
        _sha256(request["sha256"], "put.sha256")
        _integer(request["bytes"], "put.bytes", minimum=1)
        return request
    if verb in {"status", "progress", "cancel", "fetch"}:
        _job_id(request["job_id"], identity, "job_id")
        if verb == "progress":
            _integer(request["offset"], "progress.offset")
        elif verb == "cancel":
            _string(request["reason"], "cancel.reason")
        elif verb == "fetch":
            artifact = _string(request["artifact"], "fetch.artifact", maximum=128)
            if artifact not in ARTIFACT_NAMES:
                _refuse(f"artifact {artifact!r} is not in the closed fetch set")
            _integer(request["offset"], "fetch.offset")
            _integer(request["length"], "fetch.length", minimum=1, maximum=MAX_FETCH_BYTES)
        return request
    if verb == "lease":
        if identity.transport != "peer":
            raise AdmissionError(ExitCode.IDENTITY_OR_TRANSPORT, "lease is available only on the controller socket")
        _job_id(request["lease_id"], identity, "lease_id")
        name = _string(request["container_name"], "lease.container_name", maximum=128)
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) is None:
            _refuse("lease.container_name is invalid")
        _number(request["planned_cores"], "lease.planned_cores", positive=True)
        if request["footprint"] not in {"tiny", "small", "medium", "large"}:
            _refuse("lease.footprint is invalid")
        if request["priority"] not in PRIORITIES:
            _refuse("lease.priority is invalid")
        return request

    _job_id(request["job_id"], identity, "job_id")
    repo_root = _repo(request["repo"], policy)
    sha = _sha(request["sha"], "sha", full=True)
    if request["base"] is not None:
        _sha(request["base"], "base", full=False)
    project = validate_relative_path(request["project"], "project", allow_dot=True)
    checkout_root = _inside(repo_root, sha, "checkout")
    if project != ".":
        _inside(checkout_root, project, "project")
    lane = _string(request["lane"], "lane", maximum=128)
    if _LANE_RE.fullmatch(lane) is None:
        _refuse("lane is not a safe Run-Gate lane name")
    if request["shard"] is not None:
        shard = _object(request["shard"], "shard", {"index", "total"})
        _integer(shard["index"], "shard.index", minimum=0)
        _integer(shard["total"], "shard.total", minimum=1)
        _refuse("non-null shard is reserved in fleet-host-agent/1")

    resources = _object(
        request["resources"], "resources",
        {"planned_cores", "cpu_set", "footprint", "declared_peak_bytes"},
    )
    _number(resources["planned_cores"], "resources.planned_cores", positive=True)
    if resources["cpu_set"] is not None:
        if not isinstance(resources["cpu_set"], list) or any(type(cpu) is not int or cpu < 0 for cpu in resources["cpu_set"]):
            _refuse("resources.cpu_set must be null or an array of non-negative CPU ids")
        if len(set(resources["cpu_set"])) != len(resources["cpu_set"]):
            _refuse("resources.cpu_set contains duplicate CPU ids")
        _refuse("non-null resources.cpu_set is reserved in fleet-host-agent/1")
    if resources["footprint"] not in {"tiny", "small", "medium", "large"}:
        _refuse("resources.footprint is invalid")
    if resources["declared_peak_bytes"] is not None:
        _integer(resources["declared_peak_bytes"], "resources.declared_peak_bytes")
    if request["priority"] not in PRIORITIES:
        _refuse("priority is invalid")

    requested_by = _object(request["requested_by"], "requested_by", {"client", "version", "run"})
    client = _string(requested_by["client"], "requested_by.client", maximum=128)
    if client not in policy.requested_by_clients:
        _refuse(f"requested_by.client {client!r} is not on the host allow-list")
    version = _string(requested_by["version"], "requested_by.version", maximum=128)
    if _SEMVER_RE.fullmatch(version) is None:
        _refuse("requested_by.version must be SemVer")
    _string(requested_by["run"], "requested_by.run", maximum=128)

    stores = _object(request["stores"], "stores", {"collect", "seed"})
    if stores["collect"] is not None:
        collect = validate_relative_path(stores["collect"], "stores.collect")
        _inside(checkout_root, collect, "stores.collect")
    if stores["seed"] is not None:
        seed = _file_descriptor(stores["seed"], "stores.seed")
        _inside(checkout_root, str(seed["path"]), "stores.seed.path")
    extra_argv_value = request["extra_argv"]
    if not isinstance(extra_argv_value, list):
        _refuse("extra_argv must be an argv array")
    extra_argv = tuple(_string(item, f"extra_argv[{index}]") for index, item in enumerate(extra_argv_value))
    candidate_indexes = [index for index, item in enumerate(extra_argv) if item == "--only-candidates"]
    if candidate_indexes:
        if len(candidate_indexes) != 1 or candidate_indexes[0] + 1 >= len(extra_argv):
            _refuse("--only-candidates requires exactly one host-created file")
        index = candidate_indexes[0]
        candidate_path = extra_argv[index + 1]
        if candidate_path not in created_candidate_paths:
            _refuse("--only-candidates must name a host-created candidates file for the authenticated group")
        normalized_argv = (*extra_argv[:index], "--only-candidates", "<host-candidates>", *extra_argv[index + 2:])
        if normalized_argv not in policy.extra_argv:
            _refuse("extra_argv is not on the host allow-list")
    elif extra_argv and extra_argv not in policy.extra_argv:
        _refuse("extra_argv is not on the host allow-list")
    _check_env(request["env"], policy)

    kind = request["kind"]
    if kind not in {"lane", "dev"}:
        _refuse("kind must be 'lane' or 'dev'")
    if kind == "lane":
        if request["dev"] is not None:
            _refuse("dev payload is only allowed for kind 'dev'")
    else:
        dev = _object(request["dev"], "dev", {"tracked", "untracked", "verb_argv", "tty", "worktree_key"})
        for key in ("tracked", "untracked"):
            _object(dev[key], f"dev.{key}", {"sha256", "bytes"})
            _sha256(dev[key]["sha256"], f"dev.{key}.sha256")
            _integer(dev[key]["bytes"], f"dev.{key}.bytes", minimum=1)
        _check_argv(dev["verb_argv"], "dev.verb_argv", policy.dev_argv, allow_empty=False)
        if type(dev["tty"]) is not bool:
            _refuse("dev.tty must be a boolean")
        worktree_key = _string(dev["worktree_key"], "dev.worktree_key", maximum=128)
        if _WORKTREE_KEY_RE.fullmatch(worktree_key) is None:
            _refuse("dev.worktree_key is invalid")
    return request
