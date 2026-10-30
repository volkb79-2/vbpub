#!/usr/bin/env python3
"""Plant a bounded TrueNAS workload, update, and compare its canonical state.

This is an external-host acceptance tool. It uses only an explicitly selected
disposable host and requires separate mutation, update, and reboot guards.
"""

from __future__ import annotations

import argparse
import base64
import copy
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Any, Iterator

from live_update import (
    RemoteError,
    check_no_running_guests,
    wait_for_api,
    wait_for_update_job,
)
from remote import Target, flatten_datasets


SCHEMA_VERSION = 1
SECRET_KEY_RE = re.compile(
    r"password|secret|token|private.?key|api.?key|credential|stored.?key|server.?key|client.?key|salt",
    re.I,
)
FIXTURE_ID_RE = re.compile(r"\d{8}-[0-9a-f]{10}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
BUILD_RE = re.compile(r"\d+\.\d+\.\d+-(?:RC\.\d+|MASTER\+\d{8}-\d{6})\Z")
EXIT_RE = re.compile(r"(?:\A|\n)__TRUENAS_EXIT__([0-9]+)\n\Z")
PATH_A = ("boot-pool/user-data", "/mnt/boot-pool/user-data")
PATH_B = ("boot-disk-data", "/mnt/boot-disk-data")


class SurvivalError(RemoteError):
    """The acceptance could not establish preservation of a fixture."""


@dataclass(frozen=True)
class Difference:
    path: str
    before: Any
    after: Any


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SurvivalError(message)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonicalize(value: Any, *, key: str = "") -> Any:
    """Return JSON-safe stable values while replacing secrets with digests."""
    if SECRET_KEY_RE.search(key) and not key.endswith((".query", ".config")):
        encoded = value if isinstance(value, str) else json.dumps(
            value, sort_keys=True, separators=(",", ":"), default=str
        )
        return {"sha256": sha256_text(encoded)}
    if isinstance(value, dict):
        return {str(name): canonicalize(item, key=str(name)) for name, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        rows = [canonicalize(item) for item in value]
        return sorted(rows, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in sorted(value.items()):
            path = f"{prefix}.{key}" if prefix else str(key)
            result.update(flatten(item, path))
        return result or {prefix: {}}
    if isinstance(value, list):
        if value and all(isinstance(row, dict) and "id" in row for row in value):
            identifiers = [row["id"] for row in value]
            if len(set(json.dumps(item, sort_keys=True) for item in identifiers)) == len(identifiers):
                result: dict[str, Any] = {}
                for row in value:
                    identity = json.dumps(row["id"], sort_keys=True, separators=(",", ":"))
                    result.update(flatten(row, f"{prefix}[id={identity}]")
                                  if prefix else flatten(row, f"[id={identity}]"))
                return result
        return {prefix: value}
    return {prefix: value}


def compare_snapshots(
    before: dict[str, Any], after: dict[str, Any], *, expected_paths: set[str],
    expected_values: dict[str, Any] | None = None,
) -> tuple[list[Difference], list[Difference]]:
    left, right = flatten(before), flatten(after)
    differences = [
        Difference(path, left.get(path, "<missing>"), right.get(path, "<missing>"))
        for path in sorted(set(left) | set(right))
        if left.get(path, "<missing>") != right.get(path, "<missing>")
    ]
    expected_values = expected_values or {}
    expected = [
        item for item in differences
        if item.path in expected_paths
        and (item.path not in expected_values or item.after == expected_values[item.path])
    ]
    unexpected = [item for item in differences if item not in expected]
    return expected, unexpected


def expected_zvol_reservation_migration_paths(
    *, mode: str, before_version: str, expected_after: str,
    dataset_names: list[str], baseline: dict[str, Any] | None,
) -> set[str]:
    """Allow only the observed RC.1→Oct27 patched boot-pool zvol default release."""
    if (mode != "patched" or before_version != "27.0.0-RC.1"
            or expected_after != "27.0.0-MASTER+20261027-173422" or not isinstance(baseline, dict)):
        return set()
    rows = baseline.get("middleware", {}).get("datasets", {})
    if not isinstance(rows, dict):
        return set()
    result: set[str] = set()
    for dataset in dataset_names:
        if not (dataset.startswith("boot-pool/user-data/") and dataset.endswith("/record-zvol")):
            continue
        row = rows.get(dataset)
        if not isinstance(row, dict):
            continue
        reservation = row.get("refreservation", {})
        volsize = row.get("volsize", {})
        blocksize = row.get("volblocksize", {})
        if (
            isinstance(reservation, dict) and reservation.get("rawvalue") == "70254592"
            and reservation.get("parsed") == 70254592
            and isinstance(volsize, dict) and volsize.get("rawvalue") == "67108864"
            and isinstance(blocksize, dict) and blocksize.get("rawvalue") == "16384"
        ):
            parent = PurePosixPath(dataset).parent.as_posix()
            identity = json.dumps(dataset, sort_keys=True, separators=(",", ":"))
            prefixes = (
                f"middleware.datasets.{dataset}.refreservation",
                f"middleware.datasets.{parent}.children[id={identity}].refreservation",
            )
            for prefix in prefixes:
                result.update({f"{prefix}.parsed", f"{prefix}.rawvalue"})
    return result


def expected_differences(
    *, mode: str, profile: str, vm_ids: list[Any] | None = None,
    dataset_names: list[str] | None = None, before_version: str = "",
    expected_after: str = "", baseline: dict[str, Any] | None = None,
) -> set[str]:
    if mode not in {"vanilla", "patched"}:
        raise SurvivalError(f"mode must be vanilla or patched, got {mode!r}")
    if profile not in {"path-a", "path-b", "both"}:
        raise SurvivalError(f"profile must be path-a, path-b, or both, got {profile!r}")
    result = {"system.version", "boot_environment.active"}
    if profile in {"path-a", "both"}:
        result.add("storage.path_a.auto_mounted")
    if profile in {"path-b", "both"}:
        result.add("storage.path_b.auto_mounted")
    if mode == "patched":
        result.update({"patch.install_record.version", "patch.install_record.bundle_sha256"})
    result.update(expected_zvol_reservation_migration_paths(
        mode=mode, before_version=before_version, expected_after=expected_after,
        dataset_names=dataset_names or [], baseline=baseline,
    ))
    for vm_id in vm_ids or []:
        result.add(f"middleware.records.selected.vms[id={json.dumps(vm_id)}].video_model")
    return result


def expected_difference_values(manifest: dict[str, Any], expected_after: str) -> dict[str, Any]:
    """Bind accepted upgrade deltas to the requested build and fixture state."""
    mode, profile = manifest.get("mode"), manifest.get("profile")
    result: dict[str, Any] = {
        "system.version": expected_after,
        "boot_environment.active": expected_after.replace("+", "-"),
    }
    for role in ("path_a", "path_b"):
        selected = profile in ({"path-a", "both"} if role == "path_a" else {"path-b", "both"})
        if selected:
            if mode == "patched" and expected_after.startswith("27."):
                mounted = True
            elif expected_after.startswith("28.") and role == "path_a":
                mounted = False
            elif expected_after.startswith("28.") and role == "path_b":
                storage = manifest.get("baseline", {}).get("storage", {})
                pool_state = storage.get(role) if isinstance(storage, dict) else None
                require(isinstance(pool_state, dict) and type(pool_state.get("auto_mounted")) is bool,
                        f"patched cross-series update baseline has no {role} mount state")
                mounted = pool_state["auto_mounted"]
            else:
                mounted = False
            result[f"storage.{role}.auto_mounted"] = mounted
    if mode == "patched":
        before_version = str(manifest.get("before_version", ""))
        baseline_record = (
            manifest.get("baseline", {}).get("patch", {}).get("install_record")
            if isinstance(manifest.get("baseline"), dict) else None
        )
        require(isinstance(baseline_record, dict),
                "patched update manifest has no baseline install record")
        if expected_after.startswith("27."):
            registry = json.loads((Path(__file__).resolve().parents[1] / "supported-builds.json").read_text())
            build = registry.get("supported_builds", {}).get(expected_after, {})
            bundle = build.get("install_bundle_sha256") if isinstance(build, dict) else None
            require(isinstance(bundle, str) and SHA256_RE.fullmatch(bundle) is not None,
                    f"supported-build manifest has no exact install bundle digest for {expected_after}")
            result["patch.install_record.version"] = expected_after
            result["patch.install_record.bundle_sha256"] = bundle
        elif expected_after.startswith("28."):
            require(baseline_record.get("version") == before_version,
                    "patched cross-series update baseline marker does not match its source build")
            require(isinstance(baseline_record.get("bundle_sha256"), str)
                    and SHA256_RE.fullmatch(baseline_record["bundle_sha256"]) is not None,
                    "patched cross-series update baseline marker has no bundle digest")
            result["patch.install_record.version"] = before_version
            result["patch.install_record.bundle_sha256"] = baseline_record["bundle_sha256"]
        else:
            raise SurvivalError(f"no patched update marker policy is registered for {expected_after!r}")
        migration_paths = expected_zvol_reservation_migration_paths(
            mode=mode,
            before_version=str(manifest.get("before_version", "")),
            expected_after=expected_after,
            dataset_names=manifest.get("objects", {}).get("datasets", []),
            baseline=manifest.get("baseline"),
        )
        for path in migration_paths:
            result[path] = None if path.endswith(".parsed") else "0"
    vm_ids = manifest.get("objects", {}).get("records", {}).get("vm", [])
    for vm_id in vm_ids:
        result[f"middleware.records.selected.vms[id={json.dumps(vm_id)}].video_model"] = "VGA"
    return result


def require_expected_snapshot_values(snapshot: dict[str, Any], expected_values: dict[str, Any]) -> None:
    observed = flatten(snapshot)
    mismatches = [
        (path, expected, observed.get(path, "<missing>"))
        for path, expected in expected_values.items()
        if observed.get(path, "<missing>") != expected
    ]
    require(not mismatches,
            "post-update snapshot has an unexpected required value: "
            + "; ".join(f"{path}: expected={expected!r} actual={actual!r}"
                         for path, expected, actual in mismatches))


def require_v1001_target(host: str, port: int) -> None:
    if host != "v1001.vxxu.de" or port != 22:
        raise SurvivalError(
            f"update-survival acceptance is restricted to disposable v1001.vxxu.de:22; got {host}:{port}"
        )


def validate_root_pools(roots: list[tuple[str, str, str]], pools: set[str]) -> None:
    for role, dataset, _mountpoint in roots:
        pool = dataset.split("/", 1)[0]
        require(pool in pools,
                f"requested {role} storage pool {pool!r} is not imported (root dataset {dataset!r})")


def select_update_manifest(rows: Any, *, train: str, expected_after: str) -> dict[str, Any]:
    require(isinstance(rows, list), "update.available_versions did not return a list")
    require(BUILD_RE.fullmatch(expected_after) is not None,
            f"update target is not an exact TrueNAS build string: {expected_after!r}")
    series = expected_after.split(".", 1)[0]
    require(train.startswith(f"TrueNAS-{series}-"),
            f"update train {train!r} does not match target series {series}")
    candidates = [
        row for row in rows
        if isinstance(row, dict) and row.get("train") == train and isinstance(row.get("version"), dict)
    ]
    if not candidates:
        raise SurvivalError(f"update.available_versions has no available update manifests for {train}")
    versions = [row["version"].get("version") for row in candidates]
    require(all(isinstance(version, str) and BUILD_RE.fullmatch(version) for version in versions),
            f"update.available_versions returned an invalid build for {train}: {versions!r}")
    latest = max(versions)
    require(latest == expected_after,
            f"requested update {expected_after} is not the newest available {train} build ({latest})")
    selected = [row for row in candidates if row["version"].get("version") == expected_after]
    require(len(selected) == 1, f"expected one exact update manifest for {expected_after}, found {len(selected)}")
    manifest = selected[0]["version"].get("manifest")
    require(isinstance(manifest, dict) and manifest.get("version") == expected_after,
            f"exact update manifest is missing or identifies another build: {manifest!r}")
    filename, size, checksum = manifest.get("filename"), manifest.get("filesize"), manifest.get("checksum")
    require(isinstance(filename, str) and Path(filename).name == filename and filename.endswith(".update"),
            f"update manifest has an unsafe filename: {filename!r}")
    require(type(size) is int and size > 0, f"update manifest has an invalid filesize: {size!r}")
    require(isinstance(checksum, str) and SHA256_RE.fullmatch(checksum) is not None,
            f"update manifest has an invalid SHA-256 checksum: {checksum!r}")
    return {**manifest, "train": train}


def update_profile_for_train(train: str) -> str:
    """Map only the registered v27/v28 Nightly and RC trains to profiles."""
    match = re.fullmatch(r"TrueNAS-(27|28)-(Nightlies|RC)", train)
    if match is None:
        raise SurvivalError(f"no update profile policy is registered for train {train!r}")
    return "DEVELOPER" if match.group(2) == "Nightlies" else "EARLY_ADOPTER"


def ensure_update_profile(target: Target, train: str) -> str:
    """Select the supported profile required to expose the requested train."""
    profile = update_profile_for_train(train)
    try:
        config = target.call("update.config", timeout=120)
        choices = target.call("update.profile_choices", timeout=120)
    except RemoteError as exc:
        raise SurvivalError(f"cannot inspect TrueNAS update profile choices: {exc}") from exc
    require(isinstance(config, dict), f"update.config returned malformed data: {type(config).__name__}")
    require(isinstance(choices, dict),
            f"update.profile_choices returned malformed data: {type(choices).__name__}")
    choice = choices.get(profile)
    require(isinstance(choice, dict) and choice.get("available") is True,
            f"required update profile {profile} for {train} is not available")
    previous = config.get("profile")
    if previous != profile:
        try:
            result = target.call("update.update", {"profile": profile}, timeout=120)
            updated = target.call("update.config", timeout=120)
        except RemoteError as exc:
            raise SurvivalError(f"could not select update profile {profile}: {exc}") from exc
        require(isinstance(result, dict) and result.get("profile") == profile,
                f"update.update did not confirm profile {profile}: {result!r}")
        require(isinstance(updated, dict) and updated.get("profile") == profile,
                f"update.config did not retain profile {profile}: {updated!r}")
        print(f"UPDATE_PROFILE={previous!r}->{profile}", flush=True)
    return profile


def read_update_record(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SurvivalError(f"cannot read prior update job record {path}: {exc}") from exc
    require(isinstance(value, dict), f"prior update job record is not an object: {path}")
    return value


def validate_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SurvivalError(f"cannot read update-survival manifest {path}: {exc}") from exc
    require(isinstance(value, dict) and value.get("schema_version") == SCHEMA_VERSION,
            f"unsupported update-survival manifest schema: {path}")
    fixture_id = value.get("fixture_id")
    require(isinstance(fixture_id, str) and FIXTURE_ID_RE.fullmatch(fixture_id) is not None,
            "update-survival manifest has an invalid fixture id")
    objects = value.get("objects")
    require(isinstance(objects, dict), "update-survival manifest has no objects table")
    for kind in ("files", "zvols"):
        rows = objects.get(kind)
        require(isinstance(rows, list), f"update-survival manifest has no {kind} list")
        for row in rows:
            require(isinstance(row, dict), f"malformed {kind} row: {row!r}")
            digest = row.get("sha256")
            require(isinstance(digest, str) and SHA256_RE.fullmatch(digest) is not None,
                    f"{kind} row has invalid SHA-256: {row!r}")
            name = row.get("path") if kind == "files" else row.get("device")
            require(isinstance(name, str) and safe_absolute_path(name),
                    f"unsafe fixture path in {kind}: {name!r}")
    datasets = objects.get("datasets")
    require(isinstance(datasets, list), "update-survival manifest has no datasets list")
    for name in datasets:
        require(isinstance(name, str) and safe_dataset_name(name),
                f"unsafe fixture dataset name: {name!r}")
    return value


def safe_absolute_path(value: str) -> bool:
    path = PurePosixPath(value)
    return path.is_absolute() and ".." not in path.parts and len(path.parts) > 2


def safe_dataset_name(value: str) -> bool:
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and len(path.parts) > 1


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def shell_status(target: Target, command: str, *, timeout: int = 45) -> tuple[int, str]:
    output = target.shell(
        f"set +e; {command}; rc=$?; printf '\\n__TRUENAS_EXIT__%s\\n' \"$rc\"",
        timeout=timeout,
        check=False,
    )
    match = EXIT_RE.search(output + "\n")
    if match is None:
        raise SurvivalError(f"could not capture remote command status for: {command}")
    return int(match.group(1)), output[:match.start()].rstrip("\n")


def zfs_dataset_exists(target: Target, name: str) -> bool:
    status, output = shell_status(
        target,
        f"zfs list -H -o name {shlex.quote(name)} 2>&1",
        timeout=45,
    )
    if status == 0:
        listed_names = output.splitlines()
        require(name in listed_names,
                f"zfs list did not return the exact requested dataset {name!r}: {output!r}")
        return True
    if "dataset does not exist" in output.lower() or "no such pool or dataset" in output.lower():
        return False
    raise SurvivalError(f"cannot determine whether ZFS dataset {name} exists: {output}")


def storage_root_exists(target: Target, role: str) -> bool:
    """Check a Path A/B root by its ZFS dataset name, never its mountpoint."""
    roots = {"path_a": PATH_A, "path_b": PATH_B}
    try:
        dataset, _mountpoint = roots[role]
    except KeyError as exc:
        raise SurvivalError(f"unknown storage root role: {role!r}") from exc
    return zfs_dataset_exists(target, dataset)


def storage_mount_state(target: Target, objects: dict[str, Any]) -> dict[str, dict[str, Any]]:
    state = {}
    for role, (pool, _mountpoint) in (("path_a", PATH_A), ("path_b", PATH_B)):
        if not storage_root_exists(target, role):
            state[role] = {"exists": False, "auto_mounted": False, "datasets": []}
            continue
        root = PATH_A[0] if role == "path_a" else PATH_B[0]
        mounted = dataset_mounted(target, root)
        state[role] = {
            "exists": True, "auto_mounted": mounted == "yes",
            "datasets": sorted(name for name in objects.get("datasets", [])
                                if name.startswith(pool + "/")),
        }
    return state


def dataset_mounted(target: Target, dataset: str) -> str:
    status, mounted = shell_status(
        target, f"zfs get -H -o value mounted {shlex.quote(dataset)}",
    )
    require(status == 0 and mounted in {"yes", "no"},
            f"cannot determine ZFS mount state for {dataset}: mounted={mounted!r}")
    return mounted


def fixture_mount_states(
    target: Target, dataset_names: list[str], properties: dict[str, dict[str, str]],
) -> dict[str, bool]:
    states = {}
    for dataset in sorted(dataset_names, key=lambda name: (name.count("/"), name)):
        if not zfs_dataset_exists(target, dataset):
            continue
        dataset_properties = properties.get(dataset)
        require(dataset_properties is not None,
                f"direct ZFS inventory omitted dataset type for {dataset}")
        if dataset_mount_required(dataset_properties):
            states[dataset] = dataset_mounted(target, dataset) == "yes"
    return states


def dataset_mount_required(properties: dict[str, str]) -> bool:
    """Return whether a ZFS object is a filesystem dataset that can be mounted."""
    dataset_type = properties.get("type")
    if dataset_type == "filesystem":
        return True
    if dataset_type == "volume":
        return False
    raise SurvivalError(f"unknown ZFS dataset type while reading update fixtures: {dataset_type!r}")


@contextmanager
def temporary_fixture_mounts(
    target: Target, initial_mounts: dict[str, bool],
) -> Iterator[None]:
    """Mount for checksums, then restore states captured before middleware reads."""
    filesystems = sorted(initial_mounts, key=lambda name: (name.count("/"), name))
    try:
        for dataset in filesystems:
            if dataset_mounted(target, dataset) == "no":
                status, output = shell_status(target, f"zfs mount {shlex.quote(dataset)}")
                require(status == 0, f"cannot temporarily mount survival dataset {dataset}: {output}")
        yield
    finally:
        for dataset in reversed(filesystems):
            if not initial_mounts[dataset] and dataset_mounted(target, dataset) == "yes":
                status, output = shell_status(target, f"zfs unmount {shlex.quote(dataset)}")
                require(status == 0, f"cannot restore unmounted state for {dataset}: {output}")
        for dataset in filesystems:
            if initial_mounts[dataset] and dataset_mounted(target, dataset) == "no":
                status, output = shell_status(target, f"zfs mount {shlex.quote(dataset)}")
                require(status == 0, f"cannot restore mounted state for {dataset}: {output}")


def method_names(target: Target) -> set[str]:
    value = target.call("core.get_methods", timeout=120)
    if isinstance(value, dict):
        return set(value)
    if isinstance(value, list):
        return {
            row if isinstance(row, str) else row.get("name")
            for row in value
            if isinstance(row, str) or isinstance(row, dict)
        }
    raise SurvivalError(f"core.get_methods returned an unrecognized value: {type(value).__name__}")


def method_record(target: Target, methods: set[str], method: str) -> dict[str, Any]:
    if method not in methods:
        return {"status": "unavailable"}
    try:
        if method.endswith(".query"):
            value = target.query(method, timeout=120)
        else:
            value = target.call(method, timeout=120)
    except RemoteError as exc:
        raise SurvivalError(f"middleware {method} was advertised but could not be queried: {exc}") from exc
    return {"status": "available", "value": canonical_middleware_value(method, value)}


def canonical_middleware_value(method: str, value: Any) -> Any:
    """Remove generated replication timestamps while preserving task state."""
    if method == "replication.query" and isinstance(value, list):
        stable_rows = []
        for row in value:
            if not isinstance(row, dict):
                stable_rows.append(row)
                continue
            stable_row = dict(row)
            state = stable_row.get("state")
            if isinstance(state, dict):
                stable_state = dict(state)
                stable_state.pop("datetime", None)
                stable_row["state"] = stable_state
            stable_rows.append(stable_row)
        value = stable_rows
    elif method == "network.configuration.config" and isinstance(value, dict):
        value = dict(value)
        state = value.get("state")
        if isinstance(state, dict):
            state = dict(state)
            nameservers = sorted(
                server for server in (state.get(f"nameserver{index}") for index in range(1, 4))
                if isinstance(server, str) and server
            )
            for index in range(1, 4):
                state[f"nameserver{index}"] = nameservers[index - 1] if index <= len(nameservers) else ""
            value["state"] = state
    return canonicalize(value)


def canonical_dataset_record(value: dict[str, Any]) -> Any:
    """Keep effective ZFS properties; omit changing accounting and provenance metadata."""
    volatile_counters = {"available", "used", "usedbychildren", "usedbyrefreservation"}

    def stable(item: Any) -> Any:
        if isinstance(item, dict):
            result: dict[str, Any] = {}
            for name, child in item.items():
                if name in volatile_counters:
                    continue
                if name == "refreservation" and isinstance(child, dict):
                    result[name] = {
                        key: stable(field)
                        for key, field in child.items()
                        if key not in {"source", "source_info", "value"}
                    }
                else:
                    result[name] = stable(child)
            return result
        if isinstance(item, (list, tuple)):
            return [stable(child) for child in item]
        return item

    return canonicalize(stable(value))


def canonical_container_record(value: dict[str, Any]) -> dict[str, Any]:
    """Represent v27 default-network and v28 generated-NIC schemas uniformly."""
    stable = copy.deepcopy(value)
    devices = stable.get("devices")
    if not isinstance(devices, list):
        return stable
    nics = [
        row for row in devices
        if isinstance(row, dict) and isinstance(row.get("attributes"), dict)
        and row["attributes"].get("dtype") == "NIC"
    ]
    if "default_network" not in stable and len(nics) == 1:
        attributes = nics[0]["attributes"]
        attach = attributes.get("nic_attach")
        if isinstance(attach, str) and attach:
            stable["default_network"] = attach
            stable["devices"] = [row for row in devices if row is not nics[0]]
    stable.setdefault("devices", [])
    return stable


def normalize_survival_snapshot(
    value: dict[str, Any], *, hide_path_a_middleware: bool = False,
    normalize_container_model: bool = False,
) -> dict[str, Any]:
    """Apply current volatile-field rules to both new and saved baselines."""
    stable = copy.deepcopy(value)
    middleware = stable.get("middleware")
    if not isinstance(middleware, dict):
        return stable
    configuration = middleware.get("configuration")
    if isinstance(configuration, dict):
        for method, record in configuration.items():
            if isinstance(record, dict) and record.get("status") == "available":
                record["value"] = canonical_middleware_value(method, record.get("value"))
    datasets = middleware.get("datasets")
    if isinstance(datasets, dict):
        middleware["datasets"] = {
            name: canonical_dataset_record(row) if isinstance(row, dict) else row
            for name, row in datasets.items()
            if not hide_path_a_middleware
            or (name != PATH_A[0] and not name.startswith(PATH_A[0] + "/"))
        }
    if normalize_container_model:
        records = middleware.get("records")
        selected = records.get("selected") if isinstance(records, dict) else None
        containers = selected.get("containers") if isinstance(selected, dict) else None
        if isinstance(containers, list):
            selected["containers"] = [
                canonical_container_record(row) if isinstance(row, dict) else row
                for row in containers
            ]
    return stable


def fixture_query(target: Target, methods: set[str], method: str, filters: list[Any] | None = None) -> list[dict[str, Any]]:
    if method not in methods:
        return []
    rows = target.query(method, filters or [], timeout=120)
    require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows),
            f"middleware {method} returned a non-record list")
    return rows


def fixture_snapshot_query(target: Target, methods: set[str], objects: dict[str, Any]) -> list[dict[str, Any]]:
    method = "zfs.resource.snapshot.query"
    if method not in methods:
        return []
    paths = sorted(name for name in objects.get("datasets", [])
                   if isinstance(name, str) and name.endswith("/nested"))
    if not paths:
        return []
    rows = target.call(method, {"paths": paths, "recursive": False}, timeout=120)
    require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows),
            f"middleware {method} returned a non-record list")
    tokens = objects.get("snapshot_names", [])
    return [row for row in rows
            if any(token in str(row.get("name", row.get("id", ""))) for token in tokens)]


def _record_match(row: dict[str, Any], identifiers: set[Any], keys: tuple[str, ...]) -> bool:
    return any(row.get(key) in identifiers for key in keys)


def canonical_snapshot(target: Target, manifest: dict[str, Any]) -> dict[str, Any]:
    objects = manifest["objects"]
    # Read natural mount state before middleware inventory queries can cause
    # on-demand mounts.
    storage = storage_mount_state(target, objects)
    dataset_properties = direct_dataset_properties(target, objects.get("datasets", []))
    initial_mounts = fixture_mount_states(
        target, objects.get("datasets", []), dataset_properties,
    )
    methods = method_names(target)
    ids = objects.get("records", {})
    fixture_names = set(objects.get("record_names", []))
    methods_to_dump = (
        "network.configuration.config", "system.general.config", "system.advanced.config",
        "system.ntpserver.query", "api_key.query", "certificate.query", "cronjob.query",
        "pool.snapshottask.query", "replication.query", "cloudsync.query",
        "cloudsync.credentials.query", "app.query",
    )
    config = {method: method_record(target, methods, method) for method in methods_to_dump}
    users = fixture_query(target, methods, "user.query")
    groups = fixture_query(target, methods, "group.query")
    smb = fixture_query(target, methods, "sharing.smb.query")
    nfs = fixture_query(target, methods, "sharing.nfs.query")
    vms = fixture_query(target, methods, "vm.query")
    containers = fixture_query(target, methods, "container.query")
    datasets: dict[str, dict[str, Any]] = {}
    if "pool.dataset.query" in methods:
        flattened = flatten_datasets(target.query("pool.dataset.query", timeout=120))
        for name in objects.get("datasets", []):
            if name in flattened:
                datasets[name] = canonical_dataset_record(flattened[name])
    snapshots = fixture_snapshot_query(target, methods, objects)
    selected = {
        "users": [row for row in users if _record_match(row, fixture_names, ("username", "name"))],
        "groups": [row for row in groups if _record_match(row, fixture_names, ("name",))],
        "smb": [row for row in smb if row.get("id") in set(ids.get("smb", []))],
        "nfs": [row for row in nfs if row.get("id") in set(ids.get("nfs", []))],
        "vms": [row for row in vms if row.get("id") in set(ids.get("vm", [])) or row.get("name") in fixture_names],
        "containers": [row for row in containers if row.get("id") in set(ids.get("container", [])) or row.get("name") in fixture_names],
    }
    vm_devices = []
    if "vm.device.query" in methods and ids.get("vm"):
        vm_devices = fixture_query(target, methods, "vm.device.query", [["vm", "in", ids["vm"]]])
    record_queries = {"selected": canonicalize(selected), "vm_devices": canonicalize(vm_devices),
                      "snapshots": canonicalize(snapshots)}
    system = target.call("system.info", timeout=120)
    active_usr = target.shell("findmnt -n -o SOURCE --target /usr")
    install_record_text = target.shell(
        "if test -f /root/v27-boot-pool-patch-install.json; then "
        "cat /root/v27-boot-pool-patch-install.json; else printf ABSENT; fi",
        check=False,
    )
    try:
        install_record = json.loads(install_record_text) if install_record_text != "ABSENT" else None
    except json.JSONDecodeError as exc:
        raise SurvivalError("v27 install record exists but is not valid JSON") from exc
    if install_record is not None:
        install_record = {key: install_record.get(key) for key in ("version", "bundle_sha256", "supported")}
    checksums = {}
    # Preserve the natural auto-mount status captured above. The check mounts
    # only for hashing and restores every prior state, even if a read fails.
    with temporary_fixture_mounts(target, initial_mounts):
        for row in objects.get("files", []):
            output = target.shell(f"sha256sum {shlex.quote(row['path'])}", check=False)
            checksums[row["path"]] = output.split()[0] if output else "<unreadable>"
        for row in objects.get("zvols", []):
            output = target.shell(
                f"dd if={shlex.quote(row['device'])} bs=4096 count=1 status=none | sha256sum",
                timeout=90, check=False,
            )
            checksums[row["device"]] = output.split()[0] if output else "<unreadable>"
    snapshot_output = target.shell("zfs list -H -o name -t snapshot", timeout=90)
    fixture_tokens = set(objects.get("snapshot_names", []))
    direct_snapshots = sorted(
        name for name in snapshot_output.splitlines()
        if any(f"@{token}" in name for token in fixture_tokens)
    )
    return canonicalize({
        "system": {"version": system.get("version"), "hostname": system.get("hostname")},
        "boot_environment": {"active": active_usr.removeprefix("boot-pool/ROOT/").removesuffix("/usr")},
        "patch": {"install_record": install_record},
        "storage": storage,
        "middleware": {"configuration": config, "records": record_queries, "datasets": datasets},
        "direct_zfs": {"dataset_properties": dataset_properties, "snapshots": direct_snapshots},
        "data_checksums": checksums,
    })


def pool_names(target: Target) -> set[str]:
    output = target.shell("zpool list -H -o name")
    return {line.strip() for line in output.splitlines() if line.strip()}


def create_dataset(target: Target, name: str, mountpoint: str, *, quota: str, refquota: str) -> None:
    require(not zfs_dataset_exists(target, name), f"refusing to reuse a pre-existing survival dataset: {name}")
    target.run([
        "zfs", "create", "-o", f"mountpoint={mountpoint}", "-o", "compression=lz4",
        "-o", f"quota={quota}", "-o", f"refquota={refquota}", name,
    ], timeout=120)


def ensure_path_a_root(target: Target) -> None:
    if zfs_dataset_exists(target, PATH_A[0]):
        mountpoint = target.shell(f"zfs get -H -o value mountpoint {PATH_A[0]}")
        require(mountpoint == PATH_A[1], f"Path A has unexpected mountpoint: {mountpoint!r}")
        if target.shell(f"zfs get -H -o value mounted {PATH_A[0]}") == "no":
            target.shell(f"zfs mount {PATH_A[0]}")
        return
    # A bounded root is safe to create on a disposable full-disk RC.1 image;
    # this makes the vanilla-update test exercise user data without installing
    # the middleware adapter being evaluated.
    target.run([
        "zfs", "create", "-o", f"mountpoint={PATH_A[1]}", "-o", "canmount=on",
        "-o", "quota=2G", PATH_A[0],
    ], timeout=120)


def write_probe(target: Target, path: str, payload: bytes) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    target.shell(f"install -d -m 0755 {shlex.quote(str(PurePosixPath(path).parent))} && "
                 f"printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(path)}")
    observed = target.shell(f"sha256sum {shlex.quote(path)}").split()[0]
    expected = hashlib.sha256(payload).hexdigest()
    require(observed == expected, f"probe write hash mismatch at {path}: {observed} != {expected}")
    return expected


def fixture_user_home(files: list[dict[str, Any]], fixture_id: str,
                      pool_rows: list[dict[str, Any]]) -> str:
    require(FIXTURE_ID_RE.fullmatch(fixture_id) is not None,
            f"invalid survival fixture id for user home: {fixture_id!r}")

    # account.validate_homedir_path() uses an internal pool.query call. The
    # adapter's synthetic boot-pool row is added only to the external query
    # response and is therefore not a valid home-directory volume here.
    real_pool_paths: list[tuple[str, str]] = []
    for row in pool_rows:
        name = row.get("name")
        path_text = row.get("path")
        if not isinstance(name, str) or name == "boot-pool" or not isinstance(path_text, str):
            continue
        root = PurePosixPath(path_text)
        if (not root.is_absolute() or root.parts[:2] != ("/", "mnt")
                or ".." in root.parts or path_text == "/mnt"):
            continue
        real_pool_paths.append((name, str(root)))

    role_rank = {"path-b": 0, "nonreserved": 1, "path-a": 2}
    candidates = sorted(
        (row for row in files if row.get("role") in role_rank),
        key=lambda row: role_rank[str(row["role"])],
    )
    for candidate in candidates:
        file_text = candidate.get("path")
        if not isinstance(file_text, str):
            continue
        file_path = PurePosixPath(file_text)
        if (not file_path.is_absolute() or file_path.parts[:2] != ("/", "mnt")
                or ".." in file_path.parts):
            continue
        for _pool_name, pool_path in real_pool_paths:
            if file_text.startswith(pool_path.rstrip("/") + "/"):
                return str(file_path.parent / f"account-home-{fixture_id.split('-')[-1]}")

    raise SurvivalError(
        "no fixture file is inside a real pool.query path; the synthetic boot-pool row "
        "cannot satisfy user home validation"
    )


def survival_api_key_data(fixture_id: str) -> dict[str, str]:
    require(FIXTURE_ID_RE.fullmatch(fixture_id) is not None,
            f"invalid survival fixture id for API key: {fixture_id!r}")
    suffix = fixture_id.split("-")[-1]
    # TrueNAS refuses API keys owned by a fixture account without a privileged
    # role. Use the built-in administrator while keeping the one-time key out
    # of the manifest and report.
    return {"name": f"update-survival-{suffix}", "username": "root"}


def middleware_root_dataset(dataset_names: list[str], pool_rows: list[dict[str, Any]],
                            fixture_id: str) -> str:
    suffix = fixture_id.split("-")[-1]
    real_pool_names = {
        row.get("name") for row in pool_rows
        if isinstance(row, dict) and isinstance(row.get("name"), str) and row.get("name") != "boot-pool"
    }
    selected = next((
        name for name in dataset_names
        if name.endswith("update-survival-" + suffix)
        and name.split("/", 1)[0] in real_pool_names
    ), None)
    if selected is None:
        raise SurvivalError(
            "no planted root dataset is visible through a real pool.query row for scheduled tasks"
        )
    return selected


def preferred_middleware_pool_role(files: list[dict[str, Any]]) -> str | None:
    available_roles = {row.get("role") for row in files}
    return next((role for role in ("path-b", "nonreserved", "path-a") if role in available_roles), None)


def replication_create_data(fixture_id: str, source_dataset: str, target_dataset: str,
                            snapshot_task_id: int) -> dict[str, Any]:
    suffix = fixture_id.split("-")[-1]
    return {
        "name": f"update-survival-{suffix}", "direction": "PUSH", "transport": "LOCAL",
        "source_datasets": [source_dataset], "target_dataset": target_dataset, "recursive": False,
        "periodic_snapshot_tasks": [snapshot_task_id],
        "auto": False, "retention_policy": "NONE", "enabled": False,
    }


def vm_configuration_data(fixture_id: str) -> dict[str, Any]:
    require(FIXTURE_ID_RE.fullmatch(fixture_id) is not None,
            f"invalid survival fixture id for VM: {fixture_id!r}")
    suffix = fixture_id.split("-")[-1]
    return {
        "name": f"survivalvm{suffix}",
        "description": "stopped update-survival definition",
        "memory": 256,
        "vcpus": 1,
        "cores": 1,
        "threads": 1,
        "bootloader": "UEFI",
        "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"truenas-update-survival:{fixture_id}")),
        "autostart": False,
        "time": "UTC",
        "shutdown_timeout": 90,
        "cpu_mode": "CUSTOM",
        "cpu_model": "qemu64",
        "suspend_on_snapshot": False,
    }


def vm_disk_device_data(vm_id: int, zvol_path: str) -> dict[str, Any]:
    require(type(vm_id) is int and vm_id > 0, f"invalid survival VM id: {vm_id!r}")
    require(zvol_path.startswith("/dev/zvol/") and ".." not in PurePosixPath(zvol_path).parts,
            f"invalid survival VM zvol path: {zvol_path!r}")
    return {
        "vm": vm_id,
        "order": 1000,
        "attributes": {"dtype": "DISK", "path": zvol_path, "type": "VIRTIO", "create_zvol": False},
    }


def vm_create_refused_only_for_missing_virtualization(error: RemoteError) -> bool:
    return "This system does not support virtualization." in str(error)


def direct_dataset_properties(target: Target, dataset_names: list[str]) -> dict[str, dict[str, str]]:
    property_list = "type,mountpoint,compression,quota,refquota,recordsize,volsize"
    expected_properties = set(property_list.split(","))
    result: dict[str, dict[str, str]] = {}
    for name in sorted(set(dataset_names)):
        output = target.run([
            "zfs", "get", "-H", "-p", "-o", "property,value", property_list, name,
        ], timeout=60)
        observed: dict[str, str] = {}
        for line in output.splitlines():
            fields = line.split("\t", 1)
            require(len(fields) == 2 and fields[0] not in observed,
                    f"zfs get returned an invalid property row for {name}: {line!r}")
            observed[fields[0]] = fields[1]
        require(set(observed) == expected_properties,
                f"zfs get omitted requested dataset properties for {name}: {observed!r}")
        result[name] = observed
    return result


def plant_data(target: Target, fixture_id: str, required_profile: str) -> dict[str, Any]:
    pools = pool_names(target)
    roots: list[tuple[str, str, str]] = []
    if required_profile in {"path-a", "both"}:
        ensure_path_a_root(target)
        roots.append(("path-a", *PATH_A))
    if required_profile in {"path-b", "both"}:
        require(PATH_B[0] in pools,
                "Path B was requested but boot-disk-data is not imported; provision it before acceptance")
        roots.append(("path-b", *PATH_B))
    nonreserved = sorted(pools - {"boot-pool", "boot-disk-data"})
    if nonreserved:
        roots.append(("nonreserved", nonreserved[0], f"/mnt/{nonreserved[0]}"))
    validate_root_pools(roots, pools)

    objects: dict[str, Any] = {
        "datasets": [], "files": [], "zvols": [], "record_names": [],
        "snapshot_names": [], "records": {"smb": [], "nfs": [], "vm": [], "container": []},
        "coverage": {"encryption": "not-created: test key escrow is not configured",
                     "nonreserved_pool": nonreserved[0] if nonreserved else "unavailable"},
    }
    for role, pool, root in roots:
        require(zfs_dataset_exists(target, pool), f"storage root pool is not imported: {pool}")
        root_mount = target.shell(f"zfs get -H -o value mountpoint {shlex.quote(pool)}", check=False)
        if root_mount in {"none", "legacy", "-", ""}:
            if role == "path-a":
                root_mount = PATH_A[1]
            elif role == "path-b":
                root_mount = PATH_B[1]
            else:
                raise SurvivalError(f"non-reserved pool {pool} has no mountpoint: {root_mount!r}")
        root_mounted = target.shell(f"zfs get -H -o value mounted {shlex.quote(pool)}", check=False)
        if root_mounted != "yes":
            target.shell(f"zfs mount {shlex.quote(pool)}")
        suffix = fixture_id.split("-")[-1]
        dataset = f"{pool}/update-survival-{suffix}"
        nested = f"{dataset}/nested"
        clone = f"{pool}/update-survival-clone-{suffix}"
        mountpoint = f"/mnt/{dataset}"
        nested_mount = f"{mountpoint}/nested"
        clone_mount = f"/mnt/{clone}"
        create_dataset(target, dataset, mountpoint, quota="1G", refquota="768M")
        create_dataset(target, nested, nested_mount, quota="512M", refquota="384M")
        target.shell(f"zfs set recordsize=64K {shlex.quote(dataset)} && "
                     f"zfs set compression=lz4 {shlex.quote(nested)}")
        file_path = f"{nested_mount}/probe-{role}.bin"
        payload = (f"TrueNAS update survival fixture {fixture_id} {role}\n".encode() * 128)
        digest = write_probe(target, file_path, payload)
        target.shell(f"zfs snapshot {shlex.quote(nested + '@survival-' + suffix)}")
        target.shell(f"zfs clone -o mountpoint={shlex.quote(clone_mount)} "
                     f"{shlex.quote(nested + '@survival-' + suffix)} {shlex.quote(clone)}")
        clone_path = f"{clone_mount}/clone-probe.bin"
        clone_digest = write_probe(target, clone_path, payload + b"clone\n")
        zvol = f"{dataset}/record-zvol"
        target.run(["zfs", "create", "-V", "64M", "-o", "volmode=dev", zvol], timeout=120)
        device = f"/dev/zvol/{zvol}"
        vol_payload = hashlib.sha256(fixture_id.encode()).digest() * 128
        vol_encoded = base64.b64encode(vol_payload).decode("ascii")
        target.shell(f"printf %s {shlex.quote(vol_encoded)} | base64 -d | "
                     f"dd of={shlex.quote(device)} bs=4096 conv=notrunc status=none")
        vol_digest = target.shell(
            f"dd if={shlex.quote(device)} bs=4096 count=1 status=none | sha256sum",
            timeout=90,
        ).split()[0]
        expected_vol_digest = hashlib.sha256(vol_payload[:4096]).hexdigest()
        require(vol_digest == expected_vol_digest,
                f"zvol probe hash mismatch for {zvol}: {vol_digest} != {expected_vol_digest}")
        objects["datasets"].extend((dataset, nested, clone, zvol))
        objects["snapshot_names"].append(f"survival-{suffix}")
        objects["files"].extend((
            {"path": file_path, "sha256": digest, "role": role},
            {"path": clone_path, "sha256": clone_digest, "role": role + "-clone"},
        ))
        objects["zvols"].append({"device": device, "sha256": vol_digest, "role": role})
        objects["coverage"][role] = "planted"
    if not nonreserved:
        objects["coverage"]["nonreserved_pool"] = "unavailable: no non-reserved imported pool exists"
    return objects


def plant_middleware_records(target: Target, fixture_id: str, objects: dict[str, Any], ssh_public_key_file: Path) -> None:
    methods = method_names(target)
    for method in ("group.create", "user.create", "sharing.smb.create", "sharing.nfs.create",
                   "vm.create", "vm.device.create", "api_key.create", "cronjob.create",
                   "pool.snapshottask.create", "replication.create"):
        require(method in methods, f"required v27 middleware method is unavailable: {method}")
    key = ssh_public_key_file.read_text(encoding="utf-8").strip()
    require(key.startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-")),
            "SSH public-key file is not a recognized OpenSSH public key")
    suffix = fixture_id.split("-")[-1]
    primary_role = preferred_middleware_pool_role(objects["files"])
    require(primary_role is not None, "the selected Path A/Path B file fixture is missing")
    pool_rows = target.query("pool.query", timeout=120)
    user_home = fixture_user_home(objects["files"], fixture_id, pool_rows)
    target.shell(f"install -d -m 0700 {shlex.quote(user_home)}")
    home_marker = f"{user_home}/home-survival-marker.txt"
    home_digest = write_probe(target, home_marker, f"account home survives {fixture_id}\n".encode())
    objects["files"].append({"path": home_marker, "sha256": home_digest, "role": "account-home"})
    group_name = f"survival-{suffix}"
    user_name = f"survival-{suffix}"
    group = target.call("group.create", {"name": group_name, "smb": False})
    group_id = group if isinstance(group, int) else group.get("id") if isinstance(group, dict) else None
    require(isinstance(group_id, int), f"group.create returned no integer id: {group!r}")
    password = uuid.uuid4().hex + "Aa!9"
    user = target.call("user.create", {
        "username": user_name, "full_name": f"Update survival {suffix}",
        "group": group_id, "password": password, "sshpubkey": key, "smb": False,
        "shell": "/usr/bin/bash", "home": user_home, "password_disabled": False,
    })
    user_id = user if isinstance(user, int) else user.get("id") if isinstance(user, dict) else None
    require(isinstance(user_id, int), f"user.create returned no integer id: {user!r}")
    observed_user = target.query("user.query", [["username", "=", user_name]], timeout=120)
    require(len(observed_user) == 1 and observed_user[0].get("sshpubkey") == key,
            f"created account did not retain its SSH public key: {observed_user!r}")
    shadow = target.shell(
        f"getent shadow {shlex.quote(user_name)} | cut -d: -f2",
        timeout=45,
    )
    require(shadow not in {"", "!", "*"}, "created account does not have an active password hash")
    objects["password_hash_sha256"] = {user_name: sha256_text(shadow)}
    objects["record_names"].extend((group_name, user_name))
    objects["records"].update({"group": [group_id], "user": [user_id]})
    objects["coverage"]["accounts"] = "planted group, user, SSH key, and persistent password hash"
    api_key = target.call("api_key.create", survival_api_key_data(fixture_id))
    api_key_id = api_key.get("id")
    api_key_value = api_key.get("key")
    require(isinstance(api_key_id, int) and isinstance(api_key_value, str) and api_key_value,
            "api_key.create did not return an identifier and one-time key")
    objects["records"]["api_key"] = [api_key_id]
    objects["api_key_sha256"] = sha256_text(api_key_value)
    objects["coverage"]["api_key"] = (
        "planted for built-in root administrator; raw one-time key is not written to the manifest"
    )
    cronjob = target.call("cronjob.create", {
        "command": "/usr/bin/true", "user": user_name,
        "description": f"disabled update-survival fixture {suffix}", "enabled": False,
        "schedule": {"minute": "0", "hour": "0", "dom": "1", "month": "1", "dow": "1"},
    })
    require(isinstance(cronjob, dict) and isinstance(cronjob.get("id"), int) and cronjob.get("enabled") is False,
            f"disabled cron job was not retained as a disabled record: {cronjob!r}")
    objects["records"]["cronjob"] = [cronjob["id"]]

    share_path = user_home
    nfs = target.call("sharing.nfs.create", {
        "path": share_path, "comment": "disabled update-survival test share",
        "enabled": False, "ro": False, "maproot_user": "", "maproot_group": "",
        "mapall_user": "", "mapall_group": "", "networks": [], "hosts": [], "security": [],
    })
    smb = target.call("sharing.smb.create", {
        "purpose": "DEFAULT_SHARE", "name": f"survival-{suffix}", "path": share_path,
        "enabled": False, "comment": "disabled update-survival test share", "readonly": False,
        "browsable": True, "access_based_share_enumeration": False,
        "audit": {"enable": False, "watch_list": [], "ignore_list": []},
        "options": {"aapl_name_mangling": False, "hostsallow": [], "hostsdeny": []},
    })
    nfs_id, smb_id = nfs.get("id"), smb.get("id")
    require(isinstance(nfs_id, int) and isinstance(smb_id, int),
            "share create returned an invalid record id")
    objects["records"]["nfs"].append(nfs_id)
    objects["records"]["smb"].append(smb_id)

    root_dataset = middleware_root_dataset(objects["datasets"], pool_rows, fixture_id)
    snapshot_task = target.call("pool.snapshottask.create", {
        "dataset": root_dataset, "enabled": False, "recursive": True,
        "naming_schema": f"survival-%Y-%m-%d_%H-%M-{suffix}",
        "lifetime_unit": "DAY", "lifetime_value": 2,
    })
    snapshot_task_id = snapshot_task if isinstance(snapshot_task, int) else snapshot_task.get("id")
    require(isinstance(snapshot_task_id, int), f"snapshot-task create returned no id: {snapshot_task!r}")
    objects["records"]["snapshot_task"] = [snapshot_task_id]
    pool_name = root_dataset.split("/", 1)[0]
    replica = f"{pool_name}/update-survival-replica-{suffix}"
    replica_mount = f"/mnt/{replica}"
    create_dataset(target, replica, replica_mount, quota="512M", refquota="384M")
    objects["datasets"].append(replica)
    source_dataset = next(name for name in objects["datasets"] if name.endswith("/nested") and name.startswith(root_dataset))
    replication = target.call(
        "replication.create",
        replication_create_data(fixture_id, source_dataset, replica, snapshot_task_id),
    )
    replication_id = replication.get("id") if isinstance(replication, dict) else replication
    require(isinstance(replication_id, int), f"disabled local replication create returned no id: {replication!r}")
    objects["records"]["replication"] = [replication_id]
    objects["coverage"]["snapshot_task"] = "planted disabled"
    objects["coverage"]["replication"] = "planted disabled local target"
    objects["coverage"]["cloudsync"] = "existing definitions captured; no external credentials created"
    objects["coverage"]["certificate"] = "existing records captured; no private-key import created"
    objects["coverage"]["network_system_config"] = "read-only canonical middleware dump; network access not changed"
    objects["coverage"]["apps"] = "existing records captured; no app deployed"

    zvol = next(row for row in objects["zvols"] if row["role"] == primary_role)
    vm_data = vm_configuration_data(fixture_id)
    try:
        vm = target.call("vm.create", vm_data)
    except RemoteError as exc:
        if not vm_create_refused_only_for_missing_virtualization(exc):
            raise
        # v27 refuses the supported vm.create API on a host without KVM.
        # Seed a stopped configuration row through middleware's datastore so
        # the update test can still verify database-record preservation without
        # pretending that this host can run a guest.
        vm_id = target.call("datastore.insert", "vm.vm", vm_data)
        require(type(vm_id) is int, f"datastore.insert vm.vm returned no integer id: {vm_id!r}")
        device_id = target.call(
            "datastore.insert", "vm.device", vm_disk_device_data(vm_id, zvol["device"]),
        )
        require(type(device_id) is int,
                f"datastore.insert vm.device returned no integer id: {device_id!r}")
        objects["coverage"]["vm"] = (
            "definition and zvol device seeded as middleware datastore records because this host's "
            "vm.create API refused unsupported virtualization; no VM was started"
        )
    else:
        vm_id = vm.get("id") if isinstance(vm, dict) else None
        require(isinstance(vm_id, int), f"vm.create returned no integer id: {vm!r}")
        target.call("vm.device.create", vm_disk_device_data(vm_id, zvol["device"]))
        objects["coverage"]["vm"] = "stopped definition and zvol device created through middleware API"
    objects["record_names"].append(f"survivalvm{suffix}")
    objects["records"]["vm"].append(vm_id)
    objects["vm_zvol_role"] = primary_role

    if "certificate.create" in methods and "certificate.query" in methods and shutil.which("openssl"):
        certificate_name = f"update-survival-{suffix}"
        with tempfile.TemporaryDirectory(prefix="truenas-survival-cert-") as directory:
            key_path = Path(directory) / "private.pem"
            certificate_path = Path(directory) / "certificate.pem"
            result = subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                 "-keyout", str(key_path), "-out", str(certificate_path), "-days", "30",
                 "-subj", f"/CN={certificate_name}"],
                text=True, capture_output=True, check=False,
            )
            require(result.returncode == 0,
                    f"could not create a short-lived local test certificate: {result.stderr.strip()}")
            certificate_job = target.call("certificate.create", {
                "name": certificate_name, "create_type": "CERTIFICATE_CREATE_IMPORTED",
                "certificate": certificate_path.read_text(encoding="utf-8"),
                "privatekey": key_path.read_text(encoding="utf-8"),
                "add_to_trusted_store": False,
            }, timeout=120)
            certificate_job_id = (
                certificate_job if isinstance(certificate_job, int)
                else certificate_job.get("job_id") if isinstance(certificate_job, dict)
                else None
            )
            require(isinstance(certificate_job_id, int),
                    f"certificate.create returned no job id: {certificate_job!r}")
            target.wait_job(certificate_job_id, timeout=300, label="update-survival certificate create")
        certificate_rows = target.query("certificate.query", [["name", "=", certificate_name]], timeout=120)
        require(len(certificate_rows) == 1 and isinstance(certificate_rows[0].get("id"), int),
                f"created test certificate is not queryable: {certificate_rows!r}")
        objects["records"]["certificate"] = [certificate_rows[0]["id"]]
        objects["record_names"].append(certificate_name)
        objects["coverage"]["certificate"] = "planted short-lived self-signed test certificate"
    elif "certificate.create" not in methods or "certificate.query" not in methods:
        objects["coverage"]["certificate"] = "unavailable: certificate create/query API is absent"
    else:
        objects["coverage"]["certificate"] = "unavailable: openssl is absent from the tester"

    container_method_set = {"container.pool_choices", "container.image.query_registry", "container.create"}
    if container_method_set <= methods:
        choices = target.call("container.pool_choices", timeout=90)
        suitable_pool = next((pool for pool in choices if pool != "boot-pool"), None)
        if suitable_pool:
            image_name = "alpine:3.24:amd64:default"
            image_rows = target.call("container.image.query_registry", timeout=120)
            image = next((row for row in image_rows if row.get("name") == image_name), None)
            if image and image.get("versions"):
                image_version = sorted(row["version"] for row in image["versions"])[-1]
                container_name = f"survival-{suffix}"
                created = target.call("container.create", {
                    "name": container_name, "pool": suitable_pool,
                    "image": {"name": image_name, "version": image_version},
                    "autostart": False, "time": "LOCAL", "shutdown_timeout": 30,
                    "capabilities_policy": "DEFAULT", "idmap": {"type": "DEFAULT"},
                }, timeout=120)
                job_id = created if isinstance(created, int) else created.get("job_id")
                require(isinstance(job_id, int), f"container.create returned no job id: {created!r}")
                result = target.wait_job(job_id, timeout=900, label="update-survival container create")
                container_id = result.get("id") if isinstance(result, dict) else result
                require(isinstance(container_id, int), f"container create job returned no id: {result!r}")
                objects["record_names"].append(container_name)
                objects["records"]["container"].append(container_id)
                objects["coverage"]["container"] = "planted"
            else:
                objects["coverage"]["container"] = "unavailable: pinned registry image not published"
        else:
            objects["coverage"]["container"] = "unavailable: no user-data pool is offered to container middleware"
    else:
        objects["coverage"]["container"] = "unavailable: container middleware methods are absent"


def plant(target: Target, *, mode: str, profile: str, manifest_path: Path) -> dict[str, Any]:
    require_v1001_target(target.host, target.port)
    require(os.environ.get("TRUENAS_TEST_ALLOW_MUTATIONS") == "1",
            "set TRUENAS_TEST_ALLOW_MUTATIONS=1 only for the disposable v1001 host")
    before = target.call("system.info", timeout=120)
    expected_before = os.environ.get("TRUENAS_EXPECTED_VERSION", "")
    require(expected_before and before.get("version") == expected_before,
            f"expected TrueNAS {expected_before!r}, got {before.get('version')!r}")
    require(expected_before.startswith("27.0.0-"),
            f"update-survival planting requires a v27 source build, got {expected_before!r}")
    if mode == "patched":
        record_text = target.shell(
            "test -f /root/v27-boot-pool-patch-install.json && "
            "cat /root/v27-boot-pool-patch-install.json",
            check=False,
        )
        try:
            record = json.loads(record_text)
        except json.JSONDecodeError as exc:
            raise SurvivalError("patched mode requires a readable v27 install record for this exact build") from exc
        require(record.get("version") == expected_before and record.get("supported") is True,
                f"patched mode install record does not certify {expected_before}: {record!r}")
    else:
        record_status, record_text = shell_status(
            target, "test -f /root/v27-boot-pool-patch-install.json",
        )
        require(record_status != 0,
                "vanilla mode found a v27 patch install record; restore pristine RC.1 before planting")
    check_no_running_guests(target)
    fixture_id = time.strftime("%Y%m%d", time.gmtime()) + "-" + uuid.uuid4().hex[:10]
    objects = plant_data(target, fixture_id, profile)
    key_path = Path(os.environ.get("TRUENAS_SURVIVAL_SSH_PUBLIC_KEY_FILE", ""))
    require(key_path.is_file(), "TRUENAS_SURVIVAL_SSH_PUBLIC_KEY_FILE must name a readable public key")
    plant_middleware_records(target, fixture_id, objects, key_path)
    manifest = {
        "schema_version": SCHEMA_VERSION, "fixture_id": fixture_id,
        "before_version": expected_before, "mode": mode, "profile": profile,
        "objects": objects,
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    baseline = canonical_snapshot(target, manifest)
    manifest["baseline"] = baseline
    manifest["expected_differences"] = sorted(expected_differences(
        mode=mode, profile=profile, vm_ids=objects["records"].get("vm", []),
        dataset_names=objects.get("datasets", []), before_version=expected_before,
        expected_after=os.environ.get("TRUENAS_SURVIVAL_EXPECTED_AFTER", ""), baseline=baseline,
    ))
    selected = baseline["middleware"]["records"]["selected"]
    for category in ("smb", "nfs", "vms", "containers"):
        expected_ids = objects["records"].get({"vms": "vm"}.get(category, category), [])
        seen_ids = {row.get("id") for row in selected.get(category, [])}
        require(set(expected_ids) <= seen_ids,
                f"middleware baseline did not include planted {category} ids {expected_ids!r}: {seen_ids!r}")
    names = objects["record_names"]
    require(any(row.get("username") in names for row in selected.get("users", []))
            and any(row.get("name") in names for row in selected.get("groups", [])),
            "middleware baseline did not include the planted user and group records")
    queryable_pool_names = {
        row["name"] for row in target.query("pool.query", timeout=120)
        if isinstance(row, dict) and isinstance(row.get("name"), str) and row.get("name") != "boot-pool"
    }
    expected_middleware_datasets = {
        name for name in objects["datasets"] if name.split("/", 1)[0] in queryable_pool_names
    }
    visible_middleware_datasets = set(baseline["middleware"]["datasets"])
    require(expected_middleware_datasets <= visible_middleware_datasets,
            "middleware dataset query omitted planted datasets from real pools: "
            f"{sorted(expected_middleware_datasets - visible_middleware_datasets)!r}")
    direct_properties = baseline["direct_zfs"]["dataset_properties"]
    require(set(objects["datasets"]) <= set(direct_properties),
            "direct ZFS property inventory omitted planted datasets: "
            f"{sorted(set(objects['datasets']) - set(direct_properties))!r}")
    objects["coverage"]["middleware_dataset_query"] = (
        f"{len(expected_middleware_datasets)} real-pool fixture datasets visible; "
        f"{len(set(objects['datasets']) - expected_middleware_datasets)} boot-pool namespace datasets "
        "checked by direct ZFS properties and checksums"
    )
    config_records = baseline["middleware"]["configuration"]
    for method, record_key in (("api_key.query", "api_key"), ("cronjob.query", "cronjob"),
                               ("pool.snapshottask.query", "snapshot_task"), ("replication.query", "replication")):
        record = config_records.get(method, {"status": "unavailable"})
        require(record.get("status") == "available", f"required middleware query is unavailable: {method}")
        record_ids = {row.get("id") for row in (record.get("value") or []) if isinstance(row, dict)}
        require(set(objects["records"].get(record_key, [])) <= record_ids,
                f"middleware {method} did not include planted ids {objects['records'].get(record_key)!r}")
    if objects["records"].get("certificate"):
        cert_rows = config_records.get("certificate.query", {}).get("value") or []
        cert_ids = {row.get("id") for row in cert_rows if isinstance(row, dict)}
        require(set(objects["records"]["certificate"]) <= cert_ids,
                "middleware certificate query omitted the planted certificate")
    direct_snapshot_names = baseline["direct_zfs"]["snapshots"]
    require(all(any(f"@{token}" in name for name in direct_snapshot_names)
                for token in objects["snapshot_names"]),
            f"direct ZFS inventory omitted planted snapshots: {direct_snapshot_names!r}")
    devices = baseline["middleware"]["records"].get("vm_devices", [])
    expected_device = next(row["device"] for row in objects["zvols"]
                           if row["role"] == objects["vm_zvol_role"])
    require(any((row.get("attributes") or {}).get("path", row.get("path")) == expected_device
                for row in devices),
            f"middleware baseline lost planted VM device {expected_device}")
    app_record = baseline["middleware"]["configuration"].get("app.query", {"status": "unavailable"})
    if app_record.get("status") == "available":
        objects["coverage"]["apps"] = f"existing records captured ({len(app_record.get('value') or [])}); no app deployed"
    cloud_record = baseline["middleware"]["configuration"].get("cloudsync.credentials.query", {"status": "unavailable"})
    if cloud_record.get("status") == "available":
        objects["coverage"]["cloudsync"] = (
            f"existing definitions and credentials captured ({len(cloud_record.get('value') or [])}); "
            "no external credential created"
        )
    certificate_record = baseline["middleware"]["configuration"].get("certificate.query", {"status": "unavailable"})
    if (certificate_record.get("status") == "available"
            and objects["coverage"].get("certificate", "").startswith("existing")):
        objects["coverage"]["certificate"] = (
            f"existing records captured ({len(certificate_record.get('value') or [])}); "
            "no private-key import created"
        )
    manifest["baseline"] = baseline
    write_json(manifest_path, manifest)
    validate_manifest(manifest_path)
    print(f"PLANTED fixture={fixture_id} version={expected_before} datasets={len(objects['datasets'])} "
          f"files={len(objects['files'])} zvols={len(objects['zvols'])}; coverage={objects['coverage']}")
    return manifest


def verify(target: Target, manifest_path: Path, report_path: Path) -> dict[str, Any]:
    require_v1001_target(target.host, target.port)
    manifest = validate_manifest(manifest_path)
    before = manifest.get("baseline")
    require(isinstance(before, dict), "manifest has no canonical pre-update midclt baseline")
    expected_after = os.environ.get("TRUENAS_SURVIVAL_EXPECTED_AFTER", "").strip()
    cross_series_v27_v28 = (
        str(manifest.get("before_version", "")).startswith("27.")
        and expected_after.startswith("28.")
    )
    before = normalize_survival_snapshot(
        before, hide_path_a_middleware=cross_series_v27_v28,
        normalize_container_model=cross_series_v27_v28,
    )
    after = normalize_survival_snapshot(
        canonical_snapshot(target, manifest), hide_path_a_middleware=cross_series_v27_v28,
        normalize_container_model=cross_series_v27_v28,
    )
    if expected_after:
        require(after["system"]["version"] == expected_after,
                f"expected post-update build {expected_after!r}, got {after['system']['version']!r}")
    allowed = expected_differences(
        mode=manifest.get("mode"), profile=manifest.get("profile"),
        vm_ids=manifest["objects"].get("records", {}).get("vm", []),
        dataset_names=manifest["objects"].get("datasets", []),
        before_version=str(manifest.get("before_version", "")),
        expected_after=expected_after, baseline=before,
    )
    declared_allowed = set(manifest["expected_differences"])
    require(declared_allowed <= allowed,
            "manifest permits an upgrade difference outside the current explicit policy: "
            f"{sorted(declared_allowed - allowed)!r}")
    expected_values = expected_difference_values(manifest, expected_after)
    require_expected_snapshot_values(after, expected_values)
    expected, unexpected = compare_snapshots(
        before, after, expected_paths=allowed, expected_values=expected_values,
    )
    data_errors = []
    for row in manifest["objects"]["files"] + manifest["objects"]["zvols"]:
        actual = after["data_checksums"].get(row.get("path", row.get("device")), "<missing>")
        if actual != row["sha256"]:
            data_errors.append({"path": row.get("path", row.get("device")),
                                "expected": row["sha256"], "actual": actual})
    for username, expected_hash in manifest["objects"].get("password_hash_sha256", {}).items():
        output = target.shell(f"getent shadow {shlex.quote(username)} | cut -d: -f2", check=False)
        actual_hash = sha256_text(output) if output else "<missing>"
        if actual_hash != expected_hash:
            data_errors.append({"path": f"account:{username}:password_hash", "expected": expected_hash,
                                "actual": actual_hash})
    report_value = {
        "schema_version": SCHEMA_VERSION, "fixture_id": manifest["fixture_id"],
        "before_version": manifest["before_version"], "after_version": after["system"]["version"],
        "permitted_difference_paths": sorted(allowed),
        "expected_differences": [item.__dict__ for item in expected],
        "unexpected_differences": [item.__dict__ for item in unexpected],
        "data_errors": data_errors, "after": after,
        "result": "PASS" if not unexpected and not data_errors else "FAIL",
    }
    write_json(report_path, report_value)
    if unexpected or data_errors:
        details = [f"{item.path}: before={item.before!r} after={item.after!r}" for item in unexpected]
        details.extend(f"{row['path']}: expected={row['expected']} actual={row['actual']}" for row in data_errors)
        raise SurvivalError("update-survival mismatch:\n" + "\n".join(details))
    print(f"PASS fixture={manifest['fixture_id']} version={report_value['after_version']} "
          f"data_checksums={len(manifest['objects']['files']) + len(manifest['objects']['zvols'])}; "
          f"expected_differences={len(expected)} report={report_path}")
    return report_value


def perform_update(target: Target, *, expected_before: str, expected_after: str, train: str,
                   update_record: Path) -> None:
    require_v1001_target(target.host, target.port)
    for variable in ("TRUENAS_TEST_ALLOW_MUTATIONS", "TRUENAS_TEST_ALLOW_UPDATE", "TRUENAS_TEST_ALLOW_REBOOT"):
        require(os.environ.get(variable) == "1", f"set {variable}=1 only for a disposable TrueNAS update")
    info = target.call("system.info", timeout=120)
    current = info.get("version")
    required_profile = update_profile_for_train(train)
    if current == expected_after and update_record.is_file():
        previous = read_update_record(update_record)
        require(previous.get("schema_version") == 1 and previous.get("before") == expected_before
                and previous.get("after") == expected_after and previous.get("train") == train
                and previous.get("profile") == required_profile,
                f"completed update record does not match this exact transition: {previous!r}")
        print(f"PASS update already completed: {expected_before} -> {expected_after}; no second update started")
        return
    require(current == expected_before,
            f"expected TrueNAS {expected_before!r} before update, got {current!r}")
    check_no_running_guests(target)
    ensure_update_profile(target, train)
    metadata = select_update_manifest(
        target.call("update.available_versions", timeout=180),
        train=train,
        expected_after=expected_after,
    )
    free = int(target.shell("zfs get -Hp -o value available boot-pool"))
    required = metadata["filesize"]
    require(free > required, f"boot-pool has {free} free bytes; update requires more than {required}")
    if update_record.is_file():
        previous = read_update_record(update_record)
        require(previous.get("schema_version") == 1 and previous.get("before") == expected_before
                and previous.get("after") == expected_after
                and previous.get("train") == train
                and previous.get("profile") == required_profile
                and previous.get("manifest_checksum") == metadata["checksum"]
                and type(previous.get("job_id")) is int and previous["job_id"] > 0,
                f"prior update job record does not match this exact transition: {previous!r}")
        job_id = previous["job_id"]
        print(f"RESUME update.run job={job_id}; no second update will be started", flush=True)
    else:
        job_id = target.call("update.run", {
            "train": train,
            "version": expected_after,
            "reboot": False,
        }, timeout=120)
        require(isinstance(job_id, int) and not isinstance(job_id, bool) and job_id > 0,
                f"update.run returned an invalid job id: {job_id!r}")
        write_json(update_record, {"schema_version": 1, "job_id": job_id, "before": expected_before,
                                   "after": expected_after, "train": train, "profile": required_profile,
                                   "manifest_checksum": metadata["checksum"],
                                   "filename": metadata["filename"], "filesize": required})
        print(f"UPDATE_SELECTED train={train} version={expected_after} filename={metadata['filename']} "
              f"filesize={required} checksum={metadata['checksum']}", flush=True)
    wait_for_update_job(target, job_id)
    target.run(["systemctl", "reboot", "--no-block"], timeout=20, check=False)
    after = wait_for_api(target, expected_after)
    require(after.get("version") == expected_after, f"system reports unexpected post-update version: {after!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plant_parser = subparsers.add_parser("plant", help="plant fixtures and capture the midclt baseline")
    plant_parser.add_argument("--mode", choices=("vanilla", "patched"),
                              default=os.environ.get("TRUENAS_SURVIVAL_MODE"))
    plant_parser.add_argument("--profile", choices=("path-a", "path-b", "both"),
                              default=os.environ.get("TRUENAS_SURVIVAL_PROFILE"))
    plant_parser.add_argument("--manifest", type=Path,
                              default=Path(os.environ.get("TRUENAS_SURVIVAL_MANIFEST", ".")))
    verify_parser = subparsers.add_parser("verify", help="compare post-update midclt records and data hashes")
    verify_parser.add_argument("--manifest", type=Path,
                               default=Path(os.environ.get("TRUENAS_SURVIVAL_MANIFEST", ".")))
    verify_parser.add_argument("--report", type=Path,
                               default=Path(os.environ.get("TRUENAS_SURVIVAL_REPORT", ".")))
    update_parser = subparsers.add_parser("update", help="perform a guarded exact-version official update and reboot")
    update_parser.add_argument("--before", default=os.environ.get("TRUENAS_EXPECTED_VERSION", ""))
    update_parser.add_argument("--after", default=os.environ.get("TRUENAS_UPDATE_EXPECTED_VERSION", ""))
    update_parser.add_argument("--train", default=os.environ.get("TRUENAS_UPDATE_TRAIN", ""))
    update_parser.add_argument("--record", type=Path,
                               default=Path(os.environ.get("TRUENAS_SURVIVAL_UPDATE_RECORD", ".")))
    args = parser.parse_args(argv)
    try:
        target = Target.from_environment()
        if args.command == "plant":
            require(args.mode in {"vanilla", "patched"}, "TRUENAS_SURVIVAL_MODE must be vanilla or patched")
            require(args.profile in {"path-a", "path-b", "both"},
                    "TRUENAS_SURVIVAL_PROFILE must be path-a, path-b, or both")
            require(str(args.manifest) != ".", "TRUENAS_SURVIVAL_MANIFEST must name the local manifest file")
            plant(target, mode=args.mode, profile=args.profile, manifest_path=args.manifest)
        elif args.command == "verify":
            require(str(args.manifest) != "." and str(args.report) != ".",
                    "TRUENAS_SURVIVAL_MANIFEST and TRUENAS_SURVIVAL_REPORT must name local files")
            verify(target, args.manifest, args.report)
        else:
            require(args.before and args.after and args.train and str(args.record) != ".",
                    "update requires source/target versions, an update train, and a local job record path")
            perform_update(target, expected_before=args.before, expected_after=args.after,
                           train=args.train, update_record=args.record)
        return 0
    except RemoteError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
