"""The navigation tree, mirroring the TrueNAS web UI left menu.

Labels and order come from truenas/webui ``src/app/services/navigation/navigation.service.ts`` (master, read
2026-10-28): Dashboard, Storage, Datasets, Shares, Data Protection, Credentials (slide-out), Containers,
Virtual Machines, Apps, Reporting, System (slide-out). There is no "Virtualization" or top-level "Network"
entry in the v27 UI: Network lives under System, and Containers/VMs are separate top-level items.
TrueNAS puts “My API Keys” in the top-right account settings menu; tnt nests it under Credentials because its tree has
no account menu.
Entries the web UI shows only conditionally (entitlement, feature or hardware dependent) carry ``conditional``:
Apps, Containers, Virtual Machines, KMIP and Enclosure. tnt keeps the separately implemented Containers and Virtual
Machines routes visible and marks the TrueNAS entitlement condition in text. Per-host detection for conditional
entries is tracked in BACKLOG TNT-35. Deviations are marked ``tnt_only`` and listed in docs/DESIGN.md section 4.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MenuItem:
    key: str  # stable id; also the web UI route where one exists
    label: str
    status: str = "planned"  # implemented | planned
    milestone: str = "Later"
    tnt_only: bool = False
    children: tuple["MenuItem", ...] = ()
    matrix: tuple[str, ...] = ()  # ACTION-MATRIX row ids this screen will carry
    conditional: bool = False  # the web UI shows this entry only when the host supports it (license, feature, hardware)


def _p(key: str, label: str, milestone: str = "Later", **kw) -> MenuItem:
    return MenuItem(key, label, milestone=milestone, **kw)


MENU: tuple[MenuItem, ...] = (
    MenuItem("dashboard", "Dashboard", "implemented", "It2"),
    MenuItem("storage", "Storage", "implemented", "M0", children=(
        MenuItem("storage/health", "Health", "implemented", "It2"),
        _p("storage/pools", "Pools", "Later", matrix=("A-09", "B-03", "B-06")),
        MenuItem("storage/disks", "Disks", "implemented", "M0", matrix=("B-01",)),
        _p("storage/free-space", "Free space and partitions", "Later", tnt_only=True, matrix=("B-01", "B-02", "B-03")),
        _p("storage/import", "Import Pool", "Later", matrix=("B-06",)),
        _p("storage/boot-data", "Boot pool data", "Later", tnt_only=True, matrix=("B-01", "B-06")),
    )),
    MenuItem("datasets", "Datasets", "implemented", "M1", matrix=("A-01", "A-02", "A-03")),
    MenuItem("sharing", "Shares", "implemented", "M2", matrix=("A-07", "B-05")),
    MenuItem("data-protection", "Data Protection", "implemented", "M1", children=(
        MenuItem("snapshot-tasks", "Periodic Snapshot Tasks", "implemented", "M2"),
        MenuItem("snapshots", "Snapshots", "implemented", "M1"),
        MenuItem("replication", "Replication Tasks", "implemented", "M2"),
        MenuItem("cloud-sync", "Cloud Sync Tasks", "implemented", "M2"),
    )),
    MenuItem("credentials", "Credentials", "implemented", "M1", children=(
        MenuItem("users", "Local Users", "implemented", "M1"),
        MenuItem("api-keys", "My API Keys", "implemented", "M2", tnt_only=True),
        _p("groups", "Groups"), _p("privileges", "Privileges"), _p("directory-services", "Directory Services"),
        _p("backup-credentials", "Backup Credentials"), _p("certificates", "Certificates"), _p("s3-access-keys", "S3 Access Keys"),
        _p("kmip", "KMIP", conditional=True))),
    MenuItem("containers", "Containers", "implemented", "M1", conditional=True, matrix=("A-04", "A-06", "B-04")),
    MenuItem("vm", "Virtual Machines", "implemented", "M1", conditional=True, matrix=("A-05", "A-06", "B-04")),
    MenuItem("apps", "Apps", "implemented", "M2", conditional=True),
    _p("reportsdashboard/cpu", "Reporting"),
    MenuItem("system", "System", "implemented", "M2", children=(
        MenuItem("update", "Update", "implemented", "M2"),
        MenuItem("reboot", "Reboot", "implemented", "M2", tnt_only=True),
        MenuItem("shutdown", "Shutdown", "implemented", "M2", tnt_only=True),
        MenuItem("general", "General Settings", "implemented", "M2"), _p("advanced", "Advanced Settings"),
        MenuItem("network", "Network", "implemented", "M2"),
        MenuItem("jobs", "Jobs", "implemented", "It2"),
        MenuItem("alerts", "Alerts", "implemented", "It2"),
        _p("boot", "Boot", "Later"), MenuItem("services", "Services", "implemented", "It2"),
        _p("alert-settings", "Alert Settings"),
        MenuItem("audit", "Audit", "implemented", "M2"),
        _p("viewenclosure", "Enclosure", conditional=True))),
)


def walk(items: tuple[MenuItem, ...] = MENU, path: tuple[str, ...] = ()):
    """Yield (path of labels, item) depth-first; the command palette searches this."""
    for item in items:
        here = path + (item.label,)
        yield here, item
        yield from walk(item.children, here)
