"""The reinstall wrapper must accept what the REAL wizard dry-run prints.

The wizard redacts ``customScript`` in every printed summary, so a wrapper
that compares the printed script text can never match the reviewed bundle.
These tests drive the real ``install-host.py wizard --dry-run`` (fake Netcup
client, no network) and feed its actual stdout to the wrapper's validator.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json

import pytest

from case_harness import run_install_host
from test_pre_reinstall_preflight import _build_valid_inputs, _fake_sent_script

SSH_KEY_IDS = [31415]


def _real_wizard_dry_run(preflight, bundle_path, tmp_path, install_host_mod, monkeypatch, capsys, fake_client):
    argv = preflight._wizard_argv(
        "v1001", bundle_path, tmp_path / "out", ssh_key_ids=SSH_KEY_IDS, dry_run=True
    )
    argv = argv[2:]  # drop interpreter and script path: ``wizard ...``
    # Point the in-process run at the harness' fake server 42 and at files
    # that live under the test directory only.
    argv[argv.index("--server-id") + 1] = "42"
    argv[argv.index("--image") + 1] = "2"  # the fake server offers flavour 2
    argv[argv.index("--ssh-identity-file") + 1] = "id_test"
    argv[argv.index("--custom-script-file") + 1] = str(bundle_path)
    argv[argv.index("--out-dir") + 1] = str(tmp_path / "out")
    (tmp_path / "id_test").write_text("not a real private key\n")
    run = run_install_host(
        install_host_mod, argv, tmp_path=tmp_path, monkeypatch=monkeypatch,
        capsys=capsys, fake_client=fake_client, scenario="gather-id",
        create_inputs=False,
    )
    assert run.status in (0, None), run.err
    return run.out


def _host_for_fake_server(preflight, monkeypatch, output):
    payload = preflight._provider_payload_from_wizard_output(output)
    spec = dataclasses.replace(
        preflight.HOSTS["v1001"], server_id=42,
        hostname=payload["hostname"], image_id=2,
    )
    monkeypatch.setitem(preflight.HOSTS, "v1001", spec)


def test_real_wizard_output_redacts_script_but_prints_a_verifiable_digest(
    tmp_path, install_host_mod, monkeypatch, capsys, fake_client
):
    preflight, _config, bundle_path = _build_valid_inputs(tmp_path)
    output = _real_wizard_dry_run(
        preflight, bundle_path, tmp_path, install_host_mod, monkeypatch, capsys, fake_client
    )
    script = json.loads(bundle_path.read_text(encoding="utf-8"))["customScript"]
    assert "REDACTED customScript" in output
    assert script not in output
    digest = hashlib.sha256(script.encode("utf-8")).hexdigest()
    assert f"customScript sha256={digest} len={len(script)}" in output


def test_wrapper_accepts_the_real_wizard_dry_run_output(
    tmp_path, install_host_mod, monkeypatch, capsys, fake_client
):
    preflight, _config, bundle_path = _build_valid_inputs(tmp_path)
    output = _real_wizard_dry_run(
        preflight, bundle_path, tmp_path, install_host_mod, monkeypatch, capsys, fake_client
    )
    _host_for_fake_server(preflight, monkeypatch, output)
    script = json.loads(bundle_path.read_text(encoding="utf-8"))["customScript"]
    # The key the harness' wizard derives is FAKE_CONTROLLER_KEY, so this also
    # proves the wrapper's expansion equals the real wizard's expansion.
    assert preflight.validate_wizard_preview(
        "v1001", output, expected_custom_script=script,
        expected_sent_custom_script=_fake_sent_script(script), expected_ssh_key_ids=SSH_KEY_IDS,
    ) is None


def test_wrapper_refuses_real_wizard_output_for_a_different_script(
    tmp_path, install_host_mod, monkeypatch, capsys, fake_client
):
    preflight, _config, bundle_path = _build_valid_inputs(tmp_path)
    output = _real_wizard_dry_run(
        preflight, bundle_path, tmp_path, install_host_mod, monkeypatch, capsys, fake_client
    )
    _host_for_fake_server(preflight, monkeypatch, output)
    script = json.loads(bundle_path.read_text(encoding="utf-8"))["customScript"]
    with pytest.raises(preflight.PreflightError, match="customScript"):
        preflight.validate_wizard_preview(
            "v1001", output, expected_custom_script=script + "\ncurl x | sh\n",
            expected_sent_custom_script=_fake_sent_script(script + "\ncurl x | sh\n"),
            expected_ssh_key_ids=SSH_KEY_IDS,
        )


def test_wrapper_refuses_output_without_a_digest_line(
    tmp_path, install_host_mod, monkeypatch, capsys, fake_client
):
    preflight, _config, bundle_path = _build_valid_inputs(tmp_path)
    output = _real_wizard_dry_run(
        preflight, bundle_path, tmp_path, install_host_mod, monkeypatch, capsys, fake_client
    )
    _host_for_fake_server(preflight, monkeypatch, output)
    script = json.loads(bundle_path.read_text(encoding="utf-8"))["customScript"]
    stripped = "\n".join(line for line in output.splitlines() if "customScript sha256=" not in line)
    with pytest.raises(preflight.PreflightError, match="digest"):
        preflight.validate_wizard_preview(
            "v1001", stripped, expected_custom_script=script,
            expected_sent_custom_script=_fake_sent_script(script), expected_ssh_key_ids=SSH_KEY_IDS,
        )


def test_wrapper_refuses_real_wizard_output_when_the_sent_key_differs(
    tmp_path, install_host_mod, monkeypatch, capsys, fake_client
):
    """A different controller key (e.g. a stale CONTROLLER_SSH_PUBKEY) changes the sent digest."""
    preflight, _config, bundle_path = _build_valid_inputs(tmp_path)
    output = _real_wizard_dry_run(
        preflight, bundle_path, tmp_path, install_host_mod, monkeypatch, capsys, fake_client
    )
    _host_for_fake_server(preflight, monkeypatch, output)
    script = json.loads(bundle_path.read_text(encoding="utf-8"))["customScript"]
    other = script.replace("{{CONTROLLER_SSH_PUBKEY}}", "ssh-ed25519 AAAAstalekey operator-env")
    with pytest.raises(preflight.PreflightError, match="sent customScript"):
        preflight.validate_wizard_preview(
            "v1001", output, expected_custom_script=script,
            expected_sent_custom_script=other, expected_ssh_key_ids=SSH_KEY_IDS,
        )
