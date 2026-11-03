"""Safely prepare the bounded Path A root required by the TrueNAS UI tests."""

from __future__ import annotations


ROOT_DATASET = "boot-pool/user-data"
ROOT_MOUNTPOINT = "/mnt/boot-pool/user-data"
ROOT_QUOTA_BYTES = 2 * 1024**3
MINIMUM_FREE_BYTES = 2 * ROOT_QUOTA_BYTES


class PathARootProvisionError(RuntimeError):
    """The exact bounded Path A root could not be safely provisioned."""


def provision_path_a_ui_root(target) -> dict[str, str | int]:
    """Create only the empty, 2 GiB Path A root on a verified boot-pool.

    Caller must first run the full read-only UI inventory and host fingerprint
    checks. This helper repeats the critical pool and absence checks so it is
    also safe when called independently by a future registered lane.
    """
    health = target.run(["zpool", "list", "-H", "-o", "health", "boot-pool"], timeout=60)
    if health != "ONLINE":
        raise PathARootProvisionError(f"boot-pool must be ONLINE, got {health!r}")
    free_text = target.run(["zpool", "list", "-H", "-p", "-o", "free", "boot-pool"], timeout=60)
    if not free_text.isdecimal() or int(free_text) < MINIMUM_FREE_BYTES:
        raise PathARootProvisionError(
            f"boot-pool must have at least {MINIMUM_FREE_BYTES} free bytes; got {free_text!r}"
        )
    root_state = target.shell(
        "if zfs list -H -o name boot-pool/user-data >/dev/null 2>&1; "
        "then printf present; else printf absent; fi",
        timeout=30,
    )
    if root_state != "absent":
        raise PathARootProvisionError(
            f"refusing to provision existing {ROOT_DATASET}: state={root_state!r}"
        )
    mountpoint_state = target.shell(
        "if test -e /mnt/boot-pool/user-data || test -L /mnt/boot-pool/user-data; "
        "then printf present; else printf absent; fi",
        timeout=30,
    )
    if mountpoint_state != "absent":
        raise PathARootProvisionError(
            f"refusing to use existing {ROOT_MOUNTPOINT}: state={mountpoint_state!r}"
        )

    target.run(
        [
            "zfs", "create",
            "-o", f"quota={ROOT_QUOTA_BYTES}",
            "-o", f"mountpoint={ROOT_MOUNTPOINT}",
            "-o", "canmount=on",
            ROOT_DATASET,
        ],
        timeout=120,
    )
    properties = {
        "quota": target.run(["zfs", "get", "-H", "-p", "-o", "value", "quota", ROOT_DATASET]),
        "mountpoint": target.run(["zfs", "get", "-H", "-o", "value", "mountpoint", ROOT_DATASET]),
        "canmount": target.run(["zfs", "get", "-H", "-o", "value", "canmount", ROOT_DATASET]),
    }
    expected = {
        "quota": str(ROOT_QUOTA_BYTES),
        "mountpoint": ROOT_MOUNTPOINT,
        "canmount": "on",
    }
    if properties != expected:
        raise PathARootProvisionError(
            f"provisioned {ROOT_DATASET} has unexpected properties: {properties!r}"
        )
    source = target.run(
        ["findmnt", "-n", "-o", "SOURCE", "--target", ROOT_MOUNTPOINT], timeout=45,
    )
    if source != ROOT_DATASET:
        raise PathARootProvisionError(
            f"{ROOT_MOUNTPOINT} is mounted from {source!r}, expected {ROOT_DATASET!r}"
        )
    descendants = target.run(
        ["zfs", "list", "-H", "-t", "filesystem,volume", "-o", "name", "-r", ROOT_DATASET],
        timeout=60,
    ).splitlines()
    if descendants != [ROOT_DATASET]:
        raise PathARootProvisionError(
            f"new {ROOT_DATASET} is not empty: dataset tree is {descendants!r}"
        )
    contents = target.run(
        ["find", ROOT_MOUNTPOINT, "-mindepth", "1", "-maxdepth", "1", "-printf", "%f\\n"],
        timeout=45,
    ).splitlines()
    if contents:
        raise PathARootProvisionError(
            f"new {ROOT_MOUNTPOINT} is not empty: {contents!r}"
        )
    return {
        "dataset": ROOT_DATASET,
        "quota_bytes": ROOT_QUOTA_BYTES,
        "mountpoint": ROOT_MOUNTPOINT,
        "canmount": "on",
        "mount_source": source,
    }
