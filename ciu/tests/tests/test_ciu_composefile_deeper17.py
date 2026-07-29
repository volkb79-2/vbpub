"""Malformed-service secret-consumption contract."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.composefile import validate_consumption  # noqa: E402


def test_non_mapping_service_cannot_falsely_consume_a_declared_secret() -> None:
    """Malformed Compose service data remains visible to S4.20 accounting.

    A service rendered as a scalar/list has no valid ``secrets`` declaration.
    CIU must not credit it with consuming the declared secret: returning the
    name as unconsumed keeps the caller's warning/actionable validation path
    intact instead of silently green-lighting a secret no container receives.
    """
    compose = """services:
  api: [not, a, service, mapping]
"""

    assert validate_consumption(compose, {"api_token"}) == ["api_token"]
