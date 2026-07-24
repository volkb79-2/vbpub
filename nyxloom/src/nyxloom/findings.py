"""System->user FINDINGS channel (option C). PACKAGE FN-1.

A *finding* is an informational insight surfaced to the operator that is
neither a task event nor a blocking decision (blocking product calls live in
DECISIONS-INBOX.md, handled by `decisions.py`). Examples: "nemotron-ultra-free
scores ~= the paid pro tier", "deepseek-flash reaches X on coding at ~1/10th
the cost of the incumbent". These are advisory, one-way (system -> user), and
machine-generated.

WHY EVENT-FIRST (not files-first like decisions): decisions are human-owned and
two-way (the user edits the md to resolve them), so an md file is the durable
record. Findings are machine-generated and one-way, and their whole value is in
STRUCTURED fields (model ids, metrics, scores) that must survive verbatim to
build a SPEC-13-safe push. So a finding is recorded DIRECTLY as a typed
`FINDING_RECORDED` event (payload carries the structured fields, no lossy md
round-trip). This rides the existing event-log -> SQLite -> greppability ->
dashboard plumbing for free; the precedent is `ARTIFACT_REGISTERED`, another
projection-less informational event (see KNOWN_IGNORED_EVENT_TYPES).

OPTION C (operator decision 2026-07-24): a typed KIND REGISTRY (`FINDING_KINDS`)
marks each kind `pushable` and, when pushable, carries a FIXED, code-owned push
template over TYPED fields (model ids, numbers). Pushable kinds produce a
SPEC-13-safe push (`notify.notification_for`). The `generic` kind is
`pushable=False`: its free-text title/body render only in the dashboard (where
SPEC-13's push boundary does not apply -- exactly like the redacted
`blocked_reason` preview), never in a push.

SPEC-13 SAFETY (non-negotiable): only pushable kinds reach a notification, and
`notify` builds that push from `push_template` (code-owned) formatted over the
kind's `required_fields` ONLY -- never from `title`/`body` (which may hold
model-authored prose). Emitters of a pushable kind MUST pass typed values
(ids/numbers) for those fields; `title`/`body` are dashboard-only.

INTERFACE CONTRACT (frozen):

- FindingKind(key, pushable, required_fields: tuple[str, ...],
  push_template: str | None, tag: str). push_template is None iff not pushable.
- FINDING_KINDS: dict[str, FindingKind] -- registry. Seeded with `generic`
  (free-text, not pushable), `model_near_equivalent`, `cost_crossover`.
- SEVERITIES = ("info", "note", "important").
- Finding dataclass: finding_id ("F-<project>-<sequence>"), sequence, created
  (ISO str), project, kind, title, body, fields (dict), severity, task_id
  (str | None). `.pushable` property reads the registry.
- record_finding(project, kind, *, title, body="", fields=None, task_id=None,
  severity="info", actor=None, now=None) -> Event: validate kind is registered,
  severity is known, title is non-empty, and (for a pushable kind) every
  required_field is present in `fields`; then append a FINDING_RECORDED event
  with payload {kind, title, body, fields, severity}. Raises FindingError on any
  validation failure. actor defaults to Actor(ActorKind.OPERATOR, "findings").
- load_findings(project, *, since=0) -> list[Finding]: scan iter_events for
  FINDING_RECORDED, newest-first (descending sequence).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import storage
from .log import get_logger
from .types import Actor, ActorKind, Event, EventType

log = get_logger("findings")

SEVERITIES: tuple[str, ...] = ("info", "note", "important")


class FindingError(Exception):
    pass


@dataclass(frozen=True)
class FindingKind:
    key: str
    pushable: bool
    required_fields: tuple[str, ...]
    push_template: str | None      # code-owned; None iff not pushable
    tag: str


# The registry. `generic` is the free-text, dashboard-only arm of option C;
# the typed kinds carry a fixed push template over id/number fields only.
FINDING_KINDS: dict[str, FindingKind] = {
    "generic": FindingKind(
        key="generic", pushable=False, required_fields=(),
        push_template=None, tag="finding",
    ),
    "model_near_equivalent": FindingKind(
        key="model_near_equivalent", pushable=True,
        required_fields=("model_a", "model_b", "metric", "score_a", "score_b"),
        push_template="{model_a} ~= {model_b} on {metric} ({score_a} vs {score_b})",
        tag="finding",
    ),
    "cost_crossover": FindingKind(
        key="cost_crossover", pushable=True,
        required_fields=("cheap_model", "ref_model", "metric", "ratio"),
        push_template="{cheap_model} matches {ref_model} on {metric} at ~{ratio}x cost",
        tag="finding",
    ),
}


@dataclass
class Finding:
    finding_id: str
    sequence: int
    created: str
    project: str
    kind: str
    title: str
    body: str
    fields: dict[str, Any] = field(default_factory=dict)
    severity: str = "info"
    task_id: str | None = None

    @property
    def pushable(self) -> bool:
        spec = FINDING_KINDS.get(self.kind)
        return bool(spec and spec.pushable)


def record_finding(project: str, kind: str, *, title: str, body: str = "",
                   fields: dict[str, Any] | None = None,
                   task_id: str | None = None, severity: str = "info",
                   actor: Actor | None = None, now=None) -> Event:
    """Validate and append a FINDING_RECORDED event; return the Event.

    Raises FindingError if the kind is not registered, the severity is
    unknown, the title is blank, or a pushable kind is missing a required
    field (which would make its push template un-formattable).
    """
    spec = FINDING_KINDS.get(kind)
    if spec is None:
        raise FindingError(f"unknown finding kind: {kind!r}")
    if severity not in SEVERITIES:
        raise FindingError(f"unknown severity: {severity!r} (want one of {SEVERITIES})")
    if not title.strip():
        raise FindingError("finding title must be non-empty")
    fields = dict(fields or {})
    if spec.pushable:
        missing = [f for f in spec.required_fields if f not in fields]
        if missing:
            raise FindingError(
                f"pushable kind {kind!r} missing required field(s): {missing}")

    ev = storage.append_event(
        project,
        actor=actor or Actor(ActorKind.OPERATOR, "findings"),
        type=EventType.FINDING_RECORDED,
        payload={
            "kind": kind,
            "title": title,
            "body": body,
            "fields": fields,
            "severity": severity,
        },
        task_id=task_id,
        timestamp=now,
    )
    log.info("finding recorded", project=project, kind=kind, sequence=ev.sequence,
             pushable=spec.pushable)
    return ev


def _finding_from_event(ev: Event) -> Finding:
    p = ev.payload or {}
    return Finding(
        finding_id=f"F-{ev.project}-{ev.sequence}",
        sequence=ev.sequence,
        created=ev.timestamp.isoformat(),
        project=ev.project,
        kind=p.get("kind", "generic"),
        title=p.get("title", ""),
        body=p.get("body", ""),
        fields=dict(p.get("fields") or {}),
        severity=p.get("severity", "info"),
        task_id=ev.task_id,
    )


def load_findings(project: str, *, since: int = 0) -> list[Finding]:
    """Return this project's findings, newest-first (descending sequence)."""
    out = [
        _finding_from_event(ev)
        for ev in storage.iter_events(project, since=since)
        if ev.type is EventType.FINDING_RECORDED
    ]
    out.sort(key=lambda f: f.sequence, reverse=True)
    return out


def promote_seed_text(finding: "Finding") -> str:
    """Build the first-turn intake message that seeds a discussion of a finding.
    A fixed template over the finding's own fields -- this becomes intake prompt/
    transcript text (NOT a notification), so free-text is acceptable here."""
    lines = [
        f"Let's discuss this finding and decide what action to take (if any).",
        f"Finding {finding.finding_id} [{finding.kind}]: {finding.title}",
    ]
    if finding.body:
        lines.append(finding.body)
    if finding.fields:
        pairs = ", ".join(f"{k}={v}" for k, v in sorted(finding.fields.items()))
        lines.append(f"Details: {pairs}")
    if finding.task_id:
        lines.append(f"Related task: {finding.task_id}")
    return "\n".join(lines)
