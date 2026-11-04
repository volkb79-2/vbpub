"""Shared data types.

These live in one dependency-free module rather than in the modules that
produce them, so that a consumer (a report renderer) never has to import a
producer (the sampler) just to name a type.  Nothing here imports anything else
from the package, and nothing here does IO.

Every type is JSON round-trippable via ``to_dict`` / ``from_dict``: the run
directory is the interface between the collector and the analyser, and the two
halves may be separated by a container boundary and by hours.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

SEVERITIES = ("info", "warn", "serious", "critical")
UNITS = ("bytes", "bytes/s", "cores", "pct", "count", "count/s", "us", "ratio")
PANELS = ("memory", "swap", "cpu", "io", "pressure", "faults", "damon")


def _clean(obj: Any) -> Any:
    """Recursively drop None values so the JSONL stays compact."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


@dataclass
class Mark:
    """A phase boundary or point annotation, usually emitted by the workload."""

    t: float
    name: str
    kind: str = "phase"           # "phase" opens a phase; "event" is a point
    mono: Optional[float] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    source: str = "mark"          # "mark" | "wrapper"

    def to_dict(self) -> Dict[str, Any]:
        return _clean(asdict(self))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Mark":
        return cls(
            t=float(data["t"]),
            name=str(data.get("name", "")),
            kind=str(data.get("kind", "phase")),
            mono=data.get("mono"),
            meta=data.get("meta") or {},
            source=str(data.get("source", "mark")),
        )


@dataclass
class Phase:
    """A labelled span of the run. ``t0``/``t1`` are monotonic run seconds."""

    name: str
    t0: float
    t1: float
    source: str = "mark"          # "mark" | "wrapper" | "auto"
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return max(0.0, self.t1 - self.t0)

    def to_dict(self) -> Dict[str, Any]:
        return _clean(asdict(self))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Phase":
        return cls(
            name=str(data["name"]),
            t0=float(data["t0"]),
            t1=float(data["t1"]),
            source=str(data.get("source", "mark")),
            meta=data.get("meta") or {},
        )


@dataclass
class Event:
    """Something worth a marker on the timeline, with the numbers behind it."""

    t: float
    mono: float
    kind: str
    severity: str
    target: str
    message: str
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _clean(asdict(self))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        return cls(
            t=float(data["t"]),
            mono=float(data.get("mono", 0.0)),
            kind=str(data["kind"]),
            severity=str(data.get("severity", "info")),
            target=str(data.get("target", "")),
            message=str(data.get("message", "")),
            data=data.get("data") or {},
        )


@dataclass
class Series:
    """One plottable line. ``t`` is monotonic run seconds; ``v`` may hold None.

    A None in ``v`` means "not measurable at this instant" — a counter reset, a
    cgroup that had vanished, a metric the target did not collect. Renderers
    must break the line there rather than interpolating across it, because an
    interpolated segment across a container restart is a fiction.
    """

    key: str
    target: str
    label: str
    unit: str
    group: str
    t: List[float] = field(default_factory=list)
    v: List[Optional[float]] = field(default_factory=list)
    role: str = "subject"         # "subject" | "observer"
    meta: Dict[str, Any] = field(default_factory=dict)

    def points(self) -> List[Tuple[float, Optional[float]]]:
        return list(zip(self.t, self.v))

    def finite(self) -> List[float]:
        return [x for x in self.v if x is not None]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "target": self.target,
            "label": self.label,
            "unit": self.unit,
            "group": self.group,
            "role": self.role,
            "t": self.t,
            "v": self.v,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Series":
        return cls(
            key=data["key"],
            target=data.get("target", ""),
            label=data.get("label", data["key"]),
            unit=data.get("unit", "count"),
            group=data.get("group", "memory"),
            t=list(data.get("t") or []),
            v=list(data.get("v") or []),
            role=data.get("role", "subject"),
            meta=data.get("meta") or {},
        )


@dataclass
class Proposal:
    """A recommended change, and the measurement that argues for it.

    ``confidence`` is deliberately part of the type: a proposal derived from a
    counter we watched move is not the same claim as one inferred from a static
    limit reading, and the report must not present them alike.
    """

    id: str
    severity: str
    title: str
    rationale: str
    evidence: List[str] = field(default_factory=list)
    change: List[str] = field(default_factory=list)
    confidence: str = "inferred"   # "observed" | "inferred" | "speculative"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Proposal":
        return cls(
            id=data["id"],
            severity=data.get("severity", "info"),
            title=data["title"],
            rationale=data.get("rationale", ""),
            evidence=list(data.get("evidence") or []),
            change=list(data.get("change") or []),
            confidence=data.get("confidence", "inferred"),
        )


@dataclass
class Analysis:
    """Everything the reports render. Produced by ``analyze.build``."""

    manifest: Dict[str, Any] = field(default_factory=dict)
    targets: List[Dict[str, Any]] = field(default_factory=list)
    phases: List[Phase] = field(default_factory=list)
    events: List[Event] = field(default_factory=list)
    series: List[Series] = field(default_factory=list)
    per_phase: List[Dict[str, Any]] = field(default_factory=list)
    per_target: List[Dict[str, Any]] = field(default_factory=list)
    correlations: List[Dict[str, Any]] = field(default_factory=list)
    proposals: List[Proposal] = field(default_factory=list)
    host: Dict[str, Any] = field(default_factory=dict)
    limits: Dict[str, Any] = field(default_factory=dict)
    damon: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def series_in(self, group: str) -> List[Series]:
        return [s for s in self.series if s.group == group]

    def groups(self) -> List[str]:
        seen = [g for g in PANELS if any(s.group == g for s in self.series)]
        extra = sorted({s.group for s in self.series} - set(PANELS))
        return seen + extra

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest": self.manifest,
            "targets": self.targets,
            "phases": [p.to_dict() for p in self.phases],
            "events": [e.to_dict() for e in self.events],
            "series": [s.to_dict() for s in self.series],
            "per_phase": self.per_phase,
            "per_target": self.per_target,
            "correlations": self.correlations,
            "proposals": [p.to_dict() for p in self.proposals],
            "host": self.host,
            "limits": self.limits,
            "damon": self.damon,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Analysis":
        return cls(
            manifest=data.get("manifest") or {},
            targets=data.get("targets") or [],
            phases=[Phase.from_dict(p) for p in data.get("phases") or []],
            events=[Event.from_dict(e) for e in data.get("events") or []],
            series=[Series.from_dict(s) for s in data.get("series") or []],
            per_phase=data.get("per_phase") or [],
            per_target=data.get("per_target") or [],
            correlations=data.get("correlations") or [],
            proposals=[Proposal.from_dict(p) for p in data.get("proposals") or []],
            host=data.get("host") or {},
            limits=data.get("limits") or {},
            damon=data.get("damon") or {},
            notes=data.get("notes") or [],
        )
