"""Cross-package oracles for the P-edge-06 and P-edge-07 merged contract."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from _layout import instance_stack_dir
from _render import copy_tracked_stack, render_ciu_case
from test_calls_removal import _load_hook, _run_cli
from test_deployment_path import _fake_docker, _tree_digest


ROOT = Path(__file__).resolve().parents[1]


def _has_key(value: object, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_has_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_has_key(item, key) for item in value)
    return False


def _domain_override(stack: Path) -> None:
    (stack / "ciu.toml.j2").write_text(
        '[tls_edge]\ndomain = "edge.example.test"\n',
        encoding="utf-8",
    )


def test_disabled_schema_1_calls_migrates_under_the_stack_layout_and_hook_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack_dir, raw_config, compose = render_ciu_case(
        instance_stack_dir(tmp_path / "instance"),
        {
            "schema": 1,
            "domain": "edge.example.test",
            "calls": {"enabled": False, "public_port": 8443},
        },
    )
    hook = _load_hook(stack_dir)
    assert hook.run(raw_config, None) == {}
    assert 8443 not in {
        int(str(entry).rpartition("/")[0].rsplit(":", 2)[-2])
        for entry in compose["services"]["traefik"].get("ports", [])
    }

    result, output = _run_cli(
        monkeypatch,
        ["validate", "--root", str(stack_dir.parent)],
    )
    assert result == 0, output


def test_fresh_checkout_show_and_validate_expose_no_calls_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = copy_tracked_stack(tmp_path / "checkout")
    checkout = stack.parent
    _domain_override(stack)
    assert not (checkout / "consumer.env").exists()

    result, output = _run_cli(
        monkeypatch,
        ["show", "--root", str(checkout), "--json"],
    )
    assert result == 0, output
    payload = json.loads(output)
    assert not _has_key(payload, "calls")
    assert not _has_key(payload, "calls_enabled")
    assert not _has_key(payload, "calls_port")
    assert all(item["port"] != 8443 for item in payload["ports"])

    result, output = _run_cli(
        monkeypatch,
        ["validate", "--root", str(checkout)],
    )
    assert result == 0, output
    assert "calls" not in output.lower()
    assert "8443" not in output


def test_checkout_configure_and_certbot_hook_create_no_consumer_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = copy_tracked_stack(tmp_path / "checkout")
    checkout = stack.parent
    answers = tmp_path / "answers.toml"
    answers.write_text(
        '[tls_edge]\ndomain = "edge.example.test"\n',
        encoding="utf-8",
    )
    result, output = _run_cli(
        monkeypatch,
        [
            "configure",
            "--root",
            str(checkout),
            "--answers",
            str(answers),
            "--yes",
        ],
    )
    assert result == 0, output
    assert not (checkout / "consumer.env").exists()

    script = checkout / "scripts" / "certbot-deploy-hook.sh"
    script.parent.mkdir()
    shutil.copy2(ROOT / "scripts" / "certbot-deploy-hook.sh", script)
    before = _tree_digest(checkout)
    bin_dir, log = _fake_docker(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(log),
        "TRAEFIK_CONTAINER": "",
    }
    result = subprocess.run(
        ["bash", str(script)],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no running Traefik container found" in result.stdout
    assert not (checkout / "consumer.env").exists()
    assert before == _tree_digest(checkout)
