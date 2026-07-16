"""P90 process identity (D-013 Contract 1).

A ``ProcessKey`` is host boot ID + PID + ``/proc/PID/stat`` start time. Keying
every baseline/history/enrichment map by the full tuple (never by bare PID)
makes PID reuse structurally impossible to mis-join: a PID recycled by the
kernel gets a different ``start_ticks`` and therefore a different key, so its
history starts fresh rather than extending the previous occupant's.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from topos.model import EntityKey

_UNKNOWN_BOOT = "unknown-boot"


def read_boot_id(proc_root: Path) -> str:
    """Read the kernel-generated per-boot UUID, or a stand-in when unreadable.

    The boot ID is defense-in-depth alongside ``start_ticks`` (which already
    changes on PID reuse within a boot): it also keeps a key from a previous
    boot's stale in-memory state from ever matching a key on this boot.
    """
    path = proc_root / "sys" / "kernel" / "random" / "boot_id"
    try:
        return path.read_text().strip() or _UNKNOWN_BOOT
    except OSError:
        return _UNKNOWN_BOOT


@dataclass(frozen=True, order=True)
class ProcessKey:
    """Stable process incarnation identity: boot ID + PID + start time."""

    pid: int
    start_ticks: int
    boot_id: str = _UNKNOWN_BOOT

    def entity_key(self) -> EntityKey:
        return f"proc:{self.pid}:{self.start_ticks}:{self.boot_id[:8]}"
