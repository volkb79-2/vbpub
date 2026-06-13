"""
CIU v2 deploy_pkg — health gate and container status classification.

Implements S7.7 (health gate semantics) and S7.8 (anchored name filter).
"""
from __future__ import annotations

import time
from typing import Callable

# ---------------------------------------------------------------------------
# S7.7 — classify
# ---------------------------------------------------------------------------

# Docker inspect .State dict shapes we recognise:
#   .State.Health.Status  → 'healthy' | 'unhealthy' | 'starting'
#   No .State.Health key  → no healthcheck configured

_CLASSIFY_MAP: dict[str, str] = {
    "healthy": "healthy",
    "unhealthy": "unhealthy",
    "starting": "starting",
}


def classify(inspect_state: dict | None) -> str:
    """Classify a container's health from its Docker inspect .State dict.

    Returns one of:
        'healthy'        — healthcheck passing
        'starting'       — healthcheck present but not yet passing (S7.7: NOT passed)
        'unhealthy'      — healthcheck failing
        'no-healthcheck' — container has no healthcheck configured
        'not-found'      — inspect_state is None (container not found)
    """
    if inspect_state is None:
        return "not-found"

    health = inspect_state.get("Health")
    if health is None:
        # No Health key → no healthcheck defined
        return "no-healthcheck"

    status = health.get("Status", "")
    return _CLASSIFY_MAP.get(status, "unhealthy")


# ---------------------------------------------------------------------------
# S7.7 — evaluate_gate
# ---------------------------------------------------------------------------

def evaluate_gate(
    statuses: dict[str, str],
) -> tuple[bool, dict[str, list[str]]]:
    """Evaluate the health gate for a set of container statuses.

    The gate passes iff every status is 'healthy' or 'no-healthcheck'.
    'starting' goes to the 'pending' bucket and causes the gate to FAIL.

    Returns:
        (passed: bool, summary: dict)

    summary buckets (lists of container names):
        'healthy'        — passed health check
        'pending'        — starting / not yet healthy
        'unhealthy'      — health check failing
        'no_healthcheck' — no healthcheck configured (warning, not failing)
        'not_found'      — container not found
    """
    summary: dict[str, list[str]] = {
        "healthy": [],
        "pending": [],
        "unhealthy": [],
        "no_healthcheck": [],
        "not_found": [],
    }

    for name, status in statuses.items():
        if status == "healthy":
            summary["healthy"].append(name)
        elif status == "no-healthcheck":
            summary["no_healthcheck"].append(name)
        elif status == "starting":
            summary["pending"].append(name)
        elif status == "not-found":
            summary["not_found"].append(name)
        else:
            # 'unhealthy' or any unknown status
            summary["unhealthy"].append(name)

    passed = (
        len(summary["pending"]) == 0
        and len(summary["unhealthy"]) == 0
        and len(summary["not_found"]) == 0
    )
    return passed, summary


# ---------------------------------------------------------------------------
# S7.7 — wait_for_gate
# ---------------------------------------------------------------------------

def wait_for_gate(
    check_fn: Callable[[], dict[str, str]],
    *,
    timeout_s: float,
    interval_s: float = 5.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[bool, dict]:
    """Poll *check_fn* until the health gate passes or *timeout_s* elapses.

    *sleep_fn* and *clock* are injectable for deterministic tests.

    Returns the final evaluate_gate result: (passed, summary).
    """
    deadline = clock() + timeout_s
    passed, summary = evaluate_gate(check_fn())
    while not passed and clock() < deadline:
        sleep_fn(interval_s)
        passed, summary = evaluate_gate(check_fn())
    return passed, summary


# ---------------------------------------------------------------------------
# S7.8 — anchored_name_filter
# ---------------------------------------------------------------------------

def anchored_name_filter(project: str, env_tag: str, name: str) -> str:
    """Return an anchored regex suitable for docker --filter name=.

    S7.8: must not use substring matches; the pattern anchors both ends.

    Example:
        anchored_name_filter("myproj", "prod", "redis") == "^myproj-prod-redis$"
    """
    return f"^{project}-{env_tag}-{name}$"
