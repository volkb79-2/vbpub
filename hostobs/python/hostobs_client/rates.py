"""Counter rates over the actual monotonic interval (WIRE.md 7.5, plan v4 section C.3).

Standard library only. ``rate`` matches ``model::derive_rate``: the integer delta is computed
first and the division comes last; a counter is never converted to ``float`` before that.
"""

from __future__ import annotations

from typing import NamedTuple

from .wire import QUALITY_NAMES

_U64_MAX = 2**64 - 1
_UINT32_MAX = 2**32 - 1


class Reading(NamedTuple):
    """One counter reading: ``quality`` is a name or code, ``value_type`` ``u64`` for a counter."""

    mono_ns: int
    value: int | None
    quality: str | int = "ok"
    value_type: str = "u64"


def _reading(sample, which: str) -> tuple[Reading, str]:
    if isinstance(sample, Reading):
        reading = sample
    elif isinstance(sample, dict):
        reading = Reading(sample["mono_ns"], sample.get("value"), sample["quality"], sample.get("type", "u64"))
    else:
        reading = Reading(*sample)
    mono = reading.mono_ns
    if not isinstance(mono, int) or isinstance(mono, bool) or not 0 <= mono <= _U64_MAX:
        raise ValueError(f"{which} mono_ns {mono!r} is not a u64")
    quality = reading.quality
    if isinstance(quality, int):
        if not 0 <= quality < len(QUALITY_NAMES):
            raise ValueError(f"unknown {which} quality code {quality}")
        quality = QUALITY_NAMES[quality]
    elif quality not in QUALITY_NAMES:
        raise ValueError(f"unknown {which} quality {quality!r}")
    return reading, quality


def _no_rate(reason: str, reading: str | None, quality: str) -> dict:
    return {"ok": False, "reason": reason, "reading": reading, "quality": quality}


def rate(prev, cur, nominal_interval_ns: int) -> dict:
    """The rate between two readings.

    ``prev`` and ``cur`` are :class:`Reading` tuples or mappings with the ``rates.json`` keys
    (``quality``, ``mono_ns``, ``type``, ``value``). A rate is
    ``{"ok": True, "delta", "denominator", "per_second", "missed"}`` (``delta`` an exact ``int``,
    ``denominator`` the elapsed nanoseconds, ``missed`` the nominal samples skipped); no rate is
    ``{"ok": False, "reason", "reading", "quality"}`` with ``reason`` one of ``missing``,
    ``wrong-value-type``, ``negative-interval``, ``zero-interval``, ``counter-reset`` and
    ``quality`` the quality name to store (``model::NoRate::quality``).
    """
    previous, previous_quality = _reading(prev, "previous")
    current, current_quality = _reading(cur, "current")
    for which, reading, quality in (("previous", previous, previous_quality), ("current", current, current_quality)):
        if quality != "ok":
            return _no_rate("missing", which, quality)
        if reading.value_type != "u64":
            return _no_rate("wrong-value-type", which, "unsupported")
        value = reading.value
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= _U64_MAX:
            raise ValueError(f"{which} value {value!r} is not a u64")
    elapsed = current.mono_ns - previous.mono_ns
    if elapsed < 0:
        return _no_rate("negative-interval", None, "stale")
    if elapsed == 0:
        return _no_rate("zero-interval", None, "not-sampled")
    if current.value < previous.value:
        return _no_rate("counter-reset", "current", "reset")
    delta = current.value - previous.value
    missed = 0
    if nominal_interval_ns > 0:
        missed = min(max(elapsed // nominal_interval_ns - 1, 0), _UINT32_MAX)
    return {
        "ok": True,
        "delta": delta,
        "denominator": elapsed,
        "per_second": delta * 1_000_000_000 / elapsed,
        "missed": missed,
    }
