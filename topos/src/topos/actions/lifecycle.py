"""P93 - Lifecycle owner-chain protocol (D-016).

Freezes ``docs/LIFECYCLE-ADAPTERS.md``'s owner-chain adapter contract as
executable types and a shared resolution/verification kernel, ahead of adding
any Compose, CIU, Wings, Podman/Quadlet or future orchestrator *actions*.

An owner chain is an ordered sequence of :class:`OwnerLink` from the most
authoritative owner (index 0) down to the raw runtime object itself (the last
link). A standalone object's chain has exactly one link: itself. Discovery
and plan production are side-effect-free (contracts 1/2 of
``docs/LIFECYCLE-ADAPTERS.md``): no adapter built on this module performs a
mutating call from ``discover()`` or ``plan()``, and the fixture adapters for
Compose/CIU/Wings (``lifecycle_adapters.py``) invoke no external CLI/API at
all -- see ``docs/LIFECYCLE-ADAPTERS.md`` contract 6 and P93 oracle O11.

Execution reuses the existing P46/P78 gated-execution kernel
(``execute.py``'s ``_execute_gated``); this module supplies the pre-execution
resolution, capability check, revalidation and post-execution verification
typing that kernel plugs into via lifecycle-aware gates. Discovery metadata
never grants authorization by itself -- it only selects *which* owner the
existing root/admin/confirm/audit gates are then evaluated against.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Callable


class OwnerFamily(str, enum.Enum):
    """Closed set of lifecycle-owner families this protocol understands.

    Compose/CIU/Wings are fixture-only (contract 6): their discovery and
    capability contracts are defined and tested here, but no adapter in this
    codebase invokes their CLIs/APIs -- actual invocation is out of scope for
    P93. Future families (Podman/Quadlet, Kubernetes, Swarm, Nomad, ...) are
    documented in ``docs/LIFECYCLE-ADAPTERS.md`` and stay out of this enum
    until they get their own separately versioned adapter.
    """

    DOCKER = "docker"
    SYSTEMD = "systemd"
    COMPOSE = "compose"
    CIU = "ciu"
    WINGS = "wings"
    FAKE = "fake"  # test-only family for the O11 side-effect-free proof


class Provenance(str, enum.Enum):
    """How a link's identity/ownership signal was established."""

    LABEL = "label"
    INFERRED = "inferred"
    FIXTURE = "fixture"


class Confidence(str, enum.Enum):
    HIGH = "high"
    LOW = "low"


class Capability(str, enum.Enum):
    """Closed set of operations an adapter may advertise for one owner link.

    Absence of a capability is a refusal, never a generic fallback (contract
    2 of ``docs/LIFECYCLE-ADAPTERS.md`` / oracle O8).
    """

    INSPECT = "inspect"
    START = "start"
    STOP = "stop"
    RESTART = "restart"
    KILL = "kill"
    UPDATE = "update"
    SET_PROPERTY = "set-property"


@dataclasses.dataclass(frozen=True)
class OwnerLink:
    """One link in an owner chain.

    ``incarnation`` is the concrete, comparable instance identity used for
    revalidation (a Docker container's full id, a systemd unit's invocation
    marker, a Compose project name, ...) -- it is what changed/disappeared
    detection compares (oracles O6/O7), never a mutable label. ``detail`` is
    a bounded, secret-free identifier fragment safe for operator-facing
    messages (mirrors ``owner_safety.OwnerDetection.detail``).
    """

    family: OwnerFamily
    identity: str
    incarnation: str
    provenance: Provenance
    confidence: Confidence
    capabilities: frozenset[Capability]
    detail: str = ""
    state: str = ""


@dataclasses.dataclass(frozen=True)
class ChainConflict:
    """A typed reason discovery could not produce one clean authoritative link.

    Covers both genuinely conflicting/partial ownership signals and a failed
    discovery read (e.g. an inspect failure) -- either way, resolution must
    refuse rather than guess (oracle O5).
    """

    reason: str
    message: str


@dataclasses.dataclass(frozen=True)
class DiscoveryResult:
    """Side-effect-free discovery output (contract 1).

    ``chain`` is ordered from the most authoritative link (index 0) to the
    raw runtime object itself (the last link). ``conflict`` is set instead of
    (or alongside) a chain when discovery could not establish one clean
    authoritative owner; it always blocks resolution
    (:func:`resolve_authoritative_owner`).
    """

    chain: tuple[OwnerLink, ...]
    conflict: ChainConflict | None = None


@dataclasses.dataclass(frozen=True)
class ChainRefusal:
    """A typed, audited refusal from the owner-chain kernel.

    ``reason`` is a stable machine tag: ``conflict`` (O5), ``disappeared``
    (O6), ``stale`` (O7), ``unsupported`` (O8), ``verification-failed`` (O9).
    """

    reason: str
    message: str


@dataclasses.dataclass(frozen=True)
class LifecyclePlan:
    """Side-effect-free, immutable plan produced by an adapter (contract 2).

    ``argv`` is set for adapters that execute through a fixed local argv
    (Docker/systemd); ``api_intent`` is a bounded, secret-free description of
    the operation for adapters that would execute through an API instead.
    Exactly one of the two is set. No fixture adapter's plan is ever
    executed (Compose/CIU/Wings invocation is out of scope for P93).
    """

    owner: OwnerLink
    action: Capability
    observed_incarnation: str
    observed_state: str
    argv: tuple[str, ...] | None
    api_intent: str | None
    reversible: bool
    persistence: str
    required_authority: str = "root+admin+confirm"
    timeout_s: float = 30.0

    def __post_init__(self) -> None:
        if (self.argv is None) == (self.api_intent is None):
            raise ValueError("exactly one of argv or api_intent must be set")


@dataclasses.dataclass(frozen=True)
class VerificationOutcome:
    """Typed post-execution verification result (contract 4 / oracle O9).

    ``owner_verified``/``runtime_verified`` are independently reported so a
    partial outcome (one confirms, one doesn't) is never collapsed to a bare
    boolean and never silently swallowed.
    """

    owner_verified: bool
    runtime_verified: bool
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.owner_verified and self.runtime_verified


class LifecycleAdapter:
    """Base contract every lifecycle-owner adapter implements.

    Subclasses must not perform any mutating call from ``discover`` or
    ``plan``; only a gated execution entry point (outside this class) may
    invoke a plan's ``argv``/``api_intent``, and only after the shared P46/P78
    authorization kernel has gated it.
    """

    family: OwnerFamily

    def discover(self, target: str) -> DiscoveryResult:  # pragma: no cover - interface
        raise NotImplementedError

    def capabilities(self, owner: OwnerLink) -> frozenset[Capability]:
        return owner.capabilities

    def plan(
        self, discovery: DiscoveryResult, action: Capability, target: str
    ) -> LifecyclePlan:  # pragma: no cover - interface
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Kernel: resolution, capability gating, revalidation, verification
# ---------------------------------------------------------------------------


def resolve_authoritative_owner(discovery: DiscoveryResult) -> OwnerLink | ChainRefusal:
    """Select the authoritative owner from a discovery result (contract 1).

    Precedence is structural, not a preference score: whichever link
    discovery placed at index 0 is authoritative, because every adapter here
    orders its chain from most to least authoritative at discovery time.
    Conflicting or partial signals never reach index 0 -- they are reported
    through ``discovery.conflict`` and refused here instead (oracle O5).
    """

    if discovery.conflict is not None:
        return ChainRefusal(reason=discovery.conflict.reason, message=discovery.conflict.message)
    if not discovery.chain:
        return ChainRefusal(reason="conflict", message="discovery produced no owner chain")
    return discovery.chain[0]


def check_capability(owner: OwnerLink, action: Capability) -> ChainRefusal | None:
    """Refuse an action the resolved owner does not advertise (oracle O8).

    Absence of a capability is a refusal, never a generic fallback (contract
    2 of ``docs/LIFECYCLE-ADAPTERS.md``).
    """

    if action in owner.capabilities:
        return None
    return ChainRefusal(
        reason="unsupported",
        message=(
            f"{owner.family.value} owner does not support {action.value!r}; "
            "refusing (no generic fallback)"
        ),
    )


def revalidate(adapter: LifecycleAdapter, target: str, expected: OwnerLink) -> ChainRefusal | None:
    """Re-run discovery immediately before execution and compare identity.

    Returns a typed refusal when the previously selected owner has
    disappeared, including when a *different* (necessarily lower-precedence)
    owner is now authoritative -- oracle O6 requires a refusal, never a fall
    back to a lower link in the chain. Returns a ``stale`` refusal when the
    same owner is still authoritative but its incarnation changed (oracle
    O7, a stale plan). Returns ``None`` when execution may proceed.
    """

    fresh = adapter.discover(target)
    resolution = resolve_authoritative_owner(fresh)
    if isinstance(resolution, ChainRefusal):
        return ChainRefusal(
            reason="disappeared",
            message=(
                f"the previously selected {expected.family.value} owner "
                f"{expected.identity!r} is no longer resolvable "
                f"({resolution.message}); refusing rather than falling back to a "
                "lower link in the chain"
            ),
        )
    if resolution.family != expected.family or resolution.identity != expected.identity:
        return ChainRefusal(
            reason="disappeared",
            message=(
                f"the previously selected {expected.family.value} owner "
                f"{expected.identity!r} is no longer the authoritative owner; "
                "refusing rather than falling back to a lower link in the chain"
            ),
        )
    if resolution.incarnation != expected.incarnation:
        return ChainRefusal(
            reason="stale",
            message=(
                f"the {expected.family.value} owner {expected.identity!r}'s "
                "incarnation changed since the plan was built (stale plan); "
                "refusing -- preview again with the fresh incarnation"
            ),
        )
    return None


def verify(
    *,
    owner_check: Callable[[], bool],
    runtime_check: Callable[[], bool],
) -> VerificationOutcome:
    """Run owner-level and observed-runtime verification after execution.

    Both checks are best-effort booleans supplied by the caller (real checks
    read state; fixture checks return canned values). A failing or raising
    check is never swallowed into a bare success -- it is carried in the
    returned outcome for the caller to record as a durable partial-outcome
    audit entry (oracle O9).
    """

    try:
        owner_ok = bool(owner_check())
    except BaseException:
        owner_ok = False
    try:
        runtime_ok = bool(runtime_check())
    except BaseException:
        runtime_ok = False
    detail = ""
    if not owner_ok and not runtime_ok:
        detail = "owner and observed runtime both failed to confirm the new state"
    elif not owner_ok:
        detail = "owner-reported state did not confirm the action"
    elif not runtime_ok:
        detail = "observed runtime state did not confirm the action"
    return VerificationOutcome(owner_verified=owner_ok, runtime_verified=runtime_ok, detail=detail)
