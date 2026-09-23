from __future__ import annotations

import json
import shlex
import urllib.request
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

import pytest

from debian_install_v2.bootstrap import main
from debian_install_v2.config import Config, load_config
from debian_install_v2.customscript import (
    CONTROLLER_SSH_PUBKEY_MARKER,
    build_customscript_bundle,
)


def _launcher_from_command(command):
    tokens = shlex.split(command)
    python_index = tokens.index("python3")
    assert tokens[python_index + 1] == "-c"
    environment = dict(
        token.split("=", 1) for token in tokens[:python_index]
    )
    return environment, tokens[python_index + 2]


def test_bundle_uses_valid_config_and_resolves_the_configured_bootstrap_source():
    config = load_config(
        raw_json=json.dumps({
            "state_dir": "/srv/vbpub/state",
            "run_docker_install": False,
            "credential_mode": "systemd",
        })
    )
    bundle = build_customscript_bundle(config, repo_branch="feature/install-v2")

    assert bundle["config"]["run_docker_install"] is False
    assert bundle["completionMarker"] == "/srv/vbpub/state/stage2_done"
    command = bundle["customScript"]
    assert "feature/install-v2/scripts/debian-install-v2/bootstrap-remote.py" in command
    assert "curl" not in command
    assert " | " not in command
    env, launcher = _launcher_from_command(command)
    assert env["REPO_BRANCH"] == "feature/install-v2"
    assert load_config(raw_json=env["VBPUB_CONFIG_EXTRA_JSON"]) == config
    assert "urllib.request.urlopen" in launcher
    assert "exec(compile(source" in launcher


def test_python_launcher_reports_download_failure_and_returns_nonzero(monkeypatch):
    bundle = build_customscript_bundle(Config())
    _environment, launcher = _launcher_from_command(bundle["customScript"])

    def fail_download(*args, **kwargs):
        raise OSError("network unavailable")

    monkeypatch.setattr(urllib.request, "urlopen", fail_download)
    stderr = StringIO()
    with redirect_stderr(stderr), pytest.raises(SystemExit) as error:
        exec(compile(launcher, "<generated-bootstrap-launcher>", "exec"), {
            "__name__": "__main__",
            "__file__": "<generated-bootstrap-launcher>",
        })

    assert error.value.code == 1
    assert "bootstrap download failed: network unavailable" in stderr.getvalue()
    assert "Traceback" not in stderr.getvalue()


def test_python_launcher_rejects_an_empty_download(monkeypatch):
    bundle = build_customscript_bundle(Config())
    _environment, launcher = _launcher_from_command(bundle["customScript"])

    class EmptyResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read():
            return b"\n \t"

    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: EmptyResponse())
    stderr = StringIO()
    with redirect_stderr(stderr), pytest.raises(SystemExit) as error:
        exec(compile(launcher, "<generated-bootstrap-launcher>", "exec"), {
            "__name__": "__main__",
            "__file__": "<generated-bootstrap-launcher>",
        })

    assert error.value.code == 1
    assert "downloaded bootstrap source was empty" in stderr.getvalue()


def test_python_launcher_executes_downloaded_bootstrap(monkeypatch, capsys):
    bundle = build_customscript_bundle(Config())
    _environment, launcher = _launcher_from_command(bundle["customScript"])

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read():
            return b"print('bootstrap source executed')"

    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: Response())
    exec(compile(launcher, "<generated-bootstrap-launcher>", "exec"), {
        "__name__": "__main__",
        "__file__": "<generated-bootstrap-launcher>",
    })
    assert capsys.readouterr().out.strip() == "bootstrap source executed"


def test_bundle_can_emit_provider_replacement_marker():
    bundle = build_customscript_bundle(Config(), controller_ssh_placeholder=True)
    assert bundle["config"]["controller_ssh_pubkey"] == CONTROLLER_SSH_PUBKEY_MARKER


def test_placeholder_refuses_to_replace_an_existing_configured_key():
    with pytest.raises(ValueError, match="must be empty"):
        build_customscript_bundle(
            Config(controller_ssh_pubkey="ssh-ed25519 AAAA operator"),
            controller_ssh_placeholder=True,
        )


def test_custom_repository_requires_explicit_bootstrap_url():
    with pytest.raises(ValueError, match="bootstrap_url is required"):
        build_customscript_bundle(Config(), repo_url="https://git.example.test/ops/vbpub")


@pytest.mark.parametrize(
    ("field", "value"),
    (("repo_url", ""), ("repo_branch", ""), ("bootstrap_url", "")),
)
def test_explicit_empty_bootstrap_source_is_rejected(field, value):
    with pytest.raises(ValueError, match="non-empty"):
        build_customscript_bundle(Config(), **{field: value})


def test_cli_build_customscript_prints_a_valid_bundle(capsys, tmp_path):
    config_path = tmp_path / "install.json"
    config_path.write_text(json.dumps({"schema_version": 1, "fresh_install": True}))

    assert main([
        "build-customscript",
        "--config", str(config_path),
        "--controller-ssh-placeholder",
    ]) == 0
    bundle = json.loads(capsys.readouterr().out)
    assert bundle["config"]["controller_ssh_pubkey"] == CONTROLLER_SSH_PUBKEY_MARKER


def test_cli_protects_secret_bearing_bundle_output(capsys, tmp_path):
    config_path = tmp_path / "install.json"
    config_path.write_text(json.dumps({
        "schema_version": 1,
        "fresh_install": True,
        "telegram_bot_token": "123:private-token",
        "telegram_chat_id": "chat-42",
    }))

    assert main(["build-customscript", "--config", str(config_path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--debug-raw" in captured.err
    assert "123:private-token" not in captured.err

    assert main([
        "build-customscript", "--config", str(config_path), "--debug-raw"
    ]) == 0
    raw = json.loads(capsys.readouterr().out)
    assert raw["config"]["telegram_bot_token"] == "123:private-token"


def test_customscript_source_is_not_written_outside_the_cli_output(tmp_path, monkeypatch):
    # This module is a pure renderer: it returns the command/config bundle and
    # does not create files or make network/API calls.
    monkeypatch.chdir(tmp_path)
    build_customscript_bundle(Config())
    assert not Path("remote-install-config.json").exists()
