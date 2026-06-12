"""CIU secrets sub-package.

Re-exports the public API from .directives so callers can use::

    from ciu.secrets import SecretSpec, parse_value, discover, find_misplaced

NOTE: stdlib ``secrets`` is still accessible as ``import secrets`` (absolute
import) because Python's import machinery resolves absolute imports against
sys.path before local packages.  Never write ``from . import secrets``
elsewhere — that would shadow the stdlib module.
"""

from __future__ import annotations

from ciu.secrets.directives import (
    DIRECTIVES,
    SECRET_NAME_RE,
    SecretSpec,
    discover,
    find_misplaced,
    parse_value,
)

__all__ = [
    "DIRECTIVES",
    "SECRET_NAME_RE",
    "SecretSpec",
    "discover",
    "find_misplaced",
    "parse_value",
]
