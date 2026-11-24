"""Render the stable values shared by tls-edge consumers."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from . import config


CONTRACT_KEYS = (
    "TLS_EDGE_DOMAIN",
    "TLS_EDGE_NETWORK",
    "TLS_EDGE_HTTPS_ENTRYPOINT",
    "TLS_EDGE_HEADERS_MIDDLEWARE",
    "TLS_EDGE_HTTP3",
    "TLS_EDGE_PUBLIC_PORTS",
)

HTTPS_ENTRYPOINT = "websecure"
HEADERS_MIDDLEWARE = "secure-headers@file"

_ENV_VALUE = re.compile(r"[A-Za-z0-9._@,/-]*")


def _table(value: object, label: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise config.ConfigError(f"{label} must be a TOML table")
    return value


def consumer_contract(edge: Mapping) -> dict[str, str]:
    """Return the six values consumers may rely on from effective config."""
    if "domain" not in edge:
        raise config.ConfigError("domain is required")
    raw_domain = edge["domain"]
    if not isinstance(raw_domain, str):
        raise config.ConfigError("domain must be a string")
    domain = config.normalize_domain(raw_domain) if raw_domain else ""

    if "ingress_network" not in edge:
        raise config.ConfigError("ingress_network is required")
    network = edge["ingress_network"]
    if not isinstance(network, str):
        raise config.ConfigError("ingress_network must be a string")

    http3 = _table(edge.get("http3"), "tls_edge.http3")
    enabled = http3.get("enabled")
    if not isinstance(enabled, bool):
        raise config.ConfigError("http3.enabled must be true or false")

    public_ports = [
        (port, protocol)
        for port, protocol, _purpose in config.port_plan(edge)
    ]
    public_ports.sort(key=lambda item: (item[0], 0 if item[1] == "tcp" else 1))

    return {
        "TLS_EDGE_DOMAIN": domain,
        "TLS_EDGE_NETWORK": network,
        "TLS_EDGE_HTTPS_ENTRYPOINT": HTTPS_ENTRYPOINT,
        "TLS_EDGE_HEADERS_MIDDLEWARE": HEADERS_MIDDLEWARE,
        "TLS_EDGE_HTTP3": "true" if enabled else "false",
        "TLS_EDGE_PUBLIC_PORTS": ",".join(
            f"{port}/{protocol}" for port, protocol in public_ports
        ),
    }


def contract_for(
    stack_dir: Path,
    overlay: Mapping | None = None,
) -> dict[str, str]:
    """Derive the contract from tracked defaults plus a caller-supplied overlay."""
    defaults = config.render_defaults(Path(stack_dir) / "ciu.defaults.toml.j2")
    supplied = {} if overlay is None else overlay
    migrated = config.migrate(supplied)
    config.check_override(defaults, migrated)
    effective = config.deep_merge(defaults, migrated)
    edge = _table(effective.get("tls_edge"), "tls-edge defaults must contain a [tls_edge] table")
    config.validate_config(
        edge,
        require_domain=False,
        require_certificates=False,
        stack_dir=Path(stack_dir),
    )
    return consumer_contract(edge)


def _checked_values(contract: Mapping[str, str]) -> dict[str, str]:
    assert set(contract) == set(CONTRACT_KEYS), (
        "consumer contract must contain exactly CONTRACT_KEYS"
    )
    values: dict[str, str] = {}
    for key in CONTRACT_KEYS:
        value = contract[key]
        assert isinstance(value, str), f"{key} must be a string"
        assert _ENV_VALUE.fullmatch(value) is not None, (
            f"{key}={value!r} is not safe for an unquoted env file"
        )
        values[key] = value
    return values


def _owned_key(line: str) -> str | None:
    candidate = line.lstrip()
    if candidate.startswith("export") and candidate[6:7].isspace():
        candidate = candidate[6:].lstrip()
    key, separator, _value = candidate.partition("=")
    return key.rstrip() if separator else None


def render_env(contract: Mapping[str, str], existing: str = "") -> str:
    """Rewrite owned keys while retaining every other line and its position."""
    values = _checked_values(contract)
    result: list[str] = []
    written: set[str] = set()
    for line in existing.splitlines():
        key = _owned_key(line)
        if key is not None and key in values:
            if key not in written:
                result.append(f"{key}={values[key]}")
                written.add(key)
            continue
        result.append(line)
    for key in CONTRACT_KEYS:
        if key not in written:
            result.append(f"{key}={values[key]}")
    return "\n".join(result) + "\n"


def consumer_env_drift(
    contract: Mapping[str, str],
    existing: str,
) -> list[str]:
    """Name owned keys whose effective value or uniqueness is not canonical."""
    values = _checked_values(contract)
    if render_env(values, existing) == existing:
        return []

    actual: dict[str, str] = {}
    duplicates: set[str] = set()
    for line in existing.splitlines():
        key = _owned_key(line)
        if key is None or key not in values:
            continue
        _, _, value = line.partition("=")
        if key in actual:
            duplicates.add(key)
        actual[key] = value
    return [
        key
        for key in CONTRACT_KEYS
        if key not in actual or actual[key] != values[key] or key in duplicates
    ]


def consumer_env_drift_message(keys: Sequence[str]) -> str:
    """Return the shared actionable refusal for one or more drifted keys."""
    names = ", ".join(keys)
    verb = "differs" if len(keys) == 1 else "differ"
    return (
        f"consumer.env {names} {verb} from the site override; "
        "run tls-edge configure to re-sync"
    )
