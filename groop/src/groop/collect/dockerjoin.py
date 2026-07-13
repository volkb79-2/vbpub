from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from typing import Any

from groop.model import DockerMeta, Entity, EntityKey

DOCKER_SCOPE_RE = re.compile(r"(?:^|/)docker-([0-9a-f]{64})\.scope$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.IGNORECASE)
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


def enrich_entities(entities: dict[EntityKey, Entity], docker_inspect: DockerInspect | None = None) -> dict[EntityKey, Entity]:
    inspect = docker_inspect or default_docker_inspect
    out: dict[EntityKey, Entity] = {}
    for key, entity in entities.items():
        cid = docker_id_from_key(key)
        if cid is None:
            out[key] = entity
            continue
        try:
            data = _first_inspect(inspect(cid))
            entity.docker = meta_from_inspect(cid, data) if data is not None else None
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
