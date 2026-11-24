"""Behavioural oracles tying firewall ports to both production and trial renders."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
import yaml

from _render import render_ciu_case, render_ciu_configfiles
from conftest import defaults_config, merge, render_to
from tls_edge_cli.config import validate_config


Port = tuple[int, str]


def _port_plan_ports(plan: list[str]) -> list[Port]:
    ports: list[Port] = []
    for item in plan:
        match = re.match(r"^(\d+)/(tcp|udp)\b", item)
        assert match is not None, f"unrecognised port-plan entry: {item!r}"
        ports.append((int(match.group(1)), match.group(2)))
    return sorted(ports)


def _compose_ports(compose: Mapping[str, Any]) -> list[Port]:
    ports: list[Port] = []
    for item in compose["services"]["traefik"]["ports"]:
        if isinstance(item, Mapping):
            published = item["published"]
            protocol = item.get("protocol", "tcp")
        else:
            value = str(item)
            host_and_target, _, protocol = value.rpartition("/")
            if not host_and_target:
                host_and_target = value
                protocol = "tcp"
            parts = host_and_target.rsplit(":", 2)
            assert len(parts) >= 2, f"short port syntax has no host port: {value!r}"
            published = parts[-2]
        ports.append((int(published), str(protocol)))
    return sorted(ports)


def _mount_target(mount: object) -> str:
    if isinstance(mount, Mapping):
        target = mount.get("target")
        assert isinstance(target, str), f"mount has no target: {mount!r}"
        return target
    candidates = [
        part for part in str(mount).split(":") if part.startswith("/")
    ]
    assert candidates, f"short mount syntax has no container target: {mount!r}"
    return candidates[-1]


def _override(
    *,
    expose_http: bool,
    http3: bool,
    https_port: int,
) -> dict[str, Any]:
    return {
        "tls": {"mode": "acme-tls" if https_port == 443 else "acme-dns"},
        "ports": {"expose_http": expose_http, "https": https_port},
        "http3": {"enabled": http3},
    }


@pytest.mark.parametrize("expose_http", [False, True])
@pytest.mark.parametrize("http3_enabled", [False, True])
@pytest.mark.parametrize("https_port", [443, 8443])
def test_port_plan_matches_every_rendered_compose_port(
    tmp_path,
    expose_http: bool,
    http3_enabled: bool,
    https_port: int,
) -> None:
    override = _override(
        expose_http=expose_http,
        http3=http3_enabled,
        https_port=https_port,
    )

    standalone_cfg = merge(defaults_config(), override)
    standalone_out = render_to(tmp_path / "standalone", standalone_cfg)
    standalone_compose = yaml.safe_load(
        (standalone_out / "docker-compose.yml").read_text(encoding="utf-8")
    )
    expected = _port_plan_ports(validate_config(standalone_cfg, require_domain=False))
    assert _compose_ports(standalone_compose) == expected, (
        "standalone compose ports disagree with validate_config"
    )

    _, ciu_cfg, ciu_compose = render_ciu_case(tmp_path / "ciu", override)
    ciu_expected = _port_plan_ports(
        validate_config(ciu_cfg["tls_edge"], require_domain=False)
    )
    assert _compose_ports(ciu_compose) == ciu_expected, (
        "ciu compose ports disagree with validate_config"
    )
    assert expected == ciu_expected, (
        "standalone and ciu validate different effective port plans"
    )


def test_ciu_mounts_do_not_overlap_read_only_configfile_parents(tmp_path) -> None:
    _, stack_config, compose = render_ciu_case(
        tmp_path / "case", {"http3": {"enabled": True}}
    )
    traefik = stack_config["tls_edge"]["traefik"]
    configfiles = traefik["configfile"]
    assert configfiles, "rendered stack has no traefik configfile targets"
    traefik_volumes = compose["services"]["traefik"]["volumes"]
    assert traefik_volumes, "rendered traefik service has no volumes"
    violations: list[str] = []
    for mount in traefik_volumes:
        target = PurePosixPath(_mount_target(mount))
        for name, configfile in configfiles.items():
            parent = PurePosixPath(configfile["target"]).parent
            if target == parent or target.is_relative_to(parent):
                violations.append(
                    f"traefik mount {mount!r} targets {str(target)!r}, inside "
                    f"read-only configfile {name!r} parent {str(parent)!r} "
                    f"(file target {configfile['target']!r})"
                )
    assert not violations, "\n".join(violations)


def normalize_traefik_paths(
    document: Mapping[str, Any],
    *,
    standalone_prefixes: tuple[Path, ...],
    ciu_prefixes: tuple[Path, ...],
) -> dict[str, Any]:
    """Normalize renderer-owned host prefixes only to make drift visible.

    A host path in ``traefik.yml`` is a bug in both renderers. The oracle
    requires this helper to substitute nothing in current output before it
    compares the standalone and CIU documents.

    The helper recognizes exactly two renderer-owned prefixes:

    1. the standalone deployment's ``base_dir`` prefix; and
    2. CIU's physical hostdir/rendered-config staging prefix.

    A matching prefix on a string value becomes ``<renderer-path>`` while its
    remaining path stays intact. Keys, container paths, and all other values
    compare unchanged.
    """
    prefixes = sorted(
        {
            str(prefix).rstrip("/")
            for prefix in (*standalone_prefixes, *ciu_prefixes)
            if str(prefix)
        },
        key=len,
        reverse=True,
    )

    def normalize(value: object) -> object:
        if isinstance(value, Mapping):
            return {key: normalize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, str):
            for prefix in prefixes:
                if value == prefix:
                    return "<renderer-path>"
                if value.startswith(prefix + "/"):
                    return "<renderer-path>" + value[len(prefix):]
        return value

    return normalize(copy.deepcopy(document))  # type: ignore[return-value]


@pytest.mark.parametrize("http3_enabled", [False, True])
def test_ciu_and_standalone_render_the_same_traefik_config(
    tmp_path, http3_enabled: bool
) -> None:
    override = {"http3": {"enabled": http3_enabled}}
    ciu_stack, ciu_config, _ = render_ciu_case(tmp_path / "ciu", override)
    ciu_mount = next(
        mount
        for mount in render_ciu_configfiles(ciu_stack, ciu_config)
        if mount.target == "/etc/traefik/traefik.yml"
    )
    ciu_doc = yaml.safe_load(ciu_mount.rendered_path.read_text(encoding="utf-8"))

    standalone_cfg = merge(defaults_config(), override)
    standalone_dir = tmp_path / "standalone"
    render_to(standalone_dir, standalone_cfg)
    standalone_doc = yaml.safe_load(
        (standalone_dir / "traefik.yml").read_text(encoding="utf-8")
    )
    assert isinstance(ciu_doc, dict) and ciu_doc
    assert isinstance(standalone_doc, dict) and standalone_doc

    normalized_ciu = normalize_traefik_paths(
        ciu_doc,
        standalone_prefixes=(standalone_dir,),
        ciu_prefixes=(ciu_stack / ".ciu/rendered",),
    )
    normalized_standalone = normalize_traefik_paths(
        standalone_doc,
        standalone_prefixes=(standalone_dir,),
        ciu_prefixes=(ciu_stack / ".ciu/rendered",),
    )
    assert normalized_ciu == ciu_doc, (
        "CIU traefik.yml contains a host path that normalization would hide:\n"
        f"raw={yaml.safe_dump(ciu_doc, sort_keys=True)}\n"
        f"normalized={yaml.safe_dump(normalized_ciu, sort_keys=True)}"
    )
    assert normalized_standalone == standalone_doc, (
        "standalone traefik.yml contains a host path that normalization would hide:\n"
        f"raw={yaml.safe_dump(standalone_doc, sort_keys=True)}\n"
        f"normalized={yaml.safe_dump(normalized_standalone, sort_keys=True)}"
    )
    assert normalized_ciu == normalized_standalone, (
        "ciu and standalone parsed traefik.yml differ:\n"
        f"ciu={yaml.safe_dump(normalized_ciu, sort_keys=True)}\n"
        f"standalone={yaml.safe_dump(normalized_standalone, sort_keys=True)}"
    )
