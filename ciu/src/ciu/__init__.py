"""CIU package."""

from __future__ import annotations

# Version is written at build time by setuptools-scm into ``_version.py`` (git-tag
# derived, static once built — no import-time clock drift). Fall back to installed
# package metadata, then to a sentinel for an un-built source checkout.
try:
	from ._version import version as __version__  # generated at build (gitignored)
except Exception:  # pragma: no cover - source checkout without a build
	try:
		from importlib.metadata import version as _pkg_version

		__version__ = _pkg_version("ciu")
	except Exception:
		__version__ = "0.0.0+unknown"
