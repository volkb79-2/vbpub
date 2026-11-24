"""The single table of managed tls-edge settings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable

from .config import (
    ConfigError,
    SCHEMA,
    TLS_MODES,
    normalize_bind_ip,
    normalize_cert_base,
    normalize_cgroup_parent,
    normalize_domain,
    normalize_email,
    normalize_port,
    normalize_provider,
    normalize_static_domains,
    normalize_volume_name,
)


def _always(_edge: Mapping[str, Any]) -> bool:
    return True


def _acme_mode(edge: Mapping[str, Any]) -> bool:
    return str(edge["tls"]["mode"]).startswith("acme-")


def _acme_dns(edge: Mapping[str, Any]) -> bool:
    return edge["tls"]["mode"] == "acme-dns"


def _calls_enabled(edge: Mapping[str, Any]) -> bool:
    return edge["calls"]["enabled"] is True


def _hostdir(edge: Mapping[str, Any]) -> bool:
    return edge["acme"]["store"] == "hostdir"


def _static_or_dev(edge: Mapping[str, Any]) -> bool:
    return edge["tls"]["mode"] in ("static", "dev")


@dataclass(frozen=True)
class Layout:
    """Filesystem and effective values available to field initializers."""

    root: Path
    stack_dir: Path
    overlay: Mapping[str, Any]
    edge: Mapping[str, Any]


@dataclass(frozen=True)
class Field:
    """One managed setting below the ``[tls_edge]`` table."""

    key: str
    kind: str
    prompt: str | None
    choices: tuple[str, ...] = ()
    when: Callable[[Mapping[str, Any]], bool] = _always
    when_text: str = ""
    check: Callable[[Any], Any] | None = None
    required: bool = False
    initial: Callable[[Layout], Any] | None = None


def _static_domains(value: Any) -> list[str]:
    return normalize_static_domains(value, "tls_edge.static.domains")


def _initial_acme_store(layout: Layout) -> str:
    if layout.edge["acme"]["volume_name"]:
        return "volume"
    if (
        (layout.root / "shared" / "acme").is_dir()
        and _overlay_value(layout.overlay, ("tls_edge", "acme", "store")) is None
    ):
        return "hostdir"
    return str(layout.edge["acme"]["store"])


def _initial_acme_path(layout: Layout) -> str:
    if (layout.root / "shared" / "acme").is_dir():
        return "../../shared/acme"
    return str(layout.edge["traefik"]["hostdir"]["acme"]["path"])


def _initial_static_domains(layout: Layout) -> list[str]:
    domains = layout.edge["static"]["domains"]
    if domains:
        return list(domains)
    return [str(layout.edge["domain"])]


def _overlay_value(config: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = config
    for part in path:
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


FIELDS: tuple[Field, ...] = (
    Field(
        "domain",
        "text",
        "Base DNS domain for consumer host names",
        check=lambda value: normalize_domain(value, "tls_edge.domain"),
        required=True,
    ),
    Field(
        "tls.mode",
        "choice",
        "Certificate mode",
        choices=TLS_MODES,
    ),
    Field(
        "acme.email",
        "text",
        "ACME account email (optional)",
        when=_acme_mode,
        when_text="tls_edge.tls.mode starts with acme-",
        check=normalize_email,
    ),
    Field(
        "acme.dns.provider",
        "text",
        "lego DNS provider name",
        when=_acme_dns,
        when_text='tls_edge.tls.mode = "acme-dns"',
        check=normalize_provider,
        required=True,
    ),
    Field(
        "acme.dns.wildcard_main",
        "text",
        "Wildcard base domain (optional)",
        when=_acme_dns,
        when_text='tls_edge.tls.mode = "acme-dns"',
        check=lambda value: normalize_domain(value, "tls_edge.acme.dns.wildcard_main") if value else "",
    ),
    Field(
        "ports.expose_http",
        "bool",
        "Open an HTTP listener for HTTPS redirects and HTTP-01?",
    ),
    Field(
        "http3.enabled",
        "bool",
        "Offer HTTP/3 over QUIC on the public HTTPS UDP port?",
    ),
    Field(
        "ingress_external",
        "bool",
        "Adopt an existing external ingress_public Docker network?",
    ),
    Field(
        "ports.http",
        "port",
        "Public HTTP port",
    ),
    Field(
        "ports.https",
        "port",
        "Public HTTPS TCP port",
    ),
    Field(
        "ports.bind_ip",
        "text",
        "Host IP to bind (empty uses the host's dual-stack default)",
        check=lambda value: normalize_bind_ip(value, "tls_edge.ports.bind_ip"),
    ),
    Field(
        "acme.volume_name",
        "text",
        "Existing ACME Docker volume to adopt (empty selects managed storage)",
        check=normalize_volume_name,
    ),
    Field(
        "runtime.cgroup_parent",
        "text",
        "Docker cgroup parent or slice (empty uses Docker's default)",
        check=normalize_cgroup_parent,
    ),
    Field(
        "acme.store",
        "choice",
        None,
        choices=("volume", "hostdir"),
        initial=_initial_acme_store,
    ),
    Field(
        "traefik.hostdir.acme.path",
        "text",
        None,
        when=_hostdir,
        when_text='tls_edge.acme.store = "hostdir"',
        initial=_initial_acme_path,
    ),
    Field(
        "static.cert_base",
        "text",
        "Certificate tree parent directory",
        when=_static_or_dev,
        when_text='tls_edge.tls.mode = "static" or "dev"',
        check=normalize_cert_base,
        required=True,
    ),
    Field(
        "static.domains",
        "list",
        "Certificate lineage directory names, comma separated",
        when=_static_or_dev,
        when_text='tls_edge.tls.mode = "static" or "dev"',
        check=_static_domains,
        initial=_initial_static_domains,
    ),
    Field(
        "static.source",
        "choice",
        "Certificate source",
        choices=("bind", "volume"),
        when=_static_or_dev,
        when_text='tls_edge.tls.mode = "static" or "dev"',
    ),
    Field(
        "calls.enabled",
        "bool",
        "Publish the optional Mattermost Calls TCP+UDP media pair?",
    ),
    Field(
        "calls.public_port",
        "port",
        "Public Calls media TCP+UDP port",
        when=_calls_enabled,
        when_text="tls_edge.calls.enabled = true",
    ),
)


FIELD_BY_KEY = {field.key: field for field in FIELDS}
MANAGED_KEYS = frozenset(FIELD_BY_KEY)

LEGACY_ANSWER_KEYS: Mapping[str, str] = MappingProxyType({
    "domain": "domain",
    "tls_mode": "tls.mode",
    "acme_email": "acme.email",
    "dns_provider": "acme.dns.provider",
    "dns_wildcard": "acme.dns.wildcard_main",
    "http_enabled": "ports.expose_http",
    "http_port": "ports.http",
    "https_port": "ports.https",
    "http3_enabled": "http3.enabled",
    "calls_enabled": "calls.enabled",
    "calls_port": "calls.public_port",
    "bind_ip": "ports.bind_ip",
    "ingress_external": "ingress_external",
    "acme_volume_name": "acme.volume_name",
    "cgroup_parent": "runtime.cgroup_parent",
    "static_cert_base": "static.cert_base",
    "static_domains": "static.domains",
    "static_source": "static.source",
})


def legacy_answers_message(key: str) -> str:
    target = LEGACY_ANSWER_KEYS[key]
    table, separator, leaf = target.rpartition(".")
    table_name = f"tls_edge.{table}" if separator else "tls_edge"
    return (
        f"answers key {key!r} is the 3.1 flat form; "
        f"write [{table_name}] {leaf}"
    )


def value_at(config: Mapping[str, Any], key: str) -> Any:
    """Read one dotted managed setting without inventing a fallback."""
    current: Any = config
    for part in key.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise ConfigError(f"tls_edge.{key} is missing from the effective configuration")
        current = current[part]
    return current
