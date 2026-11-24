from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest
import tomlkit

from _layout import instance_stack_dir
from tls_edge_cli import config


def valid_config() -> dict:
    return {
        "schema": 2,
        "domain": "edge.example.com",
        "tls": {"mode": "acme-tls"},
        "ports": {"expose_http": True, "http": 80, "https": 443, "bind_ip": ""},
        "http3": {"enabled": False},
        "acme": {
            "email": "",
            "volume_name": "",
            "store": "volume",
            "dns": {"provider": "cloudflare", "wildcard_main": ""},
        },
        "traefik": {
            "hostdir": {
                "acme": {"path": "", "uid": 0, "gid": 0, "mode": "0700"}
            }
        },
        "static": {"cert_base": "/etc/letsencrypt", "domains": [], "source": "bind"},
        "ingress_external": False,
        "runtime": {"cgroup_parent": ""},
    }


def make_root(path: Path, template: str | None = None) -> Path:
    root = instance_stack_dir(path)
    defaults = root / "ciu-stack" / "ciu.defaults.toml.j2"
    defaults.parent.mkdir(parents=True)
    defaults.write_text(template or """\
[tls_edge]
schema = 2
domain = "edge.example.com"
ingress_external = false
[tls_edge.tls]
mode = "acme-tls"
[tls_edge.ports]
expose_http = true
http = 80
https = 443
bind_ip = ""
[tls_edge.http3]
enabled = false
[tls_edge.acme]
email = ""
volume_name = ""
store = "volume"
[tls_edge.acme.dns]
provider = "cloudflare"
wildcard_main = ""
[tls_edge.static]
cert_base = "/etc/letsencrypt"
domains = []
source = "bind"
[tls_edge.traefik.hostdir]
acme = { path = "", uid = 0, gid = 0, mode = "0700" }
[tls_edge.runtime]
cgroup_parent = ""
""", encoding="utf-8")
    return root


def test_locate_explicit_root_and_reject_wrong_layout(tmp_path: Path):
    root = make_root(tmp_path / "instance")
    assert config.locate_instance_root(root) == root.absolute()
    with pytest.raises(config.ConfigError, match="not a tls-edge stack directory"):
        config.locate_instance_root(tmp_path / "missing")


def test_locate_installed_root_from_current_symlink(tmp_path: Path, monkeypatch):
    root = make_root(tmp_path / "instance")
    release = root.parent / "releases" / "tls-edge-v3.1.0"
    make_root(release)
    (root.parent / "current").symlink_to(release)
    monkeypatch.setattr(sys, "prefix", str(release / "venv"))
    assert config.locate_instance_root() == root
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "unmanaged" / "venv"))
    with pytest.raises(config.ConfigError, match="pass --root PATH"):
        config.locate_instance_root()


def test_deep_merge_converts_nested_structures():
    assert config.deep_merge({"a": {"b": 1}, "x": [1]}, {"a": {"c": 2}, "x": [3]}) == {
        "a": {"b": 1, "c": 2}, "x": [3]
    }


def test_instance_paths_point_into_the_installed_stack(tmp_path: Path):
    defaults, override, env = config.instance_paths(tmp_path)
    assert defaults == tmp_path / "ciu-stack/ciu.defaults.toml.j2"
    assert override == tmp_path / "ciu-stack/ciu.toml.j2"
    assert env == tmp_path / "consumer.env"


def test_render_defaults_and_report_template_and_toml_errors(tmp_path: Path):
    root = make_root(tmp_path / "instance")
    path = root / "ciu-stack/ciu.defaults.toml.j2"
    assert config.render_defaults(path)["tls_edge"]["domain"] == "edge.example.com"
    with pytest.raises(config.ConfigError, match="cannot load tls-edge defaults"):
        config.render_defaults(tmp_path / "absent")
    path.write_text("{{ missing }}", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="cannot load tls-edge defaults"):
        config.render_defaults(path)
    path.write_text("not = [valid", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="cannot load tls-edge defaults"):
        config.render_defaults(path)


def test_load_site_merges_a_valid_override_and_requires_tls_edge_defaults(
    tmp_path: Path,
):
    root = make_root(tmp_path / "instance")
    defaults, overlay, effective = config.load_site(root / "ciu-stack")
    assert defaults["tls_edge"]["domain"] == "edge.example.com"
    assert overlay == {}
    assert effective["tls_edge"] == valid_config()

    override = root / "ciu-stack" / "ciu.toml.j2"
    override.write_text(
        '[tls_edge]\ndomain = "override.example.org"\n',
        encoding="utf-8",
    )
    _, overlay, effective = config.load_site(root / "ciu-stack")
    assert overlay["tls_edge"]["domain"] == "override.example.org"
    assert effective["tls_edge"]["domain"] == "override.example.org"

    bad = make_root(tmp_path / "bad", "[other]\nvalue = 1\n")
    with pytest.raises(config.ConfigError, match="must contain a \\[tls_edge\\] table"):
        config.load_site(bad / "ciu-stack")


@pytest.mark.parametrize("kind", ["symlink", "directory", "malformed"])
def test_load_site_refuses_invalid_overrides(tmp_path: Path, kind: str) -> None:
    root = make_root(tmp_path / kind)
    override = root / "ciu-stack" / "ciu.toml.j2"
    if kind == "symlink":
        override.symlink_to(root / "missing")
    elif kind == "directory":
        override.mkdir()
    else:
        override.write_text("broken = [", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="site override"):
        config.load_site(root / "ciu-stack")


def test_load_answers_none_valid_unknown_and_parse_error(tmp_path: Path):
    assert config.load_answers(None) == {}
    path = tmp_path / "answers.toml"
    path.write_text('[tls_edge]\ndomain = "answers.example.net"\n', encoding="utf-8")
    assert config.load_answers(path) == {
        "tls_edge": {"domain": "answers.example.net"}
    }
    path.write_text("http_enabled = true\n", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="3.1 flat form"):
        config.load_answers(path)
    path.write_text("unknown = true\n", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="unknown answers key 'unknown'") as exc:
        config.load_answers(path)
    assert f"(answers file {path})" in str(exc.value)
    path.write_text("x = [", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="cannot read answers"):
        config.load_answers(path)
    with pytest.raises(config.ConfigError, match="cannot read answers"):
        config.load_answers(tmp_path / "missing")


def test_validate_answers_accepts_schema_keys_and_refuses_other_shapes(
    tmp_path: Path,
) -> None:
    defaults = config.render_defaults(
        Path(__file__).resolve().parents[1]
        / "ciu-stack"
        / "ciu.defaults.toml.j2"
    )
    config.validate_answers(defaults, {})
    config.validate_answers(
        defaults,
        {
            "tls_edge": {
                "schema": 1,
                "http3": {"enabled": True},
            }
        },
    )
    with pytest.raises(config.ConfigError, match="answers key 'tls_edge' must be a TOML table"):
        config.validate_answers(defaults, {"tls_edge": True})
    with pytest.raises(
        config.ConfigError,
        match="answers key 'tls_edge.secrets' is an open site-override table",
    ):
        config.validate_answers(defaults, {"tls_edge": {"secrets": {}}})
    config.validate_answers(
        defaults,
        {"tls_edge": {"traefik": {"log_level": "WARN"}}},
    )
    answers_file = tmp_path / "answers.toml"
    with pytest.raises(config.ConfigError, match="unknown answers key") as exc:
        config.validate_answers(
            defaults,
            {"tls_edge": {"ports": {"nope": 1}}},
            answers_file=answers_file,
        )
    assert f"(answers file {answers_file})" in str(exc.value)
    with pytest.raises(config.ConfigError, match="newer than this release supports"):
        config.validate_answers(defaults, {"tls_edge": {"schema": 99}})


@pytest.mark.parametrize("value", [0, True, "80", 65536, -1])
def test_port_rejects_non_integer_boolean_and_out_of_range(value):
    with pytest.raises(config.ConfigError, match="integer from 1 through 65535"):
        config.normalize_port(value, "port")


def test_port_accepts_boundary_values():
    assert config.normalize_port(1, "port") == 1
    assert config.normalize_port(65535, "port") == 65535


def test_normalize_domain_idna_and_invalid_inputs():
    assert config.normalize_domain(" BÜCHER.Example. ") == "xn--bcher-kva.example"
    for value in ("", "a..b", "-bad.example", "bad-.example", "127.0.0.1", "a:b", 12):
        with pytest.raises(config.ConfigError):
            config.normalize_domain(value)


def test_normalizers_accept_empty_and_valid_scalars() -> None:
    assert config.normalize_email(" ops@example.test ") == "ops@example.test"
    assert config.normalize_email("") == ""
    assert config.normalize_provider(" cloudflare ") == "cloudflare"
    assert config.normalize_volume_name(" edge_acme ") == "edge_acme"
    assert config.normalize_volume_name("") == ""
    assert config.normalize_bind_ip(" 127.0.0.1 ") == "127.0.0.1"
    assert config.normalize_bind_ip("") == ""
    assert config.normalize_cgroup_parent(" edge.slice ") == "edge.slice"
    assert config.normalize_cgroup_parent("") == ""
    assert config.normalize_cert_base(" /srv/certs ") == "/srv/certs"
    assert config.normalize_static_domains(["BÜCHER.example", "dev.example"]) == [
        "xn--bcher-kva.example",
        "dev.example",
    ]


@pytest.mark.parametrize(
    ("normalize", "value"),
    [
        (config.normalize_email, 3),
        (config.normalize_provider, 3),
        (config.normalize_provider, "bad/provider"),
        (config.normalize_volume_name, 3),
        (config.normalize_volume_name, "bad/name"),
        (config.normalize_bind_ip, 3),
        (config.normalize_cgroup_parent, 3),
        (config.normalize_cgroup_parent, "x/../y"),
        (config.normalize_cert_base, 3),
        (config.normalize_cert_base, "relative"),
        (config.normalize_static_domains, "edge.example.test"),
        (config.normalize_static_domains, [""]),
        (config.normalize_static_domains, ["edge.example.test", "EDGE.example.test"]),
    ],
)
def test_normalizers_refuse_invalid_values(normalize, value) -> None:
    with pytest.raises(config.ConfigError):
        normalize(value)


def test_validate_config_refuses_missing_tables_and_required_keys() -> None:
    value = valid_config()
    del value["http3"]
    with pytest.raises(config.ConfigError, match="tls_edge.http3 must be a TOML table"):
        config.validate_config(value)

    value = valid_config()
    del value["ports"]["http"]
    with pytest.raises(config.ConfigError, match="ports.http is required"):
        config.validate_config(value)


def test_validate_config_builds_default_and_optional_port_plans():
    value = valid_config()
    assert config.validate_config(value) == [
        "80/tcp (HTTP redirect and/or HTTP-01)",
        "443/tcp (HTTPS, HTTP/1.1 and HTTP/2)",
    ]
    value["domain"] = ""
    value["tls"]["mode"] = "static"
    assert config.validate_config(
        value, require_domain=False, require_certificates=False
    ) == ["80/tcp (HTTP redirect and/or HTTP-01)", "443/tcp (HTTPS, HTTP/1.1 and HTTP/2)"]


@pytest.mark.parametrize(
    ("section", "key", "message"),
    [
        ("ports", "expose_http", "ports.expose_http must be true or false"),
        ("http3", "enabled", "http3.enabled must be true or false"),
    ],
)
def test_port_plan_refuses_non_boolean_listener_switches(
    section: str, key: str, message: str
) -> None:
    value = valid_config()
    value[section][key] = 1
    with pytest.raises(config.ConfigError, match=message):
        config.port_plan(value)


def test_port_plan_refuses_http_and_https_on_the_same_tcp_port() -> None:
    value = valid_config()
    value["ports"]["http"] = value["ports"]["https"]
    with pytest.raises(
        config.ConfigError,
        match="HTTP and HTTPS cannot publish the same TCP port",
    ):
        config.port_plan(value)


@pytest.mark.parametrize(("mutate", "message"), [
    (lambda c: c["tls"].update(mode="other"), "tls.mode"),
    (lambda c: c.update(domain=""), "domain is required"),
    (lambda c: c["ports"].update(expose_http=1), "ports.expose_http"),
    (lambda c: c["http3"].update(enabled="yes"), "http3.enabled"),
    (lambda c: c["ports"].update(http=443), "cannot publish the same TCP port"),
    (lambda c: c["ports"].update(https=444), "acme-tls requires"),
    (lambda c: c["ports"].update(bind_ip=123), "ports.bind_ip must be an IP address or an empty string"),
    (lambda c: (c["tls"].update(mode="acme-http"), c["ports"].update(expose_http=False)), "acme-http requires"),
    (lambda c: c["ports"].update(bind_ip="not-an-ip"), "ports.bind_ip must be a valid"),
    (lambda c: c["acme"].update(volume_name="bad/name"), "acme.volume_name"),
    (lambda c: c.update(runtime={"cgroup_parent": "x/../y"}), "runtime.cgroup_parent"),
    (lambda c: c["static"].update(cert_base="relative"), "static.cert_base"),
    (lambda c: c["static"].update(source="copy"), "static.source"),
    (lambda c: c["static"].update(domains="edge.example.com"), "static.domains"),
    (lambda c: c["static"].update(domains=["not a domain"]), "static certificate lineage"),
    (lambda c: (c["tls"].update(mode="acme-dns"), c["acme"]["dns"].update(provider="bad/name")), "acme.dns.provider"),
    (lambda c: (c["tls"].update(mode="acme-dns"), c["acme"]["dns"].update(provider="cloudflare", wildcard_main="bad domain")), "wildcard_main"),
    (lambda c: (c["tls"].update(mode="acme-dns"), c["acme"]["dns"].update(provider="cloudflare", wildcard_main=3)), "wildcard_main must be a DNS name or empty"),
    (lambda c: c["acme"].update(email="not-email"), "acme.email"),
])
def test_validate_config_rejects_invalid_combinations(mutate, message):
    value = valid_config()
    mutate(value)
    with pytest.raises(config.ConfigError, match=message):
        config.validate_config(value)


def test_validate_config_http3_dns_static_and_port_constraints():
    value = valid_config()
    value["http3"]["enabled"] = True
    assert config.validate_config(value) == [
        "80/tcp (HTTP redirect and/or HTTP-01)",
        "443/tcp (HTTPS, HTTP/1.1 and HTTP/2)",
        "443/udp (HTTP/3 over QUIC)",
    ]

    value = valid_config()
    value["tls"]["mode"] = "acme-dns"
    value["acme"]["dns"] = {"provider": "cloudflare", "wildcard_main": "example.com"}
    assert config.validate_config(value)
    value["acme"]["dns"]["wildcard_main"] = ""
    assert config.validate_config(value)

    for mode in ("static", "dev"):
        value = valid_config()
        value["tls"]["mode"] = mode
        value["static"]["domains"] = ["edge.example.com"]
        assert config.validate_config(value)

    value = valid_config()
    value["tls"]["mode"] = "static"
    with pytest.raises(config.ConfigError, match="requires at least one static certificate lineage"):
        config.validate_config(value)

def test_validate_config_hostdir_rules_and_modes(tmp_path: Path):
    value = valid_config()
    value["acme"]["store"] = "hostdir"
    value["traefik"]["hostdir"]["acme"]["path"] = "../../shared/acme"
    stack_dir = tmp_path / "instance" / "stack" / "ciu-stack"
    stack_dir.mkdir(parents=True)
    assert config.validate_config(value, stack_dir=stack_dir)
    value["traefik"]["hostdir"]["acme"]["path"] = str(tmp_path / "shared" / "acme")
    assert config.validate_config(value, stack_dir=stack_dir)
    assert config.validate_config(value)  # no containment check without stack context

    value["traefik"]["hostdir"]["acme"]["path"] = "../../shared/$EDGE/acme"
    with pytest.raises(
        config.ConfigError,
        match=r"must not contain '\$'",
    ):
        config.validate_config(value, stack_dir=stack_dir)

    value["acme"]["store"] = "archive"
    with pytest.raises(config.ConfigError, match="acme.store must be volume or hostdir"):
        config.validate_config(value)

    value["acme"]["store"] = "hostdir"
    value["traefik"]["hostdir"]["acme"] = ""
    with pytest.raises(config.ConfigError, match="hostdir.acme must be a TOML table"):
        config.validate_config(value)

    value["traefik"]["hostdir"]["acme"] = {
        "path": 3, "uid": 0, "gid": 0, "mode": "0700",
    }
    with pytest.raises(config.ConfigError, match="hostdir.acme.path is required"):
        config.validate_config(value)

    value["traefik"]["hostdir"]["acme"] = {
        "path": "../../shared/acme", "uid": False, "gid": 0, "mode": "0700",
    }
    with pytest.raises(config.ConfigError, match="hostdir.acme.uid must be 0"):
        config.validate_config(value)


def test_validate_config_hostdir_is_refused_by_standalone_renderer():
    value = valid_config()
    value["acme"]["store"] = "hostdir"
    value["traefik"]["hostdir"]["acme"]["path"] = "../../shared/acme"
    with pytest.raises(
        config.ConfigError,
        match='acme.store = "hostdir" requires ciu',
    ):
        config.validate_config(value, for_standalone=True)


def test_updated_override_creates_preserves_and_rejects_invalid_paths(tmp_path: Path):
    root = make_root(tmp_path / "instance")
    path, text, mode = config.updated_override(root, {"domain": "new.example.net"})
    assert path.name == "ciu.toml.j2" and mode == 0o644
    assert tomlkit.parse(text)["tls_edge"]["domain"] == "new.example.net"

    path.write_text('# keep this comment\n[tls_edge]\ndomain = "old.example.net"\n', encoding="utf-8")
    path.chmod(0o600)
    _, text, mode = config.updated_override(root, {"domain": "new.example.net"})
    assert "# keep this comment" in text and mode == 0o600
    _, text, _ = config.updated_override(
        root,
        {"domain": "new.example.net"},
        {"traefik.log_level": "DEBUG"},
    )
    assert tomlkit.parse(text)["tls_edge"]["traefik"]["log_level"] == "DEBUG"
    with pytest.raises(config.ConfigError, match="must use field-table values"):
        config.updated_override(root, {}, {"domain": "new.example.net"})
    with pytest.raises(config.ConfigError, match="unsupported configuration field"):
        config.updated_override(root, {"unknown": True})

    path.unlink()
    path.symlink_to(root / "target")
    with pytest.raises(config.ConfigError, match="site override must be a regular file"):
        config.updated_override(root, {"domain": "x.example"})


def test_updated_override_reports_parse_and_nested_shape_errors(tmp_path: Path):
    root = make_root(tmp_path / "instance")
    path = root / "ciu-stack/ciu.toml.j2"
    path.write_text("broken = [", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="malformed site override"):
        config.updated_override(root, {"domain": "x.example"})
    path.write_text("tls_edge = 3\n", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="is not a table"):
        config.updated_override(root, {"domain": "x.example"})


def _consumer_contract(domain: str) -> dict[str, str]:
    return {
        "TLS_EDGE_DOMAIN": domain,
        "TLS_EDGE_NETWORK": "ingress_public",
        "TLS_EDGE_HTTPS_ENTRYPOINT": "websecure",
        "TLS_EDGE_HEADERS_MIDDLEWARE": "secure-headers@file",
        "TLS_EDGE_HTTP3": "false",
        "TLS_EDGE_PUBLIC_PORTS": "80/tcp,443/tcp",
    }


def test_updated_consumer_env_creates_and_replaces_only_owned_entries(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path / "instance")
    one = _consumer_contract("one.example")
    path, text, mode = config.updated_consumer_env(root, one)
    assert text == (
        "TLS_EDGE_DOMAIN=one.example\n"
        "TLS_EDGE_NETWORK=ingress_public\n"
        "TLS_EDGE_HTTPS_ENTRYPOINT=websecure\n"
        "TLS_EDGE_HEADERS_MIDDLEWARE=secure-headers@file\n"
        "TLS_EDGE_HTTP3=false\n"
        "TLS_EDGE_PUBLIC_PORTS=80/tcp,443/tcp\n"
    )
    assert mode == 0o644
    path.write_text("OTHER=value\nTLS_EDGE_DOMAIN=old\nTLS_EDGE_DOMAIN=duplicate\n", encoding="utf-8")
    path.chmod(0o600)
    two = _consumer_contract("two.example")
    _, text, mode = config.updated_consumer_env(root, two)
    assert text == (
        "OTHER=value\n"
        "TLS_EDGE_DOMAIN=two.example\n"
        "TLS_EDGE_NETWORK=ingress_public\n"
        "TLS_EDGE_HTTPS_ENTRYPOINT=websecure\n"
        "TLS_EDGE_HEADERS_MIDDLEWARE=secure-headers@file\n"
        "TLS_EDGE_HTTP3=false\n"
        "TLS_EDGE_PUBLIC_PORTS=80/tcp,443/tcp\n"
    )
    assert mode == 0o600
    path.write_text("OTHER=value\n", encoding="utf-8")
    three = _consumer_contract("three.example")
    _, text, mode = config.updated_consumer_env(root, three)
    assert text == (
        "OTHER=value\n"
        "TLS_EDGE_DOMAIN=three.example\n"
        "TLS_EDGE_NETWORK=ingress_public\n"
        "TLS_EDGE_HTTPS_ENTRYPOINT=websecure\n"
        "TLS_EDGE_HEADERS_MIDDLEWARE=secure-headers@file\n"
        "TLS_EDGE_HTTP3=false\n"
        "TLS_EDGE_PUBLIC_PORTS=80/tcp,443/tcp\n"
    )
    assert mode == 0o600
    path.unlink()
    path.mkdir()
    with pytest.raises(config.ConfigError, match="consumer environment must be a regular file"):
        config.updated_consumer_env(root, three)


def test_atomic_write_sets_mode(tmp_path: Path):
    path = tmp_path / "nested" / "override.toml"
    config.atomic_write(path, "hello\n", 0o640)
    assert path.read_text(encoding="utf-8") == "hello\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_atomic_write_removes_the_temporary_file_when_replace_fails(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(OSError):
        config.atomic_write(target, "replacement\n", 0o640)
    assert target.is_dir()
    assert list(tmp_path.glob(".target.*.tmp")) == []
