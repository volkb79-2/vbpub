"""The navigation tree, mirroring the TrueNAS web UI left menu.

Labels and order come from truenas/webui ``src/app/services/navigation/navigation.service.ts`` (master, read
2026-10-28): Dashboard, Storage, Datasets, Shares, Data Protection, Credentials (slide-out), Containers,
Virtual Machines, Apps, Reporting, System (slide-out). There is no "Virtualization" or top-level "Network"
entry in the v27 UI: Network lives under System, and Containers/VMs are separate top-level items.
Entries the web UI shows only conditionally (entitlement, feature or hardware dependent) carry ``conditional``:
Apps, KMIP and Enclosure. M1's Containers and Virtual Machines are always shown as separate top-level workflow
entries. Per-host detection for the remaining conditional entries is tracked in BACKLOG TNT-35. Deviations are
marked ``tnt_only`` and listed in docs/DESIGN.md section 4.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MenuItem:
    key: str  # stable id; also the web UI route where one exists
    label: str
    status: str = "planned"  # implemented | planned
    milestone: str = "M3"
    tnt_only: bool = False
    children: tuple["MenuItem", ...] = ()
    matrix: tuple[str, ...] = ()  # ACTION-MATRIX row ids this screen will carry
    conditional: bool = False  # the web UI shows this entry only when the host supports it (license, feature, hardware)


def _p(key: str, label: str, milestone: str = "M3", **kw) -> MenuItem:
    return MenuItem(key, label, milestone=milestone, **kw)


MENU: tuple[MenuItem, ...] = (
    _p("dashboard", "Dashboard", "M1"),
    MenuItem("storage", "Storage", "implemented", "M0", children=(
        _p("storage/pools", "Pools", "M1", matrix=("A-09", "B-03", "B-06")),
        MenuItem("storage/disks", "Disks", "implemented", "M0", matrix=("B-01",)),
        _p("storage/free-space", "Free space and partitions", "M2", tnt_only=True, matrix=("B-01", "B-02", "B-03")),
        _p("storage/import", "Import Pool", "M2", matrix=("B-06",)),
        _p("storage/boot-data", "Boot pool data", "M2", tnt_only=True, matrix=("B-01", "B-06")),
    )),
    MenuItem("datasets", "Datasets", "implemented", "M1", matrix=("A-01", "A-02", "A-03")),
    _p("sharing", "Shares", "M3", matrix=("A-07", "B-05")),
    MenuItem("data-protection", "Data Protection", "implemented", "M1", children=(
        MenuItem("snapshots", "Snapshots", "implemented", "M1"),
        _p("replication", "Replication"), _p("cloud-sync", "Cloud Sync"),
    )),
    MenuItem("credentials", "Credentials", "implemented", "M1", children=(
        MenuItem("users", "Local Users", "implemented", "M1"), _p("groups", "Groups"), _p("privileges", "Privileges"), _p("directory-services", "Directory Services"),
        _p("backup-credentials", "Backup Credentials"), _p("certificates", "Certificates"), _p("s3-access-keys", "S3 Access Keys"),
        _p("kmip", "KMIP", conditional=True))),
    MenuItem("containers", "Containers", "implemented", "M1", matrix=("A-04", "A-06", "B-04")),
    MenuItem("vm", "Virtual Machines", "implemented", "M1", matrix=("A-05", "A-06", "B-04")),
    _p("apps", "Apps", conditional=True),
    _p("reportsdashboard/cpu", "Reporting"),
    _p("system", "System", "M1", children=(
        _p("update", "Update"), _p("general", "General Settings"), _p("advanced", "Advanced Settings"), _p("network", "Network"),
        _p("boot", "Boot", "M1"), _p("services", "Services"), _p("alert-settings", "Alert Settings"), _p("audit", "Audit"),
        _p("viewenclosure", "Enclosure", conditional=True))),
)


def walk(items: tuple[MenuItem, ...] = MENU, path: tuple[str, ...] = ()):
    """Yield (path of labels, item) depth-first; the command palette searches this."""
    for item in items:
        here = path + (item.label,)
        yield here, item
        yield from walk(item.children, here)
