"""CIU package.

The version is not exposed as a module attribute: ``cli_utils.ciu_identity()`` is the one
resolver (installed distribution metadata, cross-checked against the build-time
``_version.py`` when a source tree has one), and it never falls back to a literal.
"""

from __future__ import annotations
