from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

EntityKey = str
EntityKind = Literal["root", "slice", "scope", "service", "other", "process"]
MetricSource = Literal["exact", "derived", "netns", "bpf", "host", "unlimited", "unavail_perm", "unavail_kernel"]


@dataclass
class CiuMeta:
    """CIU (Configurable Infrastructure Utility) stack/phase metadata.

    Describes whether a container is managed by ciu and, if so, which stack
    and deploy phase it belongs to. Two detection tiers exist — label-confirmed
    (guaranteed) and inferred (heuristic) — and are never merged.
    """

    stack: str | None = None
    """Stack directory name (e.g. ``infra/redis-core``). Populated from
    ``ciu.stack`` label (label-confirmed) or inferred from compose project."""

    phase_raw: str | None = None
    """Raw phase label value (e.g. ``phase_2``). None when no phase is
    assigned or the container is not ciu-deploy-managed."""

    phase: int | None = None
    """Parsed numeric phase number. ``phase_2`` → ``2``, ``phase_10`` → ``10``.
    Malformed values (``phase_``, ``phase_abc``, ``phase_-1``) set this to
    ``None`` — the container still has ciu metadata but its phase is unknown."""

    source: str = "label"
    """Detection tier: ``"label"`` for label-confirmed (``ciu.managed="true"``
    labels present), ``"inferred"`` for heuristic (name-pattern + compose
    project matches a known stack root)."""


@dataclass
class DockerMeta:
    cid: str
    full_id: str
    name: str
    image: str
    compose_project: str | None = None
    ptero_uuid: str | None = None


@dataclass
class Entity:
    key: EntityKey
    kind: EntityKind
    parent: EntityKey | None
    docker: DockerMeta | None = None
    ciu: CiuMeta | None = None
    tier: str | None = None
    is_protected: bool = False


@dataclass
class MetricValue:
    v: float | int | None
    src: MetricSource
    raw: int | None = None


@dataclass
class Finding:
    rule_id: str
    severity: str
    message: str
    remedy: str | None = None
    source_metrics: tuple[str, ...] = ()
    confidence: str = "exact"


@dataclass
class EntityFrame:
    entity: Entity
    metrics: dict[str, MetricValue]
    findings: list[Finding] = field(default_factory=list)
    governance: dict[str, object] | None = None
    network: dict[str, object] | None = None
    damon: dict[str, object] | None = None
    process: dict[str, object] | None = None
    """P90 process identity/owner-provenance block (comm, cmdline, ppid, user,
    state, cgroup/unit/slice/docker/ciu owner join). Only ever populated on
    "process"-kind EntityFrames in the separate process Frame stream; a cgroup
    EntityFrame never sets this. Not in P81's explicit visitor set, so it fails
    closed to the ``sensitive`` marker like any other unrecognized value-bearing
    field until a future frontend registers a typed visitor for it."""


@dataclass
class Frame:
    schema_version: int
    ts: float
    interval_s: float
    host: dict[str, MetricValue]
    entities: dict[EntityKey, EntityFrame]
    host_meta: dict[str, object] | None = None


def metric_to_jsonable(value: MetricValue) -> list[float | int | str | None]:
    out: list[float | int | str | None] = [value.v, value.src]
    if value.raw is not None:
        out.append(value.raw)
    return out


def metric_from_jsonable(value: Any) -> MetricValue:
    if not isinstance(value, list) or len(value) not in (2, 3):
        raise ValueError(f"invalid MetricValue compact form: {value!r}")
    raw = value[2] if len(value) == 3 else None
    if raw is not None and not isinstance(raw, int):
        raise ValueError(f"invalid MetricValue raw counter: {value!r}")
    return MetricValue(v=value[0], src=value[1], raw=raw)


def docker_to_jsonable(meta: DockerMeta | None) -> dict[str, Any] | None:
    if meta is None:
        return None
    return {
        "cid": meta.cid,
        "full_id": meta.full_id,
        "name": meta.name,
        "image": meta.image,
        "compose_project": meta.compose_project,
        "ptero_uuid": meta.ptero_uuid,
    }


def docker_from_jsonable(value: Any) -> DockerMeta | None:
    if value is None:
        return None
    return DockerMeta(
        cid=value["cid"],
        full_id=value["full_id"],
        name=value["name"],
        image=value["image"],
        compose_project=value.get("compose_project"),
        ptero_uuid=value.get("ptero_uuid"),
    )


def ciu_to_jsonable(meta: CiuMeta | None) -> dict[str, Any] | None:
    if meta is None:
        return None
    return {
        "stack": meta.stack,
        "phase_raw": meta.phase_raw,
        "phase": meta.phase,
        "source": meta.source,
    }


def ciu_from_jsonable(value: Any) -> CiuMeta | None:
    if value is None:
        return None
    return CiuMeta(
        stack=value.get("stack"),
        phase_raw=value.get("phase_raw"),
        phase=value.get("phase"),
        source=value.get("source", "label"),
    )


def entity_to_jsonable(entity: Entity) -> dict[str, Any]:
    out: dict[str, Any] = {
        "key": entity.key,
        "kind": entity.kind,
        "parent": entity.parent,
        "docker": docker_to_jsonable(entity.docker),
        "tier": entity.tier,
        "is_protected": entity.is_protected,
    }
    # Omitted rather than emitted as null when absent, matching governance /
    # network / damon in entity_frame_to_jsonable.  Emitting "ciu": null on
    # every entity would rewrite every recorded frame on disk and force the
    # existing fixtures to be regenerated for a field they do not carry.
    if entity.ciu is not None:
        out["ciu"] = ciu_to_jsonable(entity.ciu)
    return out


def entity_from_jsonable(value: Any) -> Entity:
    return Entity(
        key=value["key"],
        kind=value["kind"],
        parent=value.get("parent"),
        docker=docker_from_jsonable(value.get("docker")),
        ciu=ciu_from_jsonable(value.get("ciu")),
        tier=value.get("tier"),
        is_protected=bool(value.get("is_protected", False)),
    )


def finding_to_jsonable(finding: Finding) -> dict[str, Any]:
    return {
        "rule_id": finding.rule_id,
        "severity": finding.severity,
        "message": finding.message,
        "remedy": finding.remedy,
        "source_metrics": list(finding.source_metrics),
        "confidence": finding.confidence,
    }


def finding_from_jsonable(value: Any) -> Finding:
    return Finding(
        rule_id=value["rule_id"],
        severity=value["severity"],
        message=value["message"],
        remedy=value.get("remedy"),
        source_metrics=tuple(value.get("source_metrics", ())),
        confidence=value.get("confidence", "exact"),
    )


def entity_frame_to_jsonable(frame: EntityFrame) -> dict[str, Any]:
    out: dict[str, Any] = {
        "entity": entity_to_jsonable(frame.entity),
        "metrics": {k: metric_to_jsonable(v) for k, v in sorted(frame.metrics.items())},
        "findings": [finding_to_jsonable(f) for f in frame.findings],
    }
    if frame.governance is not None:
        out["governance"] = frame.governance
    if frame.network is not None:
        out["network"] = frame.network
    if frame.damon is not None:
        out["damon"] = frame.damon
    if frame.process is not None:
        out["process"] = frame.process
    return out


def entity_frame_from_jsonable(value: Any) -> EntityFrame:
    return EntityFrame(
        entity=entity_from_jsonable(value["entity"]),
        metrics={k: metric_from_jsonable(v) for k, v in value["metrics"].items()},
        findings=[finding_from_jsonable(v) for v in value.get("findings", ())],
        governance=value.get("governance"),
        network=value.get("network"),
        damon=value.get("damon"),
        process=value.get("process"),
    )


def frame_to_jsonable(frame: Frame) -> dict[str, Any]:
    validate_frame_metrics(frame)
    out: dict[str, Any] = {
        "schema_version": frame.schema_version,
        "ts": frame.ts,
        "interval_s": frame.interval_s,
        "host": {k: metric_to_jsonable(v) for k, v in sorted(frame.host.items())},
        "entities": {k: entity_frame_to_jsonable(v) for k, v in sorted(frame.entities.items())},
    }
    if frame.host_meta is not None:
        out["host_meta"] = frame.host_meta
    return out


def frame_from_jsonable(value: Any) -> Frame:
    host_meta = value.get("host_meta")
    if host_meta is not None and not isinstance(host_meta, dict):
        raise ValueError(f"invalid host_meta: {host_meta!r}")
    frame = Frame(
        schema_version=int(value["schema_version"]),
        ts=float(value["ts"]),
        interval_s=float(value["interval_s"]),
        host={k: metric_from_jsonable(v) for k, v in value["host"].items()},
        entities={k: entity_frame_from_jsonable(v) for k, v in value["entities"].items()},
        host_meta=host_meta,
    )
    validate_frame_metrics(frame)
    return frame


def validate_frame_metrics(frame: Frame) -> None:
    from topos.registry import REGISTRY

    unknown = set(frame.host) - set(REGISTRY)
    for entity_frame in frame.entities.values():
        unknown.update(set(entity_frame.metrics) - set(REGISTRY))
    if unknown:
        raise ValueError(f"frame contains metrics absent from registry: {', '.join(sorted(unknown))}")
