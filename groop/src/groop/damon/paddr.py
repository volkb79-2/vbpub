from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from groop.config import DamonConfig
from groop.damon.control import (
    APPROVAL_TEXT,
    DamonControlError,
    DamonSession,
    NoFreeKdamond,
    OwnershipError,
    SysfsWrite,
    _audit,
    _check_root,
    _free_kdamond_idx,
    _marker_path,
    _owned_markers,
    _read_json,
    _write_json,
    _write_sysfs,
    default_state_dir,
    is_confirmed,
)
from groop.damon.passive import DEFAULT_DAMON_ROOT


@dataclass(frozen=True)
class PaddrStartPlan:
    kdamond_idx: int
    damon_root: Path
    state_dir: Path
    config: DamonConfig
    writes: tuple[SysfsWrite, ...]


def paddr_confirmation_text(plan: PaddrStartPlan) -> str:
    writes = "\n".join(f"  {write.rel_path} <- {write.value}" for write in plan.writes)
    return (
        "Start DAMON paddr host DRAM heat session\n"
        f"kdamond: {plan.kdamond_idx}\n"
        f"sample_us={plan.config.paddr_sample_us} aggr_us={plan.config.paddr_aggr_us} update_us={plan.config.paddr_update_us}\n"
        "The session is groop-owned, host-wide, and will keep running after the viewer exits.\n"
        f"Type {APPROVAL_TEXT} to apply these sysfs writes:\n{writes}"
    )


def plan_start_paddr_session(
    *,
    damon_root: Path = DEFAULT_DAMON_ROOT,
    state_dir: Path | None = None,
    config: DamonConfig,
    require_root: bool = True,
    is_root: Callable[[], bool] | None = None,
) -> PaddrStartPlan:
    _check_root(require_root=require_root, is_root=is_root)
    root = state_dir or default_state_dir()
    _refuse_existing_groop_paddr(root, damon_root)
    idx = _free_kdamond_idx(damon_root, root)
    return PaddrStartPlan(
        kdamond_idx=idx,
        damon_root=damon_root,
        state_dir=root,
        config=config,
        writes=_paddr_start_writes(idx, config),
    )


def start_planned_paddr_session(
    plan: PaddrStartPlan,
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
    marker = _marker_path(plan.state_dir, plan.kdamond_idx)
    if marker.exists():
        raise OwnershipError(f"kdamond {plan.kdamond_idx} already has a groop marker")
    for write in plan.writes:
        _write_sysfs(plan.damon_root / write.rel_path, write.value)
    marker_payload = {
        "owner": "groop",
        "mode": "paddr",
        "entity_key": "",
        "pids": [],
        "kdamond_idx": plan.kdamond_idx,
        "damon_root": str(plan.damon_root),
        "created_at": now(),
        "sample_us": plan.config.paddr_sample_us,
        "aggr_us": plan.config.paddr_aggr_us,
        "update_us": plan.config.paddr_update_us,
    }
    _write_json(marker, marker_payload)
    _audit(plan.state_dir, "damon-paddr-start", marker_payload, now=now, user=user)
    return DamonSession("", (), plan.kdamond_idx, marker)


def start_paddr_session(
    *,
    damon_root: Path = DEFAULT_DAMON_ROOT,
    state_dir: Path | None = None,
    config: DamonConfig,
    confirmed_text: str,
    now: Callable[[], float] = time.time,
    user: str | None = None,
    require_root: bool = True,
    is_root: Callable[[], bool] | None = None,
) -> DamonSession:
    plan = plan_start_paddr_session(
        damon_root=damon_root,
        state_dir=state_dir,
        config=config,
        require_root=require_root,
        is_root=is_root,
    )
    return start_planned_paddr_session(
        plan,
        confirmed_text=confirmed_text,
        now=now,
        user=user,
        require_root=require_root,
        is_root=is_root,
    )


def _paddr_start_writes(idx: int, config: DamonConfig) -> tuple[SysfsWrite, ...]:
    base = f"{idx}/contexts/0"
    return (
        SysfsWrite(f"{idx}/state", "off"),
        SysfsWrite(f"{idx}/contexts/nr_contexts", "1"),
        SysfsWrite(f"{base}/operations", "paddr"),
        SysfsWrite(f"{base}/monitoring_attrs/intervals/sample_us", str(config.paddr_sample_us)),
        SysfsWrite(f"{base}/monitoring_attrs/intervals/aggr_us", str(config.paddr_aggr_us)),
        SysfsWrite(f"{base}/monitoring_attrs/intervals/update_us", str(config.paddr_update_us)),
        SysfsWrite(f"{base}/targets/nr_targets", "0"),
        SysfsWrite(f"{base}/schemes/nr_schemes", "1"),
        SysfsWrite(f"{base}/schemes/0/action", "stat"),
        SysfsWrite(f"{idx}/state", "on"),
    )


def _refuse_existing_groop_paddr(state_dir: Path, damon_root: Path) -> None:
    for marker in _owned_markers(state_dir):
        payload = _read_json(marker)
        if payload.get("owner") != "groop":
            continue
        if payload.get("mode") == "paddr" and Path(str(payload.get("damon_root", damon_root))) == damon_root:
            raise NoFreeKdamond("groop-owned paddr DAMON session already exists")
