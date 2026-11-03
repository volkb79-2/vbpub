from pathlib import Path
import subprocess
from unittest.mock import call, patch

import pytest

from remote import RemoteError, Target, wait_for_container, wait_for_vm


def target() -> Target:
    return Target("test-host", 22, Path("/key"), Path("/known_hosts"))


def test_midclt_retries_only_a_failed_connection_handshake(capsys) -> None:
    with (
        patch.object(
            Target,
            "run",
            side_effect=[
                RemoteError("midclt: ClientException('Failed connection handshake')"),
                '{"version":"27.0.0-RC.1"}',
            ],
        ) as run,
        patch("remote.time.sleep") as sleep,
    ):
        assert target().call("system.info") == {"version": "27.0.0-RC.1"}

    assert run.call_count == 2
    assert sleep.call_args_list == [call(1)]
    assert "[retry] midclt system.info" in capsys.readouterr().err


def test_ssh_retries_banner_timeout_before_remote_command_start(capsys) -> None:
    with (
        patch(
            "remote.subprocess.run",
            side_effect=[
                subprocess.CompletedProcess(
                    args=["ssh"], returncode=255, stdout="",
                    stderr="Connection timed out during banner exchange",
                ),
                subprocess.CompletedProcess(
                    args=["ssh"], returncode=0, stdout="ok", stderr=""
                ),
            ],
        ) as run,
        patch("remote.time.sleep") as sleep,
    ):
        assert target().run(["true"]) == "ok"

    assert run.call_count == 2
    assert sleep.call_args_list == [call(1)]
    assert "before command startup; retrying in 1s" in capsys.readouterr().err


def test_ssh_retries_key_exchange_reset_before_remote_command_start(capsys) -> None:
    with (
        patch(
            "remote.subprocess.run",
            side_effect=[
                subprocess.CompletedProcess(
                    args=["ssh"], returncode=255, stdout="",
                    stderr=(
                        "kex_exchange_identification: read: Connection reset by peer\n"
                        "Connection reset by 172.19.0.6 port 2223"
                    ),
                ),
                subprocess.CompletedProcess(
                    args=["ssh"], returncode=0, stdout="ok", stderr=""
                ),
            ],
        ) as run,
        patch("remote.time.sleep") as sleep,
    ):
        assert target().run(["true"]) == "ok"

    assert run.call_count == 2
    assert sleep.call_args_list == [call(1)]
    assert "before command startup; retrying in 1s" in capsys.readouterr().err


def test_ssh_does_not_retry_unclassified_connection_reset() -> None:
    with (
        patch(
            "remote.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["ssh"], returncode=255, stdout="",
                stderr="Connection reset by peer",
            ),
        ) as run,
        patch("remote.time.sleep") as sleep,
    ):
        with pytest.raises(RemoteError, match="Connection reset by peer"):
            target().run(["true"])

    assert run.call_count == 1
    sleep.assert_not_called()


def test_ssh_retries_temporary_dns_failure_before_remote_command_start(capsys) -> None:
    with (
        patch(
            "remote.subprocess.run",
            side_effect=[
                subprocess.CompletedProcess(
                    args=["ssh"], returncode=255, stdout="",
                    stderr=(
                        "ssh: Could not resolve hostname truenas-test: "
                        "Temporary failure in name resolution"
                    ),
                ),
                subprocess.CompletedProcess(
                    args=["ssh"], returncode=0, stdout="ok", stderr=""
                ),
            ],
        ) as run,
        patch("remote.time.sleep") as sleep,
    ):
        assert target().run(["true"]) == "ok"

    assert run.call_count == 2
    assert sleep.call_args_list == [call(1)]
    assert "before command startup; retrying in 1s" in capsys.readouterr().err


def test_ssh_does_not_retry_permanent_dns_name_failure() -> None:
    with (
        patch(
            "remote.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["ssh"], returncode=255, stdout="",
                stderr="ssh: Could not resolve hostname bad-name: Name or service not known",
            ),
        ) as run,
        patch("remote.time.sleep") as sleep,
    ):
        with pytest.raises(RemoteError, match="Name or service not known"):
            target().run(["true"])

    assert run.call_count == 1
    sleep.assert_not_called()


def test_midclt_does_not_retry_ambiguous_ssh_disconnect() -> None:
    failure = RemoteError(
        "remote command failed (255): midclt\nConnection reset by peer"
    )
    with (
        patch.object(Target, "run", side_effect=failure) as run,
        patch("remote.time.sleep") as sleep,
    ):
        with pytest.raises(RemoteError, match="Connection reset by peer"):
            target().call("update.manual", "/mnt/update.iso")

    assert run.call_count == 1
    sleep.assert_not_called()


def test_midclt_does_not_retry_method_errors() -> None:
    failure = RemoteError("pool.dataset.create: dataset already exists")
    with (
        patch.object(Target, "run", side_effect=failure) as run,
        patch("remote.time.sleep") as sleep,
    ):
        with pytest.raises(RemoteError, match="dataset already exists"):
            target().call("pool.dataset.create", {"name": "tank/existing"})

    assert run.call_count == 1
    sleep.assert_not_called()


def test_midclt_handshake_retries_are_bounded() -> None:
    failure = RemoteError("midclt: ClientException('Failed connection handshake')")
    with (
        patch.object(Target, "run", side_effect=failure) as run,
        patch("remote.time.sleep") as sleep,
    ):
        with pytest.raises(RemoteError, match="Failed connection handshake"):
            target().call("system.info")

    assert run.call_count == 3
    assert sleep.call_args_list == [call(1), call(2)]


def test_read_only_query_retries_timeouts_but_only_twice() -> None:
    with (
        patch.object(
            Target,
            "call",
            side_effect=[RemoteError("middleware Call timeout"), [{"id": 1}]],
        ) as call_method,
        patch("remote.time.sleep") as sleep,
    ):
        assert target().query("vm.query") == [{"id": 1}]

    assert call_method.call_count == 2
    assert sleep.call_args_list == [call(1)]


def test_read_only_query_refuses_to_retry_mutating_methods() -> None:
    with patch.object(Target, "call") as call_method:
        with pytest.raises(RemoteError, match="non-query method"):
            target().query("vm.start", 1)

    call_method.assert_not_called()


@pytest.mark.parametrize(
    ("waiter", "method", "row"),
    [
        (
            wait_for_vm,
            "vm.query",
            {"id": 9, "status": {"state": "RUNNING"}},
        ),
        (
            wait_for_container,
            "container.query",
            {"id": 9, "status": {"state": "RUNNING"}},
        ),
    ],
)
def test_state_waiters_retry_indeterminate_queries(waiter, method, row) -> None:
    with (
        patch.object(
            Target,
            "query",
            side_effect=[RemoteError(f"{method}: Call timeout"), [row]],
        ) as query_method,
        patch("remote.time.sleep"),
    ):
        assert waiter(target(), 9, "RUNNING", timeout=10) == row

    assert query_method.call_count == 2
