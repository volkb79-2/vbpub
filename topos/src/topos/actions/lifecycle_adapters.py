"""P93 - Concrete lifecycle-owner adapters and the P87 migration bridge.

``DockerAdapter`` and ``SystemdAdapter`` are the real, production-reachable
adapters. ``ComposeAdapter``, ``CiuAdapter`` and ``WingsAdapter`` are
fixture-only per ``docs/LIFECYCLE-ADAPTERS.md`` contract 6: they define
discovery/capability contracts and are exercised by tests, but nothing in
this codebase invokes their CLI/API (their ``plan()`` raises). ``FakeAdapter``
exists only to give oracle O11 a family with no production ties whatsoever.

``evaluate_docker_owner_chain`` is the P93 migration of the P87
``owner_safety.evaluate`` gate onto this protocol (contract 5): Docker
verbs are now resolved through ``DiscoveryResult`` / ``resolve_authoritative_
owner`` / capability-checking instead of a bespoke if/elif chain, while
reusing ``owner_safety``'s tested identity/label/message helpers so the
externally visible refusal reasons and messages are unchanged for existing
callers and tests (oracle O10).
"""

from __future__ import annotations

from collections.abc import Callable

from topos.actions import owner_safety
from topos.actions.catalog import DOCKER_EXECUTABLE, SYSTEMCTL_EXECUTABLE
from topos.actions.lifecycle import (
    Capability,
    ChainConflict,
    ChainRefusal,
    Confidence,
    DiscoveryResult,
    LifecycleAdapter,
    LifecyclePlan,
    OwnerFamily,
    OwnerLink,
    Provenance,
    resolve_authoritative_owner,
)

# ---------------------------------------------------------------------------
# Capability sets
# ---------------------------------------------------------------------------

DOCKER_STANDALONE_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.INSPECT,
        Capability.START,
        Capability.STOP,
        Capability.RESTART,
        Capability.KILL,
        Capability.UPDATE,
    }
)

SYSTEMD_STANDALONE_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.INSPECT,
        Capability.START,
        Capability.STOP,
        Capability.RESTART,
        Capability.KILL,
        Capability.SET_PROPERTY,
    }
)

# Fixture owner families (Compose/CIU/Wings): discovery/capability contracts
# only. No adapter here invokes their CLI/API, so no mutate capability is
# ever advertised -- a mutation request against one of these owners always
# refuses through check_capability() (oracle O8), matching the P87-era
# "owner-managed" refusal it replaces.
FIXTURE_OWNER_CAPABILITIES: frozenset[Capability] = frozenset({Capability.INSPECT})

_DOCKER_ACTION_TO_KIND = {
    Capability.START: "docker-start",
    Capability.STOP: "docker-stop",
    Capability.RESTART: "docker-restart",
    Capability.KILL: "docker-kill",
    Capability.UPDATE: "docker-update",
}
_DOCKER_ACTION_TO_VERB = {
    Capability.START: "start",
    Capability.STOP: "stop",
    Capability.RESTART: "restart",
    Capability.KILL: "kill",
    Capability.UPDATE: "update",
}
_SYSTEMD_ACTION_TO_VERB = {
    Capability.START: "start",
    Capability.STOP: "stop",
    Capability.RESTART: "restart",
    Capability.KILL: "kill",
}


# ---------------------------------------------------------------------------
# Fixture adapters: Compose, CIU, Wings (contract 6 -- discovery/capability
# contracts only; no CLI/API invocation anywhere in this module).
# ---------------------------------------------------------------------------


class ComposeAdapter(LifecycleAdapter):
    """Fixture adapter for Docker Compose ownership (no `docker compose` calls)."""

    family = OwnerFamily.COMPOSE

    def describe_link(self, project: str) -> OwnerLink:
        return OwnerLink(
            family=OwnerFamily.COMPOSE,
            identity=project or "",
            incarnation=project or "",
            provenance=Provenance.LABEL,
            confidence=Confidence.HIGH,
            capabilities=FIXTURE_OWNER_CAPABILITIES,
            detail=project or "",
        )

    def discover(self, target: str) -> DiscoveryResult:
        raise NotImplementedError(
            "ComposeAdapter discovery is fixture-only; use describe_link() with "
            "data already obtained by DockerAdapter (no compose CLI is invoked)"
        )

    def plan(self, discovery: DiscoveryResult, action: Capability, target: str) -> LifecyclePlan:
        raise NotImplementedError(
            "Compose invocation is out of scope for P93 (fixture adapter only)"
        )


class CiuAdapter(LifecycleAdapter):
    """Fixture adapter for CIU ownership (no CIU deploy-tooling calls)."""

    family = OwnerFamily.CIU

    def describe_link(self, stack: str) -> OwnerLink:
        return OwnerLink(
            family=OwnerFamily.CIU,
            identity=stack or "",
            incarnation=stack or "",
            provenance=Provenance.LABEL,
            confidence=Confidence.HIGH,
            capabilities=FIXTURE_OWNER_CAPABILITIES,
            detail=stack or "",
        )

    def discover(self, target: str) -> DiscoveryResult:
        raise NotImplementedError(
            "CiuAdapter discovery is fixture-only; use describe_link() with data "
            "already obtained by DockerAdapter (no CIU CLI is invoked)"
        )

    def plan(self, discovery: DiscoveryResult, action: Capability, target: str) -> LifecyclePlan:
        raise NotImplementedError(
            "CIU invocation is out of scope for P93 (fixture adapter only)"
        )


class WingsAdapter(LifecycleAdapter):
    """Fixture adapter for Pterodactyl/Wings ownership (no panel/Wings API calls)."""

    family = OwnerFamily.WINGS

    def describe_link(self) -> OwnerLink:
        return OwnerLink(
            family=OwnerFamily.WINGS,
            identity="wings",
            incarnation="wings",
            provenance=Provenance.LABEL,
            confidence=Confidence.HIGH,
            capabilities=FIXTURE_OWNER_CAPABILITIES,
        )

    def discover(self, target: str) -> DiscoveryResult:
        raise NotImplementedError(
            "WingsAdapter discovery is fixture-only; use describe_link() with "
            "data already obtained by DockerAdapter (no Wings API is invoked)"
        )

    def plan(self, discovery: DiscoveryResult, action: Capability, target: str) -> LifecyclePlan:
        raise NotImplementedError(
            "Wings invocation is out of scope for P93 (fixture adapter only)"
        )


class FakeAdapter(LifecycleAdapter):
    """Test-only adapter proving discovery/plan can be fully side-effect-free.

    Carries no ties to any real family (``OwnerFamily.FAKE``) so oracle O11's
    "no invocation of any external CLI" proof cannot pass by accident through
    a real adapter's already-injected fixture.
    """

    family = OwnerFamily.FAKE

    def __init__(self, *, incarnation: str = "fake-1", state: str = "running") -> None:
        self._incarnation = incarnation
        self._state = state

    def discover(self, target: str) -> DiscoveryResult:
        link = OwnerLink(
            family=OwnerFamily.FAKE,
            identity=target,
            incarnation=self._incarnation,
            provenance=Provenance.FIXTURE,
            confidence=Confidence.HIGH,
            capabilities=frozenset({Capability.INSPECT, Capability.RESTART}),
            state=self._state,
        )
        return DiscoveryResult(chain=(link,))

    def plan(self, discovery: DiscoveryResult, action: Capability, target: str) -> LifecyclePlan:
        owner = discovery.chain[0]
        return LifecyclePlan(
            owner=owner,
            action=action,
            observed_incarnation=owner.incarnation,
            observed_state=owner.state,
            argv=None,
            api_intent=f"fake:{action.value}:{target}",
            reversible=True,
            persistence="runtime",
        )


# ---------------------------------------------------------------------------
# DockerAdapter -- production adapter, reuses owner_safety's tested identity
# and label-detection helpers.
# ---------------------------------------------------------------------------


class DockerAdapter(LifecycleAdapter):
    """Discovers a Docker container's owner chain from one ``docker inspect``.

    Reuses ``owner_safety.resolve_identity``/``detect_owner`` (contract 1's
    single-inspect, no-TOCTOU discipline) rather than re-implementing label
    parsing. ``systemd_owner_lookup`` is an injectable, test-only seam
    (oracle O2): production wiring passes ``None`` because no real
    "which systemd unit owns this container" detector exists yet -- that is
    exactly the kind of future extension ``docs/LIFECYCLE-ADAPTERS.md``
    reserves for a separately versioned adapter.
    """

    family = OwnerFamily.DOCKER

    def __init__(
        self,
        *,
        inspect: Callable[[str], object],
        systemd_owner_lookup: Callable[[str], OwnerLink | None] | None = None,
    ) -> None:
        self._inspect = inspect
        self._systemd_owner_lookup = systemd_owner_lookup

    def discover(self, target: str) -> DiscoveryResult:
        try:
            raw = self._inspect(target)
        except Exception:
            return DiscoveryResult(
                chain=(),
                conflict=ChainConflict(
                    "inspect-failed",
                    f"could not inspect container {target!r} to verify its owner; "
                    "refusing the mutation (docker inspect failed)",
                ),
            )

        resolved = owner_safety.resolve_identity(raw)
        labels = owner_safety._extract_labels(raw)  # reuse existing (no name-only fallback)
        if resolved is None or labels is None:
            return DiscoveryResult(
                chain=(),
                conflict=ChainConflict(
                    "inspect-failed",
                    f"could not establish the identity of container {target!r} "
                    "from docker inspect; refusing the mutation (no name-only "
                    "fallback)",
                ),
            )

        state = _docker_state(raw)
        docker_link = OwnerLink(
            family=OwnerFamily.DOCKER,
            identity=resolved.name or resolved.short_id,
            incarnation=resolved.full_id,
            provenance=Provenance.LABEL,
            confidence=Confidence.HIGH,
            capabilities=DOCKER_STANDALONE_CAPABILITIES,
            state=state,
        )

        detection = owner_safety.detect_owner(labels)
        if detection.ambiguous:
            return DiscoveryResult(
                chain=(docker_link,),
                conflict=ChainConflict(
                    "owner-ambiguous",
                    "container owner metadata is conflicting or incomplete "
                    "(owner-ambiguous); refusing the mutation. Reconcile the "
                    "container's owner labels or act through the authoritative "
                    "owner",
                ),
            )

        chain = [docker_link]
        if detection.owner == "compose":
            chain.insert(0, ComposeAdapter().describe_link(detection.detail))
        elif detection.owner == "ciu":
            chain.insert(0, CiuAdapter().describe_link(detection.detail))
        elif detection.owner == "wings":
            chain.insert(0, WingsAdapter().describe_link())
        elif self._systemd_owner_lookup is not None:
            owner_link = None
            try:
                owner_link = self._systemd_owner_lookup(target)
            except Exception:
                owner_link = None
            if owner_link is not None:
                chain.insert(0, owner_link)
        return DiscoveryResult(chain=tuple(chain))

    def plan(self, discovery: DiscoveryResult, action: Capability, target: str) -> LifecyclePlan:
        owner = discovery.chain[0] if discovery.chain else None
        if owner is None or owner.family is not OwnerFamily.DOCKER:
            raise ValueError("DockerAdapter.plan requires a docker-family authoritative owner")
        verb = _DOCKER_ACTION_TO_VERB.get(action)
        if verb is None:
            raise ValueError(f"unsupported docker action for planning: {action!r}")
        argv = (DOCKER_EXECUTABLE, verb, target)
        return LifecyclePlan(
            owner=owner,
            action=action,
            observed_incarnation=owner.incarnation,
            observed_state=owner.state,
            argv=argv,
            api_intent=None,
            reversible=action is not Capability.KILL,
            persistence="runtime",
        )


def _docker_state(raw: object) -> str:
    data = owner_safety._first_inspect(raw)
    if not isinstance(data, dict):
        return ""
    state = data.get("State")
    if not isinstance(state, dict):
        return ""
    status = state.get("Status")
    return status if isinstance(status, str) else ""


# ---------------------------------------------------------------------------
# SystemdAdapter -- production adapter (self-owning by default).
# ---------------------------------------------------------------------------


class SystemdAdapter(LifecycleAdapter):
    """Discovers a systemd unit's owner chain.

    A bare unit is its own authority by default: no adapter in this codebase
    yet detects a higher owner above a systemd unit (Podman/Quadlet is
    reserved future work per ``docs/LIFECYCLE-ADAPTERS.md``). ``owner_lookup``
    is an injectable, test-only seam that proves the protocol's precedence
    handling generalizes to that future case without shipping a production
    detector for it now.
    """

    family = OwnerFamily.SYSTEMD

    def __init__(
        self,
        *,
        owner_lookup: Callable[[str], OwnerLink | None] | None = None,
        invocation_reader: Callable[[str], str | None] | None = None,
        state_reader: Callable[[str], str | None] | None = None,
    ) -> None:
        self._owner_lookup = owner_lookup
        self._invocation_reader = invocation_reader
        self._state_reader = state_reader

    def discover(self, target: str) -> DiscoveryResult:
        incarnation = target
        if self._invocation_reader is not None:
            try:
                read = self._invocation_reader(target)
            except Exception:
                read = None
            if read:
                incarnation = read
        state = ""
        if self._state_reader is not None:
            try:
                state = self._state_reader(target) or ""
            except Exception:
                state = ""
        self_link = OwnerLink(
            family=OwnerFamily.SYSTEMD,
            identity=target,
            incarnation=incarnation,
            provenance=Provenance.LABEL,
            confidence=Confidence.HIGH,
            capabilities=SYSTEMD_STANDALONE_CAPABILITIES,
            state=state,
        )
        chain = [self_link]
        if self._owner_lookup is not None:
            owner_link = None
            try:
                owner_link = self._owner_lookup(target)
            except Exception:
                owner_link = None
            if owner_link is not None:
                chain.insert(0, owner_link)
        return DiscoveryResult(chain=tuple(chain))

    def plan(self, discovery: DiscoveryResult, action: Capability, target: str) -> LifecyclePlan:
        owner = discovery.chain[0] if discovery.chain else None
        if owner is None or owner.family is not OwnerFamily.SYSTEMD:
            raise ValueError("SystemdAdapter.plan requires a systemd-family authoritative owner")
        verb = _SYSTEMD_ACTION_TO_VERB.get(action)
        if verb is None:
            raise ValueError(f"unsupported systemd action for planning: {action!r}")
        argv = (SYSTEMCTL_EXECUTABLE, verb, target)
        return LifecyclePlan(
            owner=owner,
            action=action,
            observed_incarnation=owner.incarnation,
            observed_state=owner.state,
            argv=argv,
            api_intent=None,
            reversible=True,
            persistence="runtime",
        )


# ---------------------------------------------------------------------------
# P93 migration bridge: Docker verbs now resolve through the owner-chain
# kernel. Returns the same owner_safety.OwnerSafetyRefusal shape so
# execute.py's existing gate wiring (and its tests) are unaffected.
# ---------------------------------------------------------------------------


def evaluate_docker_owner_chain(
    kind: str,
    target: str,
    *,
    inspect: Callable[[str], object] | None,
    protected_services: object = (),
) -> owner_safety.OwnerSafetyRefusal | None:
    """P93 migration of the P87 Docker owner/protected-ID gate.

    A no-op (returns ``None``) when *kind* is not a guarded Docker verb or no
    ``inspect`` seam is engaged, matching ``owner_safety.evaluate``'s legacy
    no-op contract. Once engaged, resolution flows through
    ``DiscoveryResult`` -> :func:`resolve_authoritative_owner` -> capability
    checking, so Docker/Compose/CIU/Wings share the same kernel a future
    adapter plugs into, while producing the identical refusal reasons and
    messages ``owner_safety.evaluate`` always has.
    """

    if kind not in owner_safety.DOCKER_OWNER_GUARDED_KINDS:
        return None
    if inspect is None:
        return None

    # Exactly one inspect call (contract 1 / no TOCTOU): the raw payload is
    # cached and reused for discovery, owner-message rendering and the
    # protected-id check below instead of re-invoking *inspect*.
    try:
        raw = inspect(target)
    except Exception:
        return owner_safety.OwnerSafetyRefusal(
            reason="inspect-failed",
            message=(
                f"could not inspect container {target!r} to verify its owner; "
                "refusing the mutation (docker inspect failed)"
            ),
        )

    adapter = DockerAdapter(inspect=lambda _target, _raw=raw: _raw)
    discovery = adapter.discover(target)
    resolution = resolve_authoritative_owner(discovery)
    if isinstance(resolution, ChainRefusal):
        # discovery's ChainConflict.reason is already one of owner_safety's own
        # tags ("owner-ambiguous", "inspect-failed") -- passed through as-is.
        return owner_safety.OwnerSafetyRefusal(reason=resolution.reason, message=resolution.message)

    if resolution.family is not OwnerFamily.DOCKER:
        # An owner above the raw container is authoritative: refuse the raw
        # verb regardless of capability (mirrors the P87 "owner-managed"
        # refusal -- these fixture owners advertise no mutate capability
        # anyway, but the message stays owner-specific rather than generic).
        labels = owner_safety._extract_labels(raw) or {}
        detection = owner_safety.detect_owner(labels)
        return owner_safety.OwnerSafetyRefusal(
            reason="owner-managed", message=owner_safety._owner_message(kind, detection)
        )

    # Standalone: fall through to the existing protected-service check, which
    # is identity-canonicalization, not ownership, and stays as-is.
    resolved = owner_safety.resolve_identity(raw)
    if resolved is not None and owner_safety._is_protected(resolved, protected_services):
        return owner_safety.OwnerSafetyRefusal(
            reason="protected",
            message=(
                f"container {target!r} resolves to a protected service; refusing "
                "the mutation (matched by canonical id/name)"
            ),
        )
    return None
