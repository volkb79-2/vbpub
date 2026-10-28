"""API generation 28 (nightlies, e.g. 20261014). A STUB: inherits the v27 manifest, nothing here is measured yet."""
from __future__ import annotations

from .v27 import V27


class V28(V27):
    generation = "v28"
    verified = "copied"  # copied from v27 and unconfirmed; the v28 nightly patch set moved middleware code (matrix: v28 pending)
