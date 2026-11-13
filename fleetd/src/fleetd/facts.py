"""Read-only host facts collection from an injectable proc/sys root."""

from __future__ import annotations

import hashlib
import json
import math
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .contracts import ContractError, validate_host_facts
from .guard import validate_guard


DEFAULT_ROOT = Path("/")
DEFAULT_CONFIG_PATH = "/etc/fleetd/facts.toml"
_BENCHMARK_SCHEMA = "mdt-host-cpu-benchmark/1"
_BENCHMARK_BASELINE = {6: 2500, 7: 2500}
_MAX_CPU_ID = 1_000_000


@dataclass(frozen=True)
class FactsConfig:
    geekbench_record: str
    guard_config: str
    guard_state: str | None
    ksm_optin_shim: str | None
    control_plane_slice: str
    overcommit: float
    oversubscribe_max: float
    psi_refuse_some_avg10: float
    host_role: str | None
    max_lane_containers: int | None


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


def load_facts_config(root: Path = DEFAULT_ROOT) -> FactsConfig:
    """Read and validate the required, versioned host-owned facts config."""
    config_path = _rooted(root, DEFAULT_CONFIG_PATH)
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"facts config {DEFAULT_CONFIG_PATH}: cannot read required config ({exc.strerror or 'I/O error'})") from exc
    try:
        parsed = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise ContractError(f"facts config {DEFAULT_CONFIG_PATH}: invalid TOML: {exc}") from exc
    top = _object(parsed, "facts.toml", {"schema_version", "paths", "control_plane", "policy"})
    if type(top["schema_version"]) is not int or top["schema_version"] != 1:
        raise ContractError("facts.toml.schema_version: expected integer 1")

    paths = _object(top["paths"], "facts.toml.paths", {"geekbench_record", "guard_config"}, {"guard_state", "ksm_optin_shim"})
    geekbench_record = _config_path(paths["geekbench_record"], "facts.toml.paths.geekbench_record")
    guard_config = _config_path(paths["guard_config"], "facts.toml.paths.guard_config")
    guard_state = None if "guard_state" not in paths else _config_path(paths["guard_state"], "facts.toml.paths.guard_state")
    ksm_optin_shim = None if "ksm_optin_shim" not in paths else _config_path(paths["ksm_optin_shim"], "facts.toml.paths.ksm_optin_shim")

    control = _object(top["control_plane"], "facts.toml.control_plane", {"slice"})
    control_slice = _slice_name(control["slice"], "facts.toml.control_plane.slice")

    policy = _object(
        top["policy"], "facts.toml.policy",
        {"overcommit", "oversubscribe_max", "psi_refuse_some_avg10"},
        {"host_role", "max_lane_containers"},
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
    host_role = policy.get("host_role")
    if host_role is not None and (not isinstance(host_role, str) or not host_role.strip()):
        raise ContractError("facts.toml.policy.host_role: expected non-empty string when configured")
    max_lane = policy.get("max_lane_containers")
    if max_lane is not None and (type(max_lane) is not int or max_lane < 1):
        raise ContractError("facts.toml.policy.max_lane_containers: expected positive integer")

    return FactsConfig(
        geekbench_record, guard_config, guard_state, ksm_optin_shim, control_slice,
        overcommit, oversubscribe_max, psi_refuse_number, host_role, max_lane,
    )


def _rooted(root: Path, absolute_path: str) -> Path:
    """Map a configured host-absolute path beneath the supplied test root."""
    path = PurePosixPath(absolute_path)
    if not path.is_absolute() or ".." in path.parts:
        raise ContractError(f"configured path {absolute_path!r} is not a safe absolute path")
    return root / Path(*path.parts[1:])


def _read_text(path: Path, description: str) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding="utf-8"), None
    except OSError as exc:
        return None, f"{description} unavailable ({exc.strerror or 'I/O error'})"
    except UnicodeError:
        return None, f"{description} is not UTF-8 text"


def _read_bytes(path: Path, description: str) -> tuple[bytes | None, str | None]:
    try:
        return path.read_bytes(), None
    except OSError as exc:
        return None, f"{description} unavailable ({exc.strerror or 'I/O error'})"


def _nonempty_text(path: Path, description: str) -> tuple[str | None, str | None]:
    text, reason = _read_text(path, description)
    if reason is not None:
        return None, reason
    value = text.strip() if text is not None else ""
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
    cpus: set[int] = set()
    for part in value.split(","):
        low_text, dash, high_text = part.partition("-")
        if not low_text.isdecimal() or (dash and not high_text.isdecimal()):
            return None
        low = int(low_text)
        high = int(high_text) if dash else low
        if high < low or high > _MAX_CPU_ID or high - low > _MAX_CPU_ID:
            return None
        cpus.update(range(low, high + 1))
        if len(cpus) > _MAX_CPU_ID:
            return None
    return len(cpus) if cpus else None


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


def _read_psi(proc_root: Path, name: str) -> dict[str, Any]:
    source = f"/proc/pressure/{name}"
    text, reason = _read_text(proc_root / "pressure" / name, source)
    if reason is not None:
        return {
            "some_avg10_pct": None, "some_avg10_pct_reason": reason,
            "full_avg10_pct": None, "full_avg10_pct_reason": reason,
        }
    lines = {line.split()[0]: line for line in (text or "").splitlines() if line.split()}
    some, some_reason = _psi_value(lines.get("some"), "some", source)
    full, full_reason = _psi_value(lines.get("full"), "full", source)
    result: dict[str, Any] = {"some_avg10_pct": some, "full_avg10_pct": full}
    if some_reason is not None:
        result["some_avg10_pct_reason"] = some_reason
    if full_reason is not None:
        result["full_avg10_pct_reason"] = full_reason
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


def _nullable_leaf(value: object, reason: str | None, reason_key: str) -> dict[str, Any]:
    result = {reason_key.removesuffix("_reason"): value}
    if value is None:
        result[reason_key] = reason or "source is unavailable"
    return result


def _slice_path(name: str) -> Path:
    """Translate systemd's hyphen-nested slice name to its cgroup path."""
    if name == "-.slice":
        return Path()
    stem = name.removesuffix(".slice")
    parts = stem.split("-")
    return Path(*("-".join(parts[:index]) + ".slice" for index in range(1, len(parts) + 1)))


def _guard_facts(root: Path, config: FactsConfig) -> dict[str, Any]:
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
    enabled_reason: str | None = None
    if parse_reason is None:
        try:
            guard = validate_guard(merged)
            enabled = bool(guard.to_dict()["enabled"])
        except ContractError as exc:
            enabled_reason = f"guard configuration is invalid ({exc})"
    else:
        enabled_reason = parse_reason

    state, state_reason = _guard_state(root, config)
    result: dict[str, Any] = {"enabled": enabled, "config_digest": digest, "state": state}
    if enabled is None:
        result["enabled_reason"] = enabled_reason or "guard enabled state is unavailable"
    if digest is None:
        result["config_digest_reason"] = digest_reason or "guard configuration inputs could not be read completely"
    if state is None:
        result["state_reason"] = state_reason or "guard runtime state is unavailable"
    return result


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


def collect_host_facts(root: Path = DEFAULT_ROOT) -> dict[str, Any]:
    """Collect one ``host-facts/1`` document without writes or subprocesses."""
    root = Path(root)
    config = load_facts_config(root)
    proc_root = root / "proc"
    sys_root = root / "sys"

    boot_id, boot_reason = _nonempty_text(proc_root / "sys/kernel/random/boot_id", "/proc/sys/kernel/random/boot_id")
    uptime_text, uptime_reason = _read_text(proc_root / "uptime", "/proc/uptime")
    boottime: float | None = None
    if uptime_reason is None:
        first = (uptime_text or "").split()
        if first:
            try:
                parsed_uptime = float(first[0])
                if math.isfinite(parsed_uptime) and parsed_uptime >= 0:
                    boottime = parsed_uptime
            except ValueError:
                pass
        if boottime is None:
            uptime_reason = "/proc/uptime has no valid first field"

    model, model_reason = _cpu_model(proc_root)
    online, online_reason = _online_cpu_count(sys_root)
    speed, speed_reason = _benchmark_speed(root, config, model, online)

    meminfo = _meminfo_values(proc_root)
    mem_total, total_reason = meminfo["MemTotal"]
    mem_available, available_reason = meminfo["MemAvailable"]
    swap_total, swap_total_reason = meminfo["SwapTotal"]
    swap_free, swap_free_reason = meminfo["SwapFree"]

    cpu: dict[str, Any] = {
        "model": model,
        "online": online,
        "for_lanes": float(online) if online is not None else None,
        "oversubscribe_max": config.oversubscribe_max,
    }
    if model is None:
        cpu["model_reason"] = model_reason or "CPU model is unavailable"
    if online is None:
        cpu["online_reason"] = online_reason or "online CPU count is unavailable"
        cpu["for_lanes_reason"] = "online CPU count is unavailable"

    memory: dict[str, Any] = {
        "total_bytes": mem_total,
        "available_bytes": mem_available,
        "for_lanes_bytes": mem_total,
        "swap_total_bytes": swap_total,
        "swap_free_bytes": swap_free,
        "swap_for_parking_bytes": swap_free,
        "overcommit": config.overcommit,
    }
    for key, reason in (
        ("total_bytes", total_reason), ("available_bytes", available_reason),
        ("for_lanes_bytes", total_reason), ("swap_total_bytes", swap_total_reason),
        ("swap_free_bytes", swap_free_reason), ("swap_for_parking_bytes", swap_free_reason),
    ):
        if memory[key] is None:
            memory[f"{key}_reason"] = reason or f"{key} is unavailable"

    cgroup_root = sys_root / "fs/cgroup"
    controllers_path = cgroup_root / "cgroup.controllers"
    controllers_text, controllers_read_reason = _read_text(controllers_path, "cgroup v2 controllers")
    cgroup_mounted = controllers_path.is_file()
    if controllers_text is None:
        controllers: list[str] | None = None
        controllers_reason = controllers_read_reason or "cgroup v2 controllers file is absent"
    else:
        controllers = sorted(set(controllers_text.split()))
        controllers_reason = None
    cgroup: dict[str, Any] = {"mounted": cgroup_mounted, "controllers": controllers}
    if controllers is None:
        cgroup["controllers_reason"] = controllers_reason

    slice_path = _slice_path(config.control_plane_slice)
    control_path = cgroup_root / slice_path
    control_present = control_path.is_dir()
    memory_min, memory_min_reason = _read_integer(control_path / "memory.min", "control-plane memory.min") if control_present else (None, "control-plane slice is absent")
    cpu_weight, cpu_weight_reason = _read_integer(control_path / "cpu.weight", "control-plane cpu.weight", minimum=1) if control_present else (None, "control-plane slice is absent")
    control: dict[str, Any] = {
        "slice": config.control_plane_slice,
        "present": control_present,
        "memory_min_bytes": memory_min,
        "cpu_weight": cpu_weight,
    }
    if memory_min is None:
        control["memory_min_bytes_reason"] = memory_min_reason
    if cpu_weight is None:
        control["cpu_weight_reason"] = cpu_weight_reason

    mounted_reason = "cgroup v2 is not mounted"
    kernel_interfaces: dict[str, Any] = {}
    for key, path in (
        ("cgroup_freeze", control_path / "cgroup.freeze"),
        ("memory_reclaim", control_path / "memory.reclaim"),
    ):
        kernel_interfaces[key] = path.exists() if cgroup_mounted else None
        if not cgroup_mounted:
            kernel_interfaces[f"{key}_reason"] = mounted_reason
    kernel_interfaces["memory_reclaim_swappiness_arg"] = None
    kernel_interfaces["memory_reclaim_swappiness_arg_reason"] = "read-only collection does not probe write-command support"

    ksm_root = sys_root / "kernel/mm/ksm"
    ksm: dict[str, Any] = {}
    for key, filename in (("run", "run"), ("advisor_max_cpu", "advisor_max_cpu"), ("pages_sharing", "pages_sharing")):
        value, reason = _read_integer(ksm_root / filename, f"KSM {filename}")
        ksm[key] = value
        if value is None:
            ksm[f"{key}_reason"] = reason or f"KSM {filename} is unavailable"
    advisor_mode, advisor_reason = _nonempty_text(ksm_root / "advisor_mode", "KSM advisor_mode")
    ksm["advisor_mode"] = advisor_mode
    if advisor_mode is None:
        ksm["advisor_mode_reason"] = advisor_reason or "KSM advisor_mode is unavailable"
    if config.ksm_optin_shim is None:
        ksm["lane_optin_shim"] = None
        ksm["lane_optin_shim_reason"] = "KSM opt-in shim path is not configured"
    else:
        shim_path = _rooted(root, config.ksm_optin_shim)
        ksm["lane_optin_shim"] = shim_path.is_file()

    host_role = config.host_role
    policy: dict[str, Any] = {
        "host_role": host_role,
        "psi_refuse_some_avg10": config.psi_refuse_some_avg10,
        "max_lane_containers": config.max_lane_containers,
    }
    if host_role is None:
        policy["host_role_reason"] = "host role is not configured"
    if config.max_lane_containers is None:
        policy["max_lane_containers_reason"] = "maximum lane container count is not configured"

    facts: dict[str, Any] = {
        "schema": "host-facts/1",
        "boot_id": boot_id,
        "host_now_boottime_s": boottime,
        "cpu": cpu,
        "speed": speed,
        "memory": memory,
        "psi": {name: _read_psi(proc_root, name) for name in ("cpu", "memory", "io")},
        "cgroup_v2": cgroup,
        "kernel_interfaces": kernel_interfaces,
        "ksm": ksm,
        "control_plane": control,
        "guard": _guard_facts(root, config),
        "policy": policy,
        "jobs": None,
        "jobs_reason": "job inventory is not part of WP1(a)",
        "tools": None,
        "tools_reason": "tool inventory source is not configured",
    }
    if boot_id is None:
        facts["boot_id_reason"] = boot_reason or "boot id is unavailable"
    if boottime is None:
        facts["host_now_boottime_s_reason"] = uptime_reason or "host boottime is unavailable"
    if speed is None:
        facts["speed_reason"] = speed_reason or "host Geekbench record is unavailable"
    validate_host_facts(facts)
    return facts
