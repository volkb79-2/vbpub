"""Behavioural tests for the rendered consumer contract API."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from tls_edge_cli import config


ROOT = Path(__file__).resolve().parents[1]


def _contract():
    from tls_edge_cli import contract

    return contract


def _edge(overlay: dict | None = None) -> dict:
    defaults = config.render_defaults(ROOT / "ciu-stack" / "ciu.defaults.toml.j2")
    edge = config.deep_merge(defaults, overlay or {})["tls_edge"]
    config.validate_config(
        edge,
        require_domain=False,
        require_certificates=False,
    )
    return edge


def _stack_copy(tmp_path: Path) -> Path:
    stack = tmp_path / "ciu-stack"
    stack.mkdir()
    shutil.copy2(ROOT / "ciu-stack" / "ciu.defaults.toml.j2", stack)
    return stack


def test_contract_has_exact_keys_values_and_order() -> None:
    contract = _contract()
    edge = _edge({
        "tls_edge": {
            "domain": "Example.TEST.",
            "http3": {"enabled": True},
        }
    })
    result = contract.consumer_contract(edge)
    assert tuple(result) == contract.CONTRACT_KEYS
    assert result == {
        "TLS_EDGE_DOMAIN": "example.test",
        "TLS_EDGE_NETWORK": "ingress_public",
        "TLS_EDGE_HTTPS_ENTRYPOINT": "websecure",
        "TLS_EDGE_HEADERS_MIDDLEWARE": "secure-headers@file",
        "TLS_EDGE_HTTP3": "true",
        "TLS_EDGE_PUBLIC_PORTS": "80/tcp,443/tcp,443/udp",
    }


def test_port_plan_is_the_one_source_for_the_text_plan() -> None:
    edge = _edge({
        "tls_edge": {
            "http3": {"enabled": True},
        }
    })
    expected = [
        (80, "tcp", "HTTP redirect and/or HTTP-01"),
        (443, "tcp", "HTTPS, HTTP/1.1 and HTTP/2"),
        (443, "udp", "HTTP/3 over QUIC"),
    ]
    assert config.port_plan(edge) == expected
    assert config.validate_config(
        edge,
        require_domain=False,
        require_certificates=False,
    ) == [
        f"{port}/{protocol} ({purpose})"
        for port, protocol, purpose in expected
    ]


def test_public_ports_sort_by_number_then_tcp_before_udp() -> None:
    contract = _contract()
    edge = _edge({
        "tls_edge": {
            "http3": {"enabled": True},
        }
    })
    result = contract.consumer_contract(edge)
    assert result["TLS_EDGE_PUBLIC_PORTS"].split(",") == [
        "80/tcp",
        "443/tcp",
        "443/udp",
    ]


def test_contract_for_renders_defaults_without_reading_the_site_override(
    tmp_path: Path,
) -> None:
    contract = _contract()
    stack = _stack_copy(tmp_path)
    (stack / "ciu.toml.j2").write_text(
        '[tls_edge]\ndomain = "ignored.example.test"\n',
        encoding="utf-8",
    )
    result = contract.contract_for(
        stack,
        {"tls_edge": {"http3": {"enabled": True}}},
    )
    assert result["TLS_EDGE_DOMAIN"] == ""
    assert result["TLS_EDGE_HTTP3"] == "true"
    assert result["TLS_EDGE_PUBLIC_PORTS"] == "80/tcp,443/tcp,443/udp"


def test_contract_for_checks_the_overlay_and_uses_relaxed_validation(
    tmp_path: Path,
) -> None:
    contract = _contract()
    stack = _stack_copy(tmp_path)
    with pytest.raises(config.ConfigError, match="unknown override key"):
        contract.contract_for(stack, {"tls_edge": {"unknown": {"value": 1}}})

    result = contract.contract_for(
        stack,
        {"tls_edge": {"tls": {"mode": "static"}}},
    )
    assert result["TLS_EDGE_DOMAIN"] == ""


def test_contract_for_accepts_no_overlay_and_refuses_malformed_defaults(
    tmp_path: Path,
) -> None:
    contract = _contract()
    stack = _stack_copy(tmp_path)
    assert contract.contract_for(stack)["TLS_EDGE_DOMAIN"] == ""
    (stack / "ciu.defaults.toml.j2").write_text(
        "tls_edge = 3\n",
        encoding="utf-8",
    )
    with pytest.raises(
        config.ConfigError,
        match=r"tls-edge defaults must contain a \[tls_edge\] table",
    ):
        contract.contract_for(stack)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda edge: edge.pop("domain"), "domain is required"),
        (lambda edge: edge.update(domain=3), "domain must be a string"),
        (lambda edge: edge.pop("ingress_network"), "ingress_network is required"),
        (lambda edge: edge.update(ingress_network=3), "ingress_network must be a string"),
        (lambda edge: edge.pop("http3"), "tls_edge.http3 must be a TOML table"),
        (
            lambda edge: edge["http3"].update(enabled="yes"),
            "http3.enabled must be true or false",
        ),
    ],
)
def test_consumer_contract_refuses_non_contract_inputs(
    mutate: Callable[[dict], object],
    message: str,
) -> None:
    contract = _contract()
    edge = _edge()
    mutate(edge)
    with pytest.raises(config.ConfigError, match=message):
        contract.consumer_contract(edge)


def test_render_env_creates_the_exact_contract_order() -> None:
    contract = _contract()
    values = {
        "TLS_EDGE_DOMAIN": "example.test",
        "TLS_EDGE_NETWORK": "ingress_public",
        "TLS_EDGE_HTTPS_ENTRYPOINT": "websecure",
        "TLS_EDGE_HEADERS_MIDDLEWARE": "secure-headers@file",
        "TLS_EDGE_HTTP3": "false",
        "TLS_EDGE_PUBLIC_PORTS": "80/tcp,443/tcp",
    }
    assert contract.render_env(values) == (
        "TLS_EDGE_DOMAIN=example.test\n"
        "TLS_EDGE_NETWORK=ingress_public\n"
        "TLS_EDGE_HTTPS_ENTRYPOINT=websecure\n"
        "TLS_EDGE_HEADERS_MIDDLEWARE=secure-headers@file\n"
        "TLS_EDGE_HTTP3=false\n"
        "TLS_EDGE_PUBLIC_PORTS=80/tcp,443/tcp\n"
    )


@pytest.mark.parametrize(
    "values",
    [
        {
            "TLS_EDGE_DOMAIN": "bad domain",
            "TLS_EDGE_NETWORK": "ingress_public",
            "TLS_EDGE_HTTPS_ENTRYPOINT": "websecure",
            "TLS_EDGE_HEADERS_MIDDLEWARE": "secure-headers@file",
            "TLS_EDGE_HTTP3": "false",
            "TLS_EDGE_PUBLIC_PORTS": "80/tcp,443/tcp",
        },
        {
            "TLS_EDGE_DOMAIN": "example.test",
            "TLS_EDGE_NETWORK": "ingress_public",
            "TLS_EDGE_HTTPS_ENTRYPOINT": "websecure",
            "TLS_EDGE_HEADERS_MIDDLEWARE": "secure-headers@file",
            "TLS_EDGE_HTTP3": "false",
        },
    ],
    ids=["invalid-value", "missing-contract-key"],
)
def test_render_env_refuses_unquotable_or_incomplete_contract(
    values: dict[str, str],
) -> None:
    contract = _contract()
    with pytest.raises(AssertionError):
        contract.render_env(values)


def test_consumer_env_drift_names_duplicates_and_multiple_keys() -> None:
    contract = _contract()
    values = {
        "TLS_EDGE_DOMAIN": "edge.example.test",
        "TLS_EDGE_NETWORK": "ingress_public",
        "TLS_EDGE_HTTPS_ENTRYPOINT": "websecure",
        "TLS_EDGE_HEADERS_MIDDLEWARE": "secure-headers@file",
        "TLS_EDGE_HTTP3": "false",
        "TLS_EDGE_PUBLIC_PORTS": "80/tcp,443/tcp",
    }
    existing = (
        "TLS_EDGE_DOMAIN=edge.example.test\n"
        "TLS_EDGE_DOMAIN=edge.example.test\n"
        "TLS_EDGE_NETWORK=other\n"
    )
    assert contract.consumer_env_drift(values, existing) == [
        "TLS_EDGE_DOMAIN",
        "TLS_EDGE_NETWORK",
        "TLS_EDGE_HTTPS_ENTRYPOINT",
        "TLS_EDGE_HEADERS_MIDDLEWARE",
        "TLS_EDGE_HTTP3",
        "TLS_EDGE_PUBLIC_PORTS",
    ]
    assert contract.consumer_env_drift_message(
        ["TLS_EDGE_DOMAIN", "TLS_EDGE_NETWORK"]
    ) == (
        "consumer.env TLS_EDGE_DOMAIN, TLS_EDGE_NETWORK differ from the site "
        "override; run tls-edge configure to re-sync"
    )


def test_consumer_env_drift_ignores_unowned_operator_lines() -> None:
    contract = _contract()
    values = contract.consumer_contract(_edge())
    existing = "OPERATOR_FLAG=keep\n" + contract.render_env(values).replace(
        f"TLS_EDGE_DOMAIN={values['TLS_EDGE_DOMAIN']}",
        "TLS_EDGE_DOMAIN=drift.example.test",
    )
    assert contract.consumer_env_drift(values, existing) == ["TLS_EDGE_DOMAIN"]
