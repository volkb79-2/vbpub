"""Boundary tests for configuring and inspecting a tls-edge instance."""

from __future__ import annotations

import io
import runpy
import shutil
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path

import pytest

from cli_extended import identity as identity_module
from cli_extended import prompts as prompts_module

from _layout import stack_dir
from tls_edge_cli import cli


ROOT = Path(__file__).resolve().parents[1]


class TTYBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class FixedPrompts:
    def __init__(
        self,
        *,
        confirm_value: bool = True,
        select_value: str | None = None,
        text_responses: Mapping[str, str] | None = None,
    ) -> None:
        self.confirm_value = confirm_value
        self.select_value = select_value
        self.text_responses = dict(text_responses or {})

    def text(self, message, *, default=None, required=True):
        if message in self.text_responses:
            return self.text_responses[message]
        return default if default is not None else "edge.example.test"

    def password(self, _message, *, required=True):
        return "unused"

    def confirm(self, _message, *, default=False):
        return self.confirm_value

    def select(self, _message, choices, *, default=None):
        if self.select_value is not None and self.select_value in choices:
            return self.select_value
        return default if default is not None else choices[0]

    def checkbox(self, _message, choices, *, default=()):
        return list(default)


def make_root(path: Path) -> Path:
    root = stack_dir(path)
    stack_dir = root / "ciu-stack"
    stack_dir.mkdir(parents=True)
    shutil.copy2(ROOT / "ciu-stack" / "ciu.defaults.toml.j2", stack_dir)
    (root.parent / "current").symlink_to(root.parent / "releases" / "tls-edge")
    return root


def answer_file(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def run_cli(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    *,
    confirm_value: bool = True,
    version_lookup=None,
    select_value: str | None = None,
    text_responses: Mapping[str, str] | None = None,
) -> tuple[int, str]:
    stdin = TTYBuffer()
    stdout = TTYBuffer()
    stderr = TTYBuffer()
    with monkeypatch.context() as context:
        context.setattr(sys, "stdin", stdin)
        context.setattr(sys, "stdout", stdout)
        context.setattr(sys, "stderr", stderr)
        context.setattr(
            identity_module,
            "installed_version",
            version_lookup or (lambda _name: "0.0.0"),
        )
        context.setattr(
            prompts_module,
            "_load_questionary_driver",
            lambda *_args, **_kwargs: FixedPrompts(
                confirm_value=confirm_value,
                select_value=select_value,
                text_responses=text_responses,
            ),
        )
        result = cli.main(argv)
    return result, stdout.getvalue() + stderr.getvalue()


def test_configure_dry_run_decline_write_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    answers = answer_file(
        tmp_path / "answers.toml",
        '[tls_edge]\ndomain = "edge.example.org"\n',
    )

    result, output = run_cli(
        monkeypatch,
        ["configure", "--root", str(root), "--answers", str(answers), "--dry-run"],
    )
    assert result == 0, output
    assert "Dry run: no files were changed." in output
    assert not (root / "ciu-stack/ciu.toml.j2").exists()
    assert not (root / "consumer.env").exists()

    result, output = run_cli(
        monkeypatch,
        ["configure", "--root", str(root), "--answers", str(answers)],
        confirm_value=False,
    )
    assert result == 0, output
    assert "Configuration was not changed." in output
    assert not (root / "consumer.env").exists()

    result, output = run_cli(
        monkeypatch,
        ["configure", "--root", str(root), "--answers", str(answers), "--yes"],
    )
    assert result == 0, output
    override = root / "ciu-stack/ciu.toml.j2"
    assert 'domain = "edge.example.org"' in override.read_text(encoding="utf-8")
    assert (root / "consumer.env").read_text(encoding="utf-8") == (
        "TLS_EDGE_DOMAIN=edge.example.org\n"
        "TLS_EDGE_NETWORK=ingress_public\n"
        "TLS_EDGE_HTTPS_ENTRYPOINT=websecure\n"
        "TLS_EDGE_HEADERS_MIDDLEWARE=secure-headers@file\n"
        "TLS_EDGE_HTTP3=false\n"
        "TLS_EDGE_PUBLIC_PORTS=80/tcp,443/tcp\n"
    )
    assert "sudo ./ciu check" in output

    result, output = run_cli(
        monkeypatch,
        ["configure", "--root", str(root), "--answers", str(answers), "--yes"],
    )
    assert result == 0, output
    assert "Configuration already matches these answers." in output


def test_configure_writes_dns_and_static_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    answers = answer_file(
        tmp_path / "answers.toml",
        """\
[tls_edge]
domain = "meet.example.org"
ingress_external = true

[tls_edge.tls]
mode = "acme-dns"

[tls_edge.acme]
email = "ops@example.org"

[tls_edge.acme.dns]
provider = "cloudflare"
wildcard_main = "example.org"

[tls_edge.http3]
enabled = true

[tls_edge.ports]
bind_ip = "127.0.0.1"

[tls_edge.runtime]
cgroup_parent = "dev-background.slice"
""",
    )
    result, output = run_cli(
        monkeypatch,
        ["configure", "--root", str(root), "--answers", str(answers), "--yes"],
    )
    assert result == 0, output
    document = tomllib.loads(
        (root / "ciu-stack/ciu.toml.j2").read_text(encoding="utf-8")
    )
    settings = document["tls_edge"]
    assert settings["acme"]["email"] == "ops@example.org"
    assert settings["acme"]["dns"] == {
        "provider": "cloudflare",
        "wildcard_main": "example.org",
    }
    assert settings["http3"]["enabled"] is True
    assert settings["ingress_external"] is True
    assert settings["ports"]["bind_ip"] == "127.0.0.1"
    assert settings["runtime"]["cgroup_parent"] == "dev-background.slice"

    static_root = make_root(tmp_path / "static")
    static_answers = answer_file(
        tmp_path / "static.toml",
        """\
[tls_edge]
domain = "static.example.org"

[tls_edge.tls]
mode = "static"

[tls_edge.static]
cert_base = "/srv/certs"
domains = ["static.example.org"]
source = "volume"
""",
    )
    result, output = run_cli(
        monkeypatch,
        [
            "configure",
            "--root",
            str(static_root),
            "--answers",
            str(static_answers),
            "--yes",
        ],
    )
    assert result == 0, output
    static_doc = tomllib.loads(
        (static_root / "ciu-stack/ciu.toml.j2").read_text(encoding="utf-8")
    )
    assert static_doc["tls_edge"]["static"] == {
        "cert_base": "/srv/certs",
        "domains": ["static.example.org"],
        "source": "volume",
    }

    dev_root = make_root(tmp_path / "dev")
    dev_answers = answer_file(
        tmp_path / "dev.toml",
        """\
[tls_edge]
domain = "dev.example.org"

[tls_edge.tls]
mode = "dev"
""",
    )
    result, output = run_cli(
        monkeypatch,
        [
            "configure",
            "--root",
            str(dev_root),
            "--answers",
            str(dev_answers),
            "--yes",
        ],
    )
    assert result == 0, output
    dev_doc = tomllib.loads(
        (dev_root / "ciu-stack/ciu.toml.j2").read_text(encoding="utf-8")
    )
    assert dev_doc["tls_edge"]["static"]["domains"] == ["dev.example.org"]


def test_interactive_configure_and_cross_field_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    override = root / "ciu-stack/ciu.toml.j2"
    override.write_text(
        '[tls_edge]\ndomain = "edge.example.test"\n'
        '[tls_edge.tls]\nmode = "static"\n',
        encoding="utf-8",
    )

    result, output = run_cli(
        monkeypatch,
        ["configure", "--root", str(root), "--yes"],
        select_value="acme-tls",
    )
    assert result == 0, output
    written = tomllib.loads(override.read_text(encoding="utf-8"))
    assert written["tls_edge"]["schema"] == 2
    assert written["tls_edge"]["tls"]["mode"] == "acme-tls"


def test_interactive_prompt_refuses_a_non_decimal_port(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    result, output = run_cli(
        monkeypatch,
        ["configure", "--root", str(root), "--dry-run", "--yes"],
        text_responses={"Public HTTP port": "not-a-port"},
    )
    assert result == 2, output
    assert "tls_edge.ports.http must be an integer from 1 through 65535" in output
    assert "(interactive prompt)" in output


def test_interactive_static_lineages_are_parsed_from_the_list_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    result, output = run_cli(
        monkeypatch,
        ["configure", "--root", str(root), "--yes"],
        select_value="static",
        text_responses={
            "Certificate lineage directory names, comma separated": (
                "one.example.test, two.example.test"
            )
        },
    )
    assert result == 0, output
    written = tomllib.loads(
        (root / "ciu-stack" / "ciu.toml.j2").read_text(encoding="utf-8")
    )
    assert written["tls_edge"]["static"]["domains"] == [
        "one.example.test",
        "two.example.test",
    ]


@pytest.mark.parametrize("mode", ["static", "dev"])
def test_interactive_static_modes_default_lineages_to_domain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    root = make_root(tmp_path / "instance")
    domain = f"{mode}.lineage.example.test"
    result, output = run_cli(
        monkeypatch,
        ["configure", "--root", str(root), "--yes"],
        select_value=mode,
        text_responses={
            "Base DNS domain for consumer host names": domain,
        },
    )
    assert result == 0, output
    written = tomllib.loads(
        (root / "ciu-stack" / "ciu.toml.j2").read_text(encoding="utf-8")
    )
    assert written["tls_edge"]["static"]["domains"] == [domain]


def test_configure_reports_a_failure_after_the_override_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    answers = answer_file(
        tmp_path / "answers.toml",
        '[tls_edge]\ndomain = "edge.example.test"\n',
    )
    root.chmod(0o500)
    try:
        result, output = run_cli(
            monkeypatch,
            ["configure", "--root", str(root), "--answers", str(answers), "--yes"],
        )
    finally:
        root.chmod(0o700)
    assert result == 2, output
    assert "could not finish writing configuration" in output
    assert "inspect" in output


@pytest.mark.parametrize(
    ("answers", "message"),
    [
        (
            '[tls_edge]\ndomain = "edge.example.test"\n'
            '[tls_edge.http3]\nenabled = "yes"\n',
            "tls_edge.http3.enabled must be true or false",
        ),
        (
            '[tls_edge]\ndomain = "bad domain"\n',
            "tls_edge.domain is not a valid DNS name",
        ),
        (
            "[tls_edge]\ndomain = 12\n",
            "tls_edge.domain must be a string",
        ),
        (
            '[tls_edge]\ndomain = ""\n',
            "tls_edge.domain is required",
        ),
        (
            '[tls_edge]\ndomain = "edge.example.test"\n'
            '[tls_edge.tls]\nmode = "other"\n',
            "tls_edge.tls.mode must be one of",
        ),
        (
            '[tls_edge]\ndomain = "edge.example.test"\n'
            "[tls_edge.ports]\nhttps = true\n",
            "tls_edge.ports.https must be an integer from 1 through 65535",
        ),
        (
            '[tls_edge]\ndomain = "edge.example.test"\n'
            "[tls_edge.ports]\nhttps = 65536\n",
            "tls_edge.ports.https must be an integer from 1 through 65535",
        ),
        (
            '[tls_edge]\ndomain = "edge.example.test"\n'
            '[tls_edge.tls]\nmode = "static"\n'
            "[tls_edge.static]\ndomains = \"edge.example.test\"\n",
            "tls_edge.static.domains must be a list",
        ),
        (
            '[tls_edge]\ndomain = "edge.example.test"\n'
            '[tls_edge.tls]\nmode = "static"\n'
            '[tls_edge.acme]\nemail = "ops@example.test"\n',
            "tls_edge.acme.email applies only when tls_edge.tls.mode starts with acme-",
        ),
    ],
)
def test_configure_boundary_refusals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    answers: str,
    message: str,
) -> None:
    root = make_root(tmp_path / "instance")
    answers_path = answer_file(tmp_path / "answers.toml", answers)
    result, output = run_cli(
        monkeypatch,
        [
            "configure",
            "--root",
            str(root),
            "--answers",
            str(answers_path),
            "--dry-run",
            "--yes",
        ],
    )
    assert result == 2, output
    assert message in output


def test_show_and_validate_valid_empty_and_invalid_instances(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    result, output = run_cli(monkeypatch, ["show", "--root", str(root)])
    assert result == 0, output
    assert "Site override: not configured" in output
    assert "Inbound ports:" in output
    assert "Run `tls-edge configure`" in output

    result, output = run_cli(monkeypatch, ["validate", "--root", str(root)])
    assert result != 0, output
    assert "domain is required" in output

    override = root / "ciu-stack/ciu.toml.j2"
    override.write_text(
        '[tls_edge]\ndomain = "custom.example.org"\n'
        "[tls_edge.http3]\nenabled = true\n",
        encoding="utf-8",
    )
    result, output = run_cli(monkeypatch, ["show", "--root", str(root)])
    assert result == 0, output
    assert "Site override: present" in output
    assert "HTTP/3: enabled" in output
    result, output = run_cli(monkeypatch, ["validate", "--root", str(root)])
    assert result == 0, output
    assert "443/udp (HTTP/3 over QUIC)" in output

    override.write_text(
        '[tls_edge]\ndomain = "custom.example.org"\n'
        '[tls_edge.tls]\nmode = "wrong"\n',
        encoding="utf-8",
    )
    result, output = run_cli(monkeypatch, ["show", "--root", str(root)])
    assert result == 2, output
    assert "tls.mode" in output
    result, output = run_cli(monkeypatch, ["validate", "--root", str(root)])
    assert result == 2, output
    assert "tls.mode" in output


def test_main_reports_version_lookup_and_root_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_version(_name: str) -> str:
        raise identity_module.PackageNotFoundError("missing")

    result, output = run_cli(
        monkeypatch,
        ["--help"],
        version_lookup=missing_version,
    )
    assert result == 2, output
    assert "cannot determine tls-edge-cli version" in output

    result, output = run_cli(
        monkeypatch,
        ["show", "--root", str(tmp_path / "missing")],
    )
    assert result == 2, output
    assert "not a tls-edge stack directory" in output


def test_configure_refuses_defaults_that_omit_a_managed_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    defaults = root / "ciu-stack/ciu.defaults.toml.j2"
    defaults.write_text(
        '[tls_edge]\ndomain = "edge.example.test"\n',
        encoding="utf-8",
    )
    answers = answer_file(
        tmp_path / "answers.toml",
        '[tls_edge]\ndomain = "edge.example.test"\n',
    )
    result, output = run_cli(
        monkeypatch,
        [
            "configure",
            "--root",
            str(root),
            "--answers",
            str(answers),
            "--dry-run",
            "--yes",
        ],
    )
    assert result == 2, output
    assert "tls_edge.tls.mode is missing from the effective configuration" in output


def test_module_entrypoint_runs_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["tls-edge", "--help"])
    monkeypatch.setattr(identity_module, "installed_version", lambda _name: "0.0.0")
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("tls_edge_cli.cli", run_name="__main__")
    assert exc.value.code == 0
