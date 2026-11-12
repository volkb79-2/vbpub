"""Resolve `extract --since` marker, ISO timestamp, and relative-duration forms."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

_RELATIVE_RE = re.compile(r"^([1-9][0-9]*)(s|m|h|d)$", re.IGNORECASE)
_DURATION_LIKE_RE = re.compile(r"^[0-9]+[A-Za-z]+$")


@dataclass(frozen=True)
class SinceSpec:
    cutoff: str
    inclusive: bool
    description: str


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_since(value: str, *, now: datetime | None = None) -> SinceSpec | None:
    """Resolve a timestamp/duration value, or return None for a marker.

    Relative durations use one invocation-time instant and include events at
    that instant. ISO-8601 values must carry a timezone and keep the existing
    strict-after cursor semantics.
    """
    raw = value.strip()
    if not raw:
        raise ValueError("--since value must not be empty")
    relative = _RELATIVE_RE.fullmatch(raw)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2).lower()
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        unit_seconds = {"s": 1, "m": 60, "h": 3600, "d": 86_400}[unit]
        try:
            cutoff = current.astimezone(UTC) - timedelta(seconds=amount * unit_seconds)
        except OverflowError as exc:
            raise ValueError("--since relative duration is out of range") from exc
        return SinceSpec(
            _utc_text(cutoff),
            True,
            f"relative {raw} -> {_utc_text(cutoff)} (inclusive)",
        )

    iso_like = (
        "T" in raw
        or (len(raw) >= 5 and raw[:4].isdigit() and raw[4] == "-")
    )
    if iso_like:
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(
                "--since accepts an adapter marker, ISO-8601 timestamp with an offset, "
                "or a relative duration such as 5h or 90m"
            ) from exc
        if parsed.tzinfo is None:
            raise ValueError("--since ISO-8601 timestamps must include a timezone offset")
        return SinceSpec(
            _utc_text(parsed),
            False,
            f"ISO {raw} -> {_utc_text(parsed)} (strictly after)",
        )

    if _DURATION_LIKE_RE.fullmatch(raw):
        raise ValueError(
            "--since relative durations use a positive number followed by s, m, h, or d"
        )
    return None
