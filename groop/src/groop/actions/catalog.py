"""Allowlisted action catalog — defines every permitted action kind and how to
build its argv command preview.

No subprocess, no shell, no host mutation. Every action kind is an enum member
so unknown kinds are rejected at import time rather than at runtime.
"""

from __future__ import annotations

import enum
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


# ---------------------------------------------------------------------------
# Builder helpers — each returns a list of argv strings, never a shell string.
# ---------------------------------------------------------------------------

def _docker_restart(target: str) -> list[str]:
    return ["docker", "restart", target]


def _docker_stop(target: str) -> list[str]:
    return ["docker", "stop", target]


def _docker_start(target: str) -> list[str]:
    return ["docker", "start", target]


def _systemd_restart(target: str) -> list[str]:
    return ["systemctl", "restart", target]


def _systemd_stop(target: str) -> list[str]:
    return ["systemctl", "stop", target]


def _systemd_start(target: str) -> list[str]:
    return ["systemctl", "start", target]


def _systemd_set_property(target: str) -> list[str]:
    # target format: "UNIT KEY=VALUE [KEY=VALUE...]"
    parts = target.split()
    if len(parts) < 2:
        msg = f"systemd-set-property target must be 'UNIT KEY=VALUE ...', got {target!r}"
        raise ValueError(msg)
    return ["systemctl", "set-property", *parts]


# ---------------------------------------------------------------------------
# Catalog entry
# ---------------------------------------------------------------------------

class CatalogEntry(NamedTuple):
    kind: ActionKind
    builder: Callable[[str], list[str]]
    description: str


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
