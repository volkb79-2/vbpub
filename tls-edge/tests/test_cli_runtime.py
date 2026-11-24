from __future__ import annotations

import runpy
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from cli_extended import CliFailure, CliIdentity

from tls_edge_cli import cli


def make_root(path: Path) -> Path:
    defaults = path / "stack" / "ciu-stack" / "ciu.defaults.toml.j2"
    defaults.parent.mkdir(parents=True)
    defaults.write_text("""\
[tls_edge]
schema = 1
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
[tls_edge.calls]
enabled = false
[tls_edge.acme]
email = ""
volume_name = ""
[tls_edge.static]
cert_base = "/etc/letsencrypt"
domains = []
source = "bind"
[tls_edge.runtime]
cgroup_parent = ""
""", encoding="utf-8")
    return path


class Prompts:
    def text(self, _prompt, *, default=None, required=True):
        if default is not None:
            return default
        return "edge.example.com" if required else ""

    def confirm(self, _prompt, *, default):
        return default

    def select(self, _prompt, _choices, *, default):
        return default


class InvalidPortPrompts(Prompts):
    def text(self, _prompt, *, default=None, required=True):
        return "not-a-port"


class Output:
    def __init__(self):
        self.lines = []

    def primary(self, text):
        self.lines.append(text)


class Runtime:
    def __init__(self, *, confirm=True):
        self.prompts = Prompts()
        self.output = Output()
        self.confirm_result = confirm
        self.confirm_calls = 0

    def confirm(self, _prompt):
        self.confirm_calls += 1
        return self.confirm_result


def answer_file(path: Path, text: str = 'domain = "edge.example.org"\n') -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_identity_and_root_resolution(monkeypatch, tmp_path: Path):
    expected = CliIdentity("TLS EDGE", "3.1.0", "Configure a shared edge", "tls-edge")
    monkeypatch.setattr(cli.CliIdentity, "from_distribution", staticmethod(lambda **_kwargs: expected))
    assert cli._identity() is expected

    root = make_root(tmp_path / "instance")
    assert cli._root(SimpleNamespace(root=root)) == root
    with pytest.raises(CliFailure, match="not a tls-edge instance root"):
        cli._root(SimpleNamespace(root=tmp_path / "missing"))


def test_answer_text_boolean_port_and_choice_helpers():
    assert cli._answer({}, "n", int) is None
    assert cli._answer({"n": 3}, "n", int) == 3
    with pytest.raises(cli.ConfigError, match="must be an integer"):
        cli._answer({"n": True}, "n", int)
    with pytest.raises(cli.ConfigError, match="must be int"):
        cli._answer({"n": "3"}, "n", int)

    runtime = Runtime()
    assert cli._text(runtime, {"name": "  x  "}, "name", "Name", None) == "x"
    assert cli._text(runtime, {}, "name", "Name", "default", allow_prompt=False) == "default"
    assert cli._text(runtime, {}, "optional", "Optional", None, required=False, allow_prompt=False) == ""
    assert cli._text(runtime, {}, "name", "Name", None) == "edge.example.com"
    with pytest.raises(cli.ConfigError, match="name is required"):
        cli._text(runtime, {"name": "  "}, "name", "Name", None)

    assert cli._boolean(runtime, {"flag": False}, "flag", "Flag", True) is False
    assert cli._boolean(runtime, {}, "flag", "Flag", True, allow_prompt=False) is True
    assert cli._port(runtime, {"port": 65535}, "port", "Port", 80) == 65535
    assert cli._port(runtime, {}, "port", "Port", 8080, allow_prompt=False) == 8080
    invalid_prompt = Runtime()
    invalid_prompt.prompts = InvalidPortPrompts()
    with pytest.raises(cli.ConfigError, match="whole number"):
        cli._port(invalid_prompt, {}, "port", "Port", 80)
    with pytest.raises(cli.ConfigError, match="whole number"):
        cli._port(runtime, {"port": 65536}, "port", "Port", 80)
    assert cli._choices(runtime, {"mode": "static"}, "mode", "Mode", ("acme", "static"), "acme") == "static"
    assert cli._choices(runtime, {}, "mode", "Mode", ("acme", "static"), "acme", allow_prompt=False) == "acme"
    with pytest.raises(cli.ConfigError, match="must be one of"):
        cli._choices(runtime, {"mode": "wrong"}, "mode", "Mode", ("acme", "static"), "acme")


def test_static_domain_parser_handles_lists_prompts_and_errors():
    assert cli._static_domains(["edge.example.com"]) == ["edge.example.com"]
    assert cli._static_domains(None, "one.example, two.example") == ["one.example", "two.example"]
    for value in ([""], [3], ["edge.example.com", "edge.example.com"]):
        with pytest.raises(cli.ConfigError):
            cli._static_domains(value)
    with pytest.raises(cli.ConfigError, match="TOML array"):
        cli._static_domains("edge.example.com")


def test_collect_values_defaults_dns_static_and_calls_variants():
    runtime = Runtime()
    current = {
        "domain": "edge.example.com",
        "tls": {"mode": "acme-tls"},
        "ports": {"expose_http": True, "http": 80, "https": 443, "bind_ip": ""},
        "http3": {"enabled": False}, "calls": {"enabled": False},
        "acme": {"email": "ops@example.com", "volume_name": ""},
        "static": {"cert_base": "/etc/letsencrypt", "domains": [], "source": "bind"},
    }
    value = cli._collect_values(current, runtime, {}, allow_prompt=True)
    assert value["domain"] == "edge.example.com" and value["tls_mode"] == "acme-tls"
    assert value["acme_email"] == "ops@example.com"

    dns = cli._collect_values(current, runtime, {
        "tls_mode": "acme-dns", "dns_provider": "cloudflare", "dns_wildcard": "example.com"
    }, allow_prompt=False)
    assert dns["dns_provider"] == "cloudflare" and dns["dns_wildcard"] == "example.com"

    static = cli._collect_values(current, runtime, {"tls_mode": "static"}, allow_prompt=False)
    assert static["static_domains"] == ["edge.example.com"]
    assert static["static_source"] == "bind"
    prompted_static = cli._collect_values(current, runtime, {"tls_mode": "static"}, allow_prompt=True)
    assert prompted_static["static_domains"] == ["edge.example.com"]
    supplied_static = cli._collect_values(current, runtime, {
        "tls_mode": "dev", "static_domains": ["one.example", "two.example"],
        "static_source": "volume", "static_cert_base": "/srv/certs",
    }, allow_prompt=False)
    assert supplied_static["static_domains"] == ["one.example", "two.example"]
    assert supplied_static["static_source"] == "volume"
    assert supplied_static["static_cert_base"] == "/srv/certs"

    calls = cli._collect_values(current, runtime, {"calls_enabled": True, "calls_port": 9443}, allow_prompt=False)
    assert calls["calls_port"] == 9443
    disabled_calls = cli._collect_values(current, runtime, {"calls_port": 8444}, allow_prompt=False)
    assert disabled_calls["calls_port"] == 8444


@pytest.mark.parametrize(("answers", "message"), [
    ({"tls_mode": "static", "acme_email": "ops@example.org"}, "requires an ACME"),
    ({"dns_provider": "cloudflare"}, "require tls_mode = 'acme-dns'"),
    ({"static_domains": ["edge.example.com"]}, "static certificate answers require"),
])
def test_collect_values_rejects_answers_in_incompatible_modes(answers, message):
    current = {
        "domain": "edge.example.com", "tls": {"mode": "acme-tls"},
        "ports": {"expose_http": True, "http": 80, "https": 443, "bind_ip": ""},
        "http3": {"enabled": False}, "calls": {"enabled": False}, "acme": {}, "static": {},
    }
    with pytest.raises(cli.ConfigError, match=message):
        cli._collect_values(current, Runtime(), answers, allow_prompt=False)


def test_render_config_wraps_storage_errors(tmp_path: Path, monkeypatch):
    root = make_root(tmp_path / "instance")
    values = {"domain": "edge.example.org"}
    override_path, override_text, override_mode, env_path, env_text, env_mode = cli._render_config(root, values)
    assert override_path.name == "ciu.toml.j2" and 'edge.example.org' in override_text
    assert env_path.name == "consumer.env" and env_text == "TLS_EDGE_DOMAIN=edge.example.org\n"
    assert override_mode == env_mode == 0o644
    monkeypatch.setattr(cli, "updated_override", lambda *_args: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(CliFailure, match="disk"):
        cli._render_config(root, values)


def test_diff_handles_new_file_and_changed_existing_file(tmp_path: Path):
    path = tmp_path / "site.toml"
    assert "(new)" in cli._diff(path, "", "x = 1\n")
    assert "-old" in cli._diff(path, "old\n", "new\n")


def test_configure_dry_run_decline_write_and_idempotent_write(tmp_path: Path):
    root = make_root(tmp_path / "instance")
    answers = answer_file(tmp_path / "answers.toml")

    dry_runtime = Runtime()
    assert cli.configure(SimpleNamespace(root=root, answers=answers, dry_run=True), dry_runtime) == 0
    assert not (root / "stack/ciu-stack/ciu.toml.j2").exists()
    assert any("Dry run" in line for line in dry_runtime.output.lines)

    decline_runtime = Runtime(confirm=False)
    assert cli.configure(SimpleNamespace(root=root, answers=answers, dry_run=False), decline_runtime) == 0
    assert not (root / "stack/consumer.env").exists()
    assert any("not changed" in line for line in decline_runtime.output.lines)

    write_runtime = Runtime()
    assert cli.configure(SimpleNamespace(root=root, answers=answers, dry_run=False), write_runtime) == 0
    assert 'domain = "edge.example.org"' in (root / "stack/ciu-stack/ciu.toml.j2").read_text()
    assert (root / "stack/consumer.env").read_text() == "TLS_EDGE_DOMAIN=edge.example.org\n"
    assert any("sudo ./ciu check" in line for line in write_runtime.output.lines)

    repeat_runtime = Runtime()
    assert cli.configure(SimpleNamespace(root=root, answers=answers, dry_run=False), repeat_runtime) == 0
    assert repeat_runtime.confirm_calls == 0
    assert any("already matches" in line for line in repeat_runtime.output.lines)


def test_configure_writes_dns_calls_and_static_values(tmp_path: Path):
    root = make_root(tmp_path / "instance")
    answers = answer_file(tmp_path / "answers.toml", """\
domain = "meet.example.org"
tls_mode = "acme-dns"
dns_provider = "cloudflare"
dns_wildcard = "example.org"
http3_enabled = true
calls_enabled = true
calls_port = 9443
ingress_external = true
cgroup_parent = "dev-background.slice"
""")
    assert cli.configure(SimpleNamespace(root=root, answers=answers, dry_run=False), Runtime()) == 0
    document = tomllib.loads((root / "stack/ciu-stack/ciu.toml.j2").read_text())
    settings = document["tls_edge"]
    assert settings["acme"]["dns"] == {"provider": "cloudflare", "wildcard_main": "example.org"}
    assert settings["calls"]["public_port"] == 9443
    assert settings["http3"]["enabled"] is True
    assert settings["ingress_external"] is True
    assert settings["runtime"]["cgroup_parent"] == "dev-background.slice"

    static_root = make_root(tmp_path / "static")
    static_answers = answer_file(tmp_path / "static.toml", """\
domain = "static.example.org"
tls_mode = "static"
static_cert_base = "/srv/certs"
static_domains = ["static.example.org"]
static_source = "volume"
""")
    assert cli.configure(SimpleNamespace(root=static_root, answers=static_answers, dry_run=False), Runtime()) == 0
    static_doc = tomllib.loads((static_root / "stack/ciu-stack/ciu.toml.j2").read_text())
    assert static_doc["tls_edge"]["static"] == {
        "cert_base": "/srv/certs", "domains": ["static.example.org"], "source": "volume"
    }


def test_configure_repairs_a_cross_field_invalid_current_site(tmp_path: Path):
    root = make_root(tmp_path / "instance")
    override = root / "stack/ciu-stack/ciu.toml.j2"
    override.write_text(
        '[tls_edge]\ndomain = "edge.example.com"\n'
        '[tls_edge.tls]\nmode = "static"\n',
        encoding="utf-8",
    )
    answers = answer_file(
        tmp_path / "answers.toml",
        'tls_mode = "acme-tls"\nhttps_port = 443\n',
    )

    assert cli.configure(
        SimpleNamespace(root=root, answers=answers, dry_run=False),
        Runtime(),
    ) == 0
    written = tomllib.loads(override.read_text(encoding="utf-8"))
    assert written["tls_edge"]["schema"] == 1
    assert written["tls_edge"]["tls"]["mode"] == "acme-tls"


def test_configure_wraps_answer_and_partial_write_errors(tmp_path: Path, monkeypatch):
    root = make_root(tmp_path / "instance")
    bad_answers = answer_file(tmp_path / "bad.toml", "tls_mode = \"static\"\nacme_email = \"ops@example.org\"\n")
    with pytest.raises(CliFailure, match="requires an ACME"):
        cli.configure(SimpleNamespace(root=root, answers=bad_answers, dry_run=True), Runtime())

    answers = answer_file(tmp_path / "answers.toml")
    calls = 0
    original = cli.atomic_write

    def fail_second(path, text, mode):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("second write failed")
        original(path, text, mode)

    monkeypatch.setattr(cli, "atomic_write", fail_second)
    with pytest.raises(CliFailure, match="inspect .* before retrying"):
        cli.configure(SimpleNamespace(root=root, answers=answers, dry_run=False), Runtime())


def test_show_and_validate_valid_empty_and_invalid_instances(tmp_path: Path):
    root = make_root(tmp_path / "instance")
    runtime = Runtime()
    assert cli.show(SimpleNamespace(root=root), runtime) == 0
    assert any("not configured" in line for line in runtime.output.lines)
    assert cli.validate(SimpleNamespace(root=root), Runtime()) == 0

    override = root / "stack/ciu-stack/ciu.toml.j2"
    override.write_text('[tls_edge]\ndomain = "custom.example.org"\n[tls_edge.http3]\nenabled = true\n', encoding="utf-8")
    shown = Runtime()
    assert cli.show(SimpleNamespace(root=root), shown) == 0
    assert any("Site override: present" in line for line in shown.output.lines)
    assert any("HTTP/3: enabled" in line for line in shown.output.lines)

    override.write_text("[tls_edge]\n[tls_edge.tls]\nmode = \"wrong\"\n", encoding="utf-8")
    with pytest.raises(CliFailure, match="tls.mode"):
        cli.show(SimpleNamespace(root=root), Runtime())
    with pytest.raises(CliFailure, match="tls.mode"):
        cli.validate(SimpleNamespace(root=root), Runtime())


def test_show_unconfigured_domain(tmp_path: Path):
    root = make_root(tmp_path / "unconfigured")
    override = root / "stack/ciu-stack/ciu.toml.j2"
    override.write_text('[tls_edge]\ndomain = ""\n', encoding="utf-8")
    runtime = Runtime()
    assert cli.show(SimpleNamespace(root=root), runtime) == 0
    assert "Inbound ports:" in runtime.output.lines
    assert any("Run `tls-edge configure`" in line for line in runtime.output.lines)


def test_cli_builder_main_and_version_failure(monkeypatch, capsys):
    identity = CliIdentity("TLS EDGE", "3.1.0", "Configure a shared edge", "tls-edge")
    monkeypatch.setattr(cli, "_identity", lambda: identity)
    assert cli.build_cli() is not None

    class App:
        def run(self, *, argv, interactive_extra):
            assert argv == ["show"]
            assert interactive_extra == "tls-edge-cli[interactive]"
            return 7

    monkeypatch.setattr(cli, "build_cli", lambda: App())
    assert cli.main(["show"]) == 7

    class VersionFailure(Exception):
        pass

    monkeypatch.setattr(cli, "VersionLookupError", VersionFailure)
    monkeypatch.setattr(cli, "build_cli", lambda: (_ for _ in ()).throw(VersionFailure("missing metadata")))
    assert cli.main([]) == 2
    assert "cannot determine tls-edge-cli version" in capsys.readouterr().err


def test_module_entrypoint_runs_main(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tls-edge", "--help"])
    monkeypatch.setattr(cli.CliIdentity, "from_distribution", staticmethod(lambda **kwargs: CliIdentity(
        kwargs["name"], "3.1.0", kwargs["long_name"], kwargs["command"]
    )))
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("tls_edge_cli.cli", run_name="__main__")
    assert exc.value.code == 0
