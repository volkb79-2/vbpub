"""Version for pwmcp-shared.

Written at build time by setuptools-scm into ``_version.py`` (git-tag derived,
static once built). Falls back to installed package metadata, then a sentinel
for an un-built source checkout. No import-time clock — see docs/VERSIONING.md.
"""
from __future__ import annotations

try:
    from ._version import version as __version__  # generated at build (gitignored)
except Exception:  # pragma: no cover - source checkout without a build
    try:
        from importlib.metadata import version as _pkg_version

        __version__ = _pkg_version("pwmcp-shared")
    except Exception:
        __version__ = "0.0.0+unknown"
