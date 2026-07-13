from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from typing import Any

from groop.model import CiuMeta, DockerMeta, Entity, EntityKey

DOCKER_SCOPE_RE = re.compile(r"(?:^|/)docker-([0-9a-f]{64})\.scope$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.IGNORECASE)
# CIU container name pattern: ^<project>-<env>-<name>$ (CIU-DEPLOY.md S7.8)
CIU_CONTAINER_NAME_RE = re.compile(r"^([^-]+)-([^-]+)-(.+)$")
PHASE_RE = re.compile(r"^phase_(\d+)$")
DockerInspect = Callable[[str], Any]


class ContainerResolveError(ValueError):
    """Raised when --container name/prefix resolution fails.

    The message is safe for CLI display (no raw paths, no secrets).
    """

    def __init__(self, message: str, *, candidates: list[str] | None = None) -> None:
        super().__init__(message)
        self.candidates = candidates


def docker_id_from_key(key: EntityKey) -> str | None:
    match = DOCKER_SCOPE_RE.search(key)
    return match.group(1) if match else None


def default_docker_inspect(container_id: str) -> Any:
    proc = subprocess.run(["docker", "inspect", container_id], check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if proc.returncode != 0:
        return None
    return json.loads(proc.stdout)


def _first_inspect(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list):
        return value[0] if value and isinstance(value[0], dict) else None
    return value if isinstance(value, dict) else None


def _parse_phase(raw: str | None) -> tuple[str | None, int | None]:
    """Parse a ``ciu.phase`` label value into (raw_label, numeric_phase).

    ``phase_2`` → ``("phase_2", 2)``. Malformed values (``phase_``,
    ``phase_abc``, ``phase_-1``, ``None``) return ``(None, None)`` — the
    phase is unknown and no raw value is recorded.
    """
    if raw is None:
        return None, None
    raw = raw.strip()
    if not raw:
        return None, None
    match = PHASE_RE.match(raw)
    if match is None:
        return None, None
    num = int(match.group(1))
    if num < 0:
        return None, None
    return raw, num


def detect_ciu_from_labels(labels: dict[str, str]) -> CiuMeta | None:
    """Label-confirmed CIU detection from Docker ``Config.Labels``.

    Returns ``CiuMeta(source="label")`` when ``ciu.managed="true"`` is
    present. The ``ciu.stack`` and ``ciu.phase`` labels are optional. Returns
    ``None`` when no ``ciu.managed`` label exists (fall through to inference).

    The caller is responsible for checking inference if this returns ``None``.
    The two tiers are never merged.
    """
    if labels.get("ciu.managed") != "true":
        return None
    stack = labels.get("ciu.stack")
    phase_raw, phase = _parse_phase(labels.get("ciu.phase"))
    return CiuMeta(stack=stack, phase_raw=phase_raw, phase=phase, source="label")


def detect_ciu_inferred(
    compose_project: str | None,
    container_name: str,
    known_stack_roots: set[str],
) -> CiuMeta | None:
    """Inferred (heuristic) CIU detection.

    A container is inferred to be ciu-managed when:
    1. It has a ``com.docker.compose.project`` that matches a directory name
       under a configured stack root.
    2. Its container name matches ciu's anchored
       ``^<project>-<env>-<name>$`` pattern (CIU-DEPLOY.md S7.8).

    Returns ``CiuMeta(source="inferred")`` or ``None``.
    """
    if not compose_project or not known_stack_roots:
        return None
    # Check if compose_project is a known stack directory name
    if compose_project not in known_stack_roots:
        return None
    # Container name must match ciu's anchored pattern
    if not CIU_CONTAINER_NAME_RE.match(container_name):
        return None
    return CiuMeta(
        stack=compose_project,
        phase_raw=None,
        phase=None,
        source="inferred",
    )


def meta_from_inspect(full_id: str, data: dict[str, Any]) -> DockerMeta:
    config = data.get("Config") or {}
    labels = config.get("Labels") or {}
    name = str(data.get("Name") or "").lstrip("/")
    return DockerMeta(
        cid=full_id[:12],
        full_id=str(data.get("Id") or full_id),
        name=name,
        image=str(config.get("Image") or data.get("Image") or ""),
        compose_project=labels.get("com.docker.compose.project"),
        ptero_uuid=name if UUID_RE.match(name) else None,
    )


def enrich_entities(
    entities: dict[EntityKey, Entity],
    docker_inspect: DockerInspect | None = None,
    known_stack_roots: set[str] | None = None,
) -> dict[EntityKey, Entity]:
    inspect = docker_inspect or default_docker_inspect
    stack_roots = known_stack_roots or set()
    out: dict[EntityKey, Entity] = {}
    for key, entity in entities.items():
        cid = docker_id_from_key(key)
        if cid is None:
            out[key] = entity
            continue
        try:
            data = _first_inspect(inspect(cid))
            if data is not None:
                entity.docker = meta_from_inspect(cid, data)
                # CIU detection: labels first, inference fallback
                config = data.get("Config") or {}
                labels = config.get("Labels") or {}
                ciu = detect_ciu_from_labels(labels)
                if ciu is None and entity.docker is not None:
                    ciu = detect_ciu_inferred(
                        entity.docker.compose_project,
                        entity.docker.name,
                        stack_roots,
                    )
                entity.ciu = ciu
            else:
                entity.docker = None
        except (OSError, ValueError, KeyError, TypeError):
            entity.docker = None
        out[key] = entity
    return out


def resolve_container_key(name_or_prefix: str, entities: dict[EntityKey, Entity]) -> EntityKey:
    """Resolve a container name or prefix to an EntityKey via enriched Docker metadata.

    Scans entities whose key matches DOCKER_SCOPE_RE and have non-None
    DockerMeta. An exact match on ``DockerMeta.name`` wins over any prefix
    match. If exactly one unique prefix match is found it is returned. If
    multiple distinct prefix matches exist, ContainerResolveError is raised
    listing the candidates. If zero matches exist, ContainerResolveError is
    raised with a "no running container" message.

    **Ordering constraint:** *entities* must already be docker-enriched
    (``Entity.docker`` populated by :func:`enrich_entities`). Resolution
    against a stale or cross-sweep entity set will produce stale/missing
    results — this function does not call ``docker inspect`` itself.

    The resolved EntityKey is a cgroup-path string like
    ``"system.slice/docker-<64hex>.scope"``, which is the form already
    accepted by ``--target`` flags on ``inspect-files`` and ``action``
    subcommands.
    """
    exact: EntityKey | None = None
    prefix: list[tuple[EntityKey, str]] = []

    for key, entity in entities.items():
        if entity.docker is None:
            continue
        if not DOCKER_SCOPE_RE.search(key):
            continue
        dname = entity.docker.name
        cid = entity.docker.cid

        if dname == name_or_prefix:
            exact = key
        elif dname.startswith(name_or_prefix) or cid.startswith(name_or_prefix):
            prefix.append((key, dname))

    if exact is not None:
        return exact

    if len(prefix) == 0:
        raise ContainerResolveError(
            f"no running container matches name filter '{name_or_prefix}'"
        )
    if len(prefix) == 1:
        return prefix[0][0]

    candidates = sorted(set(name for _, name in prefix))
    candidates_str = ", ".join(candidates)
    raise ContainerResolveError(
        f"ambiguous container name prefix '{name_or_prefix}' matches "
        f"multiple containers: {candidates_str}",
        candidates=candidates,
    )
