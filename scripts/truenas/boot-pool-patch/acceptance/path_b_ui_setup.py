"""Validate the explicit setup route for Path B PWMCP acceptance."""

from __future__ import annotations


PATH_B_SETUP_MODES = frozenset({"ui-wizard", "preprovisioned"})


def resolve_path_b_setup_mode(profile: str, value: str) -> str | None:
    """Return the declared Path B setup route; never infer one from absence."""
    value = value.strip()
    if profile == "path-a":
        if value:
            raise ValueError("TRUENAS_PATH_B_SETUP is valid only with Path B")
        return None
    if profile != "path-b":
        raise ValueError("TRUENAS_STORAGE_PROFILE must be path-a or path-b")
    if value not in PATH_B_SETUP_MODES:
        raise ValueError(
            "Path B live-ui requires TRUENAS_PATH_B_SETUP=ui-wizard or preprovisioned"
        )
    return value


def contains_pool_create_request(requests: list[object]) -> bool:
    """Detect any browser pool.create request, regardless of its pool name."""
    return any(
        isinstance(request, dict) and request.get("method") == "pool.create"
        for request in requests
    )
