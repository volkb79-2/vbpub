"""CLI grammar and checkpoint tests for the provider restore workflow."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from cli_extended import CliFailure
from cli_extended.parser import UsageError

NETCUP_DIR = Path(__file__).resolve().parents[1]
SCRIPT = NETCUP_DIR / "install-restore-iso.py"
SPEC = importlib.util.spec_from_file_location("install_restore_iso_under_test", SCRIPT)
install_restore_iso = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install_restore_iso)


def test_restore_cli_routes_accept_documented_option_spellings():
    app = install_restore_iso.build_cli()
    cases = {
        "start": [
            "start", "804027", "--iso", "/tmp/restore.iso", "--user-iso-name", "restore.iso",
            "--part-size-mib", "64", "--state-file", "/tmp/state.json", "--replace-state",
            "--task-timeout-seconds", "30", "--debug-raw", "--dry-run", "--yes",
        ],
        "install": [
            "install", "804027", "--iso", "/tmp/installer.iso", "--user-iso-name", "install.iso",
            "--part-size-mib", "64", "--ready-url", "http://v1001.vxxu.de/", "--ready-text", "TrueNAS",
            "--state-file", "/tmp/state.json", "--install-timeout-seconds", "30",
            "--http-ready-timeout-seconds", "30", "--server-ready-timeout-seconds", "30",
            "--task-timeout-seconds", "30", "--debug-raw", "--dry-run", "--yes",
        ],
        "finish": [
            "finish", "804027", "--state-file", "/tmp/state.json", "--restore-complete",
            "--ssh-host", "v1001.vxxu.de", "--ssh-host-key-alias", "v1001.vxxu.de",
            "--ssh-identity-file", "/tmp/root_ed25519", "--ssh-known-hosts", "/tmp/known_hosts",
            "--ssh-timeout-seconds", "10", "--server-ready-timeout-seconds", "10",
            "--task-timeout-seconds", "10", "--debug-raw", "--dry-run", "--yes",
        ],
        "recover": [
            "recover", "804027", "--state-file", "/tmp/state.json", "--try-disk-boot",
            "--ssh-host", "v1001.vxxu.de", "--ssh-host-key-alias", "v1001.vxxu.de",
            "--ssh-identity-file", "/tmp/root_ed25519", "--ssh-known-hosts", "/tmp/known_hosts",
            "--ssh-timeout-seconds", "10", "--server-ready-timeout-seconds", "10",
            "--task-timeout-seconds", "10", "--debug-raw", "--dry-run", "--yes",
        ],
        "status": ["status", "804027", "--state-file", "/tmp/state.json", "--debug-raw"],
    }
    for verb, argv in cases.items():
        parsed = app.parse_args(argv)
        assert parsed.server_id == 804027


def test_restore_cli_rejects_nonpositive_server_ids():
    app = install_restore_iso.build_cli()
    required = {
        "start": ["--iso", "/tmp/restore.iso"],
        "install": ["--iso", "/tmp/installer.iso", "--ready-url", "http://v1001.vxxu.de/"],
        "finish": [
            "--restore-complete", "--ssh-host", "v1001.vxxu.de",
            "--ssh-identity-file", "/tmp/root_ed25519", "--ssh-known-hosts", "/tmp/known_hosts",
        ],
        "recover": [
            "--try-disk-boot", "--ssh-host", "v1001.vxxu.de",
            "--ssh-identity-file", "/tmp/root_ed25519", "--ssh-known-hosts", "/tmp/known_hosts",
        ],
        "status": [],
    }
    for verb, options in required.items():
        with pytest.raises(UsageError) as exc:
            app.parse_args([verb, "0", *options])
        assert getattr(exc.value, "exit_code", 2) == 2
        assert "must be a positive integer" in str(exc.value)


def test_generic_install_accepts_no_application_readiness_url():
    app = install_restore_iso.build_cli()
    parsed = app.parse_args(["install", "804027", "--iso", "/tmp/installer.iso"])
    assert parsed.ready_url is None
    assert parsed.ready_text is None


@pytest.mark.parametrize("option", ["--ready-text", "--http-ready-timeout-seconds"])
def test_generic_install_requires_url_for_http_readiness_options(option):
    app = install_restore_iso.build_cli()
    value = "ready" if option == "--ready-text" else "30"
    stderr = io.StringIO()
    status = app.run(
        argv=["install", "804027", "--iso", "/tmp/installer.iso", option, value],
        stdout=io.StringIO(),
        stderr=stderr,
    )
    assert status == 2
    assert "requires an HTTP readiness URL" in stderr.getvalue()


class _Output:
    def __init__(self):
        self.lines: list[str] = []

    def info(self, message: str):
        self.lines.append(message)


class _Runtime:
    def __init__(self):
        self.output = _Output()
        self.dry_run = False

    def confirm(self, _message: str) -> bool:
        return True

    def progress(self):
        return SimpleNamespace(update=lambda *_args, **_kwargs: None, finish=lambda *_args, **_kwargs: None)


class _Client:
    def __init__(self):
        self.detached = False
        self.bootorder = ["CDROM", "HDD", "NETWORK"]
        self.patches: list[tuple[str, dict]] = []

    def delete(self, _path: str):
        self.detached = True

    def patch(self, path: str, payload: dict, params=None):
        self.patches.append((path, payload))
        if "bootorder" in payload:
            self.bootorder = list(payload["bootorder"])
            return {"uuid": "11111111-1111-4111-8111-111111111111"}
        return {"uuid": "22222222-2222-4222-8222-222222222222"}


def test_recover_verifies_disk_boot_without_certifying_restore(tmp_path, monkeypatch):
    client = _Client()
    server_id = 804027
    state_path = tmp_path / "restore.json"
    state = {
        "schema_version": 1,
        "server_id": server_id,
        "server_name": "v2202511209318406253",
        "phase": "awaiting_restore_completion",
        "user_iso_name": "restore-v5.iso",
        "iso_size_bytes": 1024,
        "original_boot_order": ["HDD", "NETWORK", "CDROM"],
        "tasks": {},
    }
    install_restore_iso._write_state(state_path, state)

    details = {
        "id": server_id,
        "name": "v2202511209318406253",
        "serverLiveInfo": {
            "state": "RUNNING",
            "configChanged": False,
            "bootorder": client.bootorder,
        },
    }
    monkeypatch.setattr(install_restore_iso, "_create_client", lambda _runtime: client)
    monkeypatch.setattr(install_restore_iso, "_guard_mutation", lambda *_args: details)
    monkeypatch.setattr(install_restore_iso, "_check_no_active_tasks", lambda *_args: None)
    monkeypatch.setattr(install_restore_iso, "_server_details", lambda *_args: details)
    monkeypatch.setattr(
        install_restore_iso,
        "_attached_iso",
        lambda *_args: {"isoAttached": not client.detached, "iso": {"key": "restore-v5.iso"}},
    )
    monkeypatch.setattr(install_restore_iso, "_wait_task", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        install_restore_iso,
        "_ssh_verify",
        lambda *_args: "0\n  state: ONLINE\nerrors: No known data errors\n",
    )
    monkeypatch.setattr(install_restore_iso.time, "sleep", lambda _seconds: None)

    args = SimpleNamespace(
        state_file=state_path,
        server_id=server_id,
        ssh_host="v1001.vxxu.de",
        ssh_identity_file=Path("/tmp/root_ed25519"),
        ssh_known_hosts=Path("/tmp/known_hosts"),
        ssh_timeout_seconds=1,
        ssh_host_key_alias=None,
        task_timeout_seconds=1,
        server_ready_timeout_seconds=1,
    )
    runtime = _Runtime()
    assert install_restore_iso._recover(args, runtime) == 0

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["phase"] == "recovered"
    assert saved["operation"] == "recover"
    assert saved["restore_completion_confirmed_at"] is None
    assert saved["restore_certified"] is False
    assert client.detached is True
    assert client.bootorder == ["HDD", "NETWORK", "CDROM"]
    assert "Restore completion remains uncertified." in "\n".join(runtime.output.lines)


def test_recover_refuses_checkpoint_owned_by_finish(tmp_path):
    state_path = tmp_path / "restore.json"
    state = {
        "schema_version": 1,
        "server_id": 804027,
        "phase": "awaiting_restore_completion",
        "operation": "finish",
        "user_iso_name": "restore.iso",
        "original_boot_order": ["HDD", "CDROM"],
    }
    install_restore_iso._write_state(state_path, state)
    args = SimpleNamespace(state_file=state_path, server_id=804027)
    with pytest.raises(CliFailure, match="started as 'finish'"):
        install_restore_iso._recover(args, _Runtime())


@pytest.mark.parametrize("ready_url", [None, "http://v1001.vxxu.de/"])
def test_generic_install_detaches_before_powering_the_installed_disk(tmp_path, monkeypatch, ready_url):
    class InstallerClient(_Client):
        def __init__(self):
            super().__init__()
            self.powered_on = False

        def patch(self, path, payload, params=None):
            self.patches.append((path, payload))
            if "bootorder" in payload:
                self.bootorder = list(payload["bootorder"])
                return {"uuid": "11111111-1111-4111-8111-111111111111"}
            if payload.get("state") == "ON":
                self.powered_on = True
            return {"uuid": "22222222-2222-4222-8222-222222222222"}

    client = InstallerClient()
    state_path = tmp_path / "install.json"
    iso_path = tmp_path / "installer.iso"
    iso_path.write_bytes(b"pinned installer image")
    state = {
        "schema_version": 1,
        "server_id": 804027,
        "server_name": "v1001.vxxu.de",
        "operation": "installer",
        "phase": "awaiting_installer_poweroff",
        "user_iso_name": "install.iso",
        "iso_path": str(iso_path),
        "iso_size_bytes": iso_path.stat().st_size,
        "iso_sha256": install_restore_iso._sha256(iso_path),
        "readiness": {"url": ready_url, "text": "TrueNAS" if ready_url else None},
        "original_boot_order": ["HDD", "NETWORK", "CDROM"],
        "tasks": {},
    }
    install_restore_iso._write_state(state_path, state)

    def details():
        return {
            "id": 804027,
            "name": "v1001.vxxu.de",
            "serverLiveInfo": {
                "state": "RUNNING" if client.powered_on else "OFF",
                "configChanged": False,
                "bootorder": list(client.bootorder),
            },
        }

    monkeypatch.setattr(install_restore_iso, "_create_client", lambda _runtime: client)
    monkeypatch.setattr(install_restore_iso, "_guard_mutation", lambda *_args: details())
    monkeypatch.setattr(install_restore_iso, "_check_no_active_tasks", lambda *_args: None)
    monkeypatch.setattr(install_restore_iso, "_server_details", lambda *_args: details())
    monkeypatch.setattr(
        install_restore_iso,
        "_attached_iso",
        lambda *_args: {"isoAttached": not client.detached, "iso": {"key": "install.iso"}},
    )
    monkeypatch.setattr(install_restore_iso, "_wait_guest_poweroff", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(install_restore_iso, "_wait_task", lambda *_args, **_kwargs: {})
    ready_checks = []
    monkeypatch.setattr(
        install_restore_iso,
        "_wait_http_ready",
        lambda url, text, *_args, **_kwargs: ready_checks.append((url, text)),
    )
    monkeypatch.setattr(install_restore_iso.time, "sleep", lambda _seconds: None)

    args = SimpleNamespace(
        state_file=state_path,
        iso=iso_path,
        server_id=804027,
        ready_url=ready_url,
        ready_text="TrueNAS" if ready_url else None,
        install_timeout_seconds=60,
        http_ready_timeout_seconds=60,
        task_timeout_seconds=60,
        server_ready_timeout_seconds=60,
    )
    runtime = _Runtime()
    assert install_restore_iso._install(args, runtime) == 0

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["phase"] == "complete"
    assert client.detached is True
    assert client.bootorder == ["HDD", "NETWORK", "CDROM"]
    assert [payload for _path, payload in client.patches] == [
        {"bootorder": ["HDD", "NETWORK", "CDROM"]},
        {"state": "ON"},
    ]
    assert ready_checks == ([("http://v1001.vxxu.de/", "TrueNAS")] if ready_url else [])
