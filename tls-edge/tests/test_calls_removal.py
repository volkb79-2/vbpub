"""Boundary tests for schema 2 and removal of the Calls preset."""

from __future__ import annotations

import importlib.util
import io
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from _render import copy_tracked_stack, render_ciu_case
from cli_extended import identity as identity_module
from cli_extended import prompts as prompts_module
from conftest import ROOT, defaults_config, render_to
from tls_edge_cli import cli


TRACKED_GLOBAL_DEFAULTS = ROOT / "ciu.global.defaults.toml.j2"
CALLS_TRUE = (
    "tls_edge.calls.enabled = true: tls-edge 4.0 no longer forwards media; "
    "publish the media port from the application stack "
    "(Mattermost Calls: voice-screen-share/mattermost-server publishes "
    "TCP+UDP directly), then remove [tls_edge.calls] from"
)


class TTYBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class RecordingPrompts:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def text(self, message, *, default=None, required=True):
        self.messages.append(message)
        return default if default is not None else "edge.example.test"

    def password(self, message, *, required=True):
        self.messages.append(message)
        return "unused"

    def confirm(self, message, *, default=False):
        self.messages.append(message)
        return default

    def select(self, message, choices, *, default=None):
        self.messages.append(message)
        return default if default is not None else choices[0]

    def checkbox(self, message, choices, *, default=()):
        self.messages.append(message)
        return list(default)


def _instance(root: Path, override: str) -> tuple[Path, Path]:
    stack_dir = copy_tracked_stack(root / "stack")
    (root / "stack" / "ciu.global.defaults.toml.j2").write_text(
        TRACKED_GLOBAL_DEFAULTS.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (stack_dir / "ciu.toml.j2").write_text(override, encoding="utf-8")
    return root, stack_dir


def _run_cli(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    prompts: RecordingPrompts | None = None,
) -> tuple[int, str]:
    selected_prompts = prompts or RecordingPrompts()
    stdin = TTYBuffer()
    stdout = TTYBuffer()
    stderr = TTYBuffer()
    with monkeypatch.context() as context:
        context.setattr(sys, "stdin", stdin)
        context.setattr(sys, "stdout", stdout)
        context.setattr(sys, "stderr", stderr)
        context.setattr(identity_module, "installed_version", lambda _name: "0.0.0")
        context.setattr(
            prompts_module,
            "_load_questionary_driver",
            lambda *_args, **_kwargs: selected_prompts,
        )
        result = cli.main(argv)
    return result, stdout.getvalue() + stderr.getvalue()


def _run_standalone(stack_dir: Path, out_dir: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["TLS_EDGE_PHYSICAL_DIR"] = str(ROOT)
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "render_standalone.py"),
            "--stack-dir",
            str(stack_dir),
            "--out",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


def _load_hook(stack_dir: Path):
    spec = importlib.util.spec_from_file_location(
        "calls_removal_validate_edge",
        stack_dir / "hooks" / "validate_edge.py",
    )
    assert spec is not None and spec.loader is not None
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    return hook


def _published_ports(compose: Mapping[str, Any]) -> set[int]:
    published: set[int] = set()
    for entry in compose["services"]["traefik"].get("ports", []):
        if isinstance(entry, Mapping):
            published.add(int(entry["published"]))
        else:
            spec = str(entry).rpartition("/")[0]
            published.add(int(spec.rsplit(":", 2)[-2]))
    return published


def _has_key(value: object, key: str) -> bool:
    if isinstance(value, Mapping):
        return key in value or any(_has_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_has_key(item, key) for item in value)
    return False


def _override(schema: str, calls: str = "", body: str = "") -> str:
    return (
        "[tls_edge]\n"
        f"{schema}"
        'domain = "edge.example.test"\n'
        f"{body}"
        f"{calls}"
    )


def test_schema_reading_and_refusals_through_validate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for case, schema in (
        ("absent", ""),
        ("current", "schema = 2\n"),
    ):
        root, _ = _instance(
            tmp_path / case,
            _override(schema),
        )
        result, output = _run_cli(
            monkeypatch,
            ["validate", "--root", str(root)],
        )
        assert result == 0, output

    for case, schema in (
        ("zero", "schema = 0\n"),
        ("negative", "schema = -1\n"),
        ("bool", "schema = true\n"),
        ("string", 'schema = "2"\n'),
    ):
        root, _ = _instance(
            tmp_path / case,
            _override(schema),
        )
        result, output = _run_cli(
            monkeypatch,
            ["validate", "--root", str(root)],
        )
        assert result == 2, output
        assert "tls_edge.schema must be an integer from 1 to 2" in output

    root, _ = _instance(
        tmp_path / "future",
        _override("schema = 3\n"),
    )
    result, output = _run_cli(
        monkeypatch,
        ["validate", "--root", str(root)],
    )
    assert result == 2, output
    assert "tls_edge.schema 3 is newer than this release supports (maximum 2)" in output


def test_disabled_3_1_calls_migrates_on_hook_and_both_renderers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    override = {
        "schema": 1,
        "domain": "edge.example.test",
        "calls": {"enabled": False, "public_port": 8443},
    }
    stack, stack_config, ciu_compose = render_ciu_case(
        tmp_path / "ciu",
        override,
    )
    findings = _load_hook(stack).validate_config(stack_config, None)
    assert findings == []
    assert 8443 not in _published_ports(ciu_compose)

    root, stack_dir = _instance(
        tmp_path / "instance",
        _override(
            "schema = 1\n",
            "[tls_edge.calls]\nenabled = false\npublic_port = 8443\n",
        ),
    )
    result, output = _run_cli(
        monkeypatch,
        ["validate", "--root", str(root)],
    )
    assert result == 0, output

    standalone = _run_standalone(stack_dir, tmp_path / "standalone")
    assert standalone.returncode == 0, standalone.stdout + standalone.stderr
    compose = yaml.safe_load(
        (tmp_path / "standalone" / "docker-compose.yml").read_text(encoding="utf-8")
    )
    assert 8443 not in _published_ports(compose)


def test_enabled_3_1_calls_is_refused_on_hook_standalone_validate_and_configure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = "[tls_edge.calls]\nenabled = true\npublic_port = 8443\n"
    override = _override("schema = 1\n", calls)

    root, stack_dir = _instance(tmp_path / "instance", override)
    override_path_text = str(
        (stack_dir / "ciu.toml.j2").absolute()
    )
    hook = _load_hook(stack_dir)
    stack_config = {
        "tls_edge": {
            "schema": 1,
            "calls": {"enabled": True, "public_port": 8443},
        }
    }
    findings = hook.validate_config(stack_config, None)
    assert len(findings) == 1
    assert CALLS_TRUE in findings[0]
    assert override_path_text in findings[0]

    result, output = _run_cli(
        monkeypatch,
        ["validate", "--root", str(root)],
    )
    assert result == 2, output
    assert CALLS_TRUE in output
    assert override_path_text in output

    standalone = _run_standalone(stack_dir, tmp_path / "standalone")
    assert standalone.returncode != 0
    assert CALLS_TRUE in standalone.stdout + standalone.stderr

    answers = tmp_path / "answers.toml"
    answers.write_text(
        _override("schema = 1\n", calls),
        encoding="utf-8",
    )
    (stack_dir / "ciu.toml.j2").write_text(_override("schema = 1\n"), encoding="utf-8")
    result, output = _run_cli(
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
    assert CALLS_TRUE in output
    assert str(answers.absolute()) in output


@pytest.mark.parametrize(
    ("case", "override", "expected"),
    [
        (
            "extra-key",
            _override(
                "schema = 1\n",
                "[tls_edge.calls]\nenabled = false\npublic_port = 8443\nbandwidth = 1\n",
            ),
            "unknown override key tls_edge.calls.bandwidth",
        ),
        (
            "non-bool-enabled",
            _override(
                "schema = 1\n",
                '[tls_edge.calls]\nenabled = "false"\npublic_port = 8443\n',
            ),
            "unknown override key tls_edge.calls.enabled",
        ),
        (
            "non-table",
            _override("schema = 1\n", "calls = 8443\n"),
            "unknown override key tls_edge.calls",
        ),
        (
            "schema-2",
            _override(
                "schema = 2\n",
                "[tls_edge.calls]\nenabled = false\npublic_port = 8443\n",
            ),
            "unknown override key tls_edge.calls.enabled",
        ),
    ],
)
def test_other_calls_content_is_refused_as_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    override: str,
    expected: str,
) -> None:
    root, _ = _instance(tmp_path / case, override)
    result, output = _run_cli(
        monkeypatch,
        ["validate", "--root", str(root)],
    )
    assert result == 2, output
    clean = re.sub(r"\x1b\[[0-9;]*m", "", output)
    errors = [
        line.removeprefix("[ERROR] ")
        for line in clean.splitlines()
        if line.startswith("[ERROR] ")
    ]
    assert errors == [expected]


def test_configure_drops_a_migrated_calls_table_and_shows_both_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, stack_dir = _instance(
        tmp_path / "instance",
        _override(
            "schema = 1\n",
            "[tls_edge.calls]\nenabled = false\npublic_port = 8443\n",
        ),
    )
    answers = tmp_path / "answers.toml"
    answers.write_text('[tls_edge]\ndomain = "edge.example.test"\n', encoding="utf-8")

    result, output = _run_cli(
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
    assert result == 0, output
    assert "-schema = 1" in output
    assert "+schema = 2" in output
    assert "-[tls_edge.calls]" in output

    result, output = _run_cli(
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
        (stack_dir / "ciu.toml.j2").read_text(encoding="utf-8")
    )["tls_edge"]
    assert written["schema"] == 2
    assert "calls" not in written


@pytest.mark.parametrize(
    ("key", "value"),
    [("calls_enabled", "true"), ("calls_port", "8443")],
)
def test_removed_calls_flat_answer_key_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: str,
) -> None:
    root, _ = _instance(tmp_path / key, _override(""))
    answers = tmp_path / f"{key}.toml"
    answers.write_text(f"{key} = {value}\n", encoding="utf-8")
    result, output = _run_cli(
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
    clean = re.sub(r"\x1b\[[0-9;]*m", "", output)
    errors = [
        line.removeprefix("[ERROR] ")
        for line in clean.splitlines()
        if line.startswith("[ERROR] ")
    ]
    assert errors == [
        "the Calls preset was removed in 4.0; delete this key "
        "(Mattermost publishes media directly)"
    ]


def test_tracked_defaults_render_no_calls_defaults_ports_or_entrypoints(
    tmp_path: Path,
) -> None:
    _, stack_config, ciu_compose = render_ciu_case(tmp_path / "ciu")
    assert not _has_key(stack_config["tls_edge"], "calls")
    assert 8443 not in _published_ports(ciu_compose)

    rendered = render_to(tmp_path / "standalone", defaults_config())
    static = yaml.safe_load((rendered / "traefik.yml").read_text(encoding="utf-8"))
    assert not {
        name for name in static["entryPoints"] if "mattermost-calls" in name
    }
    compose = yaml.safe_load((rendered / "docker-compose.yml").read_text(encoding="utf-8"))
    assert 8443 not in _published_ports(compose)


def test_configure_prompt_list_has_no_calls_question(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _instance(tmp_path / "instance", "")
    prompts = RecordingPrompts()
    result, output = _run_cli(
        monkeypatch,
        ["configure", "--root", str(root), "--dry-run", "--yes"],
        prompts=prompts,
    )
    assert result == 0, output
    assert prompts.messages
    assert not [message for message in prompts.messages if "calls" in message.lower()]
