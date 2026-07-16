"""Allowlisted action catalog — defines every permitted action kind and how to
build its argv command preview.

Preview is available for all catalog kinds. Execution is restricted to the
EXECUTION_ALLOWLIST (start/stop/restart only). systemd-set-property, update,
kill, and any future kinds are preview-only unless separately opted in.

No subprocess, no shell, no host mutation. Every action kind is an enum member
so unknown kinds are rejected at import time rather than at runtime.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Callable
from typing import NamedTuple


class ActionKind(str, enum.Enum):
    """Allowlisted admin action kinds. Add new kinds here (and a builder)."""

    DOCKER_RESTART = "docker-restart"
    DOCKER_STOP = "docker-stop"
    DOCKER_START = "docker-start"
    SYSTEMD_RESTART = "systemd-restart"
    SYSTEMD_STOP = "systemd-stop"
    SYSTEMD_START = "systemd-start"
    SYSTEMD_SET_PROPERTY = "systemd-set-property"
    DOCKER_KILL = "docker-kill"
    SYSTEMD_KILL = "systemd-kill"
    DOCKER_UPDATE = "docker-update"


# These are deliberately fixed.  The execution kernel never consults PATH and
# never accepts an executable or argv from a caller.
DOCKER_EXECUTABLE = "/usr/bin/docker"
SYSTEMCTL_EXECUTABLE = "/usr/bin/systemctl"


# ---------------------------------------------------------------------------
# Builder helpers — each returns a list of argv strings, never a shell string.
# ---------------------------------------------------------------------------

def _docker_restart(target: str) -> list[str]:
    return [DOCKER_EXECUTABLE, "restart", target]


def _docker_stop(target: str) -> list[str]:
    return [DOCKER_EXECUTABLE, "stop", target]


def _docker_start(target: str) -> list[str]:
    return [DOCKER_EXECUTABLE, "start", target]


def _systemd_restart(target: str) -> list[str]:
    return [SYSTEMCTL_EXECUTABLE, "restart", target]


def _systemd_stop(target: str) -> list[str]:
    return [SYSTEMCTL_EXECUTABLE, "stop", target]


def _systemd_start(target: str) -> list[str]:
    return [SYSTEMCTL_EXECUTABLE, "start", target]


def _systemd_set_property(target: str) -> list[str]:
    # P49 replaces the composite "UNIT KEY=VALUE" format with structured
    # unit/property/value inputs.  The catalog-level builder still accepts
    # a target string for backward compatibility of the catalog interface,
    # but only when it is a bare unit name (no KEY=VALUE fragments).
    # The structured path is in governance.py (build_set_property_argv).
    if not target:
        msg = "systemd-set-property target must be a bare unit name"
        raise ValueError(msg)
    if "=" in target or " " in target:
        msg = (
            "systemd-set-property composite target format is removed; "
            "use --property and --value instead"
        )
        raise ValueError(msg)
    # A bare unit name produces an incomplete argv (no property/value).
    # This builder is only used for catalog completeness; the structured
    # governance.py path builds the full argv.
    return [SYSTEMCTL_EXECUTABLE, "set-property", target]


def _docker_kill(target: str) -> list[str]:
    """Basic docker kill argv builder (signal added by kill_ops.py)."""
    return [DOCKER_EXECUTABLE, "kill", target]


def _systemd_kill(target: str) -> list[str]:
    """Basic systemctl kill argv builder (signal added by kill_ops.py)."""
    return [SYSTEMCTL_EXECUTABLE, "kill", target]


def _docker_update(target: str) -> list[str]:
    """Basic docker update argv builder (resources added by update_ops.py)."""
    return [DOCKER_EXECUTABLE, "update", target]


# ---------------------------------------------------------------------------
# Catalog entry
# ---------------------------------------------------------------------------

class CatalogEntry(NamedTuple):
    kind: ActionKind
    builder: Callable[[str], list[str]]
    description: str


# ---------------------------------------------------------------------------
# Execution allowlist — only the argument-free start/stop/restart kinds are
# executable through the generic execute_plan() path.
#
# systemd-set-property (P49), docker-kill/systemd-kill and docker-update (P72)
# are deliberately EXCLUDED: each carries validated arguments (property/value,
# signal + --force, memory/cpus + below-current) and its own gates, and each has
# a dedicated entry point (execute_set_property / execute_kill / execute_update)
# that enforces them.  Admitting them here would let execute_plan() run the
# catalog's argument-free builder — `docker kill <target>`, whose docker default
# is SIGKILL — under the generic EXECUTE token, with no signal allowlist, no
# --force gate and no protected-entity check.
# ---------------------------------------------------------------------------

EXECUTION_ALLOWLIST: frozenset[ActionKind] = frozenset({
    ActionKind.DOCKER_RESTART,
    ActionKind.DOCKER_STOP,
    ActionKind.DOCKER_START,
    ActionKind.SYSTEMD_RESTART,
    ActionKind.SYSTEMD_STOP,
    ActionKind.SYSTEMD_START,
})


# Shared plan-target contract.  Preview and execution both use this function;
# execution calls it again immediately before invoking its runner.
_INVALID_TARGET_RE = re.compile(
    r"[\x00-\x1f\x7f;&|`$(){}\[\]<>\"'\\/]"
)
_DOCKER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_DOCKER_ID_RE = re.compile(r"^[a-f0-9]{64}$")
_SYSTEMD_UNIT_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_:@.-]*\.(?:service|slice|scope|target|socket|mount|timer|path)$"
)
def validate_target(kind: ActionKind, target: str) -> None:
    """Validate the target portion of an immutable action plan.

    The validator intentionally accepts only names, never paths, options, or
    shell syntax.  ``systemd-set-property`` remains preview-only, but its
    first token is still required to be a valid unit name.
    """
    if not isinstance(target, str) or not target:
        raise ValueError("target must not be empty")
    if target.startswith("-"):
        raise ValueError(f"target must not be option-like: {target!r}")
    match = _INVALID_TARGET_RE.search(target)
    if match:
        raise ValueError(f"target contains invalid character {match.group(0)!r}: {target!r}")

    if kind in EXECUTION_ALLOWLIST:
        if any(char.isspace() for char in target):
            raise ValueError(f"target must not contain whitespace: {target!r}")
        if kind in {
            ActionKind.DOCKER_START,
            ActionKind.DOCKER_STOP,
            ActionKind.DOCKER_RESTART,
        }:
            if _DOCKER_ID_RE.fullmatch(target) or _DOCKER_NAME_RE.fullmatch(target):
                return
            raise ValueError(f"invalid Docker container identifier: {target!r}")
        if kind in {
            ActionKind.SYSTEMD_START,
            ActionKind.SYSTEMD_STOP,
            ActionKind.SYSTEMD_RESTART,
        }:
            if ".." in target or not _SYSTEMD_UNIT_RE.fullmatch(target):
                raise ValueError(f"invalid systemd unit name: {target!r}")
            return
        raise ValueError(f"execution not allowed for kind {kind.value!r}")

    if kind is ActionKind.SYSTEMD_SET_PROPERTY:
        # In P49, the target is just the unit name (not a composite).
        # The property/value are validated separately in governance.py.
        if not target:
            raise ValueError(f"invalid systemd set-property target (empty): {target!r}")
        if any(char.isspace() for char in target):
            raise ValueError(f"systemd set-property target must not contain whitespace: {target!r}")
        if not _SYSTEMD_UNIT_RE.fullmatch(target):
            raise ValueError(f"invalid systemd unit name for set-property: {target!r}")

    if kind in {ActionKind.DOCKER_KILL, ActionKind.DOCKER_UPDATE}:
        # Docker kill and update: target is a container identifier.
        if any(char.isspace() for char in target):
            raise ValueError(f"target must not contain whitespace: {target!r}")
        if not (_DOCKER_ID_RE.fullmatch(target) or _DOCKER_NAME_RE.fullmatch(target)):
            raise ValueError(f"invalid Docker container identifier: {target!r}")

    if kind is ActionKind.SYSTEMD_KILL:
        # Systemd kill: target is a unit name.
        if any(char.isspace() for char in target):
            raise ValueError(f"target must not contain whitespace: {target!r}")
        if ".." in target or not _SYSTEMD_UNIT_RE.fullmatch(target):
            raise ValueError(f"invalid systemd unit name: {target!r}")


ACTION_CATALOG: dict[ActionKind, CatalogEntry] = {
    ActionKind.DOCKER_RESTART: CatalogEntry(
        ActionKind.DOCKER_RESTART,
        _docker_restart,
        "Restart a Docker container by container id or name.",
    ),
    ActionKind.DOCKER_STOP: CatalogEntry(
        ActionKind.DOCKER_STOP,
        _docker_stop,
        "Stop a Docker container by container id or name.",
    ),
    ActionKind.DOCKER_START: CatalogEntry(
        ActionKind.DOCKER_START,
        _docker_start,
        "Start a Docker container by container id or name.",
    ),
    ActionKind.SYSTEMD_RESTART: CatalogEntry(
        ActionKind.SYSTEMD_RESTART,
        _systemd_restart,
        "Restart a systemd unit by unit name.",
    ),
    ActionKind.SYSTEMD_STOP: CatalogEntry(
        ActionKind.SYSTEMD_STOP,
        _systemd_stop,
        "Stop a systemd unit by unit name.",
    ),
    ActionKind.SYSTEMD_START: CatalogEntry(
        ActionKind.SYSTEMD_START,
        _systemd_start,
        "Start a systemd unit by unit name.",
    ),
    ActionKind.SYSTEMD_SET_PROPERTY: CatalogEntry(
        ActionKind.SYSTEMD_SET_PROPERTY,
        _systemd_set_property,
        "Preview systemctl set-property for memory.high governance (use administrative preview instead).",
    ),
    ActionKind.DOCKER_KILL: CatalogEntry(
        ActionKind.DOCKER_KILL,
        _docker_kill,
        "Send a signal to a Docker container (use --signal).",
    ),
    ActionKind.SYSTEMD_KILL: CatalogEntry(
        ActionKind.SYSTEMD_KILL,
        _systemd_kill,
        "Send a signal to a systemd unit (use --signal).",
    ),
    ActionKind.DOCKER_UPDATE: CatalogEntry(
        ActionKind.DOCKER_UPDATE,
        _docker_update,
        "Update Docker container resource limits (use --memory/--cpus).",
    ),
}
