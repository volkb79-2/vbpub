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
    # target format: "UNIT KEY=VALUE [KEY=VALUE...]"
    parts = target.split()
    if len(parts) < 2:
        msg = f"systemd-set-property target must be 'UNIT KEY=VALUE ...', got {target!r}"
        raise ValueError(msg)
    return [SYSTEMCTL_EXECUTABLE, "set-property", *parts]


# ---------------------------------------------------------------------------
# Catalog entry
# ---------------------------------------------------------------------------

class CatalogEntry(NamedTuple):
    kind: ActionKind
    builder: Callable[[str], list[str]]
    description: str


# ---------------------------------------------------------------------------
# Execution allowlist — only start, stop, restart kinds are executable.
# systemd-set-property, update, kill, and unknown kinds are excluded.
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
        parts = target.split()
        if len(parts) < 2 or not _SYSTEMD_UNIT_RE.fullmatch(parts[0]):
            raise ValueError(f"invalid systemd set-property target: {target!r}")
        if any("=" not in part or part.startswith(("-", ".")) for part in parts[1:]):
            raise ValueError(f"invalid systemd property target: {target!r}")


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
        "Preview systemctl set-property for cgroup memory knobs on a unit.",
    ),
}
