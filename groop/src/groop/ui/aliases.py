from __future__ import annotations

"""Centralized column-name alias layer for user/profile-facing metric keys.

Aliases allow operators to use clearer names like ``swap_dev`` in configured
profiles while the canonical frame/model/registry keys (``swap_disk``,
``rf_d_per_s``) remain unchanged.

Canonical keys always work.  Aliases are resolved at the profile/UI boundary,
not in model data or serialization.
"""

# Map of user-facing alias → canonical metric key.
_COLUMN_ALIASES: dict[str, str] = {
    "swap_dev": "swap_disk",
    "rf_dev_per_s": "rf_d_per_s",
    "rf_dev": "rf_d_per_s",
}

# Reverse lookup: canonical → set of known aliases (for doc / display).
_CANONICAL_TO_ALIASES: dict[str, tuple[str, ...]] = {}
for alias, canonical in _COLUMN_ALIASES.items():
    _CANONICAL_TO_ALIASES.setdefault(canonical, ())
    _CANONICAL_TO_ALIASES[canonical] = (*_CANONICAL_TO_ALIASES[canonical], alias)


def resolve_column(name: str) -> str:
    """Return the canonical metric key for *name*, resolving aliases.

    If *name* is not an alias it is returned unchanged (safe for canonical
    keys and unknown future column names).
    """
    return _COLUMN_ALIASES.get(name, name)


def is_alias(name: str) -> bool:
    """Return True if *name* is a registered alias (not a canonical key)."""
    return name in _COLUMN_ALIASES


def known_aliases(canonical: str) -> tuple[str, ...]:
    """Return the known aliases for a canonical key, or empty tuple."""
    return _CANONICAL_TO_ALIASES.get(canonical, ())


# Backend-aware display labels.
# These replace the plain labels in _LABELS when the canonical key should
# avoid overclaiming physical disk.
BACKEND_AWARE_LABELS: dict[str, str] = {
    "swap_disk": "SWAP_DEV",
    "rf_d_per_s": "RF_DEV/S",
}

# Diagnostic wording template for drill-down text.
# {rate} is replaced with the numeric value.
REFUALT_DRILL_WORDING = (
    "anonymous refaults that missed zswap; "
    "backend is disk, zram, or mixed according to host swap classification; "
    "cgroup backend attribution is unavailable"
)
