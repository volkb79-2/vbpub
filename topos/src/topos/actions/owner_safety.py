"""P87 - Docker action owner / protected-ID safety gate.

A narrow, fail-closed stopgap under D-016 (it is *not* the owner-adapter system
of P93). It closes two bypasses in the raw Docker mutation verbs
(``start``/``stop``/``restart``/``kill``/durable ``update``):

1. **Protected-ID bypass.** A container listed in ``[tiers] protected_services``
   by *name* could be mutated by addressing it with its short or full 64-hex id,
   because the P72 protected check compares the raw target string only. This
   module resolves the accepted identifier **once** (a single ``docker inspect``)
   to its canonical full id, short id and inspected name, then compares the
   protection rules against all three.

2. **Owner bypass.** A container owned by Docker Compose, CIU or Pterodactyl/
   Wings could be mutated behind its owner's back through Topos's raw Docker
   verb. Positive owner metadata is a typed refusal; the refusal names the
   detected owner and the safe next step, and never invokes the owner's CLI.

Contract highlights (the handoff is authoritative):

- Resolve the accepted id/name **once**; no second inspect between authorization
  and execution (contract 1 / no TOCTOU).
- Positive owner metadata is a refusal; *unknown* metadata is not permission
  (contract 2). Detection is identity/provenance only and never grants
  authorization (contract 5).
- Conflicting/partial owner labels fail closed as ``owner-ambiguous``
  (contract 6).
- ``docker inspect`` failure or an unresolvable identity is a typed refusal,
  never a name-only fallback (contract 7).

The label names are the ones already defined in the codebase; this module does
not invent new ones:

- Compose: ``com.docker.compose.project`` (``collect/dockerjoin.py``).
- CIU:     ``ciu.managed="true"`` / ``ciu.stack`` (``collect/dockerjoin.py``).
- Wings:   ``Service="Pterodactyl"`` + ``ContainerType="server_process"``
           (``TUI-SPEC.md`` friendly-name resolver: "the Docker labels wings
           sets are exactly ``Service=Pterodactyl`` + ``ContainerType=
           server_process``").
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Collection
from typing import Any

# ---------------------------------------------------------------------------
# Reused label names (do NOT invent new ones -- see module docstring).
# ---------------------------------------------------------------------------

COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
CIU_MANAGED_LABEL = "ciu.managed"
CIU_STACK_LABEL = "ciu.stack"
WINGS_SERVICE_LABEL = "Service"
WINGS_SERVICE_VALUE = "Pterodactyl"
WINGS_CONTAINER_TYPE_LABEL = "ContainerType"
WINGS_CONTAINER_TYPE_VALUE = "server_process"

# The Docker action kinds whose raw runtime verb this gate guards. systemd
# kinds and non-Docker targets are out of scope (the gate is a no-op for them).
DOCKER_OWNER_GUARDED_KINDS = frozenset(
    {"docker-start", "docker-stop", "docker-restart", "docker-kill", "docker-update"}
)

_FULL_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_DETAIL = 128
# A safe, bounded identifier fragment (compose project / ciu stack) that may
# appear in an operator-facing message. Anything outside this class is dropped
# so an unexpected label value can never reach the message surface.
_SAFE_IDENT_RE = re.compile(r"[^A-Za-z0-9_.:/@-]")

# An inspect seam: given the accepted target string it returns the raw
# ``docker inspect`` payload (a JSON array, a single object, or ``None`` on
# failure), or raises. Injected in tests; wired to a real resolver in the CLI.
OwnerInspect = Callable[[str], Any]


@dataclasses.dataclass(frozen=True)
class ResolvedContainer:
    """The canonical identity of one container from a single inspect."""

    full_id: str
    short_id: str
    name: str


@dataclasses.dataclass(frozen=True)
class OwnerSafetyRefusal:
    """A typed owner-safety refusal.

    ``reason`` is a stable machine tag (``owner-managed``, ``owner-ambiguous``,
    ``protected``, ``inspect-failed``). ``message`` is the bounded,
    secret-free operator-facing text.
    """

    reason: str
    message: str


def _first_inspect(value: Any) -> dict[str, Any] | None:
    """Return the first inspect object from a list, or the object itself."""
    if isinstance(value, list):
        return value[0] if value and isinstance(value[0], dict) else None
    return value if isinstance(value, dict) else None


def _sanitize_ident(value: str) -> str:
    """Bound and strip an identifier fragment for safe display.

    Only a conservative identifier character class survives, so a compose
    project / ciu stack name is shown while any surprising label content is
    reduced to nothing rather than echoed.
    """
    if not isinstance(value, str):
        return ""
    cleaned = _SAFE_IDENT_RE.sub("", value)
    return cleaned[:_MAX_DETAIL]


def resolve_identity(raw: Any) -> ResolvedContainer | None:
    """Derive the canonical identity from one inspect payload.

    Returns ``None`` when the payload is missing or its identity cannot be
    established (contract 7: no name-only fallback -- the caller refuses).
    """
    data = _first_inspect(raw)
    if data is None:
        return None
    full_id = data.get("Id")
    if not isinstance(full_id, str) or not _FULL_ID_RE.match(full_id):
        return None
    name = data.get("Name")
    name = name.lstrip("/") if isinstance(name, str) else ""
    return ResolvedContainer(full_id=full_id, short_id=full_id[:12], name=name)


def _extract_labels(raw: Any) -> dict[str, str] | None:
    """Return the container's ``Config.Labels`` map.

    ``None`` signals a malformed inspect object (``Config``/``Labels`` present
    but not a mapping) which the caller treats as an inspect failure. An
    absent labels map is the empty dict (a legitimately unlabelled container).
    """
    data = _first_inspect(raw)
    if data is None:
        return None
    config = data.get("Config")
    if config is None:
        return {}
    if not isinstance(config, dict):
        return None
    labels = config.get("Labels")
    if labels is None:
        return {}
    if not isinstance(labels, dict):
        return None
    out: dict[str, str] = {}
    for key, value in labels.items():
        if isinstance(key, str) and isinstance(value, str):
            out[key] = value
    return out


@dataclasses.dataclass(frozen=True)
class OwnerDetection:
    """The provenance verdict for one container's labels."""

    owner: str | None  # "compose" | "ciu" | "wings" | None
    ambiguous: bool
    detail: str = ""  # bounded, secret-free identifier for the message


def detect_owner(labels: dict[str, str]) -> OwnerDetection:
    """Classify a container's lifecycle owner from its labels.

    Detection is provenance only. It recognizes the coherent real chains
    (CIU sits *above* Compose, so ``ciu.managed`` + compose labels is CIU, not a
    conflict) and fails closed on genuinely conflicting or partial signals:

    - Two distinct owner families that cannot form a chain (e.g. Wings with
      Compose or CIU) -> ambiguous.
    - ``ciu.managed`` present with a value that is neither ``"true"`` nor
      ``"false"`` -> ambiguous (a partial owner signal we must not interpret).

    Unknown labels are not ownership (contract 2): a container with only
    unrelated labels returns ``owner=None, ambiguous=False``.
    """
    compose_project = labels.get(COMPOSE_PROJECT_LABEL)
    has_compose = isinstance(compose_project, str) and compose_project != ""

    ciu_managed = labels.get(CIU_MANAGED_LABEL)
    ciu_partial = ciu_managed is not None and ciu_managed not in ("true", "false")
    has_ciu = ciu_managed == "true"

    has_wings = (
        labels.get(WINGS_SERVICE_LABEL) == WINGS_SERVICE_VALUE
        or labels.get(WINGS_CONTAINER_TYPE_LABEL) == WINGS_CONTAINER_TYPE_VALUE
    )

    # A partial/uninterpretable CIU signal fails closed regardless of anything
    # else present.
    if ciu_partial:
        return OwnerDetection(owner=None, ambiguous=True)

    # Collapse the coherent CIU-over-Compose chain: CIU is the authoritative
    # top owner, so its presence subsumes the compose signal.
    families: list[str] = []
    if has_ciu:
        families.append("ciu")
    elif has_compose:
        families.append("compose")
    if has_wings:
        families.append("wings")

    if len(families) >= 2:
        # e.g. Wings labels alongside Compose/CIU labels: two incompatible
        # sources of truth. Fail closed.
        return OwnerDetection(owner=None, ambiguous=True)
    if not families:
        return OwnerDetection(owner=None, ambiguous=False)

    owner = families[0]
    if owner == "ciu":
        detail = _sanitize_ident(labels.get(CIU_STACK_LABEL, "") or "")
        return OwnerDetection(owner="ciu", ambiguous=False, detail=detail)
    if owner == "compose":
        detail = _sanitize_ident(compose_project or "")
        return OwnerDetection(owner="compose", ambiguous=False, detail=detail)
    return OwnerDetection(owner="wings", ambiguous=False)


def _owner_message(kind: str, detection: OwnerDetection) -> str:
    """Compose a bounded, secret-free refusal message naming the safe step."""
    verb = kind.removeprefix("docker-")
    if detection.owner == "compose":
        if detection.detail:
            step = f"use 'docker compose -p {detection.detail} {verb}' instead of raw docker {verb}"
        else:
            step = f"use 'docker compose' in the project instead of raw docker {verb}"
        return f"container is managed by Docker Compose; {step}"
    if detection.owner == "ciu":
        stack = f" (stack '{detection.detail}')" if detection.detail else ""
        return (
            f"container is managed by CIU{stack}; use the CIU deploy tooling for "
            f"this stack instead of raw docker {verb}"
        )
    if detection.owner == "wings":
        return (
            "container is managed by Pterodactyl/Wings; use the Pterodactyl panel "
            f"or Wings API instead of raw docker {verb}"
        )
    return f"container is owner-managed; refusing raw docker {verb}"


def _is_protected(
    resolved: ResolvedContainer, protected_services: Collection[str]
) -> bool:
    """True if the canonical identity intersects the protected list.

    Contract 1: compare against the canonical full id, short id and inspected
    name. Removing this canonicalization is what oracle 1's mutation test
    detects (a name-listed container addressed by its 64-hex id would slip
    through a raw ``target in protected_services`` check).
    """
    if not protected_services:
        return False
    candidates = {resolved.full_id, resolved.short_id}
    if resolved.name:
        candidates.add(resolved.name)
    return any(entry in candidates for entry in protected_services)


def evaluate(
    kind: str,
    target: str,
    *,
    inspect: OwnerInspect | None,
    protected_services: Collection[str] = (),
) -> OwnerSafetyRefusal | None:
    """Evaluate the owner/protected-ID safety gate for one Docker mutation.

    Returns ``None`` to allow the action, or a typed :class:`OwnerSafetyRefusal`
    to refuse it. The gate is a no-op (returns ``None``) when the kind is not a
    guarded Docker verb, or when no ``inspect`` seam is engaged (the legacy
    P46/P72 path). Once ``inspect`` is engaged the gate is fail-closed.

    A single ``inspect`` call resolves the canonical identity *and* the labels;
    no second inspect occurs between this authorization and execution.
    """
    if kind not in DOCKER_OWNER_GUARDED_KINDS:
        return None
    if inspect is None:
        return None

    try:
        raw = inspect(target)
    except Exception:
        return OwnerSafetyRefusal(
            reason="inspect-failed",
            message=(
                f"could not inspect container {target!r} to verify its owner; "
                "refusing the mutation (docker inspect failed)"
            ),
        )

    resolved = resolve_identity(raw)
    labels = _extract_labels(raw)
    if resolved is None or labels is None:
        return OwnerSafetyRefusal(
            reason="inspect-failed",
            message=(
                f"could not establish the identity of container {target!r} from "
                "docker inspect; refusing the mutation (no name-only fallback)"
            ),
        )

    detection = detect_owner(labels)
    if detection.ambiguous:
        return OwnerSafetyRefusal(
            reason="owner-ambiguous",
            message=(
                "container owner metadata is conflicting or incomplete "
                "(owner-ambiguous); refusing the mutation. Reconcile the "
                "container's owner labels or act through the authoritative owner"
            ),
        )
    if detection.owner is not None:
        return OwnerSafetyRefusal(
            reason="owner-managed",
            message=_owner_message(kind, detection),
        )

    if _is_protected(resolved, protected_services):
        return OwnerSafetyRefusal(
            reason="protected",
            message=(
                f"container {target!r} resolves to a protected service; refusing "
                "the mutation (matched by canonical id/name)"
            ),
        )
    return None


def default_owner_inspect(target: str) -> Any:
    """Production inspect seam: one ``docker inspect`` of the accepted target.

    Delegates to the same resolver the collector join uses, so there is one
    inspect implementation. Returns the raw payload or ``None`` on failure;
    :func:`evaluate` maps ``None`` to a fail-closed refusal.
    """
    from topos.collect.dockerjoin import default_docker_inspect

    return default_docker_inspect(target)


def default_protected_services() -> tuple[str, ...]:
    """Production protected list from ``[tiers] protected_services``."""
    from topos.config import load

    return tuple(load(None).protected_services)
