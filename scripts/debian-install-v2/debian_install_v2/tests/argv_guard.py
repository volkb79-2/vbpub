"""Shared guard: test doubles must send every argv through the PRODUCTION allowlist.

Live finding 2026-11-05 (v1001 reinstall): test doubles that replaced `actions.run` never executed
`HostActions._validate`, so `sfdisk -N` / `--no-tell-kernel` were missing from the allowlist and stage two
died on a real host. Every fake/mocked run path in the installer tests calls `validate_argv` first, so
allowlist drift fails the unit tests.
"""

from __future__ import annotations

from debian_install_v2.actions import HostActions


def validate_argv(argv) -> list[str]:
    """Run the production validator on `argv`; returns the list so callers can chain it."""
    argv = list(argv)
    HostActions._validate(argv)
    return argv
