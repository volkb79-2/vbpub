"""Behavioral contract for Mattermost's directly published Calls media."""
from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from _render import (
    render_mattermost,
    service_environment,
    service_label_keys,
    tls_edge_root,
)

PortTuple = tuple[int, int, str]


def normalize_ports(entries: Any) -> list[PortTuple]:
    """Normalize Compose short and long port syntax to (host, container, protocol)."""
    if entries in (None, []):
        return []
    if not isinstance(entries, list):
        raise AssertionError(f"unsupported compose ports shape: {entries!r}")

    result: list[PortTuple] = []
    for entry in entries:
        if isinstance(entry, str):
            spec, _, protocol = entry.partition("/")
            protocol = protocol or "tcp"
            parts = spec.rsplit(":", 2)
            if len(parts) == 1:
                host_port, container_port = None, parts[0]
            else:
                host_port, container_port = parts[-2], parts[-1]
            result.append(
                (
                    int(host_port) if host_port is not None else -1,
                    int(container_port),
                    protocol,
                )
            )
            continue
        if isinstance(entry, dict):
            published = entry.get("published")
            result.append(
                (
                    int(published) if published not in (None, "") else -1,
                    int(entry["target"]),
                    str(entry.get("protocol", "tcp")),
                )
            )
            continue
        raise AssertionError(f"unsupported compose port entry: {entry!r}")
    return result


def _mattermost_service(document: dict[str, Any]) -> dict[str, Any]:
    return document["services"]["mattermost"]


def _labels(service: Mapping[str, Any]) -> dict[str, str]:
    raw = service.get("labels", {})
    if isinstance(raw, Mapping):
        return {str(key): str(value) for key, value in raw.items()}
    if isinstance(raw, list):
        result: dict[str, str] = {}
        for item in raw:
            key, separator, value = str(item).partition("=")
            if separator:
                result[key] = value
        return result
    raise AssertionError(f"unsupported compose labels shape: {raw!r}")


def _contract_ports(value: str) -> set[tuple[int, str]]:
    result: set[tuple[int, str]] = set()
    for item in value.split(","):
        port, protocol = item.split("/", 1)
        result.add((int(port), protocol))
    return result


def test_normalizer_supports_short_and_long_compose_syntax() -> None:
    expected = (8443, 8443, "udp")
    assert normalize_ports(["8443:8443/udp"]) == [expected]
    assert normalize_ports(
        [{"published": "8443", "target": 8443, "protocol": "udp"}]
    ) == [expected]


def test_defaults_publish_calls_media_directly(tmp_path: Path) -> None:
    service = _mattermost_service(render_mattermost(tmp_path))
    expected = {(8443, 8443, "tcp"), (8443, 8443, "udp")}
    assert expected <= set(normalize_ports(service.get("ports")))

    keys = service_label_keys(service)
    assert not {key for key in keys if key.startswith(("traefik.tcp.", "traefik.udp."))}

    env = service_environment(service)
    assert env["MM_CALLS_ICE_HOST_PORT_OVERRIDE"] == "8443"
    assert env["MM_CALLS_UDP_SERVER_PORT"] == "8443"
    assert env["MM_CALLS_TCP_SERVER_PORT"] == "8443"


@pytest.mark.parametrize(
    ("calls_enabled", "expose_public"),
    [(False, True), (True, False)],
)
def test_no_public_calls_media_without_both_flags(
    tmp_path: Path, calls_enabled: bool, expose_public: bool
) -> None:
    service = _mattermost_service(
        render_mattermost(
            tmp_path,
            calls_enabled=calls_enabled,
            expose_public=expose_public,
        )
    )
    # The service publishes nothing but Calls media, so any published port here
    # (including a container-only one that Docker maps to a random host port)
    # would be Calls media leaking out without public exposure.
    assert normalize_ports(service.get("ports")) == []
    assert not {
        key for key in service_environment(service) if key.startswith("MM_CALLS_")
    }


def test_mattermost_uses_the_tls_edge_consumer_contract(
    tmp_path: Path,
) -> None:
    edge = tls_edge_root()
    if edge is None:
        pytest.skip("tls-edge not in this checkout")

    source = str(edge / "src")
    if source not in sys.path:
        sys.path.insert(0, source)
    from tls_edge_cli.contract import contract_for

    contract = contract_for(
        edge / "ciu-stack",
        {"tls_edge": {"http3": {"enabled": True}}},
    )

    service = _mattermost_service(render_mattermost(tmp_path))
    labels = _labels(service)
    assert labels["traefik.docker.network"] == contract["TLS_EDGE_NETWORK"]
    assert labels["traefik.http.routers.nyxloom-mattermost.entrypoints"] == (
        contract["TLS_EDGE_HTTPS_ENTRYPOINT"]
    )
    mattermost_ports = {
        (host_port, protocol)
        for host_port, _container_port, protocol in normalize_ports(
            service.get("ports")
        )
    }
    assert mattermost_ports.isdisjoint(
        _contract_ports(contract["TLS_EDGE_PUBLIC_PORTS"])
    )
