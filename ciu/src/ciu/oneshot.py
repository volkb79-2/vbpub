"""Stack-scoped one-shot Compose planning and extension validation (CIU-141)."""

from __future__ import annotations

import copy
import json
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

import yaml

from . import procutil

_CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_LABEL_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-/]*$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LIMIT_KEYS = ("mem_limit", "memswap_limit", "pids_limit", "cpus")
_ALLOWED_EXTENSION_KEYS = frozenset({
    *_LIMIT_KEYS, "group_add", "volumes", "environment", "labels", "init",
})
_ALLOWED_EXTENSION_ROOT_KEYS = frozenset({"services", "x-ciu-run"})


def validate_container_name(name: str) -> str:
    """Validate a Docker container name and preserve it verbatim."""
    if not isinstance(name, str) or not _CONTAINER_NAME_RE.fullmatch(name):
        raise ValueError(
            "[CIU-141] container name must match "
            "[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}"
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
        parts = volume.split(":")
        target = parts[-1]
    else:
        raise ValueError(f"[CIU-141] {where} volume must be long-form")
    if not isinstance(target, str) or not target.startswith("/"):
        raise ValueError(f"[CIU-141] {where} volume target must be an absolute path")
    if ".." in PurePosixPath(target).parts:
        raise ValueError(f"[CIU-141] {where} volume target may not contain '..'")
    return target


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
        key = sorted(str(item) for item in unknown_root)[0]
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
        key = sorted(str(item) for item in unknown)[0]
        raise ValueError(f"[CIU-141] --extend refuses service key {key!r}")

    for key in (*_LIMIT_KEYS, "init"):
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
                if not PurePosixPath(source).is_absolute() or ".." in PurePosixPath(source).parts:
                    raise ValueError(
                        "[CIU-141] --extend bind source must be absolute and may not contain '..'"
                    )
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
                    key = sorted(str(item) for item in set(bind) - {"create_host_path"})[0]
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
            name = sorted(str(item) for item in collisions)[0]
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
        key = sorted(reserved_service_labels)[0]
        raise ValueError(f"[CIU-141] rendered service label {key!r} is reserved")

    extension_path: Path | None = None
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
    prepared = engine.prepare_stack(
        Path(repo_root) / stack_path,
        define_root=Path(repo_root),
        compose_profiles=profile.compose_profiles,
        ciu_context=ciu_context,
        skip_hooks=skip_hooks,
    )
    return prepared, stack_path, service


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

    if dry_run:
        # CIU's normal preparation pipeline is chatty on stdout. The plan is a
        # machine-readable stdout contract, so preparation diagnostics stay on
        # stderr and only the allow-listed projection is serialized.
        from contextlib import redirect_stdout
        import sys

        with redirect_stdout(sys.stderr):
            prepared, stack, service = _prepare_run_stack(
                repo_root, selector, profiles=profiles, skip_hooks=skip_hooks,
            )
            plan = build_run_plan(
                prepared, stack=stack, service=service, name=name, extend=extend,
                labels=labels, skip_hooks=skip_hooks,
            )
        print(json.dumps(plan, sort_keys=True, separators=(",", ":")))
        return 0

    raise RuntimeError("[CIU-141] one-shot launch is not available in this build")
