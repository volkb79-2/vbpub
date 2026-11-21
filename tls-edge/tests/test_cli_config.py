from __future__ import annotations

import stat
from pathlib import Path
from types import MappingProxyType

import pytest
import tomlkit

from tls_edge_cli import config


def valid_config() -> dict:
    return {
        "domain": "edge.example.com",
        "tls": {"mode": "acme-tls"},
        "ports": {"expose_http": True, "http": 80, "https": 443, "bind_ip": ""},
        "http3": {"enabled": False},
        "calls": {"enabled": False},
        "acme": {"email": "", "volume_name": ""},
        "static": {"cert_base": "/etc/letsencrypt", "domains": [], "source": "bind"},
    }


def make_root(path: Path, template: str | None = None) -> Path:
    defaults = path / "stack" / "ciu-stack" / "ciu.defaults.toml.j2"
    defaults.parent.mkdir(parents=True)
    defaults.write_text(template or """\
[tls_edge]
domain = "edge.example.com"
[tls_edge.tls]
mode = "acme-tls"
[tls_edge.ports]
expose_http = true
http = 80
https = 443
bind_ip = ""
[tls_edge.http3]
enabled = false
[tls_edge.calls]
enabled = false
[tls_edge.acme]
email = ""
volume_name = ""
[tls_edge.static]
cert_base = "/etc/letsencrypt"
domains = []
source = "bind"
""", encoding="utf-8")
    return path


def test_locate_explicit_root_and_reject_wrong_layout(tmp_path: Path):
    root = make_root(tmp_path / "instance")
    assert config.locate_instance_root(root) == root.absolute()
    with pytest.raises(config.ConfigError, match="not a tls-edge instance root"):
        config.locate_instance_root(tmp_path / "missing")


def test_locate_installed_root_from_current_symlink(tmp_path: Path, monkeypatch):
    root = make_root(tmp_path / "instance")
    release = root / "releases" / "tls-edge-v3.1.0"
    make_root(release)
    (root / "current").symlink_to(release)
    monkeypatch.setattr(config.sys, "prefix", str(release / "venv"))
    assert config.locate_instance_root() == root
    monkeypatch.setattr(config.sys, "prefix", str(tmp_path / "unmanaged" / "venv"))
    with pytest.raises(config.ConfigError, match="pass --root PATH"):
        config.locate_instance_root()


def test_plain_and_deep_merge_convert_nested_structures():
    assert config._plain({"a": (1, {"b": 2})}) == {"a": [1, {"b": 2}]}
    assert config._deep_merge({"a": {"b": 1}, "x": [1]}, {"a": {"c": 2}, "x": [3]}) == {
        "a": {"b": 1, "c": 2}, "x": [3]
    }


def test_instance_paths_point_into_the_installed_stack(tmp_path: Path):
    defaults, override, env = config.instance_paths(tmp_path)
    assert defaults == tmp_path / "stack/ciu-stack/ciu.defaults.toml.j2"
    assert override == tmp_path / "stack/ciu-stack/ciu.toml.j2"
    assert env == tmp_path / "stack/consumer.env"


def test_render_defaults_and_report_template_and_toml_errors(tmp_path: Path):
    root = make_root(tmp_path / "instance")
    path = root / "stack/ciu-stack/ciu.defaults.toml.j2"
    assert config._render_defaults(path)["tls_edge"]["domain"] == "edge.example.com"
    with pytest.raises(config.ConfigError, match="cannot load tls-edge defaults"):
        config._render_defaults(tmp_path / "absent")
    path.write_text("{{ missing }}", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="cannot load tls-edge defaults"):
        config._render_defaults(path)
    path.write_text("not = [valid", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="cannot load tls-edge defaults"):
        config._render_defaults(path)


def test_load_effective_config_merges_valid_override_and_requires_tls_edge(tmp_path: Path):
    root = make_root(tmp_path / "instance")
    assert config.load_effective_config(root) == (valid_config(), False)
    override = root / "stack/ciu-stack/ciu.toml.j2"
    override.write_text('[tls_edge]\ndomain = "override.example.org"\n', encoding="utf-8")
    merged, exists = config.load_effective_config(root)
    assert exists and merged["domain"] == "override.example.org"

    bad = make_root(tmp_path / "bad", "[other]\nvalue = 1\n")
    with pytest.raises(config.ConfigError, match="must contain a \\[tls_edge\\] table"):
        config.load_effective_config(bad)


@pytest.mark.parametrize("kind", ["symlink", "directory", "malformed"])
def test_load_effective_config_refuses_invalid_site_override(tmp_path: Path, kind: str):
    root = make_root(tmp_path / kind)
    override = root / "stack/ciu-stack/ciu.toml.j2"
    if kind == "symlink":
        override.symlink_to(root / "missing")
    elif kind == "directory":
        override.mkdir()
    else:
        override.write_text("broken = [", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="site override"):
        config.load_effective_config(root)


def test_load_answers_none_valid_unknown_and_parse_error(tmp_path: Path):
    assert config.load_answers(None) == {}
    path = tmp_path / "answers.toml"
    path.write_text('domain = "answers.example.net"\n', encoding="utf-8")
    assert config.load_answers(path) == {"domain": "answers.example.net"}
    path.write_text('unknown = true\n', encoding="utf-8")
    with pytest.raises(config.ConfigError, match="unknown answer key"):
        config.load_answers(path)
    path.write_text("x = [", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="cannot read answers"):
        config.load_answers(path)
    with pytest.raises(config.ConfigError, match="cannot read answers"):
        config.load_answers(tmp_path / "missing")


def test_value_at_found_missing_and_non_mapping():
    assert config.value_at({"a": {"b": 4}}, "a", "b") == 4
    assert config.value_at({"a": 4}, "a", "b", default="fallback") == "fallback"


@pytest.mark.parametrize("value", [0, True, "80", 65536, -1])
def test_port_rejects_non_integer_boolean_and_out_of_range(value):
    with pytest.raises(config.ConfigError, match="integer from 1 through 65535"):
        config._port(value, "port")


def test_port_accepts_boundary_values():
    assert config._port(1, "port") == 1
    assert config._port(65535, "port") == 65535


def test_normalize_domain_idna_and_invalid_inputs():
    assert config.normalize_domain(" BÜCHER.Example. ") == "xn--bcher-kva.example"
    for value in ("", "a..b", "-bad.example", "bad-.example", "127.0.0.1", "a:b", 12):
        with pytest.raises(config.ConfigError):
            config.normalize_domain(value)


def test_table_returns_empty_default_and_rejects_wrong_shape():
    assert config._table({}, "tls") == {}
    with pytest.raises(config.ConfigError, match="must be a TOML table"):
        config._table({"tls": []}, "tls")


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


@pytest.mark.parametrize(("mutate", "message"), [
    (lambda c: c["tls"].update(mode="other"), "tls.mode"),
    (lambda c: c.update(domain=""), "domain is required"),
    (lambda c: c["ports"].update(expose_http=1), "ports.expose_http"),
    (lambda c: c["http3"].update(enabled="yes"), "http3.enabled"),
    (lambda c: c["calls"].update(enabled=0), "calls.enabled"),
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
    (lambda c: (c["http3"].update(enabled=True), c["calls"].update(enabled=True), c["calls"].update(public_port=80)), "Calls TCP conflicts"),
    (lambda c: (c["http3"].update(enabled=True), c["calls"].update(enabled=True), c["calls"].update(public_port=443)), "Calls TCP conflicts"),
    (lambda c: (c["tls"].update(mode="acme-dns"), c.update(acme={"dns": {"provider": "bad/name"}})), "acme.dns.provider"),
    (lambda c: (c["tls"].update(mode="acme-dns"), c.update(acme={"dns": {"provider": "cloudflare", "wildcard_main": "bad domain"}})), "wildcard_main"),
    (lambda c: (c["tls"].update(mode="acme-dns"), c.update(acme={"dns": {"provider": "cloudflare", "wildcard_main": 3}})), "wildcard_main must be a DNS name or empty"),
    (lambda c: c["acme"].update(email="not-email"), "acme.email"),
])
def test_validate_config_rejects_invalid_combinations(mutate, message):
    value = valid_config()
    mutate(value)
    with pytest.raises(config.ConfigError, match=message):
        config.validate_config(value)


def test_validate_config_http3_calls_dns_static_and_port_constraints():
    value = valid_config()
    value["http3"]["enabled"] = True
    value["calls"].update(enabled=True, public_port=8443)
    assert config.validate_config(value)[-3:] == [
        "443/udp (HTTP/3 over QUIC)",
        "8443/tcp (Calls media)",
        "8443/udp (Calls media)",
    ]

    value = valid_config()
    value["tls"]["mode"] = "acme-dns"
    value["acme"] = {"dns": {"provider": "cloudflare", "wildcard_main": "example.com"}}
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

    value = valid_config()
    value["calls"].update(enabled=True, public_port=443)
    with pytest.raises(config.ConfigError, match="Calls TCP conflicts"):
        config.validate_config(value)


def test_set_path_creates_nested_tables_and_rejects_non_mutable_parents():
    document = tomlkit.document()
    config._set_path(document, ("tls_edge", "ports", "https"), 9443)
    assert document["tls_edge"]["ports"]["https"] == 9443
    with pytest.raises(config.ConfigError, match="is not a table"):
        config._set_path({"tls_edge": 4}, ("tls_edge", "domain"), "x.example")
    with pytest.raises(config.ConfigError, match="parent is not a TOML table"):
        config._set_path(MappingProxyType({}), ("tls_edge", "domain"), "x.example")
    with pytest.raises(config.ConfigError, match="parent is not a TOML table"):
        config._set_path(MappingProxyType({}), ("domain",), "x.example")


def test_updated_override_creates_preserves_and_rejects_invalid_paths(tmp_path: Path):
    root = make_root(tmp_path / "instance")
    path, text, mode = config.updated_override(root, {"domain": "new.example.net"})
    assert path.name == "ciu.toml.j2" and mode == 0o644
    assert tomlkit.parse(text)["tls_edge"]["domain"] == "new.example.net"

    path.write_text('# keep this comment\n[tls_edge]\ndomain = "old.example.net"\n', encoding="utf-8")
    path.chmod(0o600)
    _, text, mode = config.updated_override(root, {"domain": "new.example.net"})
    assert "# keep this comment" in text and mode == 0o600
    with pytest.raises(config.ConfigError, match="unsupported configuration field"):
        config.updated_override(root, {"unknown": True})

    path.unlink()
    path.symlink_to(root / "target")
    with pytest.raises(config.ConfigError, match="site override must be a regular file"):
        config.updated_override(root, {"domain": "x.example"})


def test_updated_override_reports_parse_and_nested_shape_errors(tmp_path: Path):
    root = make_root(tmp_path / "instance")
    path = root / "stack/ciu-stack/ciu.toml.j2"
    path.write_text("broken = [", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="malformed site override"):
        config.updated_override(root, {"domain": "x.example"})
    path.write_text("tls_edge = 3\n", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="is not a table"):
        config.updated_override(root, {"domain": "x.example"})


def test_updated_consumer_env_creates_and_replaces_only_domain_entries(tmp_path: Path):
    root = make_root(tmp_path / "instance")
    path, text, mode = config.updated_consumer_env(root, "one.example")
    assert text == "TLS_EDGE_DOMAIN=one.example\n" and mode == 0o644
    path.write_text("OTHER=value\nTLS_EDGE_DOMAIN=old\nTLS_EDGE_DOMAIN=duplicate\n", encoding="utf-8")
    path.chmod(0o600)
    _, text, mode = config.updated_consumer_env(root, "two.example")
    assert text == "OTHER=value\nTLS_EDGE_DOMAIN=two.example\n"
    assert mode == 0o600
    path.write_text("OTHER=value\n", encoding="utf-8")
    _, text, mode = config.updated_consumer_env(root, "three.example")
    assert text == "OTHER=value\nTLS_EDGE_DOMAIN=three.example\n"
    assert mode == 0o600
    path.unlink()
    path.mkdir()
    with pytest.raises(config.ConfigError, match="consumer environment must be a regular file"):
        config.updated_consumer_env(root, "three.example")


def test_atomic_write_sets_mode_and_cleans_temporary_file_on_replace_failure(tmp_path: Path, monkeypatch):
    path = tmp_path / "nested" / "override.toml"
    config.atomic_write(path, "hello\n", 0o640)
    assert path.read_text(encoding="utf-8") == "hello\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o640

    def fail_replace(*_args):
        raise OSError("replace failed")

    monkeypatch.setattr(config.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        config.atomic_write(path, "new\n", 0o600)
    assert sorted(item.name for item in path.parent.iterdir()) == ["override.toml"]

    def remove_temp_then_fail(source, _target):
        Path(source).unlink()
        raise OSError("replace failed after temporary file disappeared")

    monkeypatch.setattr(config.os, "replace", remove_temp_then_fail)
    with pytest.raises(OSError, match="temporary file disappeared"):
        config.atomic_write(path, "again\n", 0o600)
    assert sorted(item.name for item in path.parent.iterdir()) == ["override.toml"]
