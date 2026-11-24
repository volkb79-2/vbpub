"""Behavioural tests for the schema-shaped configure answers grammar."""

from __future__ import annotations

import io
import shutil
import tomllib
from pathlib import Path

import pytest

from cli_extended import identity as identity_module
from cli_extended import prompts as prompts_module

from _layout import instance_stack_dir
from tls_edge_cli import cli
from tls_edge_cli.fields import Field


ROOT = Path(__file__).resolve().parents[1]


class TTYBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class FixedPrompts:
    def text(self, _message, *, default=None, required=True):
        return default if default is not None else "edge.example.test"

    def password(self, _message, *, required=True):
        return "unused"

    def confirm(self, _message, *, default=False):
        return default

    def select(self, _message, choices, *, default=None):
        return default if default is not None else choices[0]

    def checkbox(self, _message, choices, *, default=()):
        return list(default)


def make_root(path: Path) -> Path:
    root = instance_stack_dir(path)
    stack = root / "ciu-stack"
    stack.mkdir(parents=True)
    shutil.copy2(ROOT / "ciu-stack" / "ciu.defaults.toml.j2", stack)
    (root.parent / "current").symlink_to(root.parent / "releases" / "tls-edge")
    return root


def run_cli(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> tuple[int, str]:
    stdin = TTYBuffer()
    stdout = TTYBuffer()
    stderr = TTYBuffer()
    with monkeypatch.context() as context:
        context.setattr("sys.stdin", stdin)
        context.setattr("sys.stdout", stdout)
        context.setattr("sys.stderr", stderr)
        context.setattr(identity_module, "installed_version", lambda _name: "0.0.0")
        context.setattr(
            prompts_module,
            "_load_questionary_driver",
            lambda *_args, **_kwargs: FixedPrompts(),
        )
        result = cli.main(argv)
    return result, stdout.getvalue() + stderr.getvalue()


def write_override(root: Path, text: str) -> Path:
    path = root / "ciu-stack" / "ciu.toml.j2"
    path.write_text(text, encoding="utf-8")
    return path


def write_answers(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def flat_target(legacy_key: str) -> str:
    targets = {
        "domain": "[tls_edge] domain",
        "tls_mode": "[tls_edge.tls] mode",
        "acme_email": "[tls_edge.acme] email",
        "dns_provider": "[tls_edge.acme.dns] provider",
        "dns_wildcard": "[tls_edge.acme.dns] wildcard_main",
        "http_enabled": "[tls_edge.ports] expose_http",
        "http_port": "[tls_edge.ports] http",
        "https_port": "[tls_edge.ports] https",
        "http3_enabled": "[tls_edge.http3] enabled",
        "bind_ip": "[tls_edge.ports] bind_ip",
        "ingress_external": "[tls_edge] ingress_external",
        "acme_volume_name": "[tls_edge.acme] volume_name",
        "cgroup_parent": "[tls_edge.runtime] cgroup_parent",
        "static_cert_base": "[tls_edge.static] cert_base",
        "static_domains": "[tls_edge.static] domains",
        "static_source": "[tls_edge.static] source",
    }
    return targets[legacy_key]


def test_field_rejects_an_unknown_kind_at_construction() -> None:
    with pytest.raises(
        ValueError,
        match="unsupported field kind 'mystery' for tls_edge.domain",
    ):
        Field("domain", "mystery", None)


def test_choice_field_requires_choices_at_construction() -> None:
    with pytest.raises(
        ValueError,
        match="choice field tls_edge.tls.mode requires at least one choice",
    ):
        Field("tls.mode", "choice", None)


def test_sparse_answers_change_one_key_and_preserve_override_comments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    override = write_override(
        root,
        """\
# operator note: keep this comment
[tls_edge]
schema = 1
domain = "edge.example.test"

[tls_edge.traefik]
# unrelated site setting
log_level = "WARN"

[tls_edge.http3]
enabled = false
""",
    )
    answers = write_answers(
        tmp_path / "answers.toml",
        """\
# one deliberate override
[tls_edge.http3]
enabled = true
""",
    )

    result, output = run_cli(
        monkeypatch,
        ["configure", "--root", str(root), "--answers", str(answers), "--yes"],
    )
    assert result == 0, output

    text = override.read_text(encoding="utf-8")
    assert "# operator note: keep this comment" in text
    assert "# unrelated site setting" in text
    written = tomllib.loads(text)["tls_edge"]
    assert written == {
        "schema": 2,
        "domain": "edge.example.test",
        "tls": {"mode": "acme-tls"},
        "acme": {"email": "", "volume_name": "", "store": "volume"},
        "ports": {
            "expose_http": True,
            "http": 80,
            "https": 443,
            "bind_ip": "",
        },
        "http3": {"enabled": True},
        "ingress_external": False,
        "runtime": {"cgroup_parent": ""},
        "traefik": {"log_level": "WARN"},
    }


def test_unmanaged_answer_leaf_is_written_and_preserves_comments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    override = write_override(
        root,
        """\
# before the managed table
[tls_edge]
schema = 1
domain = "edge.example.test"

[tls_edge.traefik]
# keep this neighbour before log_level
name = "traefik"
# keep this neighbour after log_level
image = "traefik:v3.7"
""",
    )
    answers = write_answers(
        tmp_path / "answers.toml",
        """\
[tls_edge.traefik]
log_level = "DEBUG"
""",
    )
    result, output = run_cli(
        monkeypatch,
        ["configure", "--root", str(root), "--answers", str(answers), "--yes"],
    )
    assert result == 0, output
    text = override.read_text(encoding="utf-8")
    assert "# before the managed table" in text
    assert "# keep this neighbour before log_level" in text
    assert "# keep this neighbour after log_level" in text
    written = tomllib.loads(text)["tls_edge"]
    assert written["traefik"]["log_level"] == "DEBUG"


@pytest.mark.parametrize("legacy_key", [
    "domain",
    "tls_mode",
    "acme_email",
    "dns_provider",
    "dns_wildcard",
    "http_enabled",
    "http_port",
    "https_port",
    "http3_enabled",
    "bind_ip",
    "ingress_external",
    "acme_volume_name",
    "cgroup_parent",
    "static_cert_base",
    "static_domains",
    "static_source",
])
def test_all_3_1_flat_answer_keys_are_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    legacy_key: str,
) -> None:
    root = make_root(tmp_path / "instance")
    answers = write_answers(tmp_path / "answers.toml", f"{legacy_key} = true\n")
    result, output = run_cli(
        monkeypatch,
        ["configure", "--root", str(root), "--answers", str(answers), "--dry-run", "--yes"],
    )
    assert result == 2, output
    expected = (
        f"answers key {legacy_key!r} is the 3.1 flat form; "
        f"write {flat_target(legacy_key)}"
    )
    assert expected in output


def test_all_3_1_flat_answer_keys_are_reported_in_one_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    answers = write_answers(
        tmp_path / "answers.toml",
        """\
domain = "edge.example.test"
http_enabled = true
static_domains = []
""",
    )
    result, output = run_cli(
        monkeypatch,
        ["configure", "--root", str(root), "--answers", str(answers), "--dry-run", "--yes"],
    )
    assert result == 2, output
    for legacy_key in ("domain", "http_enabled", "static_domains"):
        expected = (
            f"answers key {legacy_key!r} is the 3.1 flat form; "
            f"write {flat_target(legacy_key)}"
        )
        assert expected in output


def test_unknown_answers_key_names_the_schema_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    answers = write_answers(
        tmp_path / "answers.toml",
        """\
[tls_edge.http3]
enable = true
""",
    )
    result, output = run_cli(
        monkeypatch,
        ["configure", "--root", str(root), "--answers", str(answers), "--dry-run", "--yes"],
    )
    assert result == 2, output
    assert (
        f"unknown answers key 'tls_edge.http3.enable' "
        f"(answers file {answers})"
    ) in output
    assert "tls_edge.http3.enabled" in output


def test_unknown_top_level_answers_key_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    answers = write_answers(tmp_path / "answers.toml", "mystery = true\n")
    result, output = run_cli(
        monkeypatch,
        ["configure", "--root", str(root), "--answers", str(answers), "--dry-run", "--yes"],
    )
    assert result == 2, output
    assert (
        f"unknown answers key 'mystery' (answers file {answers}); "
        "answers use the site-override shape "
        "[tls_edge.<table>]"
    ) in output


def test_dry_run_prints_diff_and_port_plan_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    override = write_override(
        root,
        """\
# dry-run must preserve this
[tls_edge]
schema = 1
domain = "edge.example.test"
""",
    )
    env_path = root / "consumer.env"
    env_path.write_text("TLS_EDGE_DOMAIN=edge.example.test\nOTHER=keep\n", encoding="utf-8")
    before_override = override.read_bytes()
    before_env = env_path.read_bytes()

    answers = write_answers(
        tmp_path / "answers.toml",
        """\
[tls_edge.http3]
enabled = true
""",
    )
    result, output = run_cli(
        monkeypatch,
        ["configure", "--root", str(root), "--answers", str(answers), "--dry-run", "--yes"],
    )
    assert result == 0, output
    assert "Proposed configuration changes:" in output
    assert "Inbound firewall ports:" in output
    assert "443/udp (HTTP/3 over QUIC)" in output
    assert "Dry run: no files were changed." in output
    assert override.read_bytes() == before_override
    assert env_path.read_bytes() == before_env


def test_answers_mode_uses_a_nonempty_default_for_static_lineages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    defaults = root / "ciu-stack/ciu.defaults.toml.j2"
    defaults.write_text(
        defaults.read_text(encoding="utf-8").replace(
            "domains = []",
            'domains = ["default.example.test"]',
        ),
        encoding="utf-8",
    )
    answers = write_answers(
        tmp_path / "answers.toml",
        """\
[tls_edge]
domain = "edge.example.test"

[tls_edge.tls]
mode = "static"
""",
    )
    result, output = run_cli(
        monkeypatch,
        [
            "configure",
            "--root",
            str(root),
            "--answers",
            str(answers),
            "--yes",
        ],
    )
    assert result == 0, output
    written = tomllib.loads(
        (root / "ciu-stack" / "ciu.toml.j2").read_text(encoding="utf-8")
    )
    assert written["tls_edge"]["static"]["domains"] == ["default.example.test"]


def test_hostdir_without_a_new_instance_marker_requires_a_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    answers = write_answers(
        tmp_path / "answers.toml",
        """\
[tls_edge]
domain = "edge.example.test"

[tls_edge.acme]
store = "hostdir"
""",
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
    assert "tls_edge.traefik.hostdir.acme.path is required" in output


def test_configure_reports_a_consumer_env_config_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path / "instance")
    env_path = root / "consumer.env"
    env_path.symlink_to(tmp_path / "unrelated-target")
    answers = write_answers(
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
    assert "consumer environment must be a regular file" in output
