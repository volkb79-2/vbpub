"""Read-only host facts collection from an injectable proc/sys root."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import re
import shlex
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .contracts import (
    CONTROL_PLANE_SERVICE_UNITS,
    DEFAULT_CONTROL_PLANE_PROFILE_RESERVE_BYTES,
    ContractError,
    validate_host_facts,
)


DEFAULT_ROOT = Path("/")
DEFAULT_CONFIG_PATH = "/etc/fleetd/facts.toml"
_BENCHMARK_SCHEMA = "mdt-host-cpu-benchmark/1"
_BENCHMARK_BASELINE = {6: 2500, 7: 2500}


@dataclass(frozen=True)
class FactsConfig:
    geekbench_record: str
    host_setup_env: str
    guard_config: str
    guard_state: str | None
    ksm_optin_shim: str | None
    control_plane_slice: str
    control_plane_profile_reserve_bytes: int
    overcommit: float
    oversubscribe_max: float
    psi_refuse_some_avg10: float
    max_lane_containers: int | None
    gate_slice: str | None
    checkout_root: str | None
    docker_root: str | None
    host_reserve_bytes: int | None
    planning_cap: float | None
    max_footprint: str | None
    max_planned_bytes: int | None
    footprint_charges_bytes: dict[str, int] | None
    priority_classes_by_principal: dict[str, list[str]] | None
    disk_floors: dict[str, int] | None
    max_upload_bytes: int | None
    max_upload_store_bytes: int | None
    admission: dict[str, Any] | None
    aging_s: float | None
    legacy_charge_bytes: int | None
    legacy_planned_cores: float | None


def _object(value: object, where: str, required: set[str], optional: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{where}: expected table")
    optional = optional or set()
    extra = sorted(set(value) - required - optional)
    missing = sorted(required - set(value))
    if extra:
        raise ContractError(f"{where}: unknown key(s): {', '.join(extra)}")
    if missing:
        raise ContractError(f"{where}: missing key(s): {', '.join(missing)}")
    return value


def _positive_number(value: object, where: str, *, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{where}: expected finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ContractError(f"{where}: expected finite number") from exc
    if not math.isfinite(number):
        raise ContractError(f"{where}: expected finite number")
    if number <= 0:
        raise ContractError(f"{where}: must be greater than zero")
    if maximum is not None and number > maximum:
        raise ContractError(f"{where}: must be at most {maximum}")
    return number


def _positive_integer(value: object, where: str) -> int:
    if type(value) is not int or value <= 0:
        raise ContractError(f"{where}: expected positive integer bytes")
    return value


def _config_path(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ContractError(f"{where}: expected non-empty absolute path")
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts or value == "/":
        raise ContractError(f"{where}: expected absolute path without '..' components")
    return value


def _slice_name(value: object, where: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_.-]+\.slice", value) is None:
        raise ContractError(f"{where}: expected a systemd .slice unit name")
    return value


def _optional_config_path(table: dict[str, Any], key: str, where: str) -> str | None:
    return None if key not in table else _config_path(table[key], f"{where}.{key}")


def _optional_bytes(value: object, where: str) -> int:
    return _positive_integer(value, where)


def _positive_bytes_map(value: object, where: str, *, allowed: set[str] | None = None) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ContractError(f"{where}: expected table")
    result: dict[str, int] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ContractError(f"{where}: expected non-empty keys")
        if allowed is not None and key not in allowed:
            raise ContractError(f"{where}: unknown key {key!r}")
        result[key] = _optional_bytes(item, f"{where}.{key}")
    return result


def _priority_map(value: object, where: str) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        raise ContractError(f"{where}: expected table")
    allowed = {"merge-gate", "dev", "gate", "campaign"}
    result: dict[str, list[str]] = {}
    for principal, classes in value.items():
        if not isinstance(principal, str) or not principal.strip():
            raise ContractError(f"{where}: expected non-empty principal names")
        if not isinstance(classes, list) or any(not isinstance(item, str) or item not in allowed for item in classes):
            raise ContractError(f"{where}.{principal}: expected an array of allowed priority classes")
        if len(set(classes)) != len(classes):
            raise ContractError(f"{where}.{principal}: duplicate priority class")
        result[principal] = list(classes)
    return result


def _admission_config(value: object, where: str) -> dict[str, Any]:
    obj = _object(value, where, {"queue_path", "wait_s", "settle_s"})
    queue_path = _config_path(obj["queue_path"], f"{where}.queue_path")
    result: dict[str, Any] = {"queue_path": queue_path}
    for key in ("wait_s", "settle_s"):
        result[key] = _positive_number(obj[key], f"{where}.{key}")
    return result


def load_facts_config(root: Path = DEFAULT_ROOT) -> FactsConfig:
    """Read and validate the required, versioned host-owned facts config."""
    config_path = _rooted(root, DEFAULT_CONFIG_PATH)
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"facts config {DEFAULT_CONFIG_PATH}: cannot read required config ({exc.strerror or 'I/O error'})") from exc
    except UnicodeError as exc:
        raise ContractError(f"facts config {DEFAULT_CONFIG_PATH}: config is not UTF-8 text") from exc
    try:
        parsed = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise ContractError(f"facts config {DEFAULT_CONFIG_PATH}: invalid TOML: {exc}") from exc
    top = _object(
        parsed, "facts.toml", {"schema_version", "paths", "control_plane", "policy"}, {"gate_slice"},
    )
    if type(top["schema_version"]) is not int or top["schema_version"] != 1:
        raise ContractError("facts.toml.schema_version: expected integer 1")

    paths = _object(
        top["paths"], "facts.toml.paths",
        {"geekbench_record", "guard_config", "host_setup_env"},
        {"guard_state", "ksm_optin_shim", "checkout_root", "docker_root"},
    )
    geekbench_record = _config_path(paths["geekbench_record"], "facts.toml.paths.geekbench_record")
    host_setup_env = _config_path(paths["host_setup_env"], "facts.toml.paths.host_setup_env")
    guard_config = _config_path(paths["guard_config"], "facts.toml.paths.guard_config")
    guard_state = None if "guard_state" not in paths else _config_path(paths["guard_state"], "facts.toml.paths.guard_state")
    ksm_optin_shim = None if "ksm_optin_shim" not in paths else _config_path(paths["ksm_optin_shim"], "facts.toml.paths.ksm_optin_shim")
    checkout_root = _optional_config_path(paths, "checkout_root", "facts.toml.paths")
    docker_root = _optional_config_path(paths, "docker_root", "facts.toml.paths")

    gate_slice_table = top.get("gate_slice")
    if gate_slice_table is None:
        gate_slice = None
    else:
        gate_table = _object(gate_slice_table, "facts.toml.gate_slice", {"slice"})
        gate_slice = _slice_name(gate_table["slice"], "facts.toml.gate_slice.slice")

    control = _object(
        top["control_plane"], "facts.toml.control_plane", {"slice"},
        {"profile_reserve_bytes"},
    )
    control_slice = _slice_name(control["slice"], "facts.toml.control_plane.slice")
    profile_reserve_bytes = _positive_integer(
        control.get("profile_reserve_bytes", DEFAULT_CONTROL_PLANE_PROFILE_RESERVE_BYTES),
        "facts.toml.control_plane.profile_reserve_bytes",
    )

    policy = _object(
        top["policy"], "facts.toml.policy",
        {"overcommit", "oversubscribe_max", "psi_refuse_some_avg10"},
        {
            "max_lane_containers", "host_reserve_bytes", "planning_cap", "max_footprint",
            "max_planned_bytes", "footprint_charges_bytes", "priority_classes_by_principal",
            "disk_floors", "max_upload_bytes", "max_upload_store_bytes", "admission", "aging_s",
            "legacy_charge_bytes", "legacy_planned_cores",
        },
    )
    overcommit = _positive_number(policy["overcommit"], "facts.toml.policy.overcommit", maximum=10)
    oversubscribe_max = _positive_number(policy["oversubscribe_max"], "facts.toml.policy.oversubscribe_max", maximum=10)
    psi_refuse = policy["psi_refuse_some_avg10"]
    try:
        psi_refuse_number = float(psi_refuse) if not isinstance(psi_refuse, bool) else math.nan
    except (TypeError, OverflowError):
        psi_refuse_number = math.nan
    if not isinstance(psi_refuse, (int, float)) or not math.isfinite(psi_refuse_number) or not 0 <= psi_refuse_number <= 100:
        raise ContractError("facts.toml.policy.psi_refuse_some_avg10: expected finite number from 0 through 100")
    max_lane = policy.get("max_lane_containers")
    if max_lane is not None and (type(max_lane) is not int or max_lane < 1):
        raise ContractError("facts.toml.policy.max_lane_containers: expected positive integer")

    host_reserve = policy.get("host_reserve_bytes")
    if host_reserve is not None:
        host_reserve = _optional_bytes(host_reserve, "facts.toml.policy.host_reserve_bytes")
    planning_cap = policy.get("planning_cap")
    if planning_cap is not None:
        planning_cap = _positive_number(planning_cap, "facts.toml.policy.planning_cap", maximum=10000)
    max_footprint = policy.get("max_footprint")
    if max_footprint is not None and (
        not isinstance(max_footprint, str) or max_footprint not in {"tiny", "small", "medium", "large"}
    ):
        raise ContractError("facts.toml.policy.max_footprint: expected tiny, small, medium, or large")
    max_planned_bytes = policy.get("max_planned_bytes")
    if max_planned_bytes is not None:
        max_planned_bytes = _optional_bytes(max_planned_bytes, "facts.toml.policy.max_planned_bytes")
    footprint_charges_bytes = (
        _positive_bytes_map(
            policy["footprint_charges_bytes"], "facts.toml.policy.footprint_charges_bytes",
            allowed={"tiny", "small", "medium", "large"},
        )
        if "footprint_charges_bytes" in policy else None
    )
    priority_classes_by_principal = (
        _priority_map(policy["priority_classes_by_principal"], "facts.toml.policy.priority_classes_by_principal")
        if "priority_classes_by_principal" in policy else None
    )
    disk_floors = (
        _positive_bytes_map(policy["disk_floors"], "facts.toml.policy.disk_floors")
        if "disk_floors" in policy else None
    )
    max_upload_bytes = policy.get("max_upload_bytes")
    if max_upload_bytes is not None:
        max_upload_bytes = _optional_bytes(max_upload_bytes, "facts.toml.policy.max_upload_bytes")
    max_upload_store_bytes = policy.get("max_upload_store_bytes")
    if max_upload_store_bytes is not None:
        max_upload_store_bytes = _optional_bytes(
            max_upload_store_bytes, "facts.toml.policy.max_upload_store_bytes",
        )
    admission = (
        _admission_config(policy["admission"], "facts.toml.policy.admission")
        if "admission" in policy else None
    )
    aging_s = (
        _positive_number(policy["aging_s"], "facts.toml.policy.aging_s")
        if "aging_s" in policy else None
    )
    legacy_charge_bytes = (
        _positive_integer(policy["legacy_charge_bytes"], "facts.toml.policy.legacy_charge_bytes")
        if "legacy_charge_bytes" in policy else None
    )
    legacy_planned_cores = (
        _positive_number(policy["legacy_planned_cores"], "facts.toml.policy.legacy_planned_cores")
        if "legacy_planned_cores" in policy else None
    )

    return FactsConfig(
        geekbench_record, host_setup_env, guard_config, guard_state, ksm_optin_shim,
        control_slice, profile_reserve_bytes, overcommit, oversubscribe_max,
        psi_refuse_number, max_lane, gate_slice, checkout_root, docker_root, host_reserve,
        planning_cap, max_footprint, max_planned_bytes, footprint_charges_bytes,
        priority_classes_by_principal, disk_floors, max_upload_bytes, max_upload_store_bytes, admission,
        aging_s, legacy_charge_bytes, legacy_planned_cores,
    )


def _rooted(root: Path, absolute_path: str) -> Path:
    """Map a configured host-absolute path beneath the supplied test root."""
    path = PurePosixPath(absolute_path)
    if not path.is_absolute() or ".." in path.parts:
        raise ContractError(f"configured path {absolute_path!r} is not a safe absolute path")
    return root / Path(*path.parts[1:])


def _read_text(path: Path, description: str) -> tuple[str, str | None]:
    try:
        return path.read_text(encoding="utf-8"), None
    except OSError as exc:
        return "", f"{description} unavailable ({exc.strerror or 'I/O error'})"
    except UnicodeError:
        return "", f"{description} is not UTF-8 text"


def _fact(value: Any, reason: str | None = None) -> dict[str, Any]:
    if value is None:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("an unknown fact requires a non-empty reason")
        return {"v": None, "reason": reason}
    return {"v": value}


def _configured_policy_fact(value: Any, key: str, unit: str) -> dict[str, Any]:
    """Export an optional policy input with its declared source and unit."""
    fact: dict[str, Any] = {
        "v": value,
        "unit": unit,
        "source": DEFAULT_CONFIG_PATH,
    }
    if value is None:
        fact["reason"] = f"not-configured: facts.toml.policy.{key}"
    return fact


def _agent_version() -> str:
    """Return installed fleetd metadata as SemVer without adding a host dependency."""
    try:
        raw = importlib.metadata.version("fleetd")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ContractError("fleetd agent version metadata is unavailable") from exc
    semver = re.fullmatch(
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
        r"(?:\.(dev|post|a|b|rc)([0-9]+))?"
        r"(?:\+([0-9A-Za-z.-]+))?",
        raw,
    )
    if semver is None:
        raise ContractError(f"fleetd distribution version {raw!r} cannot be represented as SemVer")
    major, minor, patch, phase, number, build = semver.groups()
    version = f"{major}.{minor}.{patch}"
    if phase is not None:
        label = {"dev": "dev", "post": "post", "a": "alpha", "b": "beta", "rc": "rc"}[phase]
        version += f"-{label}.{number}"
    if build is not None:
        version += f"+{build}"
    return version


def _load_values(proc_root: Path) -> tuple[dict[str, float | None], dict[str, str]]:
    source = "/proc/loadavg"
    text, read_reason = _read_text(proc_root / "loadavg", source)
    names = ("load1", "load5", "load15")
    if read_reason is not None:
        return {name: None for name in names}, {name: read_reason for name in names}
    fields = (text or "").split()
    values: dict[str, float | None] = {}
    reasons: dict[str, str] = {}
    for index, name in enumerate(names):
        try:
            value = float(fields[index])
        except (IndexError, ValueError):
            values[name] = None
            reasons[name] = f"{source} is missing or has a malformed {name} value"
            continue
        if not math.isfinite(value) or value < 0:
            values[name] = None
            reasons[name] = f"{source} {name} is outside the non-negative finite range"
            continue
        values[name] = value
    return values, reasons


def _host_role(root: Path, absolute_path: str) -> tuple[str | None, str | None]:
    """Read the authoritative HOST_ROLE without evaluating the shell file."""
    text, read_reason = _read_text(_rooted(root, absolute_path), "host-setup.env")
    if read_reason is not None:
        return None, read_reason
    roles: list[str] = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"HOST_ROLE=(.*)", line)
        if match is None:
            continue
        try:
            values = shlex.split(match.group(1), comments=True, posix=True)
        except ValueError as exc:
            return None, f"HOST_ROLE quoting is invalid on line {line_number}: {exc}"
        if len(values) > 1:
            return None, f"HOST_ROLE has unquoted whitespace on line {line_number}"
        roles.append(values[0] if values else "")
    if not roles:
        return None, "HOST_ROLE is absent from host-setup.env"
    if len(roles) != 1:
        return None, "HOST_ROLE appears more than once in host-setup.env"
    if roles[0] not in {"dedicated-dev", "shared-prod"}:
        return None, f"HOST_ROLE is missing or invalid in host-setup.env ({roles[0]!r})"
    return roles[0], None


def _read_bytes(path: Path, description: str) -> tuple[bytes | None, str | None]:
    try:
        return path.read_bytes(), None
    except OSError as exc:
        return None, f"{description} unavailable ({exc.strerror or 'I/O error'})"


def _nonempty_text(path: Path, description: str) -> tuple[str | None, str | None]:
    text, reason = _read_text(path, description)
    if reason is not None:
        return None, reason
    value = text.strip()
    return (value, None) if value else (None, f"{description} is empty")


def _cpu_model(proc_root: Path) -> tuple[str | None, str | None]:
    text, reason = _read_text(proc_root / "cpuinfo", "/proc/cpuinfo")
    if reason is not None:
        return None, reason
    for line in (text or "").splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() in {"model name", "Hardware"}:
            collapsed = " ".join(value.split())
            if collapsed:
                return collapsed, None
    return None, "CPU model field is absent from /proc/cpuinfo"


def _cpu_list_count(text: str) -> int | None:
    value = text.strip()
    if not value:
        return None
    ranges: list[tuple[int, int]] = []
    for part in value.split(","):
        low_text, dash, high_text = part.partition("-")
        if not low_text.isdecimal() or (dash and not high_text.isdecimal()):
            return None
        low = int(low_text)
        high = int(high_text) if dash else low
        if high < low:
            return None
        ranges.append((low, high))
    ranges.sort()
    count = 0
    previous_high = -1
    for low, high in ranges:
        if low <= previous_high:
            return None
        count += high - low + 1
        previous_high = high
    return count if count else None


def _online_cpu_count(sys_root: Path) -> tuple[int | None, str | None]:
    text, reason = _read_text(sys_root / "devices/system/cpu/online", "/sys/devices/system/cpu/online")
    if reason is not None:
        return None, reason
    count = _cpu_list_count(text or "")
    if count is None:
        return None, "CPU online list is empty or malformed"
    return count, None


def _meminfo_values(proc_root: Path) -> dict[str, tuple[int | None, str | None]]:
    required = {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}
    text, read_reason = _read_text(proc_root / "meminfo", "/proc/meminfo")
    if read_reason is not None:
        return {key: (None, read_reason) for key in required}
    found: dict[str, tuple[int | None, str | None]] = {}
    for line in (text or "").splitlines():
        key, separator, raw_value = line.partition(":")
        if key not in required or not separator:
            continue
        if key in found:
            found[key] = (None, f"{key} is duplicated in /proc/meminfo")
            continue
        match = re.fullmatch(r"\s*(\d+)\s+kB\s*", raw_value)
        if match is None:
            found[key] = (None, f"{key} is malformed in /proc/meminfo")
        else:
            found[key] = (int(match.group(1)) * 1024, None)
    for key in required - set(found):
        found[key] = (None, f"{key} is absent from /proc/meminfo")
    return found


def _psi_value(line: str | None, pressure: str, source: str) -> tuple[float | None, str | None]:
    if line is None:
        return None, f"{source} has no {pressure} line"
    for token in line.split()[1:]:
        key, separator, raw_value = token.partition("=")
        if key == "avg10" and separator:
            try:
                value = float(raw_value)
            except ValueError:
                return None, f"{source} {pressure} avg10 is malformed"
            if not math.isfinite(value) or not 0 <= value <= 100:
                return None, f"{source} {pressure} avg10 is outside 0 through 100"
            return value, None
    return None, f"{source} {pressure} avg10 is absent"


def _psi_window_value(line: str | None, pressure: str, window: int, source: str) -> tuple[float | None, str | None]:
    if line is None:
        return None, f"{source} has no {pressure} line"
    key_name = f"avg{window}"
    for token in line.split()[1:]:
        key, separator, raw_value = token.partition("=")
        if key != key_name:
            continue
        if not separator:
            break
        try:
            value = float(raw_value)
        except ValueError:
            return None, f"{source} {pressure} {key_name} is malformed"
        if not math.isfinite(value) or not 0 <= value <= 100:
            return None, f"{source} {pressure} {key_name} is outside 0 through 100"
        return value, None
    return None, f"{source} {pressure} {key_name} is absent"


def _read_psi(proc_root: Path, name: str) -> dict[str, Any]:
    source = f"/proc/pressure/{name}"
    text, reason = _read_text(proc_root / "pressure" / name, source)
    if reason is not None:
        return {
            f"{pressure}_avg{window}_pct": _fact(None, reason)
            for pressure in ("some", "full") for window in (10, 60, 300)
        }
    lines = {line.split()[0]: line for line in (text or "").splitlines() if line.split()}
    result: dict[str, Any] = {}
    for pressure in ("some", "full"):
        for window in (10, 60, 300):
            value, field_reason = _psi_window_value(lines.get(pressure), pressure, window, source)
            result[f"{pressure}_avg{window}_pct"] = _fact(value, field_reason)
    return result


def _read_integer(path: Path, description: str, *, minimum: int = 0) -> tuple[int | None, str | None]:
    text, reason = _nonempty_text(path, description)
    if reason is not None:
        return None, reason
    value = (text or "").strip()
    if not value.isdecimal():
        return None, f"{description} is not a non-negative integer"
    number = int(value)
    if number < minimum:
        return None, f"{description} is below {minimum}"
    return number, None


def _parse_systemd_memory_min(text: str, unit: str) -> tuple[int | None, str | None]:
    """Parse one `systemctl show -p MemoryMin --value` result."""
    value = text.strip()
    if not value:
        return None, f"{unit} MemoryMin is unset"
    if "\n" in value or "\r" in value:
        return None, f"{unit} MemoryMin output is not one value"
    if "=" in value:
        key, separator, raw_value = value.partition("=")
        if not separator or key != "MemoryMin":
            return None, f"{unit} systemctl output is not a MemoryMin property"
        value = raw_value.strip()
    if value.lower() == "infinity":
        return None, f"{unit} MemoryMin is infinity"
    if not value.isdecimal():
        return None, f"{unit} MemoryMin is not a non-negative integer"
    number = int(value)
    if number == 0:
        return None, f"{unit} MemoryMin is zero or unset"
    return number, None


def _parse_systemd_load_state(text: str, unit: str) -> tuple[str | None, str | None]:
    """Parse one `systemctl show -p LoadState --value` result."""
    value = text.strip()
    if not value:
        return None, f"{unit} LoadState is unset"
    if "\n" in value or "\r" in value or " " in value:
        return None, f"{unit} LoadState output is not one value"
    if "=" in value:
        key, separator, raw_value = value.partition("=")
        if not separator or key != "LoadState":
            return None, f"{unit} systemctl output is not a LoadState property"
        value = raw_value.strip()
    if not value:
        return None, f"{unit} LoadState is unset"
    return value, None


def _read_systemd_property(unit: str, prop: str) -> tuple[str | None, str | None]:
    """Read one bounded `systemctl show` property without an unbounded call."""
    try:
        result = subprocess.run(
            ["systemctl", "show", "-p", prop, "--value", "--no-pager", unit],
            check=False, capture_output=True, text=True, timeout=5,
        )
    except subprocess.TimeoutExpired:
        return None, f"systemctl show {prop} for {unit} timed out"
    except OSError as exc:
        return None, f"systemctl show {prop} for {unit} unavailable ({exc.strerror or 'I/O error'})"
    if result.returncode != 0:
        return None, f"systemctl show {prop} for {unit} exited {result.returncode}"
    return result.stdout, None


def _read_systemd_memory_min(unit: str) -> tuple[int | None, str | None]:
    """Read one service's MemoryMin without allowing an unbounded systemctl call."""
    output, reason = _read_systemd_property(unit, "MemoryMin")
    if reason is not None:
        return None, reason
    return _parse_systemd_memory_min(output or "", unit)


def _read_systemd_load_state(unit: str) -> tuple[str | None, str | None]:
    """Read one service's LoadState without allowing an unbounded systemctl call."""
    output, reason = _read_systemd_property(unit, "LoadState")
    if reason is not None:
        return None, reason
    return _parse_systemd_load_state(output or "", unit)


def _service_memory_min_facts(
    reader: Callable[[str], tuple[int | None, str | None]],
    load_state_reader: Callable[[str], tuple[str | None, str | None]] | None,
) -> dict[str, dict[str, Any]]:
    """Collect the fixed-size service list from a required query reader."""
    result: dict[str, dict[str, Any]] = {}
    for unit in CONTROL_PLANE_SERVICE_UNITS:
        value, reason = reader(unit)
        if load_state_reader is None:
            load_state, load_state_reason = None, "systemd LoadState was not queried for this collection"
        else:
            load_state, load_state_reason = load_state_reader(unit)
        row: dict[str, Any] = {"memory_min_bytes": value}
        if value is None:
            row["memory_min_bytes_reason"] = reason or f"{unit} MemoryMin is not reported"
        row["load_state"] = load_state
        if load_state is None:
            row["load_state_reason"] = load_state_reason or f"{unit} LoadState is not reported"
        result[unit] = row
    return result


def _slice_path(name: str) -> Path:
    """Translate systemd's hyphen-nested slice name to its cgroup path."""
    if name == "-.slice":
        return Path()
    stem = name.removesuffix(".slice")
    parts = stem.split("-")
    return Path(*("-".join(parts[:index]) + ".slice" for index in range(1, len(parts) + 1)))


def _read_cgroup_limit(path: Path, description: str) -> tuple[int | str | None, str | None]:
    text, reason = _nonempty_text(path, description)
    if reason is not None:
        return None, reason
    value = (text or "").strip()
    if value == "max":
        return "max", None
    if not value.isdecimal():
        return None, f"{description} is not a non-negative integer or max"
    return int(value), None


def _read_cpu_max(path: Path, description: str) -> tuple[dict[str, int | str] | None, str | None]:
    text, reason = _nonempty_text(path, description)
    if reason is not None:
        return None, reason
    fields = (text or "").split()
    if len(fields) != 2:
        return None, f"{description} must contain quota and period"
    quota_text, period_text = fields
    quota: int | str
    if quota_text == "max":
        quota = "max"
    elif quota_text.isdecimal() and int(quota_text) > 0:
        quota = int(quota_text)
    else:
        return None, f"{description} quota is malformed"
    if not period_text.isdecimal() or int(period_text) <= 0:
        return None, f"{description} period is malformed"
    return {"quota_us": quota, "period_us": int(period_text)}, None


def _read_memory_stat(path: Path, key: str) -> tuple[int | None, str | None]:
    text, reason = _read_text(path, "gate-slice memory.stat")
    if reason is not None:
        return None, reason
    matches: list[int] = []
    for line in (text or "").splitlines():
        fields = line.split()
        if fields and fields[0] == key:
            if len(fields) != 2 or not fields[1].isdecimal():
                return None, f"gate-slice memory.stat {key} is malformed"
            matches.append(int(fields[1]))
    if len(matches) != 1:
        return None, f"gate-slice memory.stat {key} is absent or duplicated"
    return matches[0], None


def _gate_slice_facts(
    root: Path,
    config: FactsConfig,
    online: int | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    names = (
        "memory_current_bytes", "memory_warm_bytes", "memory_high_bytes", "memory_max_bytes", "cpu_max",
    )
    if config.gate_slice is None:
        reason = "not-configured: facts.toml.gate_slice.slice"
        return ({name: _fact(None, reason) for name in names}, _fact(None, reason))

    path = root / "sys/fs/cgroup" / _slice_path(config.gate_slice)
    current, current_reason = _read_integer(path / "memory.current", "gate-slice memory.current")
    inactive, inactive_reason = _read_memory_stat(path / "memory.stat", "inactive_file")
    if current is None:
        warm, warm_reason = None, current_reason or "gate-slice memory.current is unavailable"
    elif inactive is None:
        warm, warm_reason = None, inactive_reason or "gate-slice inactive_file is unavailable"
    elif inactive > current:
        warm, warm_reason = None, "gate-slice inactive_file exceeds memory.current"
    else:
        warm, warm_reason = current - inactive, None

    high, high_reason = _read_cgroup_limit(path / "memory.high", "gate-slice memory.high")
    maximum, maximum_reason = _read_cgroup_limit(path / "memory.max", "gate-slice memory.max")
    cpu_max, cpu_max_reason = _read_cpu_max(path / "cpu.max", "gate-slice cpu.max")
    gate = {
        "memory_current_bytes": _fact(current, current_reason),
        "memory_warm_bytes": _fact(warm, warm_reason),
        "memory_high_bytes": _fact(high, high_reason),
        "memory_max_bytes": _fact(maximum, maximum_reason),
        "cpu_max": _fact(cpu_max, cpu_max_reason),
    }
    if cpu_max is None or online is None:
        planning_cap = None
        planning_reason = cpu_max_reason if cpu_max is None else "online CPU count is unavailable"
    elif cpu_max["quota_us"] == "max":
        planning_cap = float(online + 1)
        planning_reason = None
    else:
        planning_cap = min(float(online + 1), int(cpu_max["quota_us"]) / int(cpu_max["period_us"]))
        planning_reason = None
    return gate, _fact(planning_cap, planning_reason)


def _mountinfo_fields(root: Path) -> tuple[list[tuple[str, str]], str | None]:
    text, reason = _read_text(root / "proc/self/mountinfo", "/proc/self/mountinfo")
    if reason is not None:
        return [], reason
    mounts: list[tuple[str, str]] = []
    for line in (text or "").splitlines():
        fields = line.split()
        try:
            separator = fields.index("-")
            mountpoint = fields[4]
            filesystem = fields[separator + 1]
        except (ValueError, IndexError):
            continue
        for encoded, decoded in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
            mountpoint = mountpoint.replace(encoded, decoded)
        mounts.append((mountpoint, filesystem))
    if not mounts:
        return [], "/proc/self/mountinfo has no parseable mount rows"
    return mounts, None


def _filesystem_for_path(absolute_path: str, mounts: list[tuple[str, str]]) -> str | None:
    matches = [
        (mountpoint, filesystem)
        for mountpoint, filesystem in mounts
        if absolute_path == mountpoint or absolute_path.startswith(mountpoint.rstrip("/") + "/")
        or mountpoint == "/"
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: len(item[0]))[1]


def _collect_disks(
    root: Path,
    config: FactsConfig,
    *,
    statvfs_reader: Callable[[Path], Any] | None = None,
) -> dict[str, Any]:
    roots = (("checkout", config.checkout_root), ("docker", config.docker_root))
    missing = [name for name, path in roots if path is None]
    if missing:
        return _fact(None, "not-configured: facts.toml.paths." + ", ".join(f"{name}_root" for name in missing))
    mounts, mount_reason = _mountinfo_fields(root)
    if mount_reason is not None:
        return _fact(None, mount_reason)
    if statvfs_reader is None:
        statvfs_reader = os.statvfs
    rows: list[dict[str, Any]] = []
    for name, absolute_path in roots:
        assert absolute_path is not None
        filesystem = _filesystem_for_path(absolute_path, mounts)
        if filesystem is None:
            return _fact(None, f"filesystem type for {absolute_path} is absent from /proc/self/mountinfo")
        path = _rooted(root, absolute_path)
        try:
            stat = statvfs_reader(path)
        except OSError as exc:
            return _fact(None, f"disk path {absolute_path} unavailable ({exc.strerror or 'I/O error'})")
        block_size = stat.f_frsize or stat.f_bsize
        floor = config.disk_floors.get(name) if config.disk_floors is not None else None
        floor_reason = None if floor is not None else f"not-configured: policy.disk_floors.{name}"
        rows.append({
            "name": _fact(name),
            "mount": _fact(absolute_path),
            "total_bytes": _fact(stat.f_blocks * block_size),
            "free_bytes": _fact(stat.f_bavail * block_size),
            "fs": _fact(filesystem),
            "floor_bytes": _fact(floor, floor_reason),
        })
    return _fact(rows)


def _guard_facts_legacy(root: Path, config: FactsConfig) -> dict[str, Any]:
    config_path = _rooted(root, config.guard_config)
    main, main_reason = _read_bytes(config_path, "guard configuration")
    if main_reason is not None:
        state, state_reason = _guard_state(root, config)
        result: dict[str, Any] = {
            "enabled": None, "enabled_reason": main_reason,
            "config_digest": None, "config_digest_reason": main_reason,
            "state": state,
        }
        if state is None:
            result["state_reason"] = state_reason or "guard runtime state is unavailable"
        return result
    assert main is not None
    file_entries: list[tuple[str, bytes]] = [(config.guard_config, main)]
    dropin_dir = config_path.with_name(config_path.name + ".d")
    try:
        dropins = sorted(path for path in dropin_dir.iterdir() if path.is_file() and path.suffix == ".toml")
    except FileNotFoundError:
        dropins = []
        read_dropin_error = None
    except OSError as exc:
        dropins = []
        read_dropin_error = f"guard drop-in directory unavailable ({exc.strerror or 'I/O error'})"
    else:
        read_dropin_error = None
    merged: dict[str, Any] = {}
    parse_reason: str | None = read_dropin_error
    digest_reason: str | None = read_dropin_error
    try:
        _merge_tables(merged, tomllib.loads(main.decode("utf-8")))
    except (tomllib.TOMLDecodeError, UnicodeError) as exc:
        parse_reason = f"guard configuration {config.guard_config} is invalid ({exc})"
    if read_dropin_error is None:
        for path in dropins:
            data, read_reason = _read_bytes(path, "guard configuration drop-in")
            source_name = f"{config.guard_config}.d/{path.name}"
            if read_reason is not None:
                parse_reason = parse_reason or read_reason
                digest_reason = digest_reason or read_reason
                continue
            assert data is not None
            file_entries.append((source_name, data))
            if parse_reason is None:
                try:
                    layer = tomllib.loads(data.decode("utf-8"))
                except (tomllib.TOMLDecodeError, UnicodeError) as exc:
                    parse_reason = f"guard configuration {source_name} is invalid ({exc})"
                else:
                    _merge_tables(merged, layer)
    digest_material = b"".join(name.encode("utf-8") + b"\0" + data + b"\0" for name, data in file_entries)
    digest: str | None = None if digest_reason is not None else "sha256:" + hashlib.sha256(digest_material).hexdigest()
    enabled: bool | None = None
    enabled_reason: str | None = parse_reason
    if enabled_reason is None:
        if "enabled" not in merged:
            enabled_reason = "guard configuration has no top-level enabled value"
        else:
            value = merged["enabled"]
            if isinstance(value, bool):
                enabled = value
            else:
                enabled_reason = (
                    f"guard configuration enabled is {type(value).__name__}, expected bool"
                )

    state, state_reason = _guard_state(root, config)
    result: dict[str, Any] = {"enabled": enabled, "config_digest": digest, "state": state}
    if enabled is None:
        result["enabled_reason"] = enabled_reason or "guard enabled state is unavailable"
    if digest is None:
        result["config_digest_reason"] = digest_reason or "guard configuration inputs could not be read completely"
    if state is None:
        result["state_reason"] = state_reason or "guard runtime state is unavailable"
    return result


def _guard_facts(root: Path, config: FactsConfig) -> dict[str, Any]:
    config_path = _rooted(root, config.guard_config)
    present = config_path.is_file()
    legacy = _guard_facts_legacy(root, config)
    state = legacy.get("state")
    state_reason = legacy.get("state_reason")
    if state is not None and state not in {"off", "report", "enforce", "unknown"}:
        state_reason = f"guard runtime state {state!r} is outside off, report, enforce, or unknown"
        state = None
    frozen_reason = "not-collected: hostobs freeze events are unavailable"
    if not present:
        frozen_reason = "thrashguard-absent"
    return {
        "guard_present": _fact(present),
        "guard_state": _fact(state, state_reason),
        "config_digest": _fact(legacy.get("config_digest"), legacy.get("config_digest_reason")),
        "frozen_units": _fact(None, frozen_reason),
    }


def _guard_state(root: Path, config: FactsConfig) -> tuple[str | None, str | None]:
    if config.guard_state is None:
        return None, "guard runtime state source is not configured"
    return _nonempty_text(_rooted(root, config.guard_state), "guard runtime state")


def _merge_tables(target: dict[str, Any], layer: dict[str, Any]) -> None:
    for key, value in layer.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_tables(target[key], value)
        else:
            target[key] = value


def _benchmark_speed(root: Path, config: FactsConfig, model: str | None, online: int | None) -> tuple[dict[str, Any] | None, str | None]:
    path = _rooted(root, config.geekbench_record)
    text, reason = _read_text(path, "host Geekbench record")
    if reason is not None:
        return None, reason
    try:
        record = json.loads(text or "")
    except json.JSONDecodeError:
        return None, "host Geekbench record is invalid JSON"
    if not isinstance(record, dict) or record.get("schema") != _BENCHMARK_SCHEMA or record.get("tool") != "geekbench":
        return None, "host Geekbench record has an unsupported schema or tool"
    major = record.get("tool_major")
    if type(major) is not int or major not in _BENCHMARK_BASELINE:
        return None, "host Geekbench record has an unsupported major"
    if model is None or online is None or record.get("cpu_model") != model or record.get("cpus_online") != online:
        return None, "host Geekbench record does not match the current CPU model and online count"
    version = record.get("tool_version")
    single = record.get("single_core")
    multi = record.get("multi_core")
    baseline = record.get("baseline_score")
    if (
        not isinstance(version, str) or not version.strip()
        or type(single) is not int or single <= 0
        or type(multi) is not int or multi <= 0
        or type(baseline) is not int or baseline != _BENCHMARK_BASELINE[major]
    ):
        return None, "host Geekbench record is incomplete"
    return {
        "geekbench_major": major,
        "geekbench_version": version,
        "baseline_score": baseline,
        "single_core": single,
        "multi_core": multi,
        "basis": f"geekbench{major}-measured",
    }, None


def collect_host_facts(
    root: Path = DEFAULT_ROOT,
    *,
    service_memory_min_reader: Callable[[str], tuple[int | None, str | None]] | None = None,
    service_load_state_reader: Callable[[str], tuple[str | None, str | None]] | None = None,
    disk_statvfs_reader: Callable[[Path], Any] | None = None,
) -> dict[str, Any]:
    """Collect ``host-facts/2`` from read-only host inputs and service properties."""
    root = Path(root)
    config = load_facts_config(root)
    proc_root = root / "proc"
    sys_root = root / "sys"

    boot_id, boot_reason = _nonempty_text(proc_root / "sys/kernel/random/boot_id", "/proc/sys/kernel/random/boot_id")
    model, _ = _cpu_model(proc_root)
    online, online_reason = _online_cpu_count(sys_root)
    speed, speed_reason = _benchmark_speed(root, config, model, online)
    load_values, load_reasons = _load_values(proc_root)
    gate_slice, cpu_planning_cap = _gate_slice_facts(root, config, online)

    meminfo = _meminfo_values(proc_root)
    mem_total, total_reason = meminfo["MemTotal"]
    mem_available, available_reason = meminfo["MemAvailable"]
    swap_total, swap_total_reason = meminfo["SwapTotal"]
    swap_free, swap_free_reason = meminfo["SwapFree"]

    memory = {
        "total_bytes": _fact(mem_total, total_reason),
        "available_bytes": _fact(mem_available, available_reason),
        "host_reserve_bytes": _fact(
            config.host_reserve_bytes,
            "not-configured: facts.toml.policy.host_reserve_bytes",
        ),
        "swap_total_bytes": _fact(swap_total, swap_total_reason),
        "swap_free_bytes": _fact(swap_free, swap_free_reason),
        "overcommit": _fact(config.overcommit),
    }

    cgroup_root = sys_root / "fs/cgroup"
    controllers_path = cgroup_root / "cgroup.controllers"
    controllers_text, controllers_read_reason = _read_text(controllers_path, "cgroup v2 controllers")
    mount_rows, mountinfo_reason = _mountinfo_fields(root)
    cgroup_fs = _filesystem_for_path("/sys/fs/cgroup", mount_rows) if mountinfo_reason is None else None
    cgroup_mounted = None if mountinfo_reason is not None else cgroup_fs == "cgroup2"
    if cgroup_mounted is not True:
        controllers: list[str] | None = None
        controllers_reason = mountinfo_reason or "cgroup v2 is not mounted"
    elif controllers_read_reason is not None:
        controllers = None
        controllers_reason = controllers_read_reason
    else:
        controllers = sorted(set(controllers_text.split()))
        controllers_reason = None
    cgroup = {
        "mounted": _fact(cgroup_mounted, mountinfo_reason or "cgroup v2 mount state is unknown"),
        "controllers": _fact(controllers, controllers_reason),
    }

    slice_path = _slice_path(config.control_plane_slice)
    control_path = cgroup_root / slice_path
    control_present = control_path.is_dir()
    cpu_weight, cpu_weight_reason = _read_integer(control_path / "cpu.weight", "control-plane cpu.weight", minimum=1) if control_present else (None, "control-plane slice is absent")
    memory_min, memory_min_reason = _read_integer(control_path / "memory.min", "control-plane memory.min") if control_present else (None, "control-plane slice is absent")
    if service_memory_min_reader is None and root == DEFAULT_ROOT:
        service_memory_min_reader = _read_systemd_memory_min
        service_load_state_reader = _read_systemd_load_state
    if service_memory_min_reader is None:
        service_memory_min = {
            unit: {
                "memory_min_bytes": _fact(None, "systemd MemoryMin was not queried for this facts root"),
                "load_state": _fact(None, "systemd LoadState was not queried for this facts root"),
            }
            for unit in CONTROL_PLANE_SERVICE_UNITS
        }
    else:
        raw_services = _service_memory_min_facts(
            service_memory_min_reader, service_load_state_reader,
        )
        service_memory_min = {
            unit: {
                "memory_min_bytes": _fact(
                    row["memory_min_bytes"], row.get("memory_min_bytes_reason"),
                ),
                "load_state": _fact(row["load_state"], row.get("load_state_reason")),
            }
            for unit, row in raw_services.items()
        }
    control = {
        "slice": _fact(config.control_plane_slice),
        "present": _fact(control_present),
        "cpu_weight": _fact(cpu_weight, cpu_weight_reason),
        "memory_min_bytes": _fact(memory_min, memory_min_reason),
        "profile_reserve_bytes": _fact(config.control_plane_profile_reserve_bytes),
        "service_memory_min": service_memory_min,
    }

    mounted_reason = "cgroup v2 is not mounted"
    kernel_interfaces: dict[str, Any] = {}
    for key, path in (
        ("cgroup_freeze", control_path / "cgroup.freeze"),
        ("memory_reclaim", control_path / "memory.reclaim"),
    ):
        if not cgroup_mounted:
            kernel_interfaces[key] = _fact(None, mounted_reason)
        elif not control_present:
            kernel_interfaces[key] = _fact(None, "control-plane slice is absent; kernel interface was not checked")
        else:
            kernel_interfaces[key] = _fact(path.is_file())
    kernel_interfaces["memory_reclaim_swappiness_arg"] = _fact(None, "read-only collection does not probe write-command support")

    ksm_root = sys_root / "kernel/mm/ksm"
    ksm: dict[str, Any] = {}
    for key, filename in (("run", "run"), ("advisor_max_cpu", "advisor_max_cpu"), ("pages_sharing", "pages_sharing")):
        value, reason = _read_integer(ksm_root / filename, f"KSM {filename}")
        ksm[key] = _fact(value, reason or f"KSM {filename} is unavailable")
    advisor_mode, advisor_reason = _nonempty_text(ksm_root / "advisor_mode", "KSM advisor_mode")
    ksm["advisor_mode"] = _fact(advisor_mode, advisor_reason or "KSM advisor_mode is unavailable")
    if config.ksm_optin_shim is None:
        ksm["lane_optin_shim"] = _fact(None, "KSM opt-in shim path is not configured")
    else:
        shim_path = _rooted(root, config.ksm_optin_shim)
        ksm["lane_optin_shim"] = _fact(shim_path.is_file())

    host_role, host_role_reason = _host_role(root, config.host_setup_env)
    policy: dict[str, Any] = {
        "host_role": _fact(host_role, host_role_reason or "host role is unavailable"),
        "psi_refuse_some_avg10": _fact(config.psi_refuse_some_avg10),
        "max_lane_containers": _fact(
            config.max_lane_containers,
            "not-configured: facts.toml.policy.max_lane_containers",
        ),
        "planning_cap": _fact(config.planning_cap, "not-configured: facts.toml.policy.planning_cap"),
        "max_footprint": _fact(config.max_footprint, "not-configured: facts.toml.policy.max_footprint"),
        "max_planned_bytes": _fact(config.max_planned_bytes, "not-configured: facts.toml.policy.max_planned_bytes"),
        "footprint_charges_bytes": _fact(
            config.footprint_charges_bytes,
            "not-configured: facts.toml.policy.footprint_charges_bytes",
        ),
        "priority_classes_by_principal": _fact(
            config.priority_classes_by_principal,
            "not-configured: facts.toml.policy.priority_classes_by_principal",
        ),
        "disk_floors": _fact(config.disk_floors, "not-configured: facts.toml.policy.disk_floors"),
        "max_upload_bytes": _fact(config.max_upload_bytes, "not-configured: facts.toml.policy.max_upload_bytes"),
        "max_upload_store_bytes": _fact(
            config.max_upload_store_bytes,
            "not-configured: facts.toml.policy.max_upload_store_bytes",
        ),
        "admission": _fact(config.admission, "not-configured: facts.toml.policy.admission"),
        "aging_s": _configured_policy_fact(config.aging_s, "aging_s", "seconds"),
        "legacy_charge_bytes": _configured_policy_fact(
            config.legacy_charge_bytes, "legacy_charge_bytes", "bytes",
        ),
        "legacy_planned_cores": _configured_policy_fact(
            config.legacy_planned_cores, "legacy_planned_cores", "cores",
        ),
    }

    facts: dict[str, Any] = {
        "schema": "host-facts/2",
        "agent_version": _agent_version(),
        "collected_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "boot_id": _fact(boot_id, boot_reason or "boot id is unavailable"),
        "cpu": {
            "online": _fact(online, online_reason or "online CPU count is unavailable"),
            "planning_cap": cpu_planning_cap,
            "oversubscribe_max": _fact(config.oversubscribe_max),
        },
        "load1": _fact(load_values["load1"], load_reasons.get("load1")),
        "load5": _fact(load_values["load5"], load_reasons.get("load5")),
        "load15": _fact(load_values["load15"], load_reasons.get("load15")),
        "speed": _fact(
            {
                key: _fact(value)
                for key, value in speed.items()
            } if speed is not None else None,
            speed_reason or "host Geekbench record is unavailable",
        ),
        "memory": memory,
        "psi": {name: _read_psi(proc_root, name) for name in ("cpu", "memory", "io")},
        "gate_slice": gate_slice,
        "disks": _collect_disks(root, config, statvfs_reader=disk_statvfs_reader),
        "cgroup_v2": cgroup,
        "kernel_interfaces": kernel_interfaces,
        "ksm": ksm,
        "control_plane": control,
        "guard": _guard_facts(root, config),
        "freeze": {
            "since_utc": _fact(None, "not-collected until thrashguard P4"),
            "by": _fact(None, "not-collected until thrashguard P4"),
            "freeze_reason": _fact(None, "not-collected until thrashguard P4"),
            "queued": _fact(None, "not-collected"),
        },
        "policy": policy,
        "queue": {
            "planned_cores": _fact(None, "not-collected: admission book arrives with the host agent"),
            "charged_bytes": _fact(None, "not-collected: admission book arrives with the host agent"),
            "waiting": _fact(None, "not-collected: admission book arrives with the host agent"),
        },
        "jobs": _fact(None, "not-collected"),
        "tools": _fact(None, "not-collected: tool inventory source is not configured"),
    }
    validate_host_facts(facts)
    return facts
