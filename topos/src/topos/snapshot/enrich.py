from __future__ import annotations

from collections.abc import Callable
from typing import Any

from topos.collect.dockerjoin import default_docker_inspect, docker_id_from_key
from topos.drift.origin import SYSTEMD_PROPERTIES, ShowResult, default_systemctl_show_runner
from topos.model import EntityKey

SystemctlSnapshotRunner = Callable[[str, tuple[str, ...]], ShowResult]
DockerSnapshotInspect = Callable[[str], Any]


def collect_systemctl_show(
    entity_key: EntityKey,
    *,
    runner: SystemctlSnapshotRunner | None = None,
) -> tuple[str | None, dict[str, object]]:
    unit = _leaf_unit_name(entity_key)
    if unit is None:
        return None, {"status": "skipped", "reason": "no systemd unit segment"}
    show = runner or default_systemctl_show_runner
    try:
        result = show(unit, SYSTEMD_PROPERTIES)
    except OSError as exc:
        return None, {"status": "error", "unit": unit, "error": str(exc)}
    status: dict[str, object] = {"status": "ok" if result.returncode == 0 else "error", "unit": unit, "returncode": result.returncode}
    if result.stderr:
        status["stderr"] = result.stderr.strip()
    if result.returncode != 0:
        return result.stdout or None, status
    return result.stdout, status


def collect_docker_inspect(
    entity_key: EntityKey,
    *,
    docker_inspect: DockerSnapshotInspect | None = None,
) -> tuple[dict[str, Any] | None, dict[str, object]]:
    cid = docker_id_from_key(entity_key)
    if cid is None:
        return None, {"status": "skipped", "reason": "not a docker scope"}
    inspect = docker_inspect or default_docker_inspect
    try:
        data = _first_inspect(inspect(cid))
    except (OSError, ValueError, TypeError) as exc:
        return None, {"status": "error", "container_id": cid, "error": str(exc)}
    if data is None:
        return None, {"status": "missing", "container_id": cid}
    return data, {"status": "ok", "container_id": cid}


def _first_inspect(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list):
        return value[0] if value and isinstance(value[0], dict) else None
    return value if isinstance(value, dict) else None


def _leaf_unit_name(entity_key: EntityKey) -> str | None:
    for segment in reversed([part for part in entity_key.split("/") if part]):
        if segment.endswith((".slice", ".scope", ".service")):
            return segment
    return None
