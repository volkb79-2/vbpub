"""P90 Required contract 6 — command lines and identities follow P81 sensitivity.

Reuses P81's ``Sensitivity`` enum and marker dialect (``groop.daemon.redaction``)
rather than inventing a second classification scheme. ``cmdline`` can carry
secrets typed on a command line (tokens, connection strings) and is classified
``sensitive``; ``comm`` (the bare executable name, already visible today as
Docker/systemd unit identity elsewhere in groop) is ``operational``.
"""

from __future__ import annotations

from groop.daemon.api import Sensitivity
from groop.daemon.redaction import redaction_marker

_FIELD_SENSITIVITY: dict[str, Sensitivity] = {
    "comm": Sensitivity.OPERATIONAL,
    "cmdline": Sensitivity.SENSITIVE,
}

_RANK: dict[Sensitivity, int] = {
    Sensitivity.PUBLIC: 0,
    Sensitivity.OPERATIONAL: 1,
    Sensitivity.SENSITIVE: 2,
}


def classify_process_field(name: str) -> Sensitivity:
    """Classify a process identity field, failing closed to ``sensitive``."""
    return _FIELD_SENSITIVITY.get(name, Sensitivity.SENSITIVE)


def redact_process_row(row: dict[str, object], ceiling: Sensitivity | None) -> dict[str, object]:
    """Replace any identity field above ``ceiling`` with the P81 marker.

    ``ceiling is None`` means the principal may see everything, matching
    ``groop.daemon.redaction.redact_payload``'s convention.
    """
    if ceiling is None:
        return row
    for name in ("comm", "cmdline"):
        if name not in row or row[name] is None:
            continue
        sensitivity = classify_process_field(name)
        if _RANK[sensitivity] > _RANK[ceiling]:
            row[name] = redaction_marker(sensitivity)
    return row
