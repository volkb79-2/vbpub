"""CIU hook: run tls-edge's site-override and cross-field validation."""

from __future__ import annotations

import copy
import os
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
from tls_edge_cli.contract import (
    consumer_contract,
    consumer_env_duplicates,
    consumer_env_drift,
    consumer_env_drift_message,
)


STACK_DIR = Path(__file__).resolve().parents[1]


def _finding(config: dict[str, Any]) -> str | None:
    try:
        defaults, overlay, effective = load_site(STACK_DIR)
        overlay = migrate(overlay)
        check_override(defaults, overlay)
        # Validate what ciu deploys: its merged config, including values from
        # other ciu layers and expansion. The one exception is the
        # [tls_edge.traefik.hostdir] table, which ciu has already rewritten to
        # absolute paths. The path, UID and mutual-exclusion rules need the
        # declared table, so restore it from the site's effective config.
        deployed = copy.deepcopy(config["tls_edge"])
        declared = effective["tls_edge"].get("traefik", {}).get("hostdir")
        if declared is not None:
            deployed.setdefault("traefik", {})["hostdir"] = copy.deepcopy(declared)
        validate_edge_config(deployed, stack_dir=STACK_DIR)
        env_path = STACK_DIR.parent / "consumer.env"
        if os.path.lexists(env_path):
            if env_path.is_symlink() or not env_path.is_file():
                raise ConfigError(
                    f"consumer environment must be a regular file: {env_path}"
                )
            try:
                existing = env_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise ConfigError(
                    f"cannot read consumer environment {env_path}: {exc}"
                ) from exc
            contract = consumer_contract(effective["tls_edge"])
            drift = consumer_env_drift(contract, existing)
            if drift:
                duplicates = consumer_env_duplicates(contract, existing)
                raise ConfigError(
                    consumer_env_drift_message(drift, duplicates=duplicates)
                )
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
