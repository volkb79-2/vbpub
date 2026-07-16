from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from topos.collect.dockerjoin import docker_id_from_key
from topos.model import Entity, EntityFrame, EntityKey, Frame, MetricSource, MetricValue

SYSTEMD_PROPERTIES = (
    "FragmentPath",
    "DropInPaths",
    "ControlGroup",
    "Transient",
    "MemoryMin",
    "MemoryLow",
    "MemoryHigh",
    "MemoryMax",
    "CPUWeight",
    "IOWeight",
)

ORIGIN_CODES = {
    "unset": 0,
    "docker_default": 1,
    "systemd_unit": 2,
    "systemd_runtime_dropin": 3,
    "raw_write": 4,
}

SEVERITY_CODES = {
    "none": 0,
    "warn": 1,
    "red": 2,
}

_UNSET = object()


@dataclass(frozen=True)
class ShowResult:
    stdout: str
    stderr: str = ""
    returncode: int = 0


SystemctlShowRunner = Callable[[str, tuple[str, ...]], ShowResult]


@dataclass(frozen=True)
class GovernedLimit:
    metric_name: str
    property_name: str
    default_value: int | None


@dataclass(frozen=True)
class UnitView:
    unit: str
    found: bool
    fragment_path: str | None
    dropin_paths: tuple[str, ...]
    control_group: str | None
    transient: bool
    values: dict[str, int | None | object]

    def recorded_value(self, property_name: str) -> int | None | object:
        return self.values.get(property_name, _UNSET)


LIMITS = (
    GovernedLimit("mem_min", "MemoryMin", 0),
    GovernedLimit("mem_low", "MemoryLow", 0),
    GovernedLimit("mem_high", "MemoryHigh", None),
    GovernedLimit("mem_max", "MemoryMax", None),
    GovernedLimit("cpu_weight", "CPUWeight", 100),
    GovernedLimit("io_weight", "IOWeight", 100),
)


def default_systemctl_show_runner(unit: str, properties: tuple[str, ...]) -> ShowResult:
    argv = ["systemctl", "show", unit]
    for prop in properties:
        argv.extend(("-p", prop))
    proc = subprocess.run(argv, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return ShowResult(stdout=proc.stdout, stderr=proc.stderr, returncode=proc.returncode)


def annotate_frame_governance(frame: Frame, runner: SystemctlShowRunner | None = None) -> Frame:
    show = runner or default_systemctl_show_runner
    views = _load_unit_views(frame.entities, show)
    effective = {
        key: _effective_memory_min(frame.entities, key)
        for key in frame.entities
    }
    for key, entity_frame in frame.entities.items():
        _annotate_entity(entity_frame, views, effective[key])
    return frame


def _annotate_entity(
    entity_frame: EntityFrame,
    views: dict[str, UnitView],
    effective_info: dict[str, object],
) -> None:
    entity = entity_frame.entity
    leaf_unit = _leaf_unit_name(entity.key)
    leaf_view = views.get(leaf_unit) if leaf_unit is not None else None
    limit_details: dict[str, object] = {}
    summary_origin = "unset"
    summary_severity = "none"
    drifted_limits: list[str] = []
    summary_reasons: list[str] = []
    for limit in LIMITS:
        detail = _classify_limit(entity, entity_frame.metrics.get(limit.metric_name), limit, leaf_unit, leaf_view, effective_info)
        limit_details[limit.metric_name] = detail
        summary_origin = _pick_origin(summary_origin, str(detail["origin"]))
        summary_severity = _pick_severity(summary_severity, str(detail["severity"]))
        if bool(detail["drift"]):
            drifted_limits.append(limit.metric_name)
            reason = str(detail["reason"])
            if reason not in summary_reasons:
                summary_reasons.append(reason)

    effective_metric = MetricValue(
        effective_info["value"],
        effective_info["src"],
    )
    entity_frame.metrics["effective_memory_min"] = effective_metric
    entity_frame.metrics["governance_origin"] = MetricValue(ORIGIN_CODES[summary_origin], "derived")
    entity_frame.metrics["governance_drift"] = MetricValue(SEVERITY_CODES[summary_severity], "derived")
    entity_frame.governance = {
        "summary": {
            "origin": summary_origin,
            "drift": summary_severity != "none",
            "severity": summary_severity,
            "drifted_limits": drifted_limits,
            "reasons": summary_reasons,
            "unit": leaf_unit,
        },
        "limits": limit_details,
        "effective_memory_min": effective_info,
    }


def _classify_limit(
    entity: Entity,
    live_metric: MetricValue | None,
    limit: GovernedLimit,
    leaf_unit: str | None,
    leaf_view: UnitView | None,
    effective_info: dict[str, object],
) -> dict[str, object]:
    live_metric = live_metric or MetricValue(None, "unavail_kernel")
    recorded_value = _UNSET if leaf_view is None else leaf_view.recorded_value(limit.property_name)
    recorded_present = recorded_value is not _UNSET
    recorded_origin = _recorded_origin(leaf_view) if recorded_present else None
    origin = "unset"
    drift = False
    severity = "none"
    reason = ""
    if live_metric.src in ("unavail_perm", "unavail_kernel"):
        if recorded_present and recorded_origin is not None:
            origin = recorded_origin
            reason = f"systemd records {limit.property_name} for {leaf_unit}, but the live cgroup file is unavailable"
        else:
            origin = "unset"
            reason = f"no readable live value for {limit.metric_name}"
    elif recorded_present and recorded_origin is not None:
        if _values_equal(live_metric, recorded_value):
            origin = recorded_origin
            reason = f"systemd {recorded_origin} on {leaf_unit} matches the live cgroup value"
        else:
            origin = "raw_write"
            drift = True
            severity = "warn"
            reason = (
                f"systemd records {limit.property_name}={_fmt_value(recorded_value)} on {leaf_unit}, "
                f"but the live cgroup has {_fmt_metric(live_metric)}"
            )
    else:
        if _matches_default(entity, live_metric, limit.default_value):
            origin = "docker_default" if _is_docker_scope(entity) else "unset"
            if origin == "docker_default":
                reason = f"{limit.metric_name} is still on the docker transient-scope default"
            else:
                reason = f"{limit.metric_name} is unset and still on the kernel/systemd default"
        else:
            origin = "raw_write"
            drift = True
            severity = "warn"
            owner = leaf_unit or entity.key or "/"
            reason = f"no systemd record owns {limit.metric_name} on {owner}, but the live cgroup has {_fmt_metric(live_metric)}"

    if limit.metric_name == "mem_min":
        requested = _requested_memory_min(live_metric, recorded_value)
        effective_value = effective_info["value"]
        if entity.is_protected and isinstance(requested, int) and isinstance(effective_value, int) and effective_value < requested:
            drift = True
            severity = "red"
            clamp = effective_info.get("clamped_by")
            if isinstance(clamp, dict) and clamp.get("key"):
                reason = (
                    f"protected entity requested memory.min={requested}, but ancestor {clamp['key']} clamps "
                    f"the effective floor to {effective_value}"
                )
            else:
                reason = f"protected entity requested memory.min={requested}, but the effective floor is {effective_value}"

    return {
        "origin": origin,
        "recorded_origin": recorded_origin,
        "recorded_value": None if recorded_value is _UNSET else recorded_value,
        "live_value": live_metric.v,
        "live_src": live_metric.src,
        "drift": drift,
        "severity": severity,
        "reason": reason,
        "unit": leaf_unit,
    }


def _load_unit_views(entities: dict[EntityKey, EntityFrame], runner: SystemctlShowRunner) -> dict[str, UnitView]:
    units = sorted({unit for key in entities for unit in _unit_names_for_key(key)})
    return {
        unit: _unit_view(unit, runner(unit, SYSTEMD_PROPERTIES))
        for unit in units
    }


def _unit_view(unit: str, result: ShowResult) -> UnitView:
    props: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        props[key] = value.strip()
    values = {
        "MemoryMin": _parse_systemd_value(props.get("MemoryMin", "")),
        "MemoryLow": _parse_systemd_value(props.get("MemoryLow", "")),
        "MemoryHigh": _parse_systemd_value(props.get("MemoryHigh", "")),
        "MemoryMax": _parse_systemd_value(props.get("MemoryMax", "")),
        "CPUWeight": _parse_systemd_value(props.get("CPUWeight", "")),
        "IOWeight": _parse_systemd_value(props.get("IOWeight", "")),
    }
    return UnitView(
        unit=unit,
        found=result.returncode == 0,
        fragment_path=props.get("FragmentPath") or None,
        dropin_paths=tuple(path for path in props.get("DropInPaths", "").split() if path),
        control_group=props.get("ControlGroup") or None,
        transient=str(props.get("Transient", "")).lower() in {"1", "yes", "true"},
        values=values,
    )


def _parse_systemd_value(text: str) -> int | None | object:
    value = text.strip()
    if not value or value.lower() in {"[not set]", "n/a"}:
        return _UNSET
    if value.lower() in {"infinity", "max"}:
        return None
    try:
        return int(value)
    except ValueError:
        return _UNSET


def _recorded_origin(view: UnitView | None) -> str | None:
    if view is None or not view.found:
        return None
    if any(path.startswith("/run/systemd/system.control/") or path.startswith("/run/systemd/transient/") for path in view.dropin_paths):
        return "systemd_runtime_dropin"
    if view.fragment_path or view.dropin_paths:
        return "systemd_unit"
    return None


def _leaf_unit_name(key: EntityKey) -> str | None:
    if not key:
        return None
    leaf = key.rsplit("/", 1)[-1]
    return leaf if leaf.endswith((".slice", ".scope", ".service")) else None


def _unit_names_for_key(key: EntityKey) -> tuple[str, ...]:
    if not key:
        return ()
    return tuple(
        part
        for part in key.split("/")
        if part.endswith((".slice", ".scope", ".service"))
    )


def _effective_memory_min(entities: dict[EntityKey, EntityFrame], key: EntityKey) -> dict[str, object]:
    path_keys: list[EntityKey] = []
    current: EntityKey | None = key
    while current is not None:
        path_keys.append(current)
        current = entities[current].entity.parent if current in entities else None
    path_keys.reverse()
    if key != "":
        path_keys = [path_key for path_key in path_keys if path_key != ""]

    path: list[dict[str, object]] = []
    finite_values: list[tuple[EntityKey, int]] = []
    unavailable_src: MetricSource | None = None
    saw_unlimited = False
    for path_key in path_keys:
        metric = entities[path_key].metrics.get("mem_min", MetricValue(None, "unavail_kernel"))
        path.append({"key": path_key, "value": metric.v, "src": metric.src})
        if metric.src in ("unavail_perm", "unavail_kernel"):
            unavailable_src = metric.src
        elif isinstance(metric.v, int):
            finite_values.append((path_key, metric.v))
        elif metric.src == "unlimited":
            saw_unlimited = True

    if unavailable_src is not None:
        return {
            "value": None,
            "src": unavailable_src,
            "path": path,
            "clamped_by": None,
            "reason": "an ancestor memory.min is unavailable",
        }

    if finite_values:
        clamped_by_key, value = min(finite_values, key=lambda item: item[1])
        return {
            "value": value,
            "src": "derived",
            "path": path,
            "clamped_by": {"key": clamped_by_key, "value": value},
            "reason": f"effective memory.min is clamped by the smallest live value along the ancestor chain ({clamped_by_key or '/'})",
        }

    return {
        "value": None,
        "src": "unlimited" if saw_unlimited else "derived",
        "path": path,
        "clamped_by": None,
        "reason": "no finite memory.min value was available along the ancestor chain",
    }


def _values_equal(live_metric: MetricValue, recorded_value: int | None | object) -> bool:
    if recorded_value is _UNSET:
        return False
    if recorded_value is None:
        return live_metric.v is None and live_metric.src == "unlimited"
    return live_metric.v == recorded_value


def _requested_memory_min(live_metric: MetricValue, recorded_value: int | None | object) -> int | None:
    if isinstance(recorded_value, int):
        return recorded_value
    return live_metric.v if isinstance(live_metric.v, int) else None


def _matches_default(entity: Entity, live_metric: MetricValue, default_value: int | None) -> bool:
    if default_value is None:
        return live_metric.v is None and live_metric.src == "unlimited"
    return live_metric.v == default_value


def _is_docker_scope(entity: Entity) -> bool:
    return docker_id_from_key(entity.key) is not None


def _pick_origin(current: str, candidate: str) -> str:
    return candidate if ORIGIN_CODES[candidate] > ORIGIN_CODES[current] else current


def _pick_severity(current: str, candidate: str) -> str:
    return candidate if SEVERITY_CODES[candidate] > SEVERITY_CODES[current] else current


def _fmt_metric(metric: MetricValue) -> str:
    if metric.v is None:
        return "max" if metric.src == "unlimited" else "unavailable"
    return str(metric.v)


def _fmt_value(value: int | None | object) -> str:
    if value is _UNSET:
        return "unset"
    if value is None:
        return "max"
    return str(value)
