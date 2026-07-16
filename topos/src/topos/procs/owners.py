"""P90 owner-provenance join (Required contract 4 / oracle O7).

Reuses the SAME tick's already cgroup/docker/CIU-enriched ``Entity`` table the
main collector produces (``topos.collect.collector.Collector.collect_once``
already ran ``walk_entities`` + ``enrich_entities``) rather than re-deriving or
re-inspecting Docker: a process's cgroup path is looked up in that table for
provenance only, so no cgroup accounting metric is read or summed twice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from topos.collect.dockerjoin import DOCKER_SCOPE_RE
from topos.model import Entity, EntityKey

_SLICE_SEGMENT_RE = re.compile(r"^[^/]+\.slice$")
_UNIT_SEGMENT_RE = re.compile(r"^[^/]+\.(service|scope)$")


@dataclass(frozen=True)
class OwnerJoin:
    cgroup_key: EntityKey | None
    unit: str | None
    slice_name: str | None
    docker_cid: str | None
    docker_name: str | None
    docker_image: str | None
    ciu_stack: str | None
    ciu_phase: int | None


_EMPTY = OwnerJoin(None, None, None, None, None, None, None, None)


def _leaf_unit(cgroup_key: str) -> str | None:
    leaf = cgroup_key.rsplit("/", 1)[-1]
    return leaf if _UNIT_SEGMENT_RE.match(leaf) else None


def _nearest_slice(cgroup_key: str) -> str | None:
    for segment in reversed(cgroup_key.split("/")):
        if _SLICE_SEGMENT_RE.match(segment):
            return segment
    return None


def join_owner(cgroup_key: str | None, entities: dict[EntityKey, Entity]) -> OwnerJoin:
    """Derive systemd unit/slice/Docker/CIU provenance for a process's cgroup."""
    if cgroup_key is None:
        return _EMPTY
    unit = _leaf_unit(cgroup_key)
    slice_name = _nearest_slice(cgroup_key)
    match = DOCKER_SCOPE_RE.search(cgroup_key)
    docker_cid = match.group(1) if match else None
    entity = entities.get(cgroup_key)
    docker_name = entity.docker.name if entity and entity.docker else None
    docker_image = entity.docker.image if entity and entity.docker else None
    if entity and entity.docker and docker_cid is None:
        docker_cid = entity.docker.cid
    ciu_stack = entity.ciu.stack if entity and entity.ciu else None
    ciu_phase = entity.ciu.phase if entity and entity.ciu else None
    return OwnerJoin(
        cgroup_key=cgroup_key,
        unit=unit,
        slice_name=slice_name,
        docker_cid=docker_cid,
        docker_name=docker_name,
        docker_image=docker_image,
        ciu_stack=ciu_stack,
        ciu_phase=ciu_phase,
    )
