from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from groop.collect.cgroup import parse_int_text, read_text
from groop.config import DamonConfig
from groop.damon.passive import DEFAULT_DAMON_ROOT

APPROVAL_TEXT = "START"


class DamonControlError(RuntimeError):
    pass


class RootRequired(DamonControlError):
    pass


class NoEntityPids(DamonControlError):
    pass


class NoFreeKdamond(DamonControlError):
    pass


class StaleEntityPids(DamonControlError):
    pass


class OwnershipError(DamonControlError):
    pass


@dataclass(frozen=True)
class SysfsWrite:
    rel_path: str
    value: str


@dataclass(frozen=True)
class DamonStartPlan:
    entity_key: str
    pids: tuple[int, ...]
    kdamond_idx: int
    damon_root: Path
    cgroup_root: Path
    state_dir: Path
    config: DamonConfig
    writes: tuple[SysfsWrite, ...]


@dataclass(frozen=True)
class DamonSession:
    entity_key: str
    pids: tuple[int, ...]
    kdamond_idx: int
    marker_path: Path


def default_state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "state") / "groop"


def is_confirmed(value: str) -> bool:
    return value.strip() == APPROVAL_TEXT


def confirmation_text(plan: DamonStartPlan) -> str:
    writes = "\n".join(f"  {write.rel_path} <- {write.value}" for write in plan.writes)
    return (
        f"Start DAMON vaddr on {plan.entity_key or '/'}\n"
        f"kdamond: {plan.kdamond_idx}\n"
        f"target pids: {', '.join(str(pid) for pid in plan.pids)}\n"
        f"sample_us={plan.config.vaddr_sample_us} aggr_us={plan.config.vaddr_aggr_us} update_us={plan.config.vaddr_update_us}\n"
        "The session is groop-owned and will keep running after the viewer exits.\n"
        f"Type {APPROVAL_TEXT} to apply these sysfs writes:\n{writes}"
    )


def plan_start_session(
    entity_key: str,
    *,
    cgroup_root: Path,
    damon_root: Path = DEFAULT_DAMON_ROOT,
    state_dir: Path | None = None,
    config: DamonConfig,
    require_root: bool = True,
    is_root: Callable[[], bool] | None = None,
) -> DamonStartPlan:
    _check_root(require_root=require_root, is_root=is_root)
    pids = _entity_pids(cgroup_root, entity_key)
    if not pids:
        raise NoEntityPids(f"{entity_key or '/'} has no visible pids")
    owned = _owned_markers(state_dir or default_state_dir())
    if len(owned) >= config.max_concurrent_targets:
        raise NoFreeKdamond("groop-owned DAMON target limit reached")
    idx = _free_kdamond_idx(damon_root, state_dir or default_state_dir())
    writes = _start_writes(idx, pids, config)
    return DamonStartPlan(
        entity_key=entity_key,
        pids=pids,
        kdamond_idx=idx,
        damon_root=damon_root,
        cgroup_root=cgroup_root,
        state_dir=state_dir or default_state_dir(),
        config=config,
        writes=writes,
    )


def start_planned_session(
    plan: DamonStartPlan,
    *,
    confirmed_text: str,
    now: Callable[[], float] = time.time,
    user: str | None = None,
    require_root: bool = True,
    is_root: Callable[[], bool] | None = None,
) -> DamonSession:
    _check_root(require_root=require_root, is_root=is_root)
    if not is_confirmed(confirmed_text):
        raise DamonControlError(f"typed confirmation must be {APPROVAL_TEXT}")
    current_pids = _entity_pids(plan.cgroup_root, plan.entity_key)
    if current_pids != plan.pids:
        raise StaleEntityPids("entity pid set changed before DAMON start")
    marker = _marker_path(plan.state_dir, plan.kdamond_idx)
    if marker.exists():
        raise OwnershipError(f"kdamond {plan.kdamond_idx} already has a groop marker")
    for write in plan.writes:
        _write_sysfs(plan.damon_root / write.rel_path, write.value)
    marker_payload = {
        "owner": "groop",
        "entity_key": plan.entity_key,
        "pids": list(plan.pids),
        "kdamond_idx": plan.kdamond_idx,
        "damon_root": str(plan.damon_root),
        "created_at": now(),
        "sample_us": plan.config.vaddr_sample_us,
        "aggr_us": plan.config.vaddr_aggr_us,
        "update_us": plan.config.vaddr_update_us,
    }
    _write_json(marker, marker_payload)
    _audit(plan.state_dir, "damon-start", marker_payload, now=now, user=user)
    return DamonSession(plan.entity_key, plan.pids, plan.kdamond_idx, marker)


def start_entity_session(
    entity_key: str,
    *,
    cgroup_root: Path,
    damon_root: Path = DEFAULT_DAMON_ROOT,
    state_dir: Path | None = None,
    config: DamonConfig,
    confirmed_text: str,
    now: Callable[[], float] = time.time,
    user: str | None = None,
    require_root: bool = True,
    is_root: Callable[[], bool] | None = None,
) -> DamonSession:
    plan = plan_start_session(
        entity_key,
        cgroup_root=cgroup_root,
        damon_root=damon_root,
        state_dir=state_dir,
        config=config,
        require_root=require_root,
        is_root=is_root,
    )
    return start_planned_session(
        plan,
        confirmed_text=confirmed_text,
        now=now,
        user=user,
        require_root=require_root,
        is_root=is_root,
    )


def stop_owned_sessions(
    *,
    damon_root: Path = DEFAULT_DAMON_ROOT,
    state_dir: Path | None = None,
    all_mine: bool = False,
    kdamond_idx: int | None = None,
    now: Callable[[], float] = time.time,
    user: str | None = None,
    require_root: bool = True,
    is_root: Callable[[], bool] | None = None,
) -> int:
    _check_root(require_root=require_root, is_root=is_root)
    if not all_mine and kdamond_idx is None:
        raise DamonControlError("choose --all-mine or a kdamond index")
    root = state_dir or default_state_dir()
    markers = _owned_markers(root)
    stopped = 0
    for marker in markers:
        payload = _read_json(marker)
        idx = int(payload.get("kdamond_idx", -1))
        if kdamond_idx is not None and idx != kdamond_idx:
            continue
        if payload.get("owner") != "groop":
            raise OwnershipError(f"refusing non-groop marker {marker}")
        marker_root = Path(str(payload.get("damon_root", damon_root)))
        if marker_root != damon_root:
            continue
        _teardown_kdamond(damon_root, idx)
        marker.unlink(missing_ok=True)
        _audit(root, "damon-stop", payload, now=now, user=user)
        stopped += 1
    return stopped


def _check_root(*, require_root: bool, is_root: Callable[[], bool] | None) -> None:
    root_check = is_root if is_root is not None else lambda: os.geteuid() == 0
    if require_root and not root_check():
        raise RootRequired("DAMON control requires root")


def _entity_pids(cgroup_root: Path, entity_key: str) -> tuple[int, ...]:
    cgroup_path = cgroup_root if entity_key == "" else cgroup_root / entity_key
    procs = read_text(cgroup_path / "cgroup.procs")
    if procs.value is None:
        return ()
    pids: set[int] = set()
    for line in str(procs.value).splitlines():
        value = parse_int_text(line)
        if value is not None and value > 0:
            pids.add(value)
    return tuple(sorted(pids))


def _free_kdamond_idx(damon_root: Path, state_dir: Path) -> int:
    nr_text = read_text(damon_root / "nr_kdamonds")
    nr = parse_int_text(str(nr_text.value)) if nr_text.value is not None else None
    if nr is None:
        raise NoFreeKdamond("DAMON kdamond count is unavailable")
    owned_indexes = {_marker_idx(marker) for marker in _owned_markers(state_dir)}
    for idx in range(nr):
        if idx in owned_indexes:
            continue
        state = read_text(damon_root / str(idx) / "state")
        state_text = str(state.value).strip() if state.value is not None else "off"
        if state_text in {"", "off"}:
            return idx
    raise NoFreeKdamond("no free kdamond slot")


def _start_writes(idx: int, pids: tuple[int, ...], config: DamonConfig) -> tuple[SysfsWrite, ...]:
    base = f"{idx}/contexts/0"
    writes = [
        SysfsWrite(f"{idx}/state", "off"),
        SysfsWrite(f"{idx}/contexts/nr_contexts", "1"),
        SysfsWrite(f"{base}/operations", "vaddr"),
        SysfsWrite(f"{base}/monitoring_attrs/intervals/sample_us", str(config.vaddr_sample_us)),
        SysfsWrite(f"{base}/monitoring_attrs/intervals/aggr_us", str(config.vaddr_aggr_us)),
        SysfsWrite(f"{base}/monitoring_attrs/intervals/update_us", str(config.vaddr_update_us)),
        SysfsWrite(f"{base}/targets/nr_targets", str(len(pids))),
    ]
    for target_idx, pid in enumerate(pids):
        writes.append(SysfsWrite(f"{base}/targets/{target_idx}/pid_target", str(pid)))
    writes.extend(
        (
            SysfsWrite(f"{base}/schemes/nr_schemes", "1"),
            SysfsWrite(f"{base}/schemes/0/action", "stat"),
            SysfsWrite(f"{idx}/state", "on"),
        )
    )
    return tuple(writes)


def _teardown_kdamond(damon_root: Path, idx: int) -> None:
    _write_sysfs(damon_root / str(idx) / "state", "off")
    contexts = damon_root / str(idx) / "contexts"
    if contexts.exists():
        shutil.rmtree(contexts)
    _write_sysfs(contexts / "nr_contexts", "0")


def _write_sysfs(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{value}\n", encoding="utf-8")


def _marker_dir(state_dir: Path) -> Path:
    return state_dir / "damon"


def _marker_path(state_dir: Path, idx: int) -> Path:
    return _marker_dir(state_dir) / f"kdamond-{idx}.json"


def _owned_markers(state_dir: Path) -> list[Path]:
    directory = _marker_dir(state_dir)
    if not directory.exists():
        return []
    return sorted(directory.glob("kdamond-*.json"))


def _marker_idx(marker: Path) -> int | None:
    try:
        return int(marker.stem.rsplit("-", 1)[-1])
    except ValueError:
        return None


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _audit(state_dir: Path, action: str, payload: dict[str, object], *, now: Callable[[], float], user: str | None) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    event = {
        "ts": now(),
        "user": user or os.environ.get("USER") or "",
        "action": action,
        "entity_key": payload.get("entity_key"),
        "kdamond_idx": payload.get("kdamond_idx"),
        "pids": payload.get("pids", []),
        "sample_us": payload.get("sample_us"),
        "aggr_us": payload.get("aggr_us"),
        "update_us": payload.get("update_us"),
    }
    with (state_dir / "actions.log").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
