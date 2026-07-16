from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from typing import Any

from topos.model import CiuMeta, DockerMeta, Entity, EntityKey

DOCKER_SCOPE_RE = re.compile(r"(?:^|/)docker-([0-9a-f]{64})\.scope$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.IGNORECASE)
# The <env>-<name> tail of ciu's ^<project>-<env>-<name>$ container naming
# (CIU-DEPLOY.md S7.8).  The <project> half is matched as a literal prefix by
# detect_ciu_inferred, not by this pattern.
CIU_CONTAINER_TAIL_RE = re.compile(r"^([^-]+)-(.+)$")
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

    ``phase_2`` -> ``("phase_2", 2)``.  A label that is present but does not
    parse (``phase_``, ``phase_abc``, ``phase_-1``) keeps its raw value and
    yields ``(raw, None)``: "ciu shipped a phase we could not parse" and "ciu
    shipped no phase at all" are different states, and the honest-absence
    contract forbids collapsing them.  Only a genuinely absent label yields
    ``(None, None)``.
    """
    if raw is None:
        return None, None
    raw = raw.strip()
    if not raw:
        return None, None
    match = PHASE_RE.match(raw)
    if match is None:
        return raw, None
    return raw, int(match.group(1))


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
    1. Its ``com.docker.compose.project`` matches a configured known stack.
       Compose derives the project from the stack *directory name*, so a
       configured ``infra/redis-core`` is matched on its last path segment --
       a compose project can never itself contain ``/``, so comparing the whole
       configured string would match nothing.
    2. Its container name is anchored to that project:
       ``^<project>-<env>-<name>$`` (CIU-DEPLOY.md S7.8).  The project is
       matched as a literal prefix rather than as a regex group, because
       project names routinely contain hyphens (``redis-core``) and a
       ``([^-]+)`` group would capture only ``redis``.

    Returns ``CiuMeta(source="inferred")`` or ``None``.
    """
    if not compose_project or not known_stack_roots:
        return None
    if compose_project not in {root.rstrip("/").rpartition("/")[2] for root in known_stack_roots}:
        return None
    # The name must belong to *this* project, then carry an <env>-<name> tail.
    # Matching the bare shape instead would claim any container with two
    # hyphens in a matched project -- including unrelated and UUID-named ones.
    prefix = f"{compose_project}-"
    if not container_name.startswith(prefix):
        return None
    if not CIU_CONTAINER_TAIL_RE.match(container_name[len(prefix):]):
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
