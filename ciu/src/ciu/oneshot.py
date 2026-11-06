"""Stack-scoped one-shot Compose planning and extension validation (CIU-141)."""

# Public input-shape failures intentionally use ValueError so the CLI maps
# them to its established exit-2 validation contract.
# ruff: noqa: TRY004

from __future__ import annotations

import copy
import fcntl
import json
import os
import posixpath
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from contextlib import contextmanager, redirect_stdout, suppress
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from . import procutil

_CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_LABEL_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-/]*$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LIMIT_KEYS = ("mem_limit", "memswap_limit", "pids_limit", "cpus")
# cap_add and security_opt are deliberately NOT extendable (plan 2.4: anything
# not listed is refused; they would widen the container's privileges).
_ALLOWED_EXTENSION_KEYS = frozenset({
    *_LIMIT_KEYS, "group_add", "volumes", "environment", "labels", "init",
})
_APPEND_EXTENSION_KEYS = ("group_add",)
_HEX_LOOKALIKE_RE = re.compile(r"^[0-9a-f]{1,64}$")
# Docker resolves a hex string as a container ID (prefix) before a name, so a
# hex-only --name could address another container. Sockets that give control
# of the daemon may never be bind-mounted through --extend.
_DOCKER_SOCKETS = ("/var/run/docker.sock", "/run/docker.sock")
_RUN_ID_LABEL = "ciu.run.id"
_PLACEHOLDER_AT_LAUNCH = "<assigned at launch>"
_ALLOWED_EXTENSION_ROOT_KEYS = frozenset({"services", "x-ciu-run"})
_CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_EVENT_SCHEMA = "ciu-run-event/1"
_FOREGROUND_INSPECT_INTERVAL = 0.05
_FOREGROUND_INSPECT_TIMEOUT = 30.0
_SIGNAL_GRACE_SECONDS = 10.0
_PROC_ROOT = Path("/proc")
_DEFAULT_REAP_AGE_SECONDS = 3600.0


def validate_container_name(name: str) -> str:
    """Validate a Docker container name and preserve it verbatim."""
    if not isinstance(name, str) or not _CONTAINER_NAME_RE.fullmatch(name):
        raise ValueError(
            "[CIU-141] container name must match "
            "[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}"
        )
    if _HEX_LOOKALIKE_RE.fullmatch(name):
        raise ValueError(
            "[CIU-141] container name must not look like a Docker container ID "
            "(1-64 lowercase hex characters); Docker would resolve it as an ID"
        )
    return name


def parse_run_label(value: str) -> tuple[str, str]:
    """Parse one repeatable ``--label K=V`` token without splitting its value."""
    if not isinstance(value, str) or "=" not in value:
        raise ValueError("[CIU-141] label must be KEY=VALUE")
    key, label_value = value.split("=", 1)
    if not _LABEL_KEY_RE.fullmatch(key):
        raise ValueError(f"[CIU-141] invalid label key {key!r}")
    if key.startswith(("ciu.", "com.docker.")):
        raise ValueError(f"[CIU-141] label key {key!r} is in a reserved namespace")
    return key, label_value


def validate_env_name(name: str) -> str:
    """Validate the name-only environment forwarding form."""
    if not isinstance(name, str) or not _ENV_NAME_RE.fullmatch(name):
        raise ValueError(f"[CIU-141] invalid environment variable name {name!r}")
    return name


def _secret_values(values: Sequence[str] | set[str] | frozenset[str]) -> tuple[str, ...]:
    return tuple(value for value in values if isinstance(value, str) and value)


def _reject_secret_text(value: Any, secret_values: Sequence[str], *, where: str) -> None:
    if isinstance(value, str) and any(secret in value for secret in secret_values):
        raise ValueError(f"[CIU-141] {where} contains a CIU secret value")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_secret_text(key, secret_values, where=where)
            _reject_secret_text(item, secret_values, where=where)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_secret_text(item, secret_values, where=where)


def _compose_labels(value: Any, *, where: str) -> dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        pairs = value.items()
    elif isinstance(value, list):
        parsed: list[tuple[str, str]] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError(f"[CIU-141] {where} labels must be strings")
            key, separator, label_value = item.partition("=")
            parsed.append((key, label_value if separator else ""))
        pairs = parsed
    else:
        raise ValueError(f"[CIU-141] {where} labels must be a map or list")
    labels: dict[str, str] = {}
    for key, item in pairs:
        if not isinstance(key, str) or not _LABEL_KEY_RE.fullmatch(key):
            raise ValueError(f"[CIU-141] {where} has an invalid label key")
        if not isinstance(item, (str, int, float)) or isinstance(item, bool):
            raise ValueError(f"[CIU-141] {where} label values must be scalar strings")
        labels[key] = str(item)
    return labels


def _volume_target(volume: Any, *, where: str) -> str:
    if isinstance(volume, Mapping):
        target = volume.get("target")
    elif isinstance(volume, str):
        # Short form SRC:TARGET[:MODE] (or a bare anonymous TARGET).
        parts = volume.split(":")
        target = parts[1] if len(parts) > 1 else parts[0]
    else:
        raise ValueError(f"[CIU-141] {where} volume must be long-form")
    if not isinstance(target, str) or not target.startswith("/"):
        raise ValueError(f"[CIU-141] {where} volume target must be an absolute path")
    if ".." in PurePosixPath(target).parts:
        raise ValueError(f"[CIU-141] {where} volume target may not contain '..'")
    # Collision checks compare the path Docker will mount, not its spelling:
    # collapse '//', '/./' and trailing '/' (normpath keeps a leading '//').
    return "/" + posixpath.normpath(target).lstrip("/")


def _bind_source_refusal(source: str) -> str | None:
    """Return why a bind source is forbidden, or None when it is acceptable."""
    candidates = {"/" + posixpath.normpath(source).lstrip("/")}
    # A symlinked source resolves to wherever it points, even when it does not
    # exist yet (non-strict realpath); judge both spellings.
    candidates.add(os.path.realpath(source))
    for candidate in sorted(candidates):
        if candidate == "/":
            return "the host root"
        if posixpath.basename(candidate) == "docker.sock":
            return "a Docker socket"
        for socket_path in _DOCKER_SOCKETS:
            if candidate == socket_path or socket_path.startswith(candidate + "/"):
                return "a Docker socket or one of its parent directories"
    return None


def _base_environment_keys(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        return {str(key) for key in value}
    if isinstance(value, list):
        return {
            item.split("=", 1)[0] for item in value
            if isinstance(item, str)
        }
    return set()


def validate_extension_document(
    document: Any,
    *,
    service_name: str,
    base_service: Mapping[str, Any],
    secret_values: Sequence[str] | set[str] | frozenset[str],
) -> dict[str, Any]:
    """Validate and normalize the narrowly typed per-run Compose fragment."""
    secrets = _secret_values(secret_values)
    _reject_secret_text(document, secrets, where="--extend fragment")
    if not isinstance(document, Mapping):
        raise ValueError("[CIU-141] --extend must be a YAML mapping")
    unknown_root = set(document) - _ALLOWED_EXTENSION_ROOT_KEYS
    if unknown_root:
        key = min(str(item) for item in unknown_root)
        raise ValueError(f"[CIU-141] --extend refuses top-level key {key!r}")
    services = document.get("services")
    if not isinstance(services, Mapping) or set(services) != {service_name}:
        raise ValueError(
            f"[CIU-141] --extend must define exactly services.{service_name}"
        )
    service = services[service_name]
    if not isinstance(service, Mapping):
        raise ValueError(f"[CIU-141] --extend services.{service_name} must be a mapping")
    service = dict(service)
    unknown = set(service) - _ALLOWED_EXTENSION_KEYS
    if unknown:
        key = min(str(item) for item in unknown)
        raise ValueError(f"[CIU-141] --extend refuses service key {key!r}")

    for key in (*_LIMIT_KEYS, "init", *_APPEND_EXTENSION_KEYS):
        if key in service and key in base_service:
            raise ValueError(
                f"[CIU-141] --extend {key} collides with the rendered service value; "
                "remove the extension value or the service declaration"
            )
    if "init" in service and not isinstance(service["init"], bool):
        raise ValueError("[CIU-141] --extend init must be a boolean")

    if "group_add" in service:
        group_add = service["group_add"]
        if not isinstance(group_add, list) or any(
            isinstance(gid, bool)
            or not (isinstance(gid, int) or (isinstance(gid, str) and gid.isdecimal()))
            or int(gid) < 0
            for gid in group_add
        ):
            raise ValueError("[CIU-141] --extend group_add must be a list of numeric gids")
        service["group_add"] = [str(gid) for gid in group_add]

    if "volumes" in service:
        volumes = service["volumes"]
        if not isinstance(volumes, list):
            raise ValueError("[CIU-141] --extend volumes must be a list of long-form mounts")
        base_targets = {
            _volume_target(volume, where="rendered service")
            for volume in (base_service.get("volumes") or [])
        }
        extension_targets: set[str] = set()
        normalized_volumes: list[dict[str, Any]] = []
        for volume in volumes:
            if not isinstance(volume, Mapping):
                raise ValueError("[CIU-141] --extend volumes must use long-form mappings")
            kind = volume.get("type")
            if kind not in {"bind", "volume"}:
                raise ValueError("[CIU-141] --extend volume type must be 'bind' or 'volume'")
            target = _volume_target(volume, where="--extend")
            if target in base_targets or target in extension_targets:
                raise ValueError(
                    f"[CIU-141] --extend volume target {target!r} collides with another mount"
                )
            extension_targets.add(target)
            source = volume.get("source")
            if not isinstance(source, str) or not source:
                raise ValueError("[CIU-141] --extend volume source must be a non-empty path/name")
            if kind == "bind":
                if (
                    not PurePosixPath(source).is_absolute()
                    or ".." in PurePosixPath(source).parts
                ):
                    raise ValueError(
                        "[CIU-141] --extend bind source must be absolute and "
                        "may not contain '..'"
                    )
                refusal = _bind_source_refusal(source)
                if refusal is not None:
                    raise ValueError(
                        f"[CIU-141] --extend bind source {source!r} is {refusal}"
                    )
                source = "/" + posixpath.normpath(source).lstrip("/")
            if kind == "volume" and ("/" in source or source in {".", ".."}):
                raise ValueError("[CIU-141] --extend named volume source must be a volume name")
            read_only = volume.get("read_only", False)
            if not isinstance(read_only, bool):
                raise ValueError("[CIU-141] --extend read_only must be a boolean")
            normalized = {"type": kind, "source": source, "target": target}
            if read_only:
                normalized["read_only"] = True
            if kind == "bind":
                bind = volume.get("bind", {})
                if not isinstance(bind, Mapping):
                    raise ValueError("[CIU-141] --extend bind options must be a mapping")
                if set(bind) - {"create_host_path"}:
                    key = min(str(item) for item in set(bind) - {"create_host_path"})
                    raise ValueError(f"[CIU-141] --extend refuses bind option {key!r}")
                if bind.get("create_host_path", False) is not False:
                    raise ValueError(
                        "[CIU-141] --extend bind create_host_path must be false"
                    )
                # A missing bind source must fail at the daemon. Never let
                # Compose invent an empty host directory for a typo.
                normalized["bind"] = {"create_host_path": False}
            normalized_volumes.append(normalized)
        service = dict(service)
        service["volumes"] = normalized_volumes

    if "environment" in service:
        environment = service["environment"]
        if not isinstance(environment, Mapping):
            raise ValueError("[CIU-141] --extend environment must be a map of literal values")
        collisions = set(environment) & _base_environment_keys(base_service.get("environment"))
        if collisions:
            name = min(str(item) for item in collisions)
            raise ValueError(
                f"[CIU-141] --extend environment {name!r} collides with the rendered service"
            )
        for key, value in environment.items():
            if not isinstance(key, str) or not _ENV_NAME_RE.fullmatch(key):
                raise ValueError("[CIU-141] --extend environment keys must be variable names")
            if not isinstance(value, str) or "$" in value:
                raise ValueError(
                    "[CIU-141] --extend environment values must be literal strings"
                )

    if "labels" in service:
        labels = _compose_labels(service["labels"], where="--extend")
        for key, value in labels.items():
            if key.startswith(("ciu.", "com.docker.")):
                raise ValueError(f"[CIU-141] --extend label {key!r} is reserved")
            if "$" in value:
                raise ValueError("[CIU-141] --extend labels must have literal values")
        service = dict(service)
        service["labels"] = labels

    run_metadata = document.get("x-ciu-run", {})
    if not isinstance(run_metadata, Mapping) or set(run_metadata) - {"observer-socket-dir"}:
        raise ValueError("[CIU-141] --extend x-ciu-run only accepts observer-socket-dir")
    observer_dir = run_metadata.get("observer-socket-dir")
    if observer_dir is not None:
        if (
            not isinstance(observer_dir, str)
            or not PurePosixPath(observer_dir).is_absolute()
            or ".." in PurePosixPath(observer_dir).parts
        ):
            raise ValueError("[CIU-141] observer-socket-dir must be an absolute path")
        observer_dir = "/" + posixpath.normpath(observer_dir).lstrip("/")
        matching_mounts = [
            volume for volume in service.get("volumes", [])
            if volume.get("type") == "bind" and volume.get("source") == observer_dir
        ]
        if len(matching_mounts) != 1 or not matching_mounts[0].get("read_only"):
            raise ValueError(
                "[CIU-141] observer socket directory requires one matching read-only bind"
            )

    normalized_document = copy.deepcopy(dict(document))
    normalized_document["services"] = {service_name: dict(service)}
    if run_metadata:
        normalized_document["x-ciu-run"] = dict(run_metadata)
    return normalized_document


def _read_extension(
    path: Path,
    *,
    service_name: str,
    base_service: Mapping[str, Any],
    secret_values: Sequence[str] | set[str] | frozenset[str],
) -> tuple[Path, dict[str, Any]]:
    extension_path = Path(path).expanduser().resolve()
    if not extension_path.is_file():
        raise ValueError(f"[CIU-141] --extend file does not exist: {extension_path}")
    try:
        text = extension_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"[CIU-141] cannot read --extend file {extension_path}") from exc
    secrets = _secret_values(secret_values)
    if any(secret in text for secret in secrets):
        raise ValueError("[CIU-141] --extend file contains a CIU secret value")
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"[CIU-141] --extend file is not valid YAML: {extension_path}") from exc
    return extension_path, validate_extension_document(
        document, service_name=service_name, base_service=base_service,
        secret_values=secrets,
    )


def _compose_config(prepared: Any, *, extension_path: Path | None = None) -> dict[str, Any]:
    command = [
        "docker", "compose", "--progress", "quiet", "-p", prepared.project,
        "--project-directory", str(Path(prepared.repo_root).resolve()),
        *prepared.file_args,
    ]
    if extension_path is not None:
        command += ["-f", str(extension_path)]
    command += ["config", "--format", "json"]
    try:
        result = procutil.run_cmd(
            command, check=False, capture=True, env=prepared.compose_env,
            cwd=str(prepared.working_dir),
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "[CIU-141] Docker is unavailable while resolving the rendered Compose service; "
            "install the Docker CLI and make its daemon reachable"
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(
            "[CIU-141] docker compose config --format json failed "
            f"(exit {result.returncode}); no one-shot container was started"
        )
    try:
        document = json.loads(result.stdout or "")
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("[CIU-141] Docker Compose returned invalid JSON config") from exc
    if not isinstance(document, dict) or not isinstance(document.get("services"), dict):
        raise RuntimeError("[CIU-141] Docker Compose config has no services mapping")
    return document


def _service_from_config(document: Mapping[str, Any], service_name: str) -> dict[str, Any]:
    services = document.get("services")
    if not isinstance(services, Mapping):
        raise ValueError("[CIU-141] rendered Compose config has no services")
    service = services.get(service_name)
    if not isinstance(service, dict):
        raise ValueError(f"[CIU-141] service {service_name!r} is not present in rendered Compose")
    return service


def build_run_plan(
    prepared: Any,
    *,
    stack: str,
    service: str,
    name: str | None = None,
    extend: Path | str | None = None,
    labels: Sequence[str] = (),
    skip_hooks: bool = False,
) -> dict[str, Any]:
    """Resolve Compose's effective service and project only safe plan fields."""
    if name is not None:
        validate_container_name(name)
    user_labels = dict(parse_run_label(item) for item in labels)
    if len(user_labels) != len(labels):
        raise ValueError("[CIU-141] --label keys must be unique")

    base_document = _compose_config(prepared)
    base_service = _service_from_config(base_document, service)
    base_labels = _compose_labels(base_service.get("labels"), where="rendered service")
    reserved_service_labels = [
        key for key in base_labels if key == "ciu.run" or key.startswith("ciu.run.")
    ]
    if reserved_service_labels:
        key = min(reserved_service_labels)
        raise ValueError(f"[CIU-141] rendered service label {key!r} is reserved")

    extension_accepted = False
    if extend is not None:
        _source_path, validated = _read_extension(
            Path(extend), service_name=service, base_service=base_service,
            secret_values=prepared.secret_values,
        )
        extension_accepted = True
        with tempfile.TemporaryDirectory(prefix="ciu-run-plan-") as temp_dir:
            normalized_path = Path(temp_dir) / "extend.yml"
            normalized_path.write_text(
                yaml.safe_dump(validated, sort_keys=True), encoding="utf-8"
            )
            final_document = _compose_config(prepared, extension_path=normalized_path)
    else:
        final_document = base_document
    selected = _service_from_config(final_document, service)

    networks = selected.get("networks") or {}
    if isinstance(networks, Mapping):
        network_names = sorted(str(key) for key in networks)
    elif isinstance(networks, list):
        network_names = sorted(str(item) for item in networks)
    else:
        raise ValueError("[CIU-141] rendered service networks have an invalid shape")
    mounts = []
    for volume in selected.get("volumes") or []:
        if not isinstance(volume, Mapping):
            raise ValueError("[CIU-141] rendered service volume has an invalid shape")
        mount = {
            "type": volume.get("type"), "source": volume.get("source"),
            "target": volume.get("target"), "ro": bool(volume.get("read_only", False)),
        }
        _reject_secret_text(mount, _secret_values(prepared.secret_values), where="run plan")
        mounts.append(mount)

    limits = {
        key: selected[key] for key in _LIMIT_KEYS if selected.get(key) is not None
    }
    final_labels = _compose_labels(selected.get("labels"), where="rendered service")
    final_labels.update(user_labels)
    for value in (
        selected.get("image"), selected.get("user"), selected.get("working_dir"),
        selected.get("cgroup_parent"), final_labels, limits, network_names,
    ):
        _reject_secret_text(value, _secret_values(prepared.secret_values), where="run plan")

    plan = {
        "schema": "ciu-run-plan/1",
        "project": prepared.project,
        "stack": stack,
        "service": service,
        "image": selected.get("image"),
        "user": selected.get("user"),
        "working_dir": selected.get("working_dir"),
        "cgroup_parent": selected.get("cgroup_parent"),
        "networks": network_names,
        "mounts": mounts,
        "limits": limits,
        "labels": dict(sorted(final_labels.items())),
        "name": name,
        "extend_accepted": extension_accepted,
        "hooks": [] if skip_hooks else ["pre_secrets", "pre_compose"],
    }
    _reject_secret_text(plan, _secret_values(prepared.secret_values), where="run plan")
    return plan


def _dry_run_document(
    plan: Mapping[str, Any], *, prepared: Any, name: str, name_given: bool,
    runtime_labels: Mapping[str, str], env_names: Sequence[str], mode: str,
    argv: Sequence[str], owner_pid: int,
) -> dict[str, Any]:
    """The plan plus what a real run would add: name, runtime labels, env.

    Values that only exist at launch (run ID, start time) are placeholders;
    forwarded environment variables are listed by NAME only, never by value.
    """
    shown_labels = {
        key: value for key, value in runtime_labels.items() if key.startswith("ciu.")
    }
    shown_labels["ciu.run.id"] = _PLACEHOLDER_AT_LAUNCH
    shown_labels["ciu.run.started"] = _PLACEHOLDER_AT_LAUNCH
    injected = {
        "CIU_RUN_CONTAINER_NAME": name,
        "CIU_RUN_ID": _PLACEHOLDER_AT_LAUNCH,
    }
    if plan.get("cgroup_parent") is not None:
        injected["CIU_RUN_CGROUP_PARENT"] = plan["cgroup_parent"]
    document = dict(plan)
    document.update({
        "name": name,
        "name_generated": not name_given,
        "name_available": True,
        "networks_verified": True,
        "mode": mode,
        "owner_pid": owner_pid,
        "argv_len": len(argv),
        "runtime_labels": dict(sorted(shown_labels.items())),
        "env": {"forwarded_names": list(env_names), "injected": injected},
    })
    _reject_secret_text(document, _secret_values(prepared.secret_values), where="run plan")
    return document


def _prepare_run_stack(
    repo_root: Path,
    selector: str,
    *,
    profiles: Sequence[str] | None,
    skip_hooks: bool,
) -> tuple[Any, str, str]:
    """Render the selected service with the same profile context as ``ciu up``."""
    from . import deploy, engine

    stack, requested_service = deploy.split_service_selector(selector)
    identity_document = deploy.resolve_identities(
        repo_root, stack=stack, service=requested_service,
        profiles=list(profiles) if profiles is not None else None,
    )
    identities = identity_document["resolved"]["identities"]
    stack_path, service, _identity = deploy.select_service(identities, requested_service)

    global_config = deploy.load_global_config(repo_root, write_rendered=False)
    profile = deploy.resolve_profiles(
        global_config, list(profiles) if profiles is not None else None,
    )
    selection = deploy.build_selection(profile)
    ciu_context = deploy.profiles_pkg.render_ciu_context(profile, selection)
    stack_dir = Path(repo_root) / stack_path
    with _stack_prepare_lock(stack_dir):
        prepared = engine.prepare_stack(
            stack_dir,
            define_root=Path(repo_root),
            compose_profiles=profile.compose_profiles,
            ciu_context=ciu_context,
            skip_hooks=skip_hooks,
        )
    return prepared, stack_path, service


@contextmanager
def _stack_prepare_lock(stack_dir: Path):
    """Serialize only the shared render/secret preparation for one stack."""
    machine_dir = Path(stack_dir) / ".machine"
    if machine_dir.is_symlink():
        raise ValueError(f"[CIU-141] refusing symlinked preparation directory {machine_dir}")
    machine_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = machine_dir / ".ciu-run.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise RuntimeError(f"[CIU-141] cannot open preparation lock {lock_path}") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"[CIU-141] preparation lock is not a regular file: {lock_path}")
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _owner_identity(pid: int) -> dict[str, str]:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1:
        raise ValueError("[CIU-141] owner PID must be a positive integer")
    proc = Path("/proc") / str(pid)
    try:
        stat_line = (proc / "stat").read_text(encoding="ascii")
        fields = stat_line.rpartition(")")[2].split()
        start_ticks = int(fields[19])
        pid_namespace = str((proc / "ns/pid").stat().st_ino)
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError, IndexError, ValueError) as exc:
        raise ValueError(
            f"[CIU-141] cannot read the liveness identity for owner PID {pid}"
        ) from exc
    if not boot_id:
        raise ValueError("[CIU-141] host boot ID is unavailable for run ownership")
    return {
        "pid": str(pid), "start": str(start_ticks),
        "boot": boot_id, "pidns": pid_namespace,
    }


class _EventWriter:
    """Write JSONL only to the requested inherited fd or 0600 append file."""

    def __init__(self, *, fd: int | None = None, path: str | Path | None = None):
        if fd is not None and path is not None:
            raise ValueError("[CIU-141] choose one event sink: --events-fd or --events-file")
        self._owned_fd = False
        self.failed = False
        if fd is not None:
            if isinstance(fd, bool) or fd < 3:
                raise ValueError("[CIU-141] --events-fd must be 3 or greater")
            try:
                mode = fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE
                os.fstat(fd)
            except OSError as exc:
                raise ValueError(f"[CIU-141] --events-fd {fd} is not open") from exc
            if mode == os.O_RDONLY:
                raise ValueError(f"[CIU-141] --events-fd {fd} is not writable")
            self.fd = fd
        elif path is not None:
            event_path = Path(path).expanduser()
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            try:
                self.fd = os.open(event_path, flags, 0o600)
                self._owned_fd = True
                info = os.fstat(self.fd)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("event file must be a regular file")
                os.fchmod(self.fd, 0o600)
            except (OSError, ValueError) as exc:
                if self._owned_fd:
                    os.close(self.fd)
                raise ValueError(f"[CIU-141] cannot open event file {event_path}") from exc
        else:
            self.fd = None

    def write(self, event: Mapping[str, Any]) -> None:
        """Write one JSONL event. A broken sink never kills the user's command.

        The first write failure (EPIPE on a closed observer pipe, ENOSPC, ...)
        is reported once on stderr; the sink is then marked dead and later
        events are dropped while the run continues.
        """
        if self.fd is None or self.failed:
            return
        payload = (json.dumps(dict(event), sort_keys=True, separators=(",", ":")) + "\n").encode()
        view = memoryview(payload)
        try:
            while view:
                written = os.write(self.fd, view)
                if written <= 0:
                    raise OSError("short write to CIU run event sink")
                view = view[written:]
        except OSError as exc:
            self.failed = True
            print(
                f"[CIU-141] event sink write failed ({exc}); further run events "
                "are dropped, the command keeps running",
                file=sys.stderr, flush=True,
            )

    def close(self) -> None:
        if self._owned_fd:
            os.close(self.fd)
            self._owned_fd = False


def _compose_file_args(prepared: Any, *, temp_dir: Path, extend: str | Path | None,
                       service_name: str) -> list[str]:
    """Copy mutable ownership/extension files to a private per-run directory."""
    file_args = list(prepared.file_args)
    ownership_path = getattr(prepared, "ownership_path", None)
    if ownership_path is not None:
        ownership = Path(ownership_path)
        try:
            content = ownership.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(
                f"[CIU-141] cannot read prepared ownership overlay {ownership}"
            ) from exc
        private_owner = temp_dir / "ownership.yml"
        private_owner.write_text(content, encoding="utf-8")
        replaced = False
        for index in range(len(file_args) - 1):
            if file_args[index] != "-f":
                continue
            candidate = Path(file_args[index + 1])
            if not candidate.is_absolute():
                candidate = Path(prepared.working_dir) / candidate
            if candidate.resolve() == ownership.resolve():
                file_args[index + 1] = str(private_owner)
                replaced = True
                break
        if not replaced:
            file_args.extend(["-f", str(private_owner)])

    if extend is not None:
        base_document = _compose_config(prepared)
        base_service = _service_from_config(base_document, service_name)
        _source, validated = _read_extension(
            Path(extend), service_name=service_name, base_service=base_service,
            secret_values=prepared.secret_values,
        )
        extension_path = temp_dir / "extend.yml"
        extension_path.write_text(yaml.safe_dump(validated, sort_keys=True), encoding="utf-8")
        file_args.extend(["-f", str(extension_path)])
    return file_args


def _compose_prefix(prepared: Any, file_args: Sequence[str]) -> list[str]:
    return [
        "docker", "compose", "--progress", "quiet", "-p", prepared.project,
        "--project-directory", str(Path(prepared.repo_root).resolve()), *file_args,
    ]


def _docker_inspect(ref: str) -> dict[str, Any] | None:
    """Inspect one CONTAINER by exact name or full ID; never another object.

    ``--type container`` keeps a network/volume/image of the same name from
    answering. A full 64-hex reference must return that exact ID; any other
    reference must return that exact name (hex-lookalike names are refused at
    validation, so a name can never resolve as another container's ID prefix).
    """
    by_id = bool(_CONTAINER_ID_RE.fullmatch(ref))
    try:
        result = procutil.run_cmd(
            ["docker", "inspect", "--type", "container", ref],
            check=False, capture=True,
        )
    except OSError as exc:
        raise RuntimeError("[CIU-141] Docker became unavailable while inspecting the one-shot") from exc
    if result.returncode != 0:
        return None
    try:
        document = json.loads(result.stdout or "")
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("[CIU-141] Docker inspect returned invalid JSON") from exc
    if isinstance(document, list):
        if len(document) != 1 or not isinstance(document[0], dict):
            raise RuntimeError("[CIU-141] Docker inspect did not identify one exact container")
        document = document[0]
    if not isinstance(document, dict):
        raise RuntimeError("[CIU-141] Docker inspect returned an invalid container object")
    container_id = document.get("Id")
    if not isinstance(container_id, str) or not _CONTAINER_ID_RE.fullmatch(container_id):
        raise RuntimeError("[CIU-141] Docker inspect did not return a full 64-hex container ID")
    if by_id:
        if container_id != ref:
            raise RuntimeError(
                f"[CIU-141] Docker inspect for container {ref} returned {container_id}"
            )
        return document
    actual_name = str(document.get("Name", "")).lstrip("/")
    if actual_name != ref:
        raise RuntimeError(
            f"[CIU-141] Docker inspect for exact name {ref!r} returned {actual_name!r}"
        )
    return document


def _ensure_name_free(name: str) -> None:
    """Refuse (touching nothing) when any container already holds ``name``."""
    existing = _docker_inspect(name)
    if existing is not None:
        raise RuntimeError(
            f"[CIU-141] container name {name!r} is already in use by container "
            f"{str(existing['Id'])[:12]}; ciu run never removes a container it did not "
            "create. Choose another --name or remove it yourself"
        )


def _owned_container_ids(run_id: str) -> list[str]:
    """Full IDs of containers carrying THIS run's ``ciu.run.id`` label."""
    try:
        listing = procutil.run_cmd(
            [
                "docker", "ps", "-a", "--no-trunc",
                "--filter", f"label={_RUN_ID_LABEL}={run_id}",
                "--format", "{{.ID}}",
            ],
            check=False, capture=True,
        )
    except OSError as exc:
        raise RuntimeError("[CIU-141] Docker unavailable while locating the one-shot") from exc
    if listing.returncode != 0:
        raise RuntimeError("[CIU-141] Docker failed while locating the one-shot by run ID")
    ids = [line.strip() for line in (listing.stdout or "").splitlines() if line.strip()]
    if any(not _CONTAINER_ID_RE.fullmatch(item) for item in ids):
        raise RuntimeError("[CIU-141] Docker did not return full container IDs for the run")
    return sorted(set(ids))


def _remove_owned_container(run_id: str, *, candidate_id: str | None = None) -> bool:
    """Force-remove only containers PROVEN to be this run's, by full ID.

    Candidates are the label query for ``ciu.run.id=<run_id>`` plus an optional
    ID the caller observed. Every candidate is re-inspected by its exact full
    ID and removed only if its ``ciu.run.id`` label is this run's. Anything
    else (including a foreign container holding the same NAME) is never
    touched. Returns False when ownership could not be established or a
    removal failed; True when no owned container remains.
    """
    try:
        candidates = set(_owned_container_ids(run_id))
    except RuntimeError:
        candidates = set()
        listing_ok = False
    else:
        listing_ok = True
    if candidate_id is not None:
        candidates.add(candidate_id)
    clean = listing_ok
    for container_id in sorted(candidates):
        try:
            document = _docker_inspect(container_id)
        except RuntimeError:
            clean = False
            continue
        if document is None:
            continue  # already gone
        try:
            labels = _event_labels(document)
        except RuntimeError:
            clean = False
            continue
        if labels.get(_RUN_ID_LABEL) != run_id:
            continue  # not ours: leave it alone
        if not _run_docker_rm(container_id):
            clean = False
    return clean


def _container_started(document: Mapping[str, Any]) -> bool:
    """True once the container's process was actually started.

    A container that was only created (or whose start failed) reports
    ``Status: created`` and a zero ``StartedAt``; it is not "launched".
    """
    state = document.get("State") or {}
    if not isinstance(state, Mapping):
        return False
    if state.get("Status") in {"running", "paused", "restarting"}:
        return True
    started_at = state.get("StartedAt")
    return (
        isinstance(started_at, str) and bool(started_at)
        and not started_at.startswith("0001-01-01")
    )


def _event_labels(document: Mapping[str, Any]) -> dict[str, str]:
    config = document.get("Config") or {}
    labels = config.get("Labels") if isinstance(config, Mapping) else {}
    if labels is None:  # Docker reports a label-less container as null or {}
        labels = {}
    if not isinstance(labels, Mapping):
        raise RuntimeError("[CIU-141] inspected container labels have an invalid shape")
    return {str(key): str(value) for key, value in labels.items()}


def _launched_event(
    document: Mapping[str, Any], *, run_id: str, name: str, prepared: Any,
    stack: str, service: str, plan: Mapping[str, Any],
) -> dict[str, Any]:
    config = document.get("Config") or {}
    host_config = document.get("HostConfig") or {}
    network_settings = document.get("NetworkSettings") or {}
    networks = network_settings.get("Networks") or {}
    if not all(isinstance(value, Mapping) for value in (config, host_config, networks)):
        raise RuntimeError("[CIU-141] inspected container configuration has an invalid shape")
    return {
        "schema": _RUN_EVENT_SCHEMA,
        "event": "launched",
        "run_id": run_id,
        "container": {"id": document["Id"], "name": name},
        "project": prepared.project,
        "stack": stack,
        "service": service,
        "image": config.get("Image") or plan.get("image"),
        "image_id": document.get("Image"),
        "user": config.get("User"),
        "cgroup_parent": host_config.get("CgroupParent"),
        "cgroupns": host_config.get("CgroupnsMode"),
        "networks": sorted(str(network) for network in networks),
        "labels": _event_labels(document),
        "at": _utc_now(),
    }


def _make_runtime_labels(
    *, run_id: str, stack: str, service: str, mode: str, owner_pid: int,
) -> dict[str, str]:
    owner = _owner_identity(owner_pid)
    return {
        "ciu.run": "1",
        "ciu.run.id": run_id,
        "ciu.run.stack": stack,
        "ciu.run.service": service,
        "ciu.run.owner-pid": owner["pid"],
        "ciu.run.owner-start": owner["start"],
        "ciu.run.boot-id": owner["boot"],
        "ciu.run.pidns": owner["pidns"],
        "ciu.run.mode": mode,
        "ciu.run.started": _utc_now(),
    }


def _select_run_name(project: str, service: str, name: str | None) -> str:
    if name is not None:
        return validate_container_name(name)
    generated = f"{project}-{service}-run-{uuid.uuid4().hex[:12]}"
    if not _CONTAINER_NAME_RE.fullmatch(generated):
        raise ValueError(
            "[CIU-141] generated container name is too long; pass a shorter --name"
        )
    return generated


def _verify_run_networks(prepared: Any, service_name: str) -> None:
    from . import engine

    try:
        engine.verify_workspace_runtime_network(Path(prepared.repo_root))
    except Exception as exc:
        if isinstance(exc, (ValueError, RuntimeError)):
            raise
        raise RuntimeError("[CIU-141] could not verify managed instance network ownership") from exc

    document = _compose_config(prepared)
    service = _service_from_config(document, service_name)
    declared = service.get("networks") or {}
    if not isinstance(declared, (Mapping, list)):
        raise ValueError("[CIU-141] rendered service network declaration is invalid")
    network_keys = sorted(str(key) for key in declared)
    network_config = document.get("networks") or {}
    for key in network_keys:
        details = network_config.get(key) if isinstance(network_config, Mapping) else None
        if not isinstance(details, Mapping) or not isinstance(details.get("name"), str):
            raise ValueError(
                f"[CIU-141] cannot prove the resolved Docker network name for {key!r}"
            )
        network_name = details["name"]
        try:
            inspected = procutil.run_cmd(
                ["docker", "network", "inspect", network_name],
                check=False, capture=True,
            )
        except OSError as exc:
            raise RuntimeError("[CIU-141] Docker unavailable while checking the stack network") from exc
        if inspected.returncode != 0:
            raise ValueError(
                f"[CIU-141] required stack network {network_name!r} is not present; "
                f"run `ciu up --dir {Path(prepared.working_dir).relative_to(prepared.repo_root)}` first"
            )
        try:
            result = json.loads(inspected.stdout or "")
            if isinstance(result, list):
                if len(result) != 1 or not isinstance(result[0], dict):
                    raise ValueError("network inspect returned more than one result")
                result = result[0]
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"[CIU-141] Docker returned invalid network details for {network_name!r}"
            ) from exc
        if not isinstance(result, Mapping) or result.get("Name") != network_name:
            raise RuntimeError(
                f"[CIU-141] Docker network inspect did not prove exact network {network_name!r}"
            )
        if not details.get("external", False):
            labels = result.get("Labels") or {}
            if not isinstance(labels, Mapping) or (
                labels.get("com.docker.compose.project") != prepared.project
                or labels.get("com.docker.compose.network") != key
            ):
                raise ValueError(
                    f"[CIU-141] network {network_name!r} is not owned by Compose project "
                    f"{prepared.project!r}; run `ciu up --dir "
                    f"{Path(prepared.working_dir).relative_to(prepared.repo_root)}` first"
                )


def _run_compose_argv(
    prepared: Any,
    *,
    file_args: Sequence[str],
    service: str,
    argv: Sequence[str],
    name: str,
    labels: Mapping[str, str],
    env_names: Sequence[str],
    run_id: str,
    cgroup_parent: str | None,
    detach: bool,
    interactive: bool,
) -> list[str]:
    command = [*_compose_prefix(prepared, file_args), "run"]
    if detach:
        command.append("-d")
    if detach or not interactive:
        command.extend(["-T", "--interactive=false"])
    command.extend(["--no-deps", "--name", name])
    for key, value in labels.items():
        command.extend(["-l", f"{key}={value}"])
    for env_name in env_names:
        command.extend(["-e", validate_env_name(env_name)])
    injected = {
        "CIU_RUN_CONTAINER_NAME": name,
        "CIU_RUN_ID": run_id,
    }
    if cgroup_parent is not None:
        injected["CIU_RUN_CGROUP_PARENT"] = cgroup_parent
    for key, value in injected.items():
        command.extend(["-e", f"{key}={value}"])
    command.append(service)
    command.extend(str(item) for item in argv)
    return command


def _run_docker_rm(name: str) -> bool:
    try:
        result = procutil.run_cmd(
            ["docker", "rm", "-f", name], check=False, capture=True,
        )
    except OSError:
        return False
    return result.returncode == 0


def _stop_compose_client(process: Any) -> None:
    """Make sure the Compose client of an aborted foreground run is gone."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=_SIGNAL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _run_detached(
    prepared: Any, *, plan: Mapping[str, Any], stack: str, service: str,
    argv: Sequence[str], name: str, run_id: str, labels: Mapping[str, str],
    env_names: Sequence[str], extend: str | Path | None,
) -> dict[str, Any]:
    _verify_run_networks(prepared, service)
    # Refuse before anything is created: a collision is never "cleaned up".
    _ensure_name_free(name)
    runtime_labels = labels
    with tempfile.TemporaryDirectory(prefix="ciu-run-") as temp_dir:
        file_args = _compose_file_args(
            prepared, temp_dir=Path(temp_dir), extend=extend, service_name=service,
        )
        command = _run_compose_argv(
            prepared, file_args=file_args, service=service, argv=argv, name=name,
            labels=runtime_labels, env_names=env_names, run_id=run_id,
            cgroup_parent=plan.get("cgroup_parent"), detach=True, interactive=False,
        )
        try:
            result = procutil.run_cmd(
                command, check=False, capture=True, env=prepared.compose_env,
                cwd=str(prepared.working_dir),
            )
        except OSError as exc:
            _remove_owned_container(run_id)
            raise RuntimeError("[CIU-141] Docker Compose could not start the one-shot") from exc
        # From here every removal is keyed on THIS run's ciu.run.id label (and
        # the exact full ID), never on the container name: a failed or
        # inconsistent launch must not be able to remove somebody else's
        # container that happens to hold the name.
        if result.returncode != 0:
            _remove_owned_container(run_id)
            raise RuntimeError(
                f"[CIU-141] docker compose run failed (exit {result.returncode}); "
                "no launch event was produced"
            )
        compose_id = (result.stdout or "").strip()
        if not _CONTAINER_ID_RE.fullmatch(compose_id):
            _remove_owned_container(run_id)
            raise RuntimeError(
                "[CIU-141] docker compose run -d did not return one full 64-hex container ID"
            )
        document = _docker_inspect(compose_id)
        if document is None:
            _remove_owned_container(run_id)
            raise RuntimeError(
                "[CIU-141] launched container is not inspectable by its full ID"
            )
        if str(document.get("Name", "")).lstrip("/") != name:
            _remove_owned_container(run_id, candidate_id=compose_id)
            raise RuntimeError(
                "[CIU-141] docker compose run ID disagrees with the requested container name"
            )
        if _event_labels(document).get(_RUN_ID_LABEL) != run_id:
            _remove_owned_container(run_id, candidate_id=compose_id)
            raise RuntimeError("[CIU-141] inspected container has a different ciu.run.id")
        if not _container_started(document):
            _remove_owned_container(run_id, candidate_id=compose_id)
            raise RuntimeError(
                "[CIU-141] docker compose run -d returned a container that was not started"
            )
    event = _launched_event(
        document, run_id=run_id, name=name, prepared=prepared,
        stack=stack, service=service, plan=plan,
    )
    return event


def _run_foreground(
    prepared: Any, *, plan: Mapping[str, Any], stack: str, service: str,
    argv: Sequence[str], name: str, run_id: str, labels: Mapping[str, str],
    env_names: Sequence[str], extend: str | Path | None,
    event_writer: _EventWriter,
) -> int:
    _verify_run_networks(prepared, service)
    # Refuse before anything is created: a collision is never "cleaned up".
    _ensure_name_free(name)
    launched: dict[str, Any] | None = None
    container_id: str | None = None
    pending_signal: list[int] = []
    forwarded_signal: int | None = None
    signal_deadline: float | None = None
    escalated = False
    process = None
    signal_handlers: dict[int, Any] = {}
    interactive = bool(
        getattr(sys.stdin, "isatty", lambda: False)()
        and getattr(sys.stdout, "isatty", lambda: False)()
    )

    def remember_signal(signum, _frame):
        # Only the first terminating signal is forwarded; later ones are
        # ignored (the documented grace/escalation then applies).
        if not pending_signal:
            pending_signal.append(signum)

    def forward_resize(_signum, _frame):
        # Compose runs in its own session (so a terminal signal cannot hit it
        # twice), which means the terminal no longer delivers SIGWINCH to it.
        # Relay the resize so an interactive container tracks the terminal.
        if process is not None and process.poll() is None:
            with suppress(OSError):
                os.kill(process.pid, signal.SIGWINCH)

    try:
        # SIGHUP (terminal closed) is forwarded like SIGINT/SIGTERM so the
        # container is stopped instead of orphaned when ciu itself would die.
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal_handlers[signum] = signal.signal(signum, remember_signal)
        if interactive:
            signal_handlers[signal.SIGWINCH] = signal.signal(
                signal.SIGWINCH, forward_resize,
            )
        with tempfile.TemporaryDirectory(prefix="ciu-run-") as temp_dir:
            file_args = _compose_file_args(
                prepared, temp_dir=Path(temp_dir), extend=extend, service_name=service,
            )
            command = _run_compose_argv(
                prepared, file_args=file_args, service=service, argv=argv, name=name,
                labels=labels, env_names=env_names, run_id=run_id,
                cgroup_parent=plan.get("cgroup_parent"), detach=False,
                interactive=interactive,
            )
            try:
                process = procutil.popen(
                    command, env=prepared.compose_env, cwd=str(prepared.working_dir),
                    start_new_session=True,
                )
            except OSError as exc:
                raise RuntimeError("[CIU-141] Docker Compose could not start the one-shot") from exc

            def observe_launch() -> None:
                """Emit ``launched`` once the container really started.

                ``launched`` means the container's process was started
                (running, or already exited with a real StartedAt). A
                container that is only created, or whose start failed, is not
                launched: no event, and the run ends as a failure.
                """
                nonlocal launched, container_id
                document = _docker_inspect(name)
                if document is None:
                    return
                if _event_labels(document).get(_RUN_ID_LABEL) != run_id:
                    raise RuntimeError(
                        "[CIU-141] exact-name container has a different ciu.run.id"
                    )
                if not _container_started(document):
                    return
                container_id = document["Id"]
                launched = _launched_event(
                    document, run_id=run_id, name=name, prepared=prepared,
                    stack=stack, service=service, plan=plan,
                )
                event_writer.write(launched)

            launch_deadline = time.monotonic() + _FOREGROUND_INSPECT_TIMEOUT
            while True:
                if launched is None:
                    observe_launch()

                if pending_signal and forwarded_signal is None and launched is not None:
                    forwarded_signal = pending_signal[0]
                    signal_name = signal.Signals(forwarded_signal).name
                    # Kill by the full ID of OUR container, never by name.
                    try:
                        sent = procutil.run_cmd(
                            ["docker", "kill", "-s", signal_name, container_id],
                            check=False, capture=True,
                        )
                    except OSError as exc:
                        raise RuntimeError(
                            f"[CIU-141] could not forward {signal_name} to container "
                            f"{container_id}"
                        ) from exc
                    if sent.returncode != 0:
                        raise RuntimeError(
                            f"[CIU-141] docker kill -s {signal_name} failed for {container_id}"
                        )
                    signal_deadline = time.monotonic() + _SIGNAL_GRACE_SECONDS

                returncode = process.poll()
                if returncode is not None:
                    # A short command can exit before the first inspect query.
                    # The no- --rm foreground command stays inspectable, so emit
                    # launched before collecting the terminal state.
                    if launched is None:
                        observe_launch()
                    break
                if pending_signal and launched is None and time.monotonic() >= launch_deadline:
                    raise RuntimeError(
                        "[CIU-141] interrupted before the one-shot container became inspectable"
                    )
                if launched is None and time.monotonic() >= launch_deadline:
                    raise RuntimeError(
                        "[CIU-141] foreground one-shot did not start within 30 seconds"
                    )
                if (
                    forwarded_signal is not None and signal_deadline is not None
                    and not escalated and time.monotonic() >= signal_deadline
                ):
                    try:
                        killed = procutil.run_cmd(
                            ["docker", "kill", container_id], check=False, capture=True,
                        )
                    except OSError as exc:
                        raise RuntimeError(
                            f"[CIU-141] could not escalate termination of {container_id}"
                        ) from exc
                    if killed.returncode != 0:
                        raise RuntimeError(f"[CIU-141] docker kill failed for {container_id}")
                    escalated = True
                time.sleep(_FOREGROUND_INSPECT_INTERVAL)

            if launched is None:
                raise RuntimeError(
                    f"[CIU-141] docker compose run failed (exit {returncode}); "
                    "no started container or launch event was produced"
                )
            final = _docker_inspect(container_id)
            if final is None:
                raise RuntimeError(
                    f"[CIU-141] container {name!r} disappeared before exit status collection"
                )
            state = final.get("State") or {}
            if not isinstance(state, Mapping) or state.get("Status") != "exited":
                raise RuntimeError(
                    f"[CIU-141] container {name!r} did not have an exited state after Compose returned"
                )
            inspected_code = state.get("ExitCode")
            if isinstance(inspected_code, bool) or not isinstance(inspected_code, int):
                raise RuntimeError(f"[CIU-141] container {name!r} has no inspected exit code")
            if returncode != inspected_code:
                raise RuntimeError(
                    f"[CIU-141] Compose exit {returncode} disagrees with inspected container "
                    f"exit {inspected_code} for {name!r}"
                )
            exited = {
                "schema": _RUN_EVENT_SCHEMA,
                "event": "exited",
                "run_id": run_id,
                "exit_code": inspected_code,
                "oom_killed": bool(state.get("OOMKilled", False)),
                "finished_at": state.get("FinishedAt") or _utc_now(),
            }
            event_writer.write(exited)
            removed = _remove_owned_container(run_id, candidate_id=container_id)
            removed_event = {
                "schema": _RUN_EVENT_SCHEMA,
                "event": "removed",
                "run_id": run_id,
                "removed": removed,
                "error": None if removed else "docker rm -f failed",
            }
            event_writer.write(removed_event)
            if not removed and inspected_code == 0:
                return 1
            return inspected_code
    except BaseException:
        # A foreground controller owns cleanup if an exception interrupts its
        # lifecycle once Compose was started. Removal is keyed on this run's
        # ciu.run.id label and the exact full ID, never on the name; a
        # container this run did not create is never touched.
        if process is not None:
            _remove_owned_container(run_id, candidate_id=container_id)
            _stop_compose_client(process)
        raise
    finally:
        for signum, previous in signal_handlers.items():
            signal.signal(signum, previous)


def _reap_owner_state(
    labels: Mapping[str, str], *, proc_root: Path | None = None,
) -> tuple[str, str]:
    """Return dead/alive/unknown from the same pid+start+boot+pidns identity."""
    proc = Path(proc_root or _PROC_ROOT)
    try:
        owner_pid = int(labels["ciu.run.owner-pid"])
        owner_start = int(labels["ciu.run.owner-start"])
        owner_pidns = int(labels["ciu.run.pidns"])
        owner_boot = labels["ciu.run.boot-id"]
    except (KeyError, TypeError, ValueError):
        return "unknown", "owner identity labels are missing or malformed"
    if owner_pid < 1 or owner_start < 0 or owner_pidns < 1 or not owner_boot:
        return "unknown", "owner identity labels are outside their valid range"
    try:
        current_boot = (proc / "sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return "unknown", "host boot identity is unavailable"
    if owner_boot != current_boot:
        return "dead", "owner belongs to a previous host boot"
    try:
        current_pidns = (proc / "self/ns/pid").stat().st_ino
    except OSError:
        return "unknown", "current PID namespace is unavailable"
    if owner_pidns != current_pidns:
        return "unknown", "owner PID belongs to another namespace"

    owner_proc = proc / str(owner_pid)
    try:
        raw = (owner_proc / "stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return "dead", "owner PID no longer exists"
    except (OSError, UnicodeError):
        return "unknown", "owner process state is unreadable"
    fields = raw.rpartition(")")[2].split()
    try:
        state = fields[0]
        start_ticks = int(fields[19])
        process_pidns = (owner_proc / "ns/pid").stat().st_ino
    except (IndexError, OSError, ValueError):
        return "unknown", "owner process identity is incomplete"
    if process_pidns != owner_pidns:
        return "unknown", "owner PID was observed in another namespace"
    if state in {"Z", "X"}:
        return "dead", f"owner process is in state {state}"
    if start_ticks != owner_start:
        return "dead", "owner PID was recycled to a different process"
    return "alive", "owner process identity still matches"


def _started_at(labels: Mapping[str, str]) -> datetime | None:
    raw = labels.get("ciu.run.started")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def reap_project(
    repo_root: Path,
    project: str,
    *,
    older_than_seconds: float = _DEFAULT_REAP_AGE_SECONDS,
    yes: bool = False,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    """Survey or remove this root's one-shots from one exact Compose project."""
    from . import engine

    root = Path(repo_root).resolve()
    try:
        ownership = engine.workspace_ownership_labels(root)
    except (OSError, ValueError) as exc:
        raise RuntimeError("[CIU-141] cannot read this checkout's ownership identity") from exc
    query = [
        "docker", "ps", "-a", "--no-trunc",
        "--filter", f"label=com.docker.compose.project={project}",
        "--filter", "label=com.docker.compose.oneoff=True",
        "--filter", "label=ciu.run=1",
    ]
    if ownership is not None:
        for key in (engine.OWNERSHIP_LABEL_INSTANCE, engine.OWNERSHIP_LABEL_REPO_ROOT):
            query.extend(["--filter", f"label={key}={ownership[key]}"])
    query.extend(["--format", "{{.ID}}"])
    try:
        listing = procutil.run_cmd(query, check=False, capture=True)
    except OSError as exc:
        raise RuntimeError("[CIU-141] Docker unavailable while listing one-shot containers") from exc
    if listing.returncode != 0:
        raise RuntimeError("[CIU-141] Docker failed while listing one-shot containers")
    ids = [line.strip() for line in (listing.stdout or "").splitlines() if line.strip()]
    if any(not _CONTAINER_ID_RE.fullmatch(item) for item in ids):
        raise RuntimeError("[CIU-141] Docker did not return full container IDs for reap")

    instant = (now or datetime.now(UTC)).astimezone(UTC)
    rows: list[dict[str, str]] = []
    for container_id in sorted(set(ids)):
        try:
            inspected = procutil.run_cmd(
                ["docker", "inspect", "--type", "container", container_id],
                check=False, capture=True,
            )
        except OSError as exc:
            raise RuntimeError("[CIU-141] Docker unavailable while inspecting a one-shot") from exc
        if inspected.returncode != 0:
            raise RuntimeError(
                f"[CIU-141] one-shot {container_id} changed during inspection; retry reap"
            )
        try:
            result = json.loads(inspected.stdout or "")
        except (json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError("[CIU-141] Docker inspect returned invalid one-shot JSON") from exc
        if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
            raise RuntimeError("[CIU-141] Docker inspect did not identify one exact one-shot")
        document = result[0]
        if document.get("Id") != container_id:
            raise RuntimeError(f"[CIU-141] Docker inspect changed ID {container_id}")
        labels = _event_labels(document)
        name = str(document.get("Name", "")).lstrip("/")
        if (
            labels.get("com.docker.compose.project") != project
            or labels.get("com.docker.compose.oneoff") != "True"
            or labels.get("ciu.run") != "1"
        ):
            raise RuntimeError(
                f"[CIU-141] one-shot {container_id} no longer matches its exact label filter"
            )
        if ownership is not None:
            if any(labels.get(key) != value for key, value in ownership.items()):
                rows.append({
                    "id": container_id, "name": name, "action": "left",
                    "reason": "container ownership labels name another or unknown checkout",
                })
                continue
        elif labels.get("com.docker.compose.project.working_dir") != str(root):
            rows.append({
                "id": container_id, "name": name, "action": "left",
                "reason": "Compose working directory does not identify this primary checkout",
            })
            continue

        state = document.get("State") or {}
        status = state.get("Status") if isinstance(state, Mapping) else None
        owner_state, owner_reason = _reap_owner_state(labels)
        if owner_state != "dead":
            rows.append({
                "id": container_id, "name": name, "action": "left",
                "reason": owner_reason,
            })
            continue
        started = _started_at(labels)
        age = (instant - started).total_seconds() if started is not None else None
        if status != "exited" and (age is None or age < older_than_seconds):
            rows.append({
                "id": container_id, "name": name, "action": "left",
                "reason": "dead owner, but container is not exited and is not older than the bound",
            })
            continue
        if not yes:
            rows.append({
                "id": container_id, "name": name, "action": "would-remove",
                "reason": owner_reason,
            })
            continue
        try:
            removed = procutil.run_cmd(
                ["docker", "rm", "-f", container_id], check=False, capture=True,
            )
        except OSError as exc:
            raise RuntimeError(f"[CIU-141] Docker unavailable while removing {container_id}") from exc
        rows.append({
            "id": container_id, "name": name,
            "action": "removed" if removed.returncode == 0 else "failed",
            "reason": owner_reason if removed.returncode == 0 else "docker rm -f failed",
        })
    return rows


def reap_cli(
    repo_root: Path, *, older_than: str | None = None, yes: bool = False,
) -> int:
    """Survey one-shots for this root; ``--yes`` performs safe removals."""
    from . import deploy

    if older_than is None:
        age_seconds = _DEFAULT_REAP_AGE_SECONDS
    else:
        age_seconds = deploy.parse_duration_seconds(older_than)
        if age_seconds <= 0:
            raise ValueError("[CIU-141] --older-than must be greater than zero")
    root = Path(repo_root).resolve()
    document = deploy.resolve_identities(root)
    identities = document.get("resolved", {}).get("identities", {})
    if not isinstance(identities, Mapping):
        raise RuntimeError("[CIU-141] resolved identity document has an invalid shape")
    projects = sorted({
        str(identity.get("compose_project"))
        for services in identities.values()
        if isinstance(services, Mapping)
        for identity in services.values()
        if isinstance(identity, Mapping) and identity.get("compose_project")
    })
    if not projects:
        print("[CIU-141] no selected Compose projects have CIU run containers")
        return 0
    failed = False
    for project in projects:
        for row in reap_project(
            root, project, older_than_seconds=age_seconds, yes=yes,
        ):
            action = row["action"]
            if action == "failed":
                failed = True
                verb = "failed to remove"
            elif action == "removed":
                verb = "removed"
            elif action == "would-remove":
                verb = "would remove"
            else:
                verb = "left alone"
            print(f"[CIU-141] {verb} {row['id']} ({row['name']}): {row['reason']}")
    return 1 if failed else 0


def run_cli(
    repo_root: Path,
    selector: str,
    argv: Sequence[str],
    *,
    profiles: Sequence[str] | None = None,
    name: str | None = None,
    labels: Sequence[str] = (),
    env_names: Sequence[str] = (),
    extend: str | Path | None = None,
    detach: bool = False,
    json_output: bool = False,
    dry_run: bool = False,
    events_fd: int | None = None,
    events_file: str | Path | None = None,
    owner_pid: int | None = None,
    skip_hooks: bool = False,
) -> int:
    """CIU-141 command boundary; the launch path is supplied by C3."""
    if not argv:
        raise ValueError("[CIU-141] `ciu run` requires a command after `--`")
    for env_name in env_names:
        validate_env_name(env_name)
    if dry_run and not json_output:
        raise ValueError("[CIU-141] `ciu run --dry-run` requires --json")
    if detach and not json_output:
        raise ValueError("[CIU-141] `ciu run --detach` requires --json")
    if json_output and not detach and not dry_run:
        raise ValueError(
            "[CIU-141] foreground `ciu run` uses --events-fd or --events-file, not --json"
        )
    if events_fd is not None and events_fd < 3:
        raise ValueError("[CIU-141] --events-fd must be 3 or greater")

    forwarded_names = [validate_env_name(item) for item in env_names]
    if len(set(forwarded_names)) != len(forwarded_names):
        raise ValueError("[CIU-141] --env-name values must be unique")
    user_labels = dict(parse_run_label(item) for item in labels)
    if len(user_labels) != len(labels):
        raise ValueError("[CIU-141] --label keys must be unique")
    if name is not None:
        validate_container_name(name)

    # CIU's normal preparation pipeline is chatty on stdout. Preparation and
    # Compose config diagnostics stay on stderr; stdout is reserved for the
    # dry-run JSON or detached launch document.
    with redirect_stdout(sys.stderr):
        prepared, stack, service = _prepare_run_stack(
            repo_root, selector, profiles=profiles, skip_hooks=skip_hooks,
        )
        plan = build_run_plan(
            prepared, stack=stack, service=service, name=name, extend=extend,
            labels=labels, skip_hooks=skip_hooks,
        )
    # Detached default owner is the PARENT process. A caller that launches ciu
    # through a short-lived wrapper must pass --owner-pid, or `ciu run reap`
    # will see a dead owner as soon as that wrapper exits (see CONSUMERS.md).
    owner = (
        owner_pid if owner_pid is not None
        else (os.getppid() if detach else os.getpid())
    )
    run_id = str(uuid.uuid4())
    container_name = _select_run_name(prepared.project, service, name)
    mode = "detached" if detach else "foreground"
    runtime_labels = dict(user_labels)
    runtime_labels.update(_make_runtime_labels(
        run_id=run_id, stack=stack, service=service, mode=mode, owner_pid=owner,
    ))
    if dry_run:
        # Run the same read-only checks a real run performs first, so a dry
        # run refuses exactly where the real run would (missing stack
        # network, name already in use); nothing is created or removed.
        with redirect_stdout(sys.stderr):
            _verify_run_networks(prepared, service)
            _ensure_name_free(container_name)
        document = _dry_run_document(
            plan, prepared=prepared, name=container_name, name_given=name is not None,
            runtime_labels=runtime_labels, env_names=forwarded_names, mode=mode,
            argv=argv, owner_pid=owner,
        )
        print(json.dumps(document, sort_keys=True, separators=(",", ":")))
        return 0

    event_writer = _EventWriter(fd=events_fd, path=events_file)
    try:
        if detach:
            event = _run_detached(
                prepared, plan=plan, stack=stack, service=service, argv=argv,
                name=container_name, run_id=run_id, labels=runtime_labels,
                env_names=forwarded_names, extend=extend,
            )
            event_writer.write(event)
            print(json.dumps(event, sort_keys=True, separators=(",", ":")))
            return 0
        return _run_foreground(
            prepared, plan=plan, stack=stack, service=service, argv=argv,
            name=container_name, run_id=run_id, labels=runtime_labels,
            env_names=forwarded_names, extend=extend, event_writer=event_writer,
        )
    finally:
        event_writer.close()
