"""CIU hook: run tls-edge's site-override and cross-field validation."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_CHECKOUT_SRC = Path(__file__).resolve().parents[2] / "src"
if (_CHECKOUT_SRC / "tls_edge_cli").is_dir():
    sys.path.insert(0, str(_CHECKOUT_SRC))

from tls_edge_cli.config import (
    ConfigError,
    check_override,
    load_site,
    migrate,
    validate_config as validate_edge_config,
)


STACK_DIR = Path(__file__).resolve().parents[1]


def _finding(config: dict[str, Any]) -> str | None:
    try:
        defaults, overlay, effective = load_site(STACK_DIR)
        overlay = migrate(overlay)
        check_override(defaults, overlay)
        # CIU has already rewritten hostdirs in the merged config to absolute
        # strings. Validate the unsuffixed effective override so path, UID and
        # mutual-exclusion rules still have their declared table values.
        validate_edge_config(effective["tls_edge"], stack_dir=STACK_DIR)
    except ConfigError as exc:
        return str(exc)
    return None


def validate_config(config: dict[str, Any], ctx: Any) -> list[str]:
    finding = _finding(config)
    return [] if finding is None else [finding]


def run(config: dict[str, Any], ctx: Any) -> dict[str, Any]:
    finding = _finding(config)
    if finding is not None:
        raise ConfigError(finding)
    return {}
