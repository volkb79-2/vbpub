"""Working-set ladder: records, cliff, classes, QD90, fresh vs rewrite (IOB-CAL-02..10)."""
from __future__ import annotations

from .host import TAIL_BYTES


def fresh_label(kind: str) -> str:
    """The write measurement on a fresh extent is ``fresh`` on a device, ``fresh_fs_alloc`` in a file (IOB-TGT-08)."""
    return "fresh" if kind == "device" else "fresh_fs_alloc"
