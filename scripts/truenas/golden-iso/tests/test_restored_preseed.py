from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "step3-apply-restored-preseed.py"
SPEC = importlib.util.spec_from_file_location("golden_restored_preseed", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
PRESEED = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PRESEED
SPEC.loader.exec_module(PRESEED)


class FakeTarget:
    def __init__(self, *, version: str = "27.0.0-RC.1", pristine_exit: int = 0) -> None:
        self.version = version
        self.pristine_exit = pristine_exit
        self.calls: list[tuple[str, tuple[object, ...], bool]] = []

    def command(self, argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv == ["test", "!", "-e", "/root/v27-boot-pool-patch-install.json"]:
            return subprocess.CompletedProcess(argv, self.pristine_exit, "", "")
        if argv == ["id", "-u"]:
            return subprocess.CompletedProcess(argv, 0, "0\n", "")
        raise AssertionError(f"unexpected remote command: {argv!r}")

    def call(self, method: str, *params: object, sensitive: bool = False) -> object:
        self.calls.append((method, params, sensitive))
        if method == "system.info":
            return {"version": self.version}
        if method == "user.query" and params == ([["username", "=", "truenas_admin"]],):
            return [{"id": 1100, "username": "truenas_admin"}]
        if method == "user.query" and params == ([["username", "=", "root"]],):
            return [{"id": 0, "username": "root"}]
        if method == "user.query" and params == ([["id", "=", 0]],):
            return [{"id": 0, "username": "root", "sshpubkey": "ssh-ed25519 AAAATEST test", "ssh_password_enabled": False}]
        if method == "service.query":
            return [{"id": 13, "service": "ssh"}]
        if method == "ssh.config":
            return {"passwordauth": False}
        if method == "service.control":
            return None
        return None


def test_apply_preseed_updates_password_and_root_key_without_leaking_payload() -> None:
    target = FakeTarget()
    PRESEED.apply_preseed(
        target,
        expected_version="27.0.0-RC.1",
        admin_password="test-only-password-value",
        root_public_key="ssh-ed25519 AAAATEST test",
    )

    password_call = next(call for call in target.calls if call[0] == "user.update" and call[1][0] == 1100)
    assert password_call[1][1] == {"password": "test-only-password-value"}
    assert password_call[2] is True
    root_call = next(call for call in target.calls if call[0] == "user.update" and call[1][0] == 0)
    assert root_call[1][1] == {
        "sshpubkey": "ssh-ed25519 AAAATEST test",
        "ssh_password_enabled": False,
    }
    assert ("ssh.update", ({"passwordauth": False},), False) in target.calls


def test_apply_preseed_refuses_wrong_version_before_mutation() -> None:
    target = FakeTarget(version="27.0.0-MASTER+20261029-173417")
    with pytest.raises(PRESEED.PreseedError, match="expected TrueNAS"):
        PRESEED.apply_preseed(
            target,
            expected_version="27.0.0-RC.1",
            admin_password="test-only-password-value",
            root_public_key="ssh-ed25519 AAAATEST test",
        )
    assert [method for method, _params, _sensitive in target.calls] == ["system.info"]


def test_apply_preseed_refuses_existing_patch_record_before_mutation() -> None:
    target = FakeTarget(pristine_exit=1)
    with pytest.raises(PRESEED.PreseedError, match="existing v27 patch install record"):
        PRESEED.apply_preseed(
            target,
            expected_version="27.0.0-RC.1",
            admin_password="test-only-password-value",
            root_public_key="ssh-ed25519 AAAATEST test",
        )
    assert [method for method, _params, _sensitive in target.calls] == ["system.info"]


def test_read_admin_password_requires_private_valid_toml(tmp_path: Path) -> None:
    secret = tmp_path / "ui-secrets.toml"
    secret.write_text('[truenas_admin]\nusername = "truenas_admin"\npassword = "test-only-password-value"\n')
    secret.chmod(0o600)
    assert PRESEED._read_admin_password(secret) == "test-only-password-value"

    secret.chmod(0o644)
    with pytest.raises(PRESEED.PreseedError, match="mode 0600"):
        PRESEED._read_admin_password(secret)


def test_read_root_public_key_requires_one_ed25519_key(tmp_path: Path) -> None:
    key = tmp_path / "root.pub"
    key.write_text("ssh-ed25519 AAAATEST test\n")
    assert PRESEED._read_root_public_key(key) == "ssh-ed25519 AAAATEST test"
    key.write_text("ssh-rsa AAAATEST test\n")
    with pytest.raises(PRESEED.PreseedError, match="Ed25519"):
        PRESEED._read_root_public_key(key)


def test_sensitive_middleware_payload_is_sent_on_stdin_not_ssh_argv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = tmp_path / "root"
    identity.write_text("private-key-placeholder")
    identity.chmod(0o600)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("v1001.vxxu.de ssh-ed25519 AAAATEST\n")
    calls: list[tuple[list[str], str | None]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        input_data = kwargs.get("input")
        calls.append((argv, input_data if isinstance(input_data, str) else None))
        if argv[0] == "ssh-keygen":
            return subprocess.CompletedProcess(argv, 0, "v1001.vxxu.de\n", "")
        return subprocess.CompletedProcess(argv, 0, "null\n", "")

    monkeypatch.setattr(PRESEED.subprocess, "run", fake_run)
    target = PRESEED.RemoteTrueNAS("v1001.vxxu.de", 22, identity, known_hosts)
    target.call("user.update", 1100, {"password": "never-in-argv-value"}, sensitive=True)

    ssh_argv, stdin_data = calls[1]
    assert "never-in-argv-value" not in " ".join(ssh_argv)
    assert stdin_data == '{"password":"never-in-argv-value"}'
