"""Validate the explicit setup route for Path B PWMCP acceptance."""

from __future__ import annotations

import os


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


def reserved_partition_four_name(disk: str) -> str:
    """Derive the reserved partition name from the selected whole boot disk."""
    if not isinstance(disk, str) or not disk.startswith("/dev/") or disk.endswith("/"):
        raise ValueError("TRUENAS_DISK_DEVICE must name a whole disk under /dev")
    disk_name = os.path.basename(disk)
    if not disk_name:
        raise ValueError("TRUENAS_DISK_DEVICE must name a whole disk under /dev")
    separator = "p" if disk_name.startswith(("nvme", "pmem")) else ""
    return f"{disk_name}{separator}4"


def contains_pool_create_request(requests: list[object]) -> bool:
    """Detect any browser pool.create request, regardless of its pool name."""
    return any(
        isinstance(request, dict) and request.get("method") == "pool.create"
        for request in requests
    )


def pool_create_request_evidence(
    browser_requests: list[dict],
    jobs_before: list[dict],
    jobs_after: list[dict],
    pool_name: str,
) -> tuple[dict, str]:
    """Prefer this browser's observed request; otherwise require a new job record."""
    observed = [
        request for request in browser_requests
        if request.get("method") == "pool.create"
        and any(
            isinstance(param, dict) and param.get("name") == pool_name
            for param in (request.get("params") if isinstance(request.get("params"), list) else [])
        )
    ]
    if observed:
        if len(observed) != 1 or not isinstance(observed[0].get("params"), list):
            raise ValueError(f"expected one observed browser pool.create request for {pool_name!r}")
        request_data = next(
            (param for param in observed[0]["params"]
             if isinstance(param, dict) and param.get("name") == pool_name),
            None,
        )
        if request_data is None:
            raise ValueError(f"observed browser request has no create data for {pool_name!r}")
        return request_data, "browser WebSocket request"

    prior_ids = {job.get("id") for job in jobs_before if job.get("id") is not None}
    created = []
    for job in jobs_after:
        if (job.get("method") != "pool.create" or job.get("id") is None
                or job.get("id") in prior_ids):
            continue
        arguments = job.get("arguments")
        if (
            isinstance(arguments, list)
            and arguments
            and isinstance(arguments[0], dict)
            and arguments[0].get("name") == pool_name
        ):
            created.append((job, arguments[0]))
    if len(created) != 1:
        raise ValueError(
            f"expected one new pool.create job for {pool_name!r}; found {len(created)}"
        )
    job, data = created[0]
    return data, f"new middleware pool.create job id={job['id']}"


def validate_pool_create_topology(topology: object, expected_partition: str) -> None:
    """Require one Stripe data vdev whose only device is the reserved partition."""
    if not isinstance(topology, dict):
        raise ValueError("pool.create request has no topology object")
    data_vdevs = topology.get("data")
    if (
        not isinstance(data_vdevs, list)
        or len(data_vdevs) != 1
        or not isinstance(data_vdevs[0], dict)
        or data_vdevs[0].get("type") != "STRIPE"
        or data_vdevs[0].get("disks") != [expected_partition]
    ):
        raise ValueError(
            f"pool.create must submit one Stripe data vdev using only {expected_partition!r}: "
            f"{data_vdevs!r}"
        )

    devices = []

    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "disks" and isinstance(child, list):
                    devices.extend(item for item in child if isinstance(item, str))
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(topology)
    if devices != [expected_partition]:
        raise ValueError(
            f"pool.create must mention the reserved partition exactly once and no other devices: {devices!r}"
        )
