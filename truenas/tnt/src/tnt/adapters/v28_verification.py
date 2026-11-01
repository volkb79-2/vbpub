"""Restricted read-only surface for verifying a v27-to-v28 system update.

The v28 method contract is not generally supported by tnt yet. This adapter
only exposes the version, readiness, boot ID, active boot environment, and the
two read methods needed to verify update-survival fixtures.
"""
from __future__ import annotations

from .base import Backend, MethodSpec


class V28Verification(Backend):
    generation = "v28-verification-only"
    verified = "reference"
    read_only = True
    destructive = frozenset()
    manifest = {
        "system-verification": {
            "core.get_methods": MethodSpec("read"),
            "system.version": MethodSpec("read"),
            "system.state": MethodSpec("read"),
            "system.boot_id": MethodSpec("read"),
        },
        "boot-environment-verification": {
            "boot.environment.query": MethodSpec("read"),
        },
        "update-survival-readback": {
            "pool.dataset.query": MethodSpec("read"),
            "pool.snapshot.query": MethodSpec("read"),
        },
    }
