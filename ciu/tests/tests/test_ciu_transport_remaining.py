"""Remaining behavioural boundaries for CIU's SSH transports.

These tests never contact an SSH server.  They replace only the process or
Paramiko boundary and assert the command/cleanup contract that real callers
observe.
"""
from __future__ import annotations

import shlex
import sys
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ciu import transport_ssh as transport


def _host(key: str, **overrides: object) -> dict:
    result = {
        "ssh_host": "deploy.example",
        "ssh_user": "deploy",
        "ssh_port": 2222,
        "ssh_key": key,
        "known_host": "ssh-ed25519 AAAACIU",
    }
    result.update(overrides)
    return result


class _FailingWriter:
    """A deterministic local filesystem boundary that rejects one write."""

    def __init__(self, fd: int) -> None:
        self.fd = fd

    def __enter__(self) -> "_FailingWriter":
        return self

    def __exit__(self, *_: object) -> None:
        os.close(self.fd)

    def write(self, _: str) -> None:
        raise OSError("disk full")


def test_subprocess_preserves_remote_argv_boundary_and_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """A remote option is placed after ``--``, never interpreted as ssh's own."""
    captured: list[str] = []
    monkeypatch.setattr(
        transport.subprocess,
        "run",
        lambda argv: (captured.extend(argv), SimpleNamespace(returncode=23))[1],
    )

    rc = transport._ssh_exec_subprocess(
        "host", "user", "2200", "/private key", "/known hosts",
        ["ciu", "render", "--config", "path with spaces"], interactive=False,
    )

    assert rc == 23
    remote_start = captured.index("--")
    assert captured[remote_start + 1:] == ["ciu", "render", "--config", "path with spaces"]
    assert captured[:remote_start].count("--config") == 0
    assert "BatchMode=yes" in captured
    assert "-t" not in captured


def test_vault_key_write_failure_removes_the_partially_created_private_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local write failure cannot strand decrypted key material on disk."""
    created: list[str] = []
    real_mkstemp = transport.tempfile.mkstemp

    def make_temp(*_: object, **__: object) -> tuple[int, str]:
        fd, path = real_mkstemp(dir=tmp_path)
        created.append(path)
        return fd, path

    monkeypatch.setattr(transport.tempfile, "mkstemp", make_temp)
    monkeypatch.setattr(transport.os, "fdopen", lambda fd, *_args, **_kwargs: _FailingWriter(fd))
    monkeypatch.setattr("ciu.secrets.providers.resolve_vault_token", lambda *_: "token")
    monkeypatch.setattr("ciu.secrets.providers.vault_addr_from_config", lambda _: "https://vault.invalid")
    monkeypatch.setattr(
        "ciu.secrets.providers.VaultKV2", lambda *_: SimpleNamespace(read=lambda *_args, **_kwargs: "private")
    )

    with pytest.raises(OSError, match="disk full"):
        transport.resolve_key(_host("ASK_VAULT:secret/data/key"), {}, tmp_path)

    assert created and not Path(created[0]).exists()


def test_known_hosts_write_failure_removes_its_partial_pin_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed pin write leaves no misleading or world-visible temp file behind."""
    created: list[str] = []
    real_mkstemp = transport.tempfile.mkstemp

    def make_temp(*_: object, **__: object) -> tuple[int, str]:
        fd, path = real_mkstemp(dir=tmp_path)
        created.append(path)
        return fd, path

    monkeypatch.setattr(transport.tempfile, "mkstemp", make_temp)
    monkeypatch.setattr(transport.os, "fdopen", lambda fd, *_args, **_kwargs: _FailingWriter(fd))

    with pytest.raises(OSError, match="disk full"):
        transport._known_hosts_file(_host("/key"))

    assert created and not Path(created[0]).exists()


def test_subprocess_interactive_tofu_opens_terminal_without_remote_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The explicitly insecure bootstrap path still opens one interactive shell."""
    captured: list[str] = []
    monkeypatch.setattr(
        transport.subprocess,
        "run",
        lambda argv: (captured.extend(argv), SimpleNamespace(returncode=0))[1],
    )

    assert transport._ssh_exec_subprocess("h", "u", "22", "/key", None, [], interactive=True) == 0
    assert "StrictHostKeyChecking=no" in captured
    assert "UserKnownHostsFile=/dev/null" in captured
    assert "-t" in captured
    assert "--" not in captured


def test_paramiko_quotes_each_remote_argument_and_streams_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Metacharacters remain data, rather than becoming a second remote command."""
    stdout = MagicMock()
    stdout.__iter__.return_value = ["ok\\n"]
    stdout.channel.recv_exit_status.return_value = 19
    stderr = MagicMock()
    stderr.__iter__.return_value = ["warn\\n"]
    client = MagicMock()
    client.exec_command.return_value = (MagicMock(), stdout, stderr)
    paramiko = SimpleNamespace(SSHClient=MagicMock(return_value=client), RejectPolicy=lambda: object())
    monkeypatch.setitem(sys.modules, "paramiko", paramiko)
    output: list[str] = []
    monkeypatch.setattr(sys.stdout, "write", output.append)
    monkeypatch.setattr(sys.stderr, "write", output.append)

    rc = transport._ssh_exec_paramiko(
        "host", "user", 22, "/key", None,
        ["ciu", "render", "--name", "prod; curl attacker", "path with spaces"],
        interactive=False,
    )

    assert rc == 19
    remote_command = client.exec_command.call_args.args[0]
    assert shlex.split(remote_command) == [
        "ciu", "render", "--name", "prod; curl attacker", "path with spaces",
    ]
    assert "'prod; curl attacker'" in remote_command
    assert output == ["ok\\n", "warn\\n"]
    client.close.assert_called_once()


def test_paramiko_preserves_the_single_raw_remote_program_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatcher-provided ``cd … && ciu …`` programs remain executable remote shell syntax."""
    stdout = MagicMock()
    stdout.__iter__.return_value = []
    stdout.channel.recv_exit_status.return_value = 0
    stderr = MagicMock()
    stderr.__iter__.return_value = []
    client = MagicMock()
    client.exec_command.return_value = (MagicMock(), stdout, stderr)
    paramiko = SimpleNamespace(SSHClient=MagicMock(return_value=client), RejectPolicy=lambda: object())
    monkeypatch.setitem(sys.modules, "paramiko", paramiko)

    remote_program = "cd '/opt/selected stack' && ciu up --profile 'blue green'"
    assert transport._ssh_exec_paramiko(
        "host", "user", 22, "/key", None, [remote_program], interactive=False,
    ) == 0

    assert client.exec_command.call_args.args == (remote_program,)
    client.close.assert_called_once()


def test_paramiko_connect_failure_closes_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rejected pinned connection cannot leak a Paramiko client/socket."""
    client = MagicMock()
    client.connect.side_effect = RuntimeError("pinned host rejected")
    paramiko = SimpleNamespace(SSHClient=MagicMock(return_value=client), RejectPolicy=lambda: object())
    monkeypatch.setitem(sys.modules, "paramiko", paramiko)

    with pytest.raises(RuntimeError, match="pinned host rejected"):
        transport._ssh_exec_paramiko("host", "user", 22, "/key", "/known", ["true"], interactive=False)

    client.load_host_keys.assert_called_once_with("/known")
    client.close.assert_called_once()


def test_paramiko_interactive_streams_remote_bytes_then_returns_channel_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The interactive bridge forwards remote bytes and closes after EOF."""
    chan = MagicMock()
    chan.recv.side_effect = [b"remote prompt> ", b""]
    chan.recv_exit_status.return_value = 12
    client = MagicMock()
    client.invoke_shell.return_value = chan
    paramiko = SimpleNamespace(SSHClient=MagicMock(return_value=client), RejectPolicy=lambda: object())
    output = MagicMock()
    stdin = SimpleNamespace(buffer=MagicMock())
    monkeypatch.setitem(sys.modules, "paramiko", paramiko)
    monkeypatch.setitem(sys.modules, "select", SimpleNamespace(select=lambda *_: ([chan], [], [])))
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=output))
    monkeypatch.setattr(sys, "stdin", stdin)

    rc = transport._ssh_exec_paramiko("host", "user", 22, "/key", None, [], interactive=True)

    assert rc == 12
    output.write.assert_called_once_with(b"remote prompt> ")
    output.flush.assert_called_once()
    chan.send.assert_not_called()
    client.close.assert_called_once()


def test_paramiko_interactive_forwards_stdin_and_stops_at_local_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local input is sent bytewise; local EOF leaves the remote channel alone."""
    chan = MagicMock()
    chan.recv_exit_status.return_value = 4
    client = MagicMock()
    client.invoke_shell.return_value = chan
    paramiko = SimpleNamespace(SSHClient=MagicMock(return_value=client), RejectPolicy=lambda: object())
    stdin = SimpleNamespace(buffer=MagicMock(read=MagicMock(side_effect=[b"x", b""])))
    stdout = SimpleNamespace(buffer=MagicMock())
    calls = iter([([stdin], [], []), ([stdin], [], [])])
    monkeypatch.setitem(sys.modules, "paramiko", paramiko)
    monkeypatch.setitem(sys.modules, "select", SimpleNamespace(select=lambda *_: next(calls)))
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)

    rc = transport._ssh_exec_paramiko("host", "user", 22, "/key", None, [], interactive=True)

    assert rc == 4
    chan.send.assert_called_once_with(b"x")
    client.close.assert_called_once()


def test_ssh_exec_uses_available_paramiko_and_cleans_pinned_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public transport selects Paramiko only when requested and available."""
    key = tmp_path / "id key"
    key.write_text("not-a-real-key")
    monkeypatch.setenv("CIU_SSH_TRANSPORT", "PARAMIKO")
    monkeypatch.setitem(sys.modules, "paramiko", SimpleNamespace())
    received: dict[str, object] = {}

    def fake_paramiko(*args: object, **kwargs: object) -> int:
        received["args"] = args
        received["kwargs"] = kwargs
        known_hosts_path = args[4]
        assert isinstance(known_hosts_path, str) and Path(known_hosts_path).exists()
        return 31

    monkeypatch.setattr(transport, "_ssh_exec_paramiko", fake_paramiko)
    rc = transport.ssh_exec(_host(str(key)), ["ciu", "health"], config={}, repo_root=tmp_path)

    assert rc == 31
    assert received["args"][:4] == ("deploy.example", "deploy", 2222, str(key.resolve()))
    assert received["kwargs"] == {"interactive": False}
    known_path = received["args"][4]
    assert isinstance(known_path, str) and not Path(known_path).exists()


def test_sync_ssh_transport_string_preserves_paths_with_spaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rsync's ``-e`` shell string must retain a key/known-host path as one argument."""
    key = tmp_path / "keys" / "deploy key"
    key.parent.mkdir()
    key.write_text("key")
    captured: list[str] = []
    monkeypatch.setattr(
        transport.subprocess,
        "run",
        lambda argv: (captured.extend(argv), SimpleNamespace(returncode=0))[1],
    )

    assert transport.ssh_sync(
        _host(str(key)), "/local tree/", "/remote tree", config={}, repo_root=tmp_path,
        excludes=["*.secret", "node modules"],
    ) == 0

    ssh_string = captured[captured.index("-e") + 1]
    ssh_argv = shlex.split(ssh_string)
    assert ssh_argv[:5] == ["ssh", "-i", str(key.resolve()), "-p", "2222"]
    known_arg = next(value for value in ssh_argv if value.startswith("UserKnownHostsFile="))
    assert Path(known_arg.split("=", 1)[1]).name.startswith("ciu_known_hosts_")
    assert captured[-2:] == ["/local tree/", "deploy@deploy.example:/remote tree/"]
    assert [item for item in captured if item == "--exclude"] == ["--exclude", "--exclude"]


def test_sync_tofu_and_scp_tofu_use_explicit_insecure_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented bootstrap escape hatch is explicit in both transfer clients."""
    key = tmp_path / "id_rsa"
    key.write_text("key")
    monkeypatch.setenv("CIU_SSH_INSECURE_TOFU", "1")
    commands: list[list[str]] = []
    monkeypatch.setattr(
        transport.subprocess, "run",
        lambda argv: (commands.append(argv), SimpleNamespace(returncode=0))[1],
    )
    unpinned = _host(str(key), known_host="")

    assert transport.ssh_sync(unpinned, "/src", "/dst", config={}, repo_root=tmp_path) == 0
    assert transport.scp_file(unpinned, "/src/file", "/dst/file", config={}, repo_root=tmp_path) == 0
    assert "StrictHostKeyChecking=no" in shlex.split(commands[0][commands[0].index("-e") + 1])
    assert "UserKnownHostsFile=/dev/null" in shlex.split(commands[0][commands[0].index("-e") + 1])
    assert "StrictHostKeyChecking=no" in commands[1]
    assert "UserKnownHostsFile=/dev/null" in commands[1]


@pytest.mark.parametrize("transfer", ["sync", "scp"])
def test_transfer_cleanup_failure_does_not_mask_the_transfer_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transfer: str,
) -> None:
    """Best-effort key cleanup must not replace rsync/scp's meaningful exit code."""
    key = tmp_path / "vault key"
    key.write_text("private")
    key_paths: list[str] = []
    real_unlink = transport.os.unlink

    def fake_key(*_: object) -> str:
        key_paths.append(str(key))
        return str(key)

    monkeypatch.setattr(transport, "resolve_key", fake_key)
    monkeypatch.setattr(transport.subprocess, "run", lambda _argv: SimpleNamespace(returncode=27))
    # The transport's contract is best-effort cleanup: an unlink race/permission
    # issue is deliberately not allowed to hide the transfer's exit status.
    def deny_key_cleanup(path: str) -> None:
        if str(path) == str(key):
            raise OSError("cleanup denied")
        real_unlink(path)

    monkeypatch.setattr(transport.os, "unlink", deny_key_cleanup)
    host = _host("ASK_VAULT:secret/data/key")
    try:
        if transfer == "sync":
            rc = transport.ssh_sync(host, "/src", "/dst", config={}, repo_root=tmp_path)
        else:
            rc = transport.scp_file(host, "/src/file", "/dst/file", config={}, repo_root=tmp_path)
    finally:
        # Restore only for this explicit test-owned key; pytest's monkeypatch
        # cleanup happens later and the production cleanup intentionally failed.
        real_unlink(key)

    assert rc == 27
    assert key_paths == [str(key)]
