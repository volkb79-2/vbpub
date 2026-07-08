from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

EntityKey = str
EntityKind = Literal["root", "slice", "scope", "service", "other"]
MetricSource = Literal["exact", "derived", "netns", "host", "unlimited", "unavail_perm", "unavail_kernel"]


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


@dataclass
class Frame:
    schema_version: int
    ts: float
    interval_s: float
    host: dict[str, MetricValue]
    entities: dict[EntityKey, EntityFrame]


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


def entity_to_jsonable(entity: Entity) -> dict[str, Any]:
    return {
        "key": entity.key,
        "kind": entity.kind,
        "parent": entity.parent,
        "docker": docker_to_jsonable(entity.docker),
        "tier": entity.tier,
        "is_protected": entity.is_protected,
    }


def entity_from_jsonable(value: Any) -> Entity:
    return Entity(
        key=value["key"],
        kind=value["kind"],
        parent=value.get("parent"),
        docker=docker_from_jsonable(value.get("docker")),
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
    return out


def entity_frame_from_jsonable(value: Any) -> EntityFrame:
    return EntityFrame(
        entity=entity_from_jsonable(value["entity"]),
        metrics={k: metric_from_jsonable(v) for k, v in value["metrics"].items()},
        findings=[finding_from_jsonable(v) for v in value.get("findings", ())],
        governance=value.get("governance"),
        network=value.get("network"),
        damon=value.get("damon"),
    )


def frame_to_jsonable(frame: Frame) -> dict[str, Any]:
    validate_frame_metrics(frame)
    return {
        "schema_version": frame.schema_version,
        "ts": frame.ts,
        "interval_s": frame.interval_s,
        "host": {k: metric_to_jsonable(v) for k, v in sorted(frame.host.items())},
        "entities": {k: entity_frame_to_jsonable(v) for k, v in sorted(frame.entities.items())},
    }


def frame_from_jsonable(value: Any) -> Frame:
    frame = Frame(
        schema_version=int(value["schema_version"]),
        ts=float(value["ts"]),
        interval_s=float(value["interval_s"]),
        host={k: metric_from_jsonable(v) for k, v in value["host"].items()},
        entities={k: entity_frame_from_jsonable(v) for k, v in value["entities"].items()},
    )
    validate_frame_metrics(frame)
    return frame


def validate_frame_metrics(frame: Frame) -> None:
    from groop.registry import REGISTRY

    unknown = set(frame.host) - set(REGISTRY)
    for entity_frame in frame.entities.values():
        unknown.update(set(entity_frame.metrics) - set(REGISTRY))
    if unknown:
        raise ValueError(f"frame contains metrics absent from registry: {', '.join(sorted(unknown))}")
