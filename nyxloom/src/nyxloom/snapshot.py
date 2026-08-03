"""Typed snapshot-input descriptors and the authoritative fan-in boundary.

PACKAGE CR-02a (core-redesign plan section "CR-02 -- authoritative snapshot
fail-closed audit"; DEEP-REVIEW DR-03 / RISK-004).

WHY THIS MODULE EXISTS
----------------------
Every reconcile pass builds a snapshot of the world from ~15 independent
sources (config, projected state, the event log, the decisions inbox, handoff
frontmatter + lint, leases, routes/provider capability, gate evidence, git
facts, receipts, artifact digests) and then plans irreversible effects from it
-- launching agent processes, merging to the default branch, authorizing a
gate. Before this module each source failed in its own hand-written way, and
several of those ways were *fail-open*: a broad ``except Exception`` collapsed
"I could not read this" into a benign-looking empty/False/None value that the
planner cannot distinguish from a genuine clean reading. The canonical case is
``Daemon._build_input`` turning a lint exception into an empty finding set and
therefore into ``lint_clean=True`` -- an unreadable lint result *authorizing* a
dispatch.

The rule this module encodes:

    A fact that authorizes an irreversible effect is either KNOWN-GOOD or it
    is UNAVAILABLE. There is no third, cheerful reading.

So an acquisition never returns a bare domain value. It returns a
:class:`SnapshotInput` carrying the value *and* its class (authoritative or
advisory), its status, a stable machine-readable reason code, bounded redacted
diagnostics, and its provenance. Failures are DATA, not control flow, and they
aggregate into a :class:`SnapshotAudit` that a caller consults once at a single
fan-in boundary.

THE TWO CLASSES
---------------
``AUTHORITATIVE``
    The fact participates in authorizing an effect. If it is not ``OK`` the
    pass performs **zero** launch / merge / gate-authorizing / other
    irreversible effects and records exactly one actionable durable event.
    Sentinel defaults are forbidden: :meth:`SnapshotInput.require` raises
    rather than hand back a value the caller might mistake for a reading.

``ADVISORY``
    The fact improves a decision but never authorizes one, and its absence
    only ever *reduces* what the system will do (a provider probe that cannot
    run marks the route unusable; a missing log mtime cannot confirm a stall).
    Advisory degradation is allowed to continue -- but never silently: it is
    recorded as typed evidence in the same audit and surfaces as a durable
    event.

The classification lives HERE, in one place, so CR-05's handler registry and
CR-06's planning rules can consume the audit without re-deriving policy.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
It does not know any domain names, read any file, or import ``storage`` /
``config`` / ``daemon``. It is a pure vocabulary plus an aggregation. The
domain names ("lint", "leases", "git_head_revision", ...) stay explicit at the
acquisition sites in ``daemon.py`` -- this is not a generic ``dict[str, Any]``
bag, and ``SnapshotInput`` is generic over the value type so the acquiring code
keeps its static type.
"""

from __future__ import annotations

import enum
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Iterable, TypeVar

__all__ = [
    "DETAIL_MAX",
    "InputClass",
    "InputStatus",
    "Provenance",
    "Reason",
    "SnapshotAudit",
    "SnapshotBuilder",
    "SnapshotInput",
    "SnapshotUnavailable",
    "acquire",
    "bounded_detail",
    "redact",
]

T = TypeVar("T")

#: Hard cap on any diagnostic string carried in a descriptor or an event
#: payload. Diagnostics exist to make a fault actionable, not to relay a
#: subprocess transcript into the durable event log.
DETAIL_MAX = 200


class InputClass(enum.Enum):
    """Whether a snapshot input may authorize an irreversible effect."""

    AUTHORITATIVE = "authoritative"
    ADVISORY = "advisory"


class InputStatus(enum.Enum):
    """The outcome of one acquisition.

    Only ``OK`` carries a usable value. The three failure statuses are kept
    distinct because they call for different operator responses: ``UNAVAILABLE``
    is "the source did not answer", ``MALFORMED`` is "the source answered with
    something that is not a valid reading", and ``STALE`` is "the source
    answered about a different world than the one we are planning for" (a
    wrong task/attempt binding, a revision that has moved on).
    """

    OK = "ok"
    UNAVAILABLE = "unavailable"
    MALFORMED = "malformed"
    STALE = "stale"


class Reason(enum.Enum):
    """Stable machine-readable reason codes.

    These are part of the durable event payload and of the operator-facing
    contract, so they are a CLOSED vocabulary with stable string values --
    never a free-text message. The human-readable specifics go in the bounded
    ``detail`` field alongside.
    """

    #: The acquisition raised. The exception type name is in ``detail``.
    SOURCE_ERROR = "source-error"
    #: The source is absent where it was required to exist.
    SOURCE_MISSING = "source-missing"
    #: The source answered, but the answer does not parse / does not validate.
    MALFORMED_VALUE = "malformed-value"
    #: The answer is about a superseded revision/generation of the world.
    STALE_VALUE = "stale-value"
    #: The answer is bound to a different task/attempt/artifact than the one
    #: being planned for -- truthy by content, wrong by identity.
    IDENTITY_MISMATCH = "identity-mismatch"
    #: The acquisition did not complete within its bound.
    TIMEOUT = "timeout"
    #: The acquisition was refused by the operating system.
    PERMISSION_DENIED = "permission-denied"
    #: A subprocess/probe exited non-zero without a usable answer.
    PROBE_FAILED = "probe-failed"
    #: The declared digest and the recomputed digest disagree.
    DIGEST_MISMATCH = "digest-mismatch"


#: Reason codes that are ALWAYS a fault, independent of status. Used by the
#: constructors below to reject a nonsensical ``OK`` + reason combination.
_FAULT_STATUSES = frozenset(
    {InputStatus.UNAVAILABLE, InputStatus.MALFORMED, InputStatus.STALE}
)

#: A credential-shaped key, its separator, an OPTIONAL auth scheme word, and
#: the value. The optional scheme group is what makes
#: ``Authorization: Bearer <token>`` redact the token rather than the word
#: "Bearer" -- the naive form of this pattern masks the scheme and leaves the
#: secret in place, which is worse than not scrubbing at all because it looks
#: scrubbed.
_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|bearer|password|passwd|secret|token)"
    r"\b(\s*[:=]\s*|\s+)(?:(?:bearer|basic|token)\s+)?(\S+)"
)


def redact(text: str) -> str:
    """Mask credential-shaped ``key=value`` pairs in a diagnostic string.

    Diagnostics are built from exception reprs and probe descriptions, which
    routinely quote the command line or URL that failed -- and those carry
    ntfy tokens, webhook URLs with embedded credentials, and provider API
    keys. This is a bounded belt-and-braces scrub, not a general secret
    scanner: the primary defence is that acquisition sites pass a *reason
    code* and a short description rather than raw subprocess output (see
    ``bounded_detail``).
    """
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", text)


def bounded_detail(text: object) -> str:
    """Normalize any diagnostic into a redacted, single-line, bounded string.

    Newlines are collapsed so one descriptor is always one line in a rendered
    audit, and the result is hard-truncated to :data:`DETAIL_MAX` so a
    pathological exception message cannot bloat the event log.
    """
    s = redact(" ".join(str(text).split()))
    if len(s) > DETAIL_MAX:
        s = s[: DETAIL_MAX - 3] + "..."
    return s


@dataclass(frozen=True)
class Provenance:
    """Where a reading came from, in a form an operator can act on.

    ``kind`` is a short closed-ish token naming the acquisition mechanism
    ("config-file", "event-log", "git", "lease-store", "receipt-file",
    "lint", "provider-probe", "filesystem"). ``ref`` identifies the specific
    thing read -- a repo-relative path, a project id, a git ref, an attempt
    id. Both are bounded and redacted; neither is ever a secret carrier.
    """

    kind: str
    ref: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", bounded_detail(self.kind))
        object.__setattr__(self, "ref", bounded_detail(self.ref))

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "ref": self.ref}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Provenance":
        return cls(kind=str(d.get("kind", "")), ref=str(d.get("ref", "")))


class SnapshotUnavailable(Exception):
    """Raised by :meth:`SnapshotInput.require` on a non-``OK`` descriptor.

    This exists so that a caller which forgets to check ``.ok`` fails LOUDLY
    instead of quietly consuming ``None``. It is deliberately not caught
    anywhere in the fan-in: the fan-in checks statuses, it does not catch
    this.
    """

    def __init__(self, descriptor: "SnapshotInput[Any]") -> None:
        self.descriptor = descriptor
        reason = descriptor.reason.value if descriptor.reason else "unknown"
        super().__init__(
            f"snapshot input {descriptor.name!r} is {descriptor.status.value} "
            f"({reason}): {descriptor.detail}"
        )


@dataclass(frozen=True)
class SnapshotInput(Generic[T]):
    """One acquired fact, with its authority class and its failure semantics.

    Construct via :func:`acquire`, :meth:`ok_value`, or :meth:`failed` rather
    than directly -- those enforce the invariants ``__post_init__`` checks.
    """

    name: str
    input_class: InputClass
    status: InputStatus
    provenance: Provenance
    value: T | None = None
    reason: Reason | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status is InputStatus.OK:
            if self.reason is not None:
                raise ValueError(f"{self.name}: an OK input cannot carry a reason code")
        elif self.reason is None:
            raise ValueError(
                f"{self.name}: a {self.status.value} input must carry a reason code"
            )
        object.__setattr__(self, "detail", bounded_detail(self.detail) if self.detail else "")

    # -- construction ----------------------------------------------------

    @classmethod
    def ok_value(
        cls,
        name: str,
        input_class: InputClass,
        value: T,
        *,
        provenance: Provenance,
    ) -> "SnapshotInput[T]":
        return cls(
            name=name,
            input_class=input_class,
            status=InputStatus.OK,
            provenance=provenance,
            value=value,
        )

    @classmethod
    def failed(
        cls,
        name: str,
        input_class: InputClass,
        reason: Reason,
        *,
        provenance: Provenance,
        status: InputStatus = InputStatus.UNAVAILABLE,
        detail: object = "",
    ) -> "SnapshotInput[T]":
        if status not in _FAULT_STATUSES:
            raise ValueError(f"{name}: {status.value} is not a fault status")
        return cls(
            name=name,
            input_class=input_class,
            status=status,
            provenance=provenance,
            value=None,
            reason=reason,
            detail=bounded_detail(detail) if detail else "",
        )

    # -- reading ---------------------------------------------------------

    @property
    def ok(self) -> bool:
        return self.status is InputStatus.OK

    @property
    def authoritative(self) -> bool:
        return self.input_class is InputClass.AUTHORITATIVE

    def require(self) -> T:
        """The value, or raise. NEVER returns a substitute.

        This is the only way authoritative code should read a value. It has no
        ``default=`` parameter on purpose: a default is exactly the
        unavailable-collapses-to-benign defect this package exists to remove.
        """
        if not self.ok:
            raise SnapshotUnavailable(self)
        return self.value  # type: ignore[return-value]

    def degraded_or(self, fallback: T) -> T:
        """The value, or ``fallback`` -- ADVISORY inputs only.

        Calling this on an authoritative input is a programming error and
        raises, so the "just give me a default" escape hatch cannot be reached
        from authority-bearing code.
        """
        if self.authoritative:
            raise ValueError(
                f"{self.name}: degraded_or() is advisory-only; an authoritative "
                f"input must use require() and let the fan-in fail closed"
            )
        return self.value if self.ok else fallback  # type: ignore[return-value]

    # -- serde -----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The descriptor WITHOUT its value -- the durable/event projection.

        Values are live domain objects (a ``dict[str, TaskStateFile]``, an
        event tuple); they are not part of the audit record. What replay needs
        is the shape of the fault, and that is exactly these five fields.
        """
        return {
            "name": self.name,
            "class": self.input_class.value,
            "status": self.status.value,
            "reason": self.reason.value if self.reason else None,
            "detail": self.detail,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SnapshotInput[Any]":
        raw_reason = d.get("reason")
        return cls(
            name=str(d["name"]),
            input_class=InputClass(d["class"]),
            status=InputStatus(d["status"]),
            provenance=Provenance.from_dict(d.get("provenance") or {}),
            value=None,
            reason=Reason(raw_reason) if raw_reason else None,
            detail=str(d.get("detail") or ""),
        )


def acquire(
    name: str,
    input_class: InputClass,
    loader: Callable[[], T],
    *,
    provenance: Provenance,
    reason: Reason = Reason.SOURCE_ERROR,
    status: InputStatus = InputStatus.UNAVAILABLE,
) -> SnapshotInput[T]:
    """Run ``loader`` and translate ANY failure into a typed descriptor.

    This is THE single sanctioned broad-exception site for snapshot
    acquisition (census class: process-boundary translation). It exists so
    every other acquisition site can be written without a ``try``: instead of
    N hand-rolled ``except Exception: return <benign default>`` handlers, each
    with its own silently-chosen fail direction, there is one translator whose
    output is always a value the caller must explicitly interpret.

    ``BaseException`` is deliberately NOT caught: ``KeyboardInterrupt`` and
    ``SystemExit`` are shutdown signals, not source faults, and swallowing
    them into "the lint result is unavailable" would be a fresh fail-open.
    """
    try:
        value = loader()
    except Exception as exc:  # census: process-boundary translation (CR-02a)
        return SnapshotInput.failed(
            name,
            input_class,
            reason,
            provenance=provenance,
            status=status,
            detail=f"{type(exc).__name__}: {exc}",
        )
    return SnapshotInput.ok_value(name, input_class, value, provenance=provenance)


@dataclass(frozen=True)
class SnapshotAudit:
    """The aggregated verdict over one pass's acquisitions.

    Ordering is deterministic (by descriptor name) everywhere it is observable
    -- in :meth:`failures`, in :meth:`degradations`, in :meth:`summary`, and in
    the event payload -- so a replay of the same faults produces a
    byte-identical record and a digest that can be compared across passes.
    """

    inputs: tuple[SnapshotInput[Any], ...] = ()

    def __post_init__(self) -> None:
        names = [i.name for i in self.inputs]
        if len(names) != len(set(names)):
            dupes = sorted({n for n in names if names.count(n) > 1})
            raise ValueError(f"duplicate snapshot input name(s): {dupes}")
        object.__setattr__(
            self, "inputs", tuple(sorted(self.inputs, key=lambda i: i.name))
        )

    # -- classification --------------------------------------------------

    def failures(self) -> tuple[SnapshotInput[Any], ...]:
        """Authoritative inputs that are not OK -- the fail-closed set."""
        return tuple(i for i in self.inputs if i.authoritative and not i.ok)

    def degradations(self) -> tuple[SnapshotInput[Any], ...]:
        """Advisory inputs that are not OK -- the visible-degradation set."""
        return tuple(i for i in self.inputs if not i.authoritative and not i.ok)

    def get(self, name: str) -> SnapshotInput[Any] | None:
        return next((i for i in self.inputs if i.name == name), None)

    @property
    def permits_effects(self) -> bool:
        """True iff every authoritative input is OK.

        This is the ONE predicate the daemon consults before planning or
        executing anything irreversible. It is intentionally all-or-nothing:
        a partial snapshot cannot be reasoned about safely without knowing
        which effects each missing fact could have authorized, and encoding
        that per-effect matrix is exactly the kind of policy that drifts.
        """
        return not self.failures()

    # -- rendering -------------------------------------------------------

    @staticmethod
    def _render(items: Iterable[SnapshotInput[Any]]) -> str:
        return "; ".join(
            f"{i.name}:{i.status.value}:{i.reason.value if i.reason else 'unknown'}"
            for i in items
        )

    def summary(self) -> str:
        """Deterministic one-line ``name:status:reason`` list of the failures."""
        return self._render(self.failures())

    def degradation_summary(self) -> str:
        return self._render(self.degradations())

    def digest(self) -> str:
        """Stable short digest over (name, status, reason) of every fault.

        Deliberately excludes ``detail`` and ``provenance.ref``: those carry
        run-specific text (an errno, a path) that would make an unchanged
        fault look like a new one and defeat the degradation de-duplication.
        """
        material = "|".join(
            f"{i.name}:{i.status.value}:{i.reason.value if i.reason else ''}"
            for i in self.inputs
            if not i.ok
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

    def event_payload(self) -> dict[str, Any]:
        """The bounded, deterministic payload for a durable snapshot event."""
        return {
            "digest": self.digest(),
            "unavailable": [i.to_dict() for i in self.failures()],
            "degraded": [i.to_dict() for i in self.degradations()],
            "summary": self.summary(),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SnapshotAudit":
        """Rebuild a value-less audit from an event payload (replay).

        Round-trips :meth:`event_payload` for every field that payload
        carries, so a replayed audit reports the same failures, the same
        degradations, the same summary and the same digest as the pass that
        emitted it.
        """
        rows = list(payload.get("unavailable") or []) + list(payload.get("degraded") or [])
        return cls(tuple(SnapshotInput.from_dict(r) for r in rows))


@dataclass
class SnapshotBuilder:
    """Collects descriptors for one pass and hands back the audit.

    Usage at an acquisition site::

        b = SnapshotBuilder()
        lint_findings = b.authoritative(
            "lint", lambda: lint.lint_project(cfg),
            provenance=Provenance("lint", str(cfg.root)))
        # ...the rest of the pass's acquisitions...
        audit = b.audit()
        if not audit.permits_effects:
            return 0   # one event, zero effects

    (The example deliberately writes "# ..." rather than a bare ``...``
    line. coverage.py's exclusion pass is TEXTUAL, not AST-aware: a line
    that is only an ellipsis matches its empty-body rule even inside a
    docstring, and excluding any line of a docstring excludes the whole
    docstring statement. On a changed file the diff-coverage gate then
    reports it as a `pragma: no cover` escape -- which is exactly what it
    is, just an accidental one. It cost this package one gate run.)

    ``authoritative``/``advisory`` return the descriptor, never the raw value,
    so a caller cannot accidentally use an unavailable reading. Reading the
    value is an explicit ``.require()`` (authoritative, raises) or
    ``.degraded_or(default)`` (advisory).
    """

    _inputs: list[SnapshotInput[Any]] = field(default_factory=list)

    def add(self, descriptor: SnapshotInput[Any]) -> SnapshotInput[Any]:
        """Record an already-built descriptor (for non-``loader`` shapes).

        Used where the acquisition is not a single callable -- for example a
        per-item scan that has to distinguish "absent" from "malformed" for
        each item and then reports one aggregate descriptor.
        """
        self._inputs.append(descriptor)
        return descriptor

    def authoritative(
        self,
        name: str,
        loader: Callable[[], T],
        *,
        provenance: Provenance,
        reason: Reason = Reason.SOURCE_ERROR,
    ) -> SnapshotInput[T]:
        return self.add(
            acquire(
                name,
                InputClass.AUTHORITATIVE,
                loader,
                provenance=provenance,
                reason=reason,
            )
        )  # type: ignore[return-value]

    def advisory(
        self,
        name: str,
        loader: Callable[[], T],
        *,
        provenance: Provenance,
        reason: Reason = Reason.SOURCE_ERROR,
    ) -> SnapshotInput[T]:
        return self.add(
            acquire(
                name, InputClass.ADVISORY, loader, provenance=provenance, reason=reason
            )
        )  # type: ignore[return-value]

    def audit(self) -> SnapshotAudit:
        return SnapshotAudit(tuple(self._inputs))
