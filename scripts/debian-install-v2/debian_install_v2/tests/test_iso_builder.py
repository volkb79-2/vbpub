from __future__ import annotations

import base64
import json
import stat
from pathlib import Path

import pytest
from cli_extended import CliFailure

from debian_install_v2.bootstrap import main
from debian_install_v2.iso_builder import (
    _host_settings,
    _validate_ssh_keys_with_ssh_keygen,
    build_iso,
    render_nocloud_user_data,
)

VALID_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIcesQXRRI6R1benlU3hpLX712Iokf4htzSKpuk25Q2n "
    "fixture@example.test"
)
_KEY_TYPE = b"ssh-ed25519"
_KEY_BLOB_2 = (
    len(_KEY_TYPE).to_bytes(4, "big") + _KEY_TYPE
    + (32).to_bytes(4, "big") + bytes(range(32))
)
VALID_KEY_2 = f"ssh-ed25519 {base64.b64encode(_KEY_BLOB_2).decode()} backup@example.test"


def test_nocloud_seed_contains_private_payload_and_static_one_time_launcher():
    payload = {
        "schema_version": 1,
        "bootstrap_url": "https://raw.example.test/bootstrap-remote.py",
        "environment": {
            "VBPUB_PROFILE_KIND": "https",
            "VBPUB_PROFILE_URL": "https://profiles.example.test/private.jsonc",
            "VBPUB_PROFILE_AUTH_USERNAME": "admin",
            "VBPUB_PROFILE_AUTH_PASSWORD": "private-password",
            "VBPUB_CONFIG_EXTRA_JSON": json.dumps({"ssh_authorized_keys": [VALID_KEY]}),
        },
    }
    user_data, meta_data = render_nocloud_user_data(payload)
    cloud_config = json.loads(user_data.removeprefix("#cloud-config\n"))
    files = {entry["path"]: entry for entry in cloud_config["write_files"]}
    embedded = base64.b64decode(files["/run/vbpub-iso-payload.json"]["content"])
    decoded_payload = json.loads(embedded)

    assert decoded_payload == payload
    assert "private-password" not in user_data
    assert cloud_config["runcmd"] == [["/usr/bin/python3", "/run/vbpub-iso-bootstrap.py"]]
    launcher = base64.b64decode(files["/run/vbpub-iso-bootstrap.py"]["content"]).decode()
    assert "urllib.request.urlopen" in launcher
    assert "exec(compile(source" in launcher
    assert "private-password" not in launcher
    assert json.loads(meta_data)["instance-id"].startswith("iid-vbpub-")
    assert files["/run/vbpub-iso-payload.json"]["permissions"] == "0600"
    assert files["/run/vbpub-iso-bootstrap.py"]["permissions"] == "0700"


def test_build_iso_creates_private_artifact_without_overwrite(monkeypatch, tmp_path):
    import debian_install_v2.iso_builder as builder

    commands = []

    def fake_run(argv, **kwargs):
        commands.append(argv)
        output = Path(argv[1])
        output.write_bytes(b"ISO bytes")
        return type("Completed", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(builder.shutil, "which", lambda name: "/usr/bin/cloud-localds")
    monkeypatch.setattr(builder.subprocess, "run", fake_run)
    output = tmp_path / "private.iso"
    result = build_iso(
        output,
        {"schema_version": 1, "bootstrap_url": "https://example.test/bootstrap.py", "environment": {}},
    )

    assert result == output
    assert output.read_bytes() == b"ISO bytes"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert commands and commands[0][0] == "/usr/bin/cloud-localds"
    assert commands[0][2].endswith("/user-data")
    assert commands[0][3].endswith("/meta-data")
    with pytest.raises(CliFailure, match="refusing to overwrite"):
        build_iso(output, {})


def test_build_iso_requires_existing_output_directory_and_cloud_localds(monkeypatch, tmp_path):
    import debian_install_v2.iso_builder as builder

    monkeypatch.setattr(builder.shutil, "which", lambda _name: None)
    with pytest.raises(CliFailure, match="output directory does not exist"):
        build_iso(tmp_path / "missing" / "out.iso", {})
    with pytest.raises(CliFailure, match="cloud-localds is required"):
        build_iso(tmp_path / "out.iso", {})


def test_build_iso_cli_refuses_noninteractive_invocation(capsys, tmp_path):
    result = main(["build-iso", "--output", str(tmp_path / "out.iso")])
    captured = capsys.readouterr()
    assert result == 2
    assert "requires an interactive terminal" in captured.err
    assert not (tmp_path / "out.iso").exists()


def test_ssh_keygen_checks_each_persistent_public_key_without_echoing_key_data(monkeypatch):
    import debian_install_v2.iso_builder as builder

    invocations = []

    def fake_run(argv, **kwargs):
        invocations.append((argv, kwargs, Path(argv[-1]).read_text(encoding="utf-8")))
        return type("Completed", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(builder.shutil, "which", lambda _name: "/usr/bin/ssh-keygen")
    monkeypatch.setattr(builder.subprocess, "run", fake_run)
    _validate_ssh_keys_with_ssh_keygen([VALID_KEY, VALID_KEY_2])
    assert len(invocations) == 2
    assert all(item[2].endswith("\n") for item in invocations)
    assert all(item[1]["timeout"] == 15 for item in invocations)

    monkeypatch.setattr(
        builder.subprocess,
        "run",
        lambda *_args, **_kwargs: type("Completed", (), {"returncode": 1, "stderr": "bad", "stdout": ""})(),
    )
    with pytest.raises(CliFailure, match="not accepted by ssh-keygen"):
        _validate_ssh_keys_with_ssh_keygen([VALID_KEY])


def test_iso_host_settings_collects_a_multiline_persistent_key_list(monkeypatch):
    import debian_install_v2.iso_builder as builder

    class Answer:
        def __init__(self, value):
            self.value = value

        def ask(self, **_kwargs):
            return self.value

    class Questionary:
        def __init__(self):
            self.lines = iter([VALID_KEY, VALID_KEY_2, ""])

        def select(self, *_args, **_kwargs):
            return Answer("none")

        def text(self, *_args, **_kwargs):
            return Answer(next(self.lines))

    validated = []
    monkeypatch.setattr(builder, "_validate_ssh_keys_with_ssh_keygen", validated.append)
    settings = _host_settings(Questionary(), require_ssh_key=True)
    assert settings["ssh_authorized_keys"] == [
        VALID_KEY,
        VALID_KEY_2,
    ]
    assert validated == [settings["ssh_authorized_keys"]]
