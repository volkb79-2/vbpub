from __future__ import annotations

import json
import shlex

import pytest

from debian_install_v2.config import Config, load_config
from debian_install_v2.customscript import build_customscript_bundle
from debian_install_v2.bootstrap import main


def test_bundle_contains_valid_v2_config_and_provider_command():
    config = load_config(
        raw_json=json.dumps({
            "notify_backend": "none",
            "run_docker_install": False,
        })
    )
    bundle = build_customscript_bundle(
        config,
        controller_ssh_placeholder=True,
    )

    assert bundle["config"]["run_docker_install"] is False
    assert bundle["config"]["controller_ssh_pubkey"] == "{{CONTROLLER_SSH_PUBKEY}}"
    assert bundle["completionMarker"] == "/var/lib/vbpub/bootstrap/stage2_done"
    command = bundle["customScript"]
    assert command.startswith(
        "curl -fsSL https://raw.githubusercontent.com/volkb79-2/vbpub/main/"
    )
    env_text = command.split(" | ", 1)[1].rsplit(" python3 -", 1)[0]
    env = dict(item.split("=", 1) for item in shlex.split(env_text))
    assert env["REPO_BRANCH"] == "main"
    assert json.loads(env["VBPUB_CONFIG_EXTRA_JSON"])["run_docker_install"] is False


def test_bundle_can_render_a_manual_key_without_placeholder():
    config = Config(controller_ssh_pubkey="ssh-ed25519 AAAA operator")
    bundle = build_customscript_bundle(config)
    rendered = json.loads(
        shlex.split(bundle["customScript"].split(" | ", 1)[1])[2].split("=", 1)[1]
    )
    assert rendered["controller_ssh_pubkey"] == "ssh-ed25519 AAAA operator"
    assert "{{CONTROLLER_SSH_PUBKEY}}" not in bundle["customScript"]


def test_placeholder_cannot_override_a_configured_key():
    with pytest.raises(ValueError, match="must be empty"):
        build_customscript_bundle(
            Config(controller_ssh_pubkey="ssh-ed25519 AAAA operator"),
            controller_ssh_placeholder=True,
        )


def test_custom_repo_requires_explicit_bootstrap_url():
    with pytest.raises(ValueError, match="bootstrap_url is required"):
        build_customscript_bundle(
            Config(),
            repo_url="https://git.example.test/ops/vbpub",
        )


def test_build_customscript_action_emits_bundle(capsys):
    rc = main([
        "--action",
        "build-customscript",
        "--config-json",
        json.dumps({"notify_backend": "none", "fresh_install": True}),
        "--controller-ssh-placeholder",
    ])

    assert rc == 0
    bundle = json.loads(capsys.readouterr().out)
    assert bundle["config"]["controller_ssh_pubkey"] == "{{CONTROLLER_SSH_PUBKEY}}"
    assert isinstance(bundle["customScript"], str)
