#!/usr/bin/env python3
"""Run a registered TrueNAS live gate from a capped tester on a verified network.

run-gate's ephemeral environments intentionally have no network attachment.
This wrapper creates a short-lived tester-unified exec target on either a
managed guest's network or an explicitly selected external-host network, then
delegates the actual verdict to the installed Run-Gate CLI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

TRUENAS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TRUENAS_ROOT))
PATCH_ACCEPTANCE_ROOT = Path(__file__).resolve().parent / "acceptance"
if str(PATCH_ACCEPTANCE_ROOT) not in sys.path:
    sys.path.insert(0, str(PATCH_ACCEPTANCE_ROOT))

from tcg_vm import (
    VM_LABEL,
    VM_LABEL_VALUE,
    RunnerError,
    cgroup_levels,
    docker_inspect,
    docker_run,
    find_cgroup_inspector,
    host_pid_cgroup,
    inspect_current_container,
    resolve_docker_path,
    systemd_slice_path,
    validate_memory_headroom,
    validate_name,
)


TESTER_IMAGE = "tester-unified:local"
TESTER_NAME = "truenas-live-acceptance-runner"
TESTER_CPU_CORES = 0.5
TESTER_NANO_CPUS = 500_000_000
TESTER_MEMORY_BYTES = 512 * 1024 * 1024
TESTER_DOCKER_INSPECT_TIMEOUT_SECONDS = 30
TESTER_DOCKER_START_TIMEOUT_SECONDS = 120
RUNNER_LABEL = "org.vbpub.truenas.live-acceptance-run"
TESTER_LATE_CREATE_GRACE_SECONDS = 180
LIVE_LANES = frozenset({
    "live-storage", "live-boot", "live-update", "live-v27-patch", "live-v28-patch",
    "live-path-b-provision", "live-path-b-partition", "live-ui", "live-vdev-topology",
    "live-ui-workloads", "live-ui-auth", "live-ui-prepare-ssh", "live-ui-observed-v1001",
    "live-inspect", "live-api-probe",
})
UI_LANES = frozenset({
    "live-ui", "live-ui-workloads", "live-ui-auth", "live-ui-prepare-ssh",
    "live-vdev-topology", "live-ui-observed-v1001",
})
SSH_LANES = LIVE_LANES - {"live-ui-auth", "live-ui-prepare-ssh"}
EXTERNAL_TARGET_LANES = frozenset({
    "live-inspect", "live-v27-patch", "live-path-b-provision", "live-ui-auth",
    "live-ui-prepare-ssh", "live-path-b-partition", "live-ui", "live-ui-observed-v1001",
    "live-api-probe",
})
LIVE_LANE_TIMEOUT_SECONDS = {
    "live-storage": 2400,
    "live-boot": 2400,
    "live-update": 5400,
    "live-v27-patch": 2400,
    "live-v28-patch": 2400,
    "live-path-b-provision": 900,
    "live-path-b-partition": 900,
    "live-ui": 2700,
    "live-ui-auth": 900,
    "live-ui-prepare-ssh": 900,
    "live-ui-observed-v1001": 2700,
    "live-ui-workloads": 2700,
    "live-vdev-topology": 3600,
    "live-inspect": 900,
    "live-api-probe": 900,
}


def repo_path_in_tester(path: str, worktree_root: Path) -> str:
    if not path:
        raise RunnerError("TRUENAS_TEST_KEY and TRUENAS_TEST_KNOWN_HOSTS must name files")
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_file():
        raise RunnerError(f"test credential path is not a regular file: {resolved}")
    try:
        resolved.relative_to(worktree_root)
    except ValueError as exc:
        raise RunnerError(f"test credential path must be inside this worktree: {resolved}") from exc
    return str(resolved)


def normalized_docker_host_path(value: str, label: str) -> PurePosixPath:
    """Normalize a Docker-reported host path lexically, without local stat/resolve."""
    path = PurePosixPath(value)
    if not path.is_absolute():
        raise RunnerError(f"{label} must be an absolute Docker-host path: {value!r}")
    return PurePosixPath(posixpath.normpath(str(path)))


def run_gate_config(worktree_root: Path) -> dict[str, Any]:
    import tomllib

    config_path = worktree_root / "scripts" / "truenas" / "run-gate.toml"
    try:
        return tomllib.loads(config_path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RunnerError(f"cannot load TrueNAS run-gate config {config_path}: {exc}") from exc


def verified_vm_network(vm_name: str) -> tuple[dict[str, Any], str]:
    validate_name(vm_name)
    target = docker_inspect(vm_name)
    if not target.get("State", {}).get("Running"):
        raise RunnerError(f"TrueNAS VM container {vm_name!r} is not running")
    labels = target.get("Config", {}).get("Labels") or {}
    if labels.get(VM_LABEL) != VM_LABEL_VALUE:
        raise RunnerError(f"container {vm_name!r} is not managed by tcg_vm.py")
    networks = target.get("NetworkSettings", {}).get("Networks") or {}
    if len(networks) != 1:
        raise RunnerError(
            f"expected the local TrueNAS VM on exactly one Docker network; found {sorted(networks)}"
        )
    network = next(iter(networks))
    network_result = docker_run(["docker", "network", "inspect", network])
    try:
        network_details = json.loads(network_result.stdout)
    except json.JSONDecodeError as exc:
        raise RunnerError(f"Docker returned malformed network details for {network!r}") from exc
    if (not isinstance(network_details, list) or len(network_details) != 1
            or not isinstance(network_details[0], dict)
            or network_details[0].get("Driver") != "bridge"
            or network in {"bridge", "host", "none"}):
        raise RunnerError(f"TrueNAS guest is not on one verified custom bridge network: {network!r}")
    return target, network


def configured_vm_https_port(vm: dict[str, Any]) -> int:
    env = vm.get("Config", {}).get("Env") or []
    values = [entry.partition("=")[2] for entry in env
              if isinstance(entry, str) and entry.partition("=")[0] == "VM_HTTPS_PORT"]
    if len(values) != 1 or not values[0].isdecimal():
        raise RunnerError("managed TrueNAS VM must declare one numeric VM_HTTPS_PORT")
    port = int(values[0])
    if not 1 <= port <= 65535:
        raise RunnerError(f"managed TrueNAS VM has an invalid VM_HTTPS_PORT: {values[0]!r}")
    return port


def verify_network_alias(network: str, alias: str) -> str:
    if network in {"bridge", "host", "none"}:
        raise RunnerError(f"network {network!r} is not a verified custom bridge")
    result = docker_run(["docker", "network", "inspect", network])
    try:
        details = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RunnerError(f"Docker returned malformed network details for {network!r}") from exc
    if (not isinstance(details, list) or len(details) != 1
            or not isinstance(details[0], dict)
            or details[0].get("Driver") != "bridge"):
        raise RunnerError(f"network {network!r} is not one verified Docker bridge")
    attached = details[0].get("Containers") or {}
    for entry in attached.values():
        if not isinstance(entry, dict):
            continue
        name = entry.get("Name", "")
        if not name:
            continue
        candidate = docker_inspect(name)
        network_item = (candidate.get("NetworkSettings", {}).get("Networks") or {}).get(network, {})
        if alias in (network_item.get("Aliases") or []):
            return name
    raise RunnerError(f"custom bridge {network!r} has no container with DNS alias {alias!r}")


def verify_custom_bridge_network(network: str) -> str:
    validate_network_name(network)
    result = docker_run(["docker", "network", "inspect", network])
    try:
        details = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RunnerError(f"Docker returned malformed network details for {network!r}") from exc
    if (not isinstance(details, list) or len(details) != 1
            or not isinstance(details[0], dict)
            or details[0].get("Driver") != "bridge"):
        raise RunnerError(f"network {network!r} is not one verified Docker custom bridge")
    return network


def validate_network_name(network: str) -> str:
    if (not isinstance(network, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,126}", network) is None
            or network in {"bridge", "host", "none"}):
        raise RunnerError("--docker-network must name a custom Docker bridge")
    return network


def validate_external_ui_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise RunnerError("TRUENAS_UI_URL is not a valid HTTPS origin") from exc
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)):
        raise RunnerError("TRUENAS_UI_URL must be an HTTPS origin without credentials, path, query, or fragment")
    return value.rstrip("/")


def external_target(lane: str) -> tuple[str, str | None]:
    if lane not in EXTERNAL_TARGET_LANES:
        raise RunnerError(
            f"{lane} is not enabled for an external TrueNAS target; allowed: {sorted(EXTERNAL_TARGET_LANES)}"
        )
    host = os.environ.get("TRUENAS_TEST_HOST", "")
    if (not host or host != host.strip() or host.startswith("-")
            or any(char in host for char in "/\\@\x00 \t\r\n")):
        raise RunnerError("external-host mode requires TRUENAS_TEST_HOST to be a hostname or IP address")
    if lane in UI_LANES:
        ui_url = validate_external_ui_url(os.environ.get("TRUENAS_UI_URL", ""))
    else:
        ui_url = None
    return host, ui_url


def validate_high_psi_override(args: argparse.Namespace) -> bool:
    if args.allow_high_psi and args.docker_network is None:
        raise RunnerError("--allow-high-psi is only valid with an explicit external --docker-network")
    return bool(args.allow_high_psi)


def required_live_environment(
    lane: str,
    target_host: str,
    worktree_root: Path,
    *,
    ui_port: int | None = None,
    ui_url: str | None = None,
) -> dict[str, str]:
    if lane not in {"live-inspect", "live-ui-auth"} and os.environ.get("TRUENAS_TEST_ALLOW_MUTATIONS") != "1":
        raise RunnerError("set TRUENAS_TEST_ALLOW_MUTATIONS=1 only for a disposable TrueNAS VM")
    if not os.environ.get("TRUENAS_EXPECTED_VERSION"):
        raise RunnerError("TRUENAS_EXPECTED_VERSION is required")
    profile = os.environ.get("TRUENAS_STORAGE_PROFILE", "")
    if profile not in {"path-a", "path-b"}:
        raise RunnerError("TRUENAS_STORAGE_PROFILE must be path-a or path-b")
    if lane == "live-vdev-topology" and profile != "path-a":
        raise RunnerError("live-vdev-topology requires a fresh Path A guest with a UI-created boot-pool/user-data root")
    path_b_provision: dict[str, str] = {}
    if lane in {"live-path-b-provision", "live-path-b-partition"} or (
        lane == "live-api-probe" and profile == "path-b"
    ):
        if profile != "path-b":
            raise RunnerError("live-path-b-provision requires TRUENAS_STORAGE_PROFILE=path-b")
        if os.environ.get("TRUENAS_TEST_ALLOW_DISK_PROVISION") != "1":
            raise RunnerError(
                "set TRUENAS_TEST_ALLOW_DISK_PROVISION=1 only for a disposable disk target"
            )
        for variable in ("TRUENAS_DISK_DEVICE", "TRUENAS_BOOT_POOL_PARTITION"):
            value = os.environ.get(variable, "")
            if not value.startswith("/dev/"):
                raise RunnerError(f"{variable} must name an explicit /dev path")
            path_b_provision[variable] = value
        try:
            minimum_bytes = int(os.environ.get("TRUENAS_MINIMUM_FREE_BYTES", "1073741824"))
        except ValueError as exc:
            raise RunnerError("TRUENAS_MINIMUM_FREE_BYTES must be an integer") from exc
        if minimum_bytes <= 0:
            raise RunnerError("TRUENAS_MINIMUM_FREE_BYTES must be positive")
        path_b_provision["TRUENAS_MINIMUM_FREE_BYTES"] = str(minimum_bytes)
    if lane in {"live-boot", "live-update"}:
        if profile != "path-a":
            raise RunnerError(f"{lane} requires TRUENAS_STORAGE_PROFILE=path-a")
        if os.environ.get("TRUENAS_TEST_ALLOW_REBOOT") != "1":
            raise RunnerError("set TRUENAS_TEST_ALLOW_REBOOT=1 only when rebooting this VM is intended")
    patch_action = ""
    patch_replace_guard = ""
    prior_patch_bundle = ""
    v27_patch_action = ""
    v27_patch_repair_guard = ""
    v27_prior_bundle = ""
    v27_prior_bundle_digest = ""
    v27_prior_dataset_digest = ""
    v27_prior_dataset_info_digest = ""
    if lane == "live-v27-patch":
        v27_patch_action = os.environ.get("TRUENAS_V27_PATCH_ACTION", "install")
        if v27_patch_action not in {
            "install", "repair-root-create", "repair-root-parent-mountpoint",
            "repair-zvol-blocksize",
        }:
            raise RunnerError("TRUENAS_V27_PATCH_ACTION is not a supported v27 patch action")
        v27_patch_repair_guard = os.environ.get("TRUENAS_TEST_ALLOW_PATCH_REPAIR", "")
        v27_prior_bundle_digest = os.environ.get("TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256", "")
        prior_bundle_path = os.environ.get("TRUENAS_V27_PRIOR_PATCH_BUNDLE", "")
        v27_prior_dataset_digest = os.environ.get("TRUENAS_V27_EXPECTED_PRIOR_DATASET_SHA256", "")
        v27_prior_dataset_info_digest = os.environ.get(
            "TRUENAS_V27_EXPECTED_PRIOR_DATASET_INFO_SHA256", ""
        )
        if v27_patch_action in {
            "repair-root-create", "repair-root-parent-mountpoint", "repair-zvol-blocksize",
        }:
            if profile != "path-a":
                raise RunnerError("v27 storage repair requires TRUENAS_STORAGE_PROFILE=path-a")
            if v27_patch_repair_guard != "1":
                raise RunnerError("set TRUENAS_TEST_ALLOW_PATCH_REPAIR=1 only for a disposable v27 Path A repair")
            if not re.fullmatch(r"[0-9a-f]{64}", v27_prior_bundle_digest):
                raise RunnerError("repair requires TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256 from the installed bundle artifact")
            v27_prior_bundle = repo_path_in_tester(prior_bundle_path, worktree_root)
            if hashlib.sha256(Path(v27_prior_bundle).read_bytes()).hexdigest() != v27_prior_bundle_digest:
                raise RunnerError("prior v27 bundle does not match TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256")
            if v27_patch_action == "repair-root-parent-mountpoint":
                if not re.fullmatch(r"[0-9a-f]{64}", v27_prior_dataset_digest):
                    raise RunnerError("parent-mountpoint repair requires TRUENAS_V27_EXPECTED_PRIOR_DATASET_SHA256 from live-inspect")
                if v27_prior_dataset_info_digest:
                    raise RunnerError("TRUENAS_V27_EXPECTED_PRIOR_DATASET_INFO_SHA256 is only valid for zvol blocksize repair")
            elif v27_patch_action == "repair-zvol-blocksize":
                if not re.fullmatch(r"[0-9a-f]{64}", v27_prior_dataset_info_digest):
                    raise RunnerError("zvol blocksize repair requires TRUENAS_V27_EXPECTED_PRIOR_DATASET_INFO_SHA256 from live-inspect")
                if v27_prior_dataset_digest:
                    raise RunnerError("TRUENAS_V27_EXPECTED_PRIOR_DATASET_SHA256 is only valid for parent-mountpoint repair")
            elif v27_prior_dataset_digest or v27_prior_dataset_info_digest:
                raise RunnerError("TRUENAS_V27_EXPECTED_PRIOR_DATASET_SHA256 is only valid for parent-mountpoint repair")
        elif (
            v27_patch_repair_guard or prior_bundle_path or v27_prior_bundle_digest
            or v27_prior_dataset_digest or v27_prior_dataset_info_digest
        ):
            raise RunnerError("v27 repair variables require a guarded repair action")
    if lane == "live-v28-patch":
        patch_action = os.environ.get("TRUENAS_V28_PATCH_ACTION", "install")
        if patch_action not in {"install", "replace"}:
            raise RunnerError("TRUENAS_V28_PATCH_ACTION must be install or replace")
        patch_replace_guard = os.environ.get("TRUENAS_TEST_ALLOW_PATCH_REPLACE", "")
        prior_patch_bundle = os.environ.get("TRUENAS_V28_EXPECTED_PRIOR_BUNDLE_SHA256", "")
        if patch_action == "replace":
            if patch_replace_guard != "1":
                raise RunnerError("set TRUENAS_TEST_ALLOW_PATCH_REPLACE=1 only to replace the recorded v28 adapter")
            if not re.fullmatch(r"[0-9a-f]{64}", prior_patch_bundle):
                raise RunnerError("replace requires TRUENAS_V28_EXPECTED_PRIOR_BUNDLE_SHA256 from the installed manifest")
        elif patch_replace_guard or prior_patch_bundle:
            raise RunnerError("replacement guard and prior bundle digest are only valid with TRUENAS_V28_PATCH_ACTION=replace")
    update_paths: dict[str, str] = {}
    if lane == "live-update":
        if os.environ.get("TRUENAS_TEST_ALLOW_UPDATE") != "1":
            raise RunnerError("set TRUENAS_TEST_ALLOW_UPDATE=1 only for a disposable update target")
        if not os.environ.get("TRUENAS_UPDATE_EXPECTED_VERSION"):
            raise RunnerError("TRUENAS_UPDATE_EXPECTED_VERSION is required")
        for variable in ("TRUENAS_UPDATE_ISO", "TRUENAS_UPDATE_METADATA"):
            value = os.environ.get(variable, "")
            if not value:
                raise RunnerError(f"{variable} is required")
            resolved = Path(value).resolve(strict=True)
            if not resolved.is_file():
                raise RunnerError(f"{variable} is not a regular file: {resolved}")
            try:
                resolved.relative_to(worktree_root)
            except ValueError as exc:
                raise RunnerError(f"{variable} must be inside this worktree: {resolved}") from exc
            update_paths[variable] = str(resolved)
        workload_manifest = os.environ.get("TRUENAS_UI_WORKLOAD_MANIFEST", "")
        if workload_manifest:
            manifest_path = Path(workload_manifest).resolve(strict=True)
            if not manifest_path.is_file():
                raise RunnerError("TRUENAS_UI_WORKLOAD_MANIFEST must be a regular file")
            try:
                manifest_path.relative_to(worktree_root)
            except ValueError as exc:
                raise RunnerError(
                    "TRUENAS_UI_WORKLOAD_MANIFEST must be inside this worktree"
                ) from exc
            update_paths["TRUENAS_UI_WORKLOAD_MANIFEST"] = str(manifest_path)

    ui_settings: dict[str, str] = {}
    if lane in UI_LANES:
        if ui_url is None:
            if ui_port is None or not 1 <= ui_port <= 65535:
                raise RunnerError(f"{lane} requires a verified managed-VM port or external HTTPS URL")
            ui_url = f"https://{target_host}:{ui_port}"
        else:
            ui_url = validate_external_ui_url(ui_url)
        resume_existing_root = os.environ.get("TRUENAS_TEST_RESUME_EXISTING_UI_ROOT", "")
        if resume_existing_root not in {"", "1"}:
            raise RunnerError("TRUENAS_TEST_RESUME_EXISTING_UI_ROOT must be unset or 1")
        if resume_existing_root == "1" and profile != "path-a":
            raise RunnerError("TRUENAS_TEST_RESUME_EXISTING_UI_ROOT=1 is only valid for Path A")
        resume_existing_ui_dataset = os.environ.get("TRUENAS_TEST_RESUME_EXISTING_UI_DATASET", "")
        if resume_existing_ui_dataset and not re.fullmatch(
            r"boot-pool/user-data/ui-acceptance-[0-9]{14}[0-9a-f]{6}",
            resume_existing_ui_dataset,
        ):
            raise RunnerError(
                "TRUENAS_TEST_RESUME_EXISTING_UI_DATASET must be an exact "
                "boot-pool/user-data/ui-acceptance-<14 digits><6 lowercase hex> dataset path"
            )
        if resume_existing_ui_dataset and (profile != "path-a" or resume_existing_root != "1"):
            raise RunnerError(
                "TRUENAS_TEST_RESUME_EXISTING_UI_DATASET requires Path A and "
                "TRUENAS_TEST_RESUME_EXISTING_UI_ROOT=1"
            )
        stale_container_cleanup = os.environ.get("TRUENAS_TEST_CLEANUP_STALE_UI_CONTAINER", "")
        if stale_container_cleanup and not re.fullmatch(r"uiacceptance[0-9a-f]{8}", stale_container_cleanup):
            raise RunnerError(
                "TRUENAS_TEST_CLEANUP_STALE_UI_CONTAINER must be an exact uiacceptance<8 lowercase hex> name"
            )
        if stale_container_cleanup and (profile != "path-a" or resume_existing_root != "1"):
            raise RunnerError(
                "TRUENAS_TEST_CLEANUP_STALE_UI_CONTAINER requires Path A and "
                "TRUENAS_TEST_RESUME_EXISTING_UI_ROOT=1"
            )
        preserve_ui_workloads = os.environ.get("TRUENAS_TEST_PRESERVE_UI_WORKLOADS", "")
        if preserve_ui_workloads not in {"", "0", "1"}:
            raise RunnerError("TRUENAS_TEST_PRESERVE_UI_WORKLOADS must be unset, 0, or 1")
        if preserve_ui_workloads == "1" and profile != "path-a":
            raise RunnerError("TRUENAS_TEST_PRESERVE_UI_WORKLOADS=1 is only supported for Path A")
        if preserve_ui_workloads == "1" and lane != "live-ui":
            raise RunnerError("TRUENAS_TEST_PRESERVE_UI_WORKLOADS=1 is only valid for live-ui")
        workload_manifest = ""
        manifest_value = os.environ.get("TRUENAS_UI_WORKLOAD_MANIFEST", "")
        if preserve_ui_workloads == "1":
            if not manifest_value:
                raise RunnerError(
                    "TRUENAS_UI_WORKLOAD_MANIFEST is required when preserving UI workloads"
                )
            manifest_path = Path(manifest_value).resolve()
            try:
                manifest_path.relative_to(worktree_root)
            except ValueError as exc:
                raise RunnerError("TRUENAS_UI_WORKLOAD_MANIFEST must be inside this worktree") from exc
            if manifest_path.exists():
                raise RunnerError("refusing to overwrite existing TRUENAS_UI_WORKLOAD_MANIFEST")
            workload_manifest = str(manifest_path)
        elif lane == "live-ui-workloads":
            if not manifest_value:
                raise RunnerError("TRUENAS_UI_WORKLOAD_MANIFEST is required for live-ui-workloads")
            workload_manifest = repo_path_in_tester(manifest_value, worktree_root)
        elif manifest_value and lane in UI_LANES:
            raise RunnerError(
                "TRUENAS_UI_WORKLOAD_MANIFEST is only valid for preserve-enabled live-ui "
                "or live-ui-workloads"
            )
        if lane == "live-vdev-topology" and any((resume_existing_ui_dataset, stale_container_cleanup)):
            raise RunnerError(
                "live-vdev-topology can resume only the directly verified Path A UI root; "
                "it refuses a prior UI test dataset or stale-container cleanup"
            )
        ws_url = os.environ.get("PWMCP_WS_URL", "")
        if ws_url != "ws://pwmcp:3000/":
            raise RunnerError("PWMCP_WS_URL must use the attached network service ws://pwmcp:3000/")
        playwright_version = os.environ.get("PWMCP_PLAYWRIGHT_VERSION", "")
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", playwright_version):
            raise RunnerError("PWMCP_PLAYWRIGHT_VERSION must match the numeric version from `pwmcp contract`")
        secret_path = repo_path_in_tester(os.environ.get("TRUENAS_UI_SECRET_FILE", ""), worktree_root)
        secret_stat = Path(secret_path).stat()
        if stat.S_IMODE(secret_stat.st_mode) & 0o077:
            raise RunnerError("TrueNAS UI credential file must not be group/world accessible")
        ui_settings = {
            "TRUENAS_UI_URL": ui_url,
            "TRUENAS_UI_SECRET_FILE": secret_path,
            "PWMCP_WS_URL": ws_url,
            "PWMCP_PLAYWRIGHT_VERSION": playwright_version,
            "TRUENAS_TEST_RESUME_EXISTING_UI_ROOT": resume_existing_root,
            "TRUENAS_TEST_RESUME_EXISTING_UI_DATASET": resume_existing_ui_dataset,
            "TRUENAS_TEST_CLEANUP_STALE_UI_CONTAINER": stale_container_cleanup,
            "TRUENAS_TEST_PRESERVE_UI_WORKLOADS": preserve_ui_workloads,
            "TRUENAS_UI_WORKLOAD_MANIFEST": workload_manifest,
        }

    observed_patch_settings: dict[str, str] = {}
    observed_patch_guard = os.environ.get("TRUENAS_TEST_ALLOW_UNRECORDED_PATCH", "")
    observed_patch_manifest = os.environ.get("TRUENAS_OBSERVED_PATCH_MANIFEST", "")
    path_a_root_provision_guard = os.environ.get("TRUENAS_TEST_ALLOW_PATH_A_ROOT_PROVISION", "")
    if path_a_root_provision_guard not in {"", "1"}:
        raise RunnerError("TRUENAS_TEST_ALLOW_PATH_A_ROOT_PROVISION must be unset or 1")
    if lane == "live-ui-observed-v1001":
        if observed_patch_guard != "1":
            raise RunnerError(
                "set TRUENAS_TEST_ALLOW_UNRECORDED_PATCH=1 only for the pinned exploratory v1001 UI lane"
            )
        manifest_path = repo_path_in_tester(observed_patch_manifest, worktree_root)
        try:
            from observed_patch_manifest import load_observed_patch_manifest

            load_observed_patch_manifest(
                manifest_path,
                target_host=target_host,
                expected_version=os.environ["TRUENAS_EXPECTED_VERSION"],
                profile=profile,
            )
        except (KeyError, ValueError) as exc:
            raise RunnerError(f"invalid observed-patch UI fingerprint: {exc}") from exc
        if path_a_root_provision_guard == "1":
            if profile != "path-a":
                raise RunnerError("Path A root provisioning requires TRUENAS_STORAGE_PROFILE=path-a")
            if os.environ.get("TRUENAS_TEST_RESUME_EXISTING_UI_ROOT") == "1":
                raise RunnerError(
                    "Path A root provisioning and TRUENAS_TEST_RESUME_EXISTING_UI_ROOT are mutually exclusive"
                )
            observed_patch_settings["TRUENAS_TEST_ALLOW_PATH_A_ROOT_PROVISION"] = "1"
        observed_patch_settings = {
            "TRUENAS_TEST_ALLOW_UNRECORDED_PATCH": "1",
            "TRUENAS_OBSERVED_PATCH_MANIFEST": manifest_path,
            **observed_patch_settings,
        }
    elif observed_patch_guard or observed_patch_manifest or path_a_root_provision_guard:
        raise RunnerError(
            "TRUENAS_TEST_ALLOW_UNRECORDED_PATCH and TRUENAS_OBSERVED_PATCH_MANIFEST "
            "are only valid for live-ui-observed-v1001"
        )

    root_ssh_key_settings: dict[str, str] = {}
    if lane == "live-ui-prepare-ssh":
        public_key_path = repo_path_in_tester(
            os.environ.get("TRUENAS_ROOT_SSH_PUBLIC_KEY_FILE", ""), worktree_root
        )
        public_key_lines = [
            line.strip()
            for line in Path(public_key_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if len(public_key_lines) != 1 or not public_key_lines[0].startswith("ssh-ed25519 "):
            raise RunnerError(
                "TRUENAS_ROOT_SSH_PUBLIC_KEY_FILE must contain exactly one Ed25519 public key"
            )
        if shutil.which("ssh-keygen") is None:
            raise RunnerError("ssh-keygen is required to validate the configured root public key")
        try:
            key_check = subprocess.run(
                ["ssh-keygen", "-lf", public_key_path],
                text=True,
                capture_output=True,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise RunnerError("could not validate the configured root SSH public key") from None
        if key_check.returncode != 0 or "(ED25519)" not in key_check.stdout:
            raise RunnerError("TRUENAS_ROOT_SSH_PUBLIC_KEY_FILE is not a valid Ed25519 public key")
        root_ssh_key_settings["TRUENAS_ROOT_SSH_PUBLIC_KEY_FILE"] = public_key_path

    ssh_settings: dict[str, str] = {
        "TRUENAS_TEST_PORT": "",
        "TRUENAS_TEST_KEY": "",
        "TRUENAS_TEST_KNOWN_HOSTS": "",
        "_TRUENAS_TESTER_UID_GID": f"{os.getuid()}:{os.getgid()}",
    }
    if lane in SSH_LANES:
        try:
            port = int(os.environ.get("TRUENAS_TEST_PORT", ""))
        except ValueError as exc:
            raise RunnerError("TRUENAS_TEST_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise RunnerError("TRUENAS_TEST_PORT must be between 1 and 65535")

        key_path = repo_path_in_tester(os.environ.get("TRUENAS_TEST_KEY", ""), worktree_root)
        known_hosts_path = repo_path_in_tester(
            os.environ.get("TRUENAS_TEST_KNOWN_HOSTS", ""), worktree_root
        )
        key_stat = Path(key_path).stat()
        hosts_stat = Path(known_hosts_path).stat()
        if key_stat.st_uid == 0:
            raise RunnerError("the networked tester must run as a non-root SSH key owner")
        if stat.S_IMODE(key_stat.st_mode) & 0o077:
            raise RunnerError(f"SSH private key must not be group/world accessible: {key_path}")
        if (key_stat.st_uid, key_stat.st_gid) != (hosts_stat.st_uid, hosts_stat.st_gid):
            raise RunnerError("SSH private key and known_hosts must have the same owner for the tester")
        if lane in UI_LANES and (secret_stat.st_uid, secret_stat.st_gid) != (key_stat.st_uid, key_stat.st_gid):
            raise RunnerError("TrueNAS UI credential file and SSH key must have the same owner for the tester")
        ssh_settings = {
            "TRUENAS_TEST_PORT": str(port),
            "TRUENAS_TEST_KEY": key_path,
            "TRUENAS_TEST_KNOWN_HOSTS": known_hosts_path,
            "_TRUENAS_TESTER_UID_GID": f"{key_stat.st_uid}:{key_stat.st_gid}",
        }
    elif (secret_stat.st_uid, secret_stat.st_gid) != (os.getuid(), os.getgid()):
        raise RunnerError("TrueNAS UI credential file must be owned by the current non-root user")
    return {
        "TRUENAS_TEST_HOST": target_host,
        **ssh_settings,
        "TRUENAS_TEST_USER": os.environ.get("TRUENAS_TEST_USER", "root"),
        "TRUENAS_STORAGE_PROFILE": profile,
        "TRUENAS_EXPECTED_VERSION": os.environ["TRUENAS_EXPECTED_VERSION"],
        "TRUENAS_TEST_ALLOW_MUTATIONS": "" if lane in {"live-inspect", "live-ui-auth"} else "1",
        "TRUENAS_TEST_ALLOW_REBOOT": os.environ.get("TRUENAS_TEST_ALLOW_REBOOT", ""),
        "TRUENAS_TEST_ALLOW_UPDATE": os.environ.get("TRUENAS_TEST_ALLOW_UPDATE", ""),
        "TRUENAS_TEST_ALLOW_DISK_PROVISION": os.environ.get(
            "TRUENAS_TEST_ALLOW_DISK_PROVISION", ""
        ),
        "TRUENAS_V28_PATCH_ACTION": patch_action,
        "TRUENAS_TEST_ALLOW_PATCH_REPLACE": patch_replace_guard,
        "TRUENAS_V28_EXPECTED_PRIOR_BUNDLE_SHA256": prior_patch_bundle,
        "TRUENAS_V27_PATCH_ACTION": v27_patch_action,
        "TRUENAS_TEST_ALLOW_PATCH_REPAIR": v27_patch_repair_guard,
        "TRUENAS_V27_PRIOR_PATCH_BUNDLE": v27_prior_bundle,
        "TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256": v27_prior_bundle_digest,
        "TRUENAS_V27_EXPECTED_PRIOR_DATASET_SHA256": v27_prior_dataset_digest,
        "TRUENAS_V27_EXPECTED_PRIOR_DATASET_INFO_SHA256": v27_prior_dataset_info_digest,
        **path_b_provision,
        "TRUENAS_UPDATE_EXPECTED_VERSION": os.environ.get("TRUENAS_UPDATE_EXPECTED_VERSION", ""),
        **update_paths,
        **ui_settings,
        **root_ssh_key_settings,
        **observed_patch_settings,
    }


def current_checkout_mounts(worktree_root: Path) -> list[tuple[str, str]]:
    _, current = inspect_current_container()
    mounts = current.get("Mounts", [])
    physical_worktree = resolve_docker_path(worktree_root, mounts, writable=False)

    git_common_text = subprocess.run(
        ["git", "-C", str(worktree_root), "rev-parse", "--git-common-dir"],
        text=True,
        capture_output=True,
        check=False,
    )
    if git_common_text.returncode:
        raise RunnerError(f"cannot resolve this worktree's common git directory: {git_common_text.stderr.strip()}")
    git_common = Path(git_common_text.stdout.strip())
    if not git_common.is_absolute():
        git_common = worktree_root / git_common
    git_common = git_common.resolve(strict=True)
    physical_git_common = resolve_docker_path(git_common, mounts, writable=False)

    namespace_common = PurePosixPath(str(git_common))
    physical_common = PurePosixPath(physical_git_common)
    namespace_worktree = PurePosixPath(str(worktree_root))
    physical_tree = PurePosixPath(physical_worktree)
    mounts = [
        (physical_worktree, str(physical_tree)),
        (physical_worktree, str(namespace_worktree)),
        (physical_git_common, str(physical_common)),
        (physical_git_common, str(namespace_common)),
    ]
    for source, destination in mounts:
        if "," in source or "," in destination:
            raise RunnerError(f"Docker bind path contains a comma: {source!r} -> {destination!r}")
    return mounts


def verify_tester_container(
    *, name: str, network: str, slice_name: str, inspector: str,
    expected_cgroup: str, expected_user: str, expected_run_id: str,
) -> dict[str, Any]:
    item = docker_inspect(name)
    host = item.get("HostConfig", {})
    labels = item.get("Config", {}).get("Labels") or {}
    actual_networks = item.get("NetworkSettings", {}).get("Networks") or {}
    if not item.get("State", {}).get("Running"):
        raise RunnerError(f"tester container {name!r} stopped before the gate started")
    if host.get("CgroupParent", "").split("/")[-1] != slice_name:
        raise RunnerError(f"tester container has unexpected cgroup parent {host.get('CgroupParent')!r}")
    if host.get("NanoCpus") != TESTER_NANO_CPUS:
        raise RunnerError(f"tester container CPU limit differs from {TESTER_CPU_CORES:g} cores")
    if host.get("Memory") != TESTER_MEMORY_BYTES or host.get("MemorySwap") != TESTER_MEMORY_BYTES:
        raise RunnerError("tester container memory and swap limits do not match 512 MiB")
    if item.get("Config", {}).get("User") != expected_user:
        raise RunnerError(f"tester container user differs from SSH credential owner {expected_user!r}")
    if labels.get(RUNNER_LABEL) != expected_run_id:
        raise RunnerError("tester container ownership label changed during launch")
    if network not in actual_networks:
        raise RunnerError(f"tester container did not join the TrueNAS VM network {network!r}")

    pid = int(item["State"]["Pid"])
    cgroup = host_pid_cgroup(inspector, pid)
    if cgroup != expected_cgroup and not cgroup.startswith(expected_cgroup.rstrip("/") + "/"):
        raise RunnerError(f"tester container is outside {expected_cgroup}: {cgroup}")
    leaf = cgroup_levels(inspector, cgroup)[0]
    if leaf.get("memory_max") != TESTER_MEMORY_BYTES or leaf.get("cpu_max") != TESTER_CPU_CORES:
        raise RunnerError(f"tester container cgroup read-back differs from requested caps: {leaf}")
    return {
        "container": name,
        "network": network,
        "host_cgroup": cgroup,
        "cpu_cores": leaf["cpu_max"],
        "memory_max_bytes": leaf["memory_max"],
        "user": expected_user,
        "host_pid": pid,
    }


def inspect_tester_container(container_ref: str = TESTER_NAME) -> dict[str, Any] | None:
    inspected = docker_run(
        ["docker", "inspect", container_ref, "--format", "{{json .}}"],
        check=False,
        timeout=TESTER_DOCKER_INSPECT_TIMEOUT_SECONDS,
    )
    if inspected.returncode:
        missing = inspected.stderr.casefold()
        if "no such object" in missing or "no such container" in missing:
            return None
        raise RunnerError(
            f"could not determine network tester state: {inspected.stderr.strip()}"
        )
    try:
        item = json.loads(inspected.stdout)
    except json.JSONDecodeError as exc:
        raise RunnerError("Docker returned malformed network tester state") from exc
    if not isinstance(item, dict):
        raise RunnerError("Docker returned a non-object network tester state")
    return item


def cleanup_owned_tester(
    run_id: str,
    *,
    absent_grace_seconds: float = 0,
    container_ref: str = TESTER_NAME,
    require_stopped: bool = False,
    expected_image: str | None = None,
) -> None:
    def inspect_owned() -> dict[str, Any] | None:
        item = inspect_tester_container(container_ref)
        if item is None:
            return None
        labels = item.get("Config", {}).get("Labels") or {}
        if labels.get(RUNNER_LABEL) != run_id:
            raise RunnerError(
                f"container {container_ref!r} is now owned by another container; "
                "it was not stopped or removed"
            )
        if expected_image is not None and item.get("Config", {}).get("Image") != expected_image:
            raise RunnerError(
                f"container {container_ref!r} image changed during stale-runner recovery; "
                "it was not removed"
            )
        if require_stopped:
            state = item.get("State", {})
            if state.get("Running") is not False or state.get("Status") != "exited":
                raise RunnerError(
                    f"container {container_ref!r} is not confirmed exited; it was not removed"
                )
        return item

    item = inspect_owned()
    if item is None and absent_grace_seconds > 0:
        deadline = time.monotonic() + absent_grace_seconds
        while item is None and time.monotonic() < deadline:
            time.sleep(min(1, max(0, deadline - time.monotonic())))
            item = inspect_owned()
    if item is None:
        return
    if item.get("State", {}).get("Running"):
        stop_error = ""
        try:
            stopped = docker_run(
                ["docker", "stop", "--timeout", "10", TESTER_NAME],
                check=False,
                timeout=20,
            )
            if stopped.returncode:
                stop_error = (stopped.stderr or stopped.stdout).strip()
        except RunnerError as exc:
            stop_error = str(exc)

        item = inspect_owned()
        if item is None:
            return
        if item.get("State", {}).get("Running"):
            raise RunnerError(
                f"network tester remains running after its stop request; it was not removed"
                + (f": {stop_error}" if stop_error else "")
            )

    removed = docker_run(["docker", "rm", container_ref], check=False, timeout=20)
    if removed.returncode:
        item = inspect_owned()
        if item is None:
            return
        raise RunnerError(
            f"stopped network tester could not be removed: "
            f"{(removed.stderr or removed.stdout).strip()}"
        )


def recover_stale_tester() -> str:
    item = inspect_tester_container()
    if item is None:
        raise RunnerError("stale tester recovery was requested, but no network tester exists")
    state = item.get("State", {})
    if state.get("Running") is not False or state.get("Status") != "exited":
        raise RunnerError("stale tester recovery requires a container confirmed in exited state")
    if item.get("Config", {}).get("Image") != TESTER_IMAGE:
        raise RunnerError(
            f"stale tester recovery only accepts image {TESTER_IMAGE!r}"
        )
    labels = item.get("Config", {}).get("Labels") or {}
    run_id = labels.get(RUNNER_LABEL)
    if not isinstance(run_id, str) or re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
        raise RunnerError("stale tester has no valid live-gate ownership label")
    container_id = item.get("Id")
    if not isinstance(container_id, str) or re.fullmatch(r"[0-9a-f]{64}", container_id) is None:
        raise RunnerError("Docker did not return a valid tester container ID")

    # Pin cleanup to the inspected identity. If another wrapper replaces the
    # fixed name during recovery, the replacement is not the removal target.
    cleanup_owned_tester(
        run_id,
        container_ref=container_id,
        require_stopped=True,
        expected_image=TESTER_IMAGE,
    )
    return container_id


def export_ui_workload_manifest(tester_path: str, host_path: Path, gate_status: int) -> bool:
    """Copy the preserved UI manifest out before the read-only tester is removed."""
    present = docker_run(
        ["docker", "exec", TESTER_NAME, "test", "-f", tester_path],
        check=False,
        timeout=TESTER_DOCKER_INSPECT_TIMEOUT_SECONDS,
    )
    if present.returncode:
        if gate_status == 0:
            raise RunnerError("live-ui passed but did not produce its preserved workload manifest")
        print("[manifest-export] no preserved workload manifest was produced", flush=True)
        return False
    if host_path.exists():
        raise RunnerError(f"refusing to overwrite UI workload manifest: {host_path}")
    host_path.parent.mkdir(parents=True, exist_ok=True)
    copied = docker_run(
        ["docker", "cp", f"{TESTER_NAME}:{tester_path}", str(host_path)],
        check=False,
        timeout=TESTER_DOCKER_START_TIMEOUT_SECONDS,
    )
    if copied.returncode:
        detail = (copied.stderr or copied.stdout).strip()
        raise RunnerError(f"could not export UI workload manifest: {detail}")
    try:
        manifest = json.loads(host_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerError(f"exported UI workload manifest is unreadable: {type(exc).__name__}") from None
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("profile") != "path-a"
        or manifest.get("pool") != "boot-pool"
    ):
        raise RunnerError("exported UI workload manifest does not identify Path A on boot-pool")
    digest = hashlib.sha256(host_path.read_bytes()).hexdigest()
    print(f"[manifest-export] {host_path} sha256={digest}", flush=True)
    return True


def run_registered_lane(
    lane: str,
    worktree_root: Path,
    env: dict[str, str],
    *,
    allow_dirty: bool,
) -> int:
    """Delegate the verdict to the installed Run-Gate CLI."""
    run_gate = shutil.which("run-gate")
    if not run_gate:
        raise RunnerError(
            "installed Run-Gate CLI is not on PATH; activate /home/vscode/.venv first"
        )
    gate_argv = [run_gate, lane, "--worktree", str(worktree_root)]
    if allow_dirty:
        gate_argv.append("--allow-dirty")
    result = subprocess.run(
        gate_argv,
        cwd=TRUENAS_ROOT,
        env=env,
        check=False,
    )
    return result.returncode


def run(args: argparse.Namespace) -> int:
    if args.lane not in LIVE_LANES:
        raise RunnerError(f"unsupported live lane {args.lane!r}; choose one of {sorted(LIVE_LANES)}")
    allow_high_psi = validate_high_psi_override(args)

    if args.lane == "live-ui-observed-v1001" and args.docker_network is None:
        raise RunnerError("live-ui-observed-v1001 is restricted to its fingerprinted external host")

    worktree_root = Path(__file__).resolve().parents[3]
    if args.docker_network is not None:
        target_host, ui_url = external_target(args.lane)
        network = verify_custom_bridge_network(args.docker_network)
        vm = None
        ui_port = None
    else:
        target_host = args.vm_container
        ui_url = None
        vm, network = verified_vm_network(args.vm_container)
        ui_port = configured_vm_https_port(vm) if args.lane in UI_LANES else None
    child_env = required_live_environment(
        args.lane, target_host, worktree_root, ui_port=ui_port, ui_url=ui_url,
    )
    workload_manifest_export: tuple[Path, str] | None = None
    if args.lane == "live-ui" and child_env.get("TRUENAS_TEST_PRESERVE_UI_WORKLOADS") == "1":
        host_manifest = Path(os.environ["TRUENAS_UI_WORKLOAD_MANIFEST"]).resolve()
        tester_manifest = f"/tmp/truenas-ui-workloads-{uuid.uuid4().hex}.json"
        child_env["TRUENAS_UI_WORKLOAD_MANIFEST"] = tester_manifest
        workload_manifest_export = (host_manifest, tester_manifest)
    config = run_gate_config(worktree_root)
    environment = config.get("environments", {}).get("networked-tester", {})
    if environment.get("mode") != "exec" or environment.get("container_name") != TESTER_NAME:
        raise RunnerError("run-gate.toml does not declare the expected networked exec tester")

    if args.lane in UI_LANES:
        # The remote browser is another container. Confirm it shares this
        # custom bridge before starting the registered UI lane.
        verify_network_alias(network, "pwmcp")
    if args.lane == "live-update":
        if vm is None:
            raise RunnerError("live-update requires a managed QEMU VM with read-only update media")
        media = Path(child_env["TRUENAS_UPDATE_ISO"]).resolve(strict=True)
        _, current_container = inspect_current_container()
        host_media = resolve_docker_path(
            media, current_container.get("Mounts", []), writable=False
        )
        media_mounts = [
            row for row in vm.get("Mounts", [])
            if row.get("Destination") == "/media/truenas-readonly.iso"
        ]
        media_source_matches = False
        if len(media_mounts) == 1:
            media_source_matches = (
                normalized_docker_host_path(
                    media_mounts[0].get("Source", ""), "read-only ISO bind source"
                )
                == normalized_docker_host_path(host_media, "selected ISO host path")
            )
        if (not media_source_matches or media_mounts[0].get("RW") is not False):
            raise RunnerError(
                "live-update requires the selected ISO mounted read-only at "
                "/media/truenas-readonly.iso in the managed QEMU container"
            )
        if "VM_READONLY_MEDIA=/media/truenas-readonly.iso" not in (vm.get("Config", {}).get("Env") or []):
            raise RunnerError("managed QEMU container was not configured with the selected read-only media")
    slice_name = os.environ.get("CGROUP_PARENT_DEV_GATES", "")
    if not slice_name:
        raise RunnerError("CGROUP_PARENT_DEV_GATES is unset; refusing an unplaced tester container")
    inspector = find_cgroup_inspector()
    control_group = systemd_slice_path(inspector, slice_name)
    levels = cgroup_levels(inspector, control_group)
    validate_memory_headroom(
        levels,
        requested_cpus=TESTER_CPU_CORES,
        allow_memory_psi_pressure=allow_high_psi,
    )
    if allow_high_psi:
        print(
            "[admission] explicit PSI override: keeping finite-memory, current-memory, "
            "CPU-quota, and tester-container caps; skipping only PSI threshold refusals",
            flush=True,
        )
    if args.lane == "live-v27-patch":
        acceptance_dir = worktree_root / "scripts" / "truenas" / "boot-pool-patch" / "acceptance"
        sys.path.insert(0, str(acceptance_dir))
        from live_v27_patch import make_bundle

        bundle, digest = make_bundle(child_env["TRUENAS_STORAGE_PROFILE"])
        child_env["TRUENAS_V27_PATCH_BUNDLE"] = str(bundle.resolve(strict=True))
        child_env["TRUENAS_V27_PATCH_BUNDLE_SHA256"] = digest
        print(f"[patch-bundle] {bundle.name} sha256={digest}", flush=True)
    docker_run(["docker", "image", "inspect", TESTER_IMAGE])

    if args.recover_stale_tester:
        container_id = recover_stale_tester()
        print(f"[tester-recovery] removed exited runner container {container_id}", flush=True)

    existing = inspect_tester_container()
    if existing is not None:
        state = existing.get("State", {})
        raise RunnerError(
            f"networked tester {TESTER_NAME!r} already exists "
            f"(state={state.get('Status', 'unknown')}); inspect it before another live gate. "
            "Use --recover-stale-tester only for an exited tester-unified:local container "
            "with this runner's ownership label"
        )

    mounts = current_checkout_mounts(worktree_root)
    run_id = uuid.uuid4().hex
    argv = [
        "docker", "run", "--detach", "--init", "--name", TESTER_NAME,
        "--cgroup-parent", slice_name,
        "--cpus", str(TESTER_CPU_CORES),
        "--memory", str(TESTER_MEMORY_BYTES),
        "--memory-swap", str(TESTER_MEMORY_BYTES),
        "--pids-limit", "128",
        "--user", child_env["_TRUENAS_TESTER_UID_GID"],
        "--network", network,
        "--label", f"{RUNNER_LABEL}={run_id}",
        "--env", "GIT_CONFIG_GLOBAL=/tmp/truenas-live-gitconfig",
    ]
    for source, destination in mounts:
        argv.extend(["--mount", f"type=bind,src={source},dst={destination},readonly"])
    argv.extend([
        "--entrypoint", "/bin/bash", TESTER_IMAGE,
        "-lc", "git config --global --replace-all safe.directory '*' && "
        f"exec timeout --signal=TERM --kill-after=10s {LIVE_LANE_TIMEOUT_SECONDS[args.lane]}s sleep infinity",
    ])

    run_status: int | None = None
    cleanup_error: str | None = None
    launch_completed = False
    try:
        # Keep launch inside the cleanup scope: a Docker client error after
        # create/start must not leave an orphaned networked runner behind.
        docker_run(argv, timeout=TESTER_DOCKER_START_TIMEOUT_SECONDS)
        launch_completed = True
        placement = verify_tester_container(
            name=TESTER_NAME,
            network=network,
            slice_name=slice_name,
            inspector=inspector,
            expected_cgroup=control_group,
            expected_user=child_env["_TRUENAS_TESTER_UID_GID"],
            expected_run_id=run_id,
        )
        print("[tester] " + json.dumps(placement, sort_keys=True), flush=True)
        test_key = child_env["TRUENAS_TEST_KEY"]
        known_hosts = child_env["TRUENAS_TEST_KNOWN_HOSTS"]
        if test_key and known_hosts:
            access = docker_run([
                "docker", "exec", TESTER_NAME, "/bin/bash", "-lc",
                f"test -f {shlex.quote(test_key)} && test -r {shlex.quote(test_key)} && "
                f"test -f {shlex.quote(known_hosts)} && test -r {shlex.quote(known_hosts)}",
            ], check=False)
            if access.returncode:
                raise RunnerError("tester user cannot read the selected SSH key or known_hosts file")

        gate_env = os.environ.copy()
        gate_env.update(child_env)
        gate_env.pop("_TRUENAS_TESTER_UID_GID", None)
        run_status = run_registered_lane(
            args.lane, worktree_root, gate_env, allow_dirty=args.allow_dirty,
        )
        if workload_manifest_export is not None:
            export_ui_workload_manifest(
                workload_manifest_export[1], workload_manifest_export[0], run_status,
            )
    finally:
        try:
            cleanup_owned_tester(
                run_id,
                absent_grace_seconds=(
                    0 if launch_completed else TESTER_LATE_CREATE_GRACE_SECONDS
                ),
            )
        except RunnerError as exc:
            cleanup_error = str(exc)
    if cleanup_error:
        raise RunnerError(cleanup_error)
    assert run_status is not None
    return run_status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lane", choices=sorted(LIVE_LANES))
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--vm-container", help="running tcg_vm.py container name")
    target.add_argument(
        "--docker-network",
        help="custom Docker bridge for an external host; requires TRUENAS_TEST_HOST",
    )
    parser.add_argument(
        "--allow-high-psi", action="store_true",
        help=(
            "for one external-host run only, skip memory PSI thresholds while retaining "
            "finite-memory, hard-current, CPU-quota, and tester-container limits"
        ),
    )
    parser.add_argument(
        "--allow-dirty", action="store_true",
        help="pass run-gate's explicit dirty-worktree opt-in",
    )
    parser.add_argument(
        "--recover-stale-tester", action="store_true",
        help=(
            "remove an existing tester only when it is exited, uses tester-unified:local, "
            "and carries this runner's valid ownership label"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        return run(build_parser().parse_args(argv))
    except (OSError, ValueError, RunnerError) as exc:
        print(f"run-live-gate.py: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
