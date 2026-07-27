"""Direct contracts for the optional BPF, MCP, and gateway CLI frontends."""

from pathlib import Path
from types import SimpleNamespace

import pytest

import topos.cli as cli
from topos.daemon.api import Sensitivity
from topos.daemon.http_gateway import GatewayStartupError


@pytest.mark.parametrize("as_json", [False, True])
def test_bpf_gate_forwards_roots_and_uses_one_renderer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], as_json: bool
) -> None:
    calls: list[object] = []
    report = object()
    monkeypatch.setattr(
        cli, "parse_bpf_args",
        lambda _argv: SimpleNamespace(command="gate", proc_root=Path("/proc-test"), pin_root=Path("/pin-test"), json=as_json),
    )
    monkeypatch.setattr(cli, "run_bpf_gate", lambda **kwargs: calls.append(kwargs) or report)
    monkeypatch.setattr(cli, "report_to_jsonable", lambda received: calls.append(("json", received)) or {"ok": True})
    monkeypatch.setattr(cli, "render_report", lambda received: calls.append(("text", received)) or "TEXT")

    assert cli._main_bpf(["gate"]) == 0
    assert calls[0] == {"proc_root": Path("/proc-test"), "pin_root": Path("/pin-test")}
    assert capsys.readouterr().out == ('{"ok": true}\n' if as_json else "TEXT\n")
    assert ("json", report) in calls if as_json else ("text", report) in calls
    assert ("text", report) not in calls if as_json else ("json", report) not in calls


def test_bpf_defensive_unknown_command_does_not_run_boundary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "parse_bpf_args", lambda _argv: SimpleNamespace(command="delete"))
    monkeypatch.setattr(cli, "run_bpf_gate", lambda **_kwargs: pytest.fail("BPF run called"))
    assert cli._main_bpf(["delete"]) == 2
    assert "unknown bpf command" in capsys.readouterr().err


@pytest.mark.parametrize("threshold, expected", [(None, None), ("operational", Sensitivity.OPERATIONAL)])
def test_mcp_serve_forwards_socket_and_optional_sensitivity(
    monkeypatch: pytest.MonkeyPatch, threshold: str | None, expected: Sensitivity | None
) -> None:
    calls: list[tuple[object, object]] = []
    socket = Path("/run/topos-test.sock")
    monkeypatch.setattr(cli, "parse_mcp_args", lambda _argv: SimpleNamespace(command="serve", socket=socket, redact_above=threshold))
    monkeypatch.setattr("topos.mcp.server.run_server", lambda received_socket, *, redact_above: calls.append((received_socket, redact_above)) or 23)
    assert cli._main_mcp(["serve"]) == 23
    assert calls == [(socket, expected)]


def test_mcp_defensive_nonserve_returns_before_optional_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "parse_mcp_args", lambda _argv: SimpleNamespace(command="delete"))
    assert cli._main_mcp(["delete"]) == 2


def test_gateway_valid_config_serves_then_closes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[tuple[Path, object]] = []
    closed: list[bool] = []

    class Server:
        server_address = ("127.0.0.1", 9100)

        def serve_forever(self) -> None:
            return None

        def server_close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(cli, "parse_gateway_args", lambda _argv: SimpleNamespace(command="serve", principal=["alice:operational"], daemon_socket=Path("/daemon.sock"), host="127.0.0.1", port=9100, allow_non_loopback=False))
    monkeypatch.setattr("topos.daemon.http_gateway.serve_versioned_http_gateway", lambda socket, *, config: started.append((socket, config)) or Server())
    assert cli._main_gateway(["serve"]) == 0
    socket, config = started[0]
    assert socket == Path("/daemon.sock")
    assert config.auth.principals == {"alice": Sensitivity.OPERATIONAL}
    assert config.host == "127.0.0.1" and config.port == 9100 and not config.allow_non_loopback
    assert closed == [True]
    assert "http://127.0.0.1:9100" in capsys.readouterr().out


@pytest.mark.parametrize("principals, needle", [(["broken"], "NAME:CEILING"), (["a:public", "a:sensitive"], "unique")])
def test_gateway_invalid_principals_fail_before_startup(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], principals: list[str], needle: str
) -> None:
    monkeypatch.setattr(cli, "parse_gateway_args", lambda _argv: SimpleNamespace(command="serve", principal=principals, daemon_socket=Path("/daemon.sock"), host="127.0.0.1", port=9100, allow_non_loopback=False))
    monkeypatch.setattr("topos.daemon.http_gateway.serve_versioned_http_gateway", lambda *_args, **_kwargs: pytest.fail("gateway startup called"))
    assert cli._main_gateway(["serve"]) == 2
    assert needle in capsys.readouterr().err


def test_gateway_startup_error_and_interrupt_close_server(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    args = SimpleNamespace(command="serve", principal=["a:public"], daemon_socket=Path("/daemon.sock"), host="127.0.0.1", port=9100, allow_non_loopback=False)
    monkeypatch.setattr(cli, "parse_gateway_args", lambda _argv: args)
    monkeypatch.setattr("topos.daemon.http_gateway.serve_versioned_http_gateway", lambda *_args, **_kwargs: (_ for _ in ()).throw(GatewayStartupError("refused")))
    assert cli._main_gateway(["serve"]) == 2
    assert capsys.readouterr().err == "refused\n"

    closed: list[bool] = []
    class InterruptingServer:
        server_address = ("127.0.0.1", 9100)
        def serve_forever(self) -> None:
            raise KeyboardInterrupt
        def server_close(self) -> None:
            closed.append(True)
    monkeypatch.setattr("topos.daemon.http_gateway.serve_versioned_http_gateway", lambda *_args, **_kwargs: InterruptingServer())
    assert cli._main_gateway(["serve"]) == 0
    assert closed == [True]
