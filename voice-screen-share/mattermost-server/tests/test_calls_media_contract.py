"""Behavioral contract for Mattermost's directly published Calls media."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from _render import (
    render_mattermost,
    render_tls_edge_config,
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
    calls_ports = {(8443, 8443, "tcp"), (8443, 8443, "udp")}
    assert not calls_ports & set(normalize_ports(service.get("ports")))
    assert not {
        key for key in service_environment(service) if key.startswith("MM_CALLS_")
    }


def test_calls_ports_do_not_collide_with_tls_edge_without_calls_preset(
    tmp_path: Path,
) -> None:
    edge = tls_edge_root()
    if edge is None:
        pytest.skip("tls-edge not in this checkout")

    edge_config = render_tls_edge_config()
    edge_config["http3"]["enabled"] = True
    edge_config["calls"]["enabled"] = False
    edge_config["ports"]["expose_http"] = True

    source = str(edge / "src")
    import sys

    if source not in sys.path:
        sys.path.insert(0, source)
    from tls_edge_cli.config import validate_config

    edge_ports: set[tuple[int, str]] = set()
    for plan in validate_config(
        edge_config, require_domain=False, require_certificates=False
    ):
        match = re.match(r"(\d+)/(tcp|udp)\b", plan)
        assert match, f"unparseable tls-edge port plan entry: {plan!r}"
        edge_ports.add((int(match.group(1)), match.group(2)))

    service = _mattermost_service(render_mattermost(tmp_path))
    mattermost_ports = {
        (host_port, protocol)
        for host_port, _container_port, protocol in normalize_ports(
            service.get("ports")
        )
    }
    assert not mattermost_ports & edge_ports
