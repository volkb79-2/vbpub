"""Residual, deterministic boundaries for ``topos daemon serve``.

The serve command normally owns privileged DAMON state.  These tests replace
the collector, broker, server, and lifecycle at its composition boundary so
they exercise only the CLI's outcome and shutdown contract.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


class _Broker:
    def __init__(self, _frames: object, *, health_registry: object, **_kwargs: object) -> None:
        self.health_registry = health_registry
        self.events: list[str] = []

    def start(self) -> None:
        self.events.append("start")

    def stop(self) -> None:
        self.events.append("stop")

    def join(self, *, timeout: float) -> None:
        assert timeout == 5.0
        self.events.append("join")


def test_daemon_serve_adopted_paddr_with_zero_stop_is_quiet_and_stopped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An adopted session may need no stop, but lifecycle health still closes cleanly."""
    import topos.cli as cli
    from topos.config import DamonConfig, ToposConfig
    import topos.daemon.paddr_lifecycle as lifecycle_module
    from topos.daemon.paddr_lifecycle import PaddrLifecycleOutcome

    config = ToposConfig(
        cgroup_root=tmp_path / "cgroup", damon=DamonConfig(paddr_enabled=True)
    )
    observed: dict[str, object] = {}

    class _Collector:
        def __init__(self, cgroup_root: Path | None, config: ToposConfig) -> None:
            self.cgroup_root = cgroup_root or config.cgroup_root
            self.network_providers = ()
            self.damon_root = tmp_path / "damon"

    class _Lifecycle:
        def __init__(self, **_kwargs: object) -> None:
            self.started = False
            self.outcome = PaddrLifecycleOutcome.ADOPTED

        def start(self) -> None:
            self.started = True

        def stop(self) -> int:
            return 0

    class _Server:
        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            observed["closed"] = True

    def _make_broker(*args: object, **kwargs: object) -> _Broker:
        broker = _Broker(*args, **kwargs)
        observed["broker"] = broker
        return broker

    monkeypatch.setattr(cli, "load", lambda _path: config)
    monkeypatch.setattr(cli, "Collector", _Collector)
    monkeypatch.setattr(cli, "FrameBroker", _make_broker)
    monkeypatch.setattr(cli, "DaemonApi", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        cli, "serve_versioned_unix_socket", lambda *_args, **_kwargs: _Server()
    )
    monkeypatch.setattr(lifecycle_module, "DaemonPaddrLifecycle", _Lifecycle)

    assert cli._main_daemon(["serve", "--socket", str(tmp_path / "topos.sock")]) == 0

    output = capsys.readouterr().out
    assert "Daemon-owned paddr session adopted" in output
    assert "Stopped " not in output
    assert observed["closed"] is True
    broker = observed["broker"]
    assert isinstance(broker, _Broker)
    assert broker.events == ["start", "stop", "join"]
    component = broker.health_registry.snapshot().by_name("paddr_lifecycle")
    assert component is not None
    assert component.state.value == "stopped"
    assert component.error is None


def test_daemon_rejects_an_unrecognized_parsed_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The defensive dispatcher fails closed if a parser collaborator is extended wrongly."""
    import topos.cli as cli

    monkeypatch.setattr(
        cli, "parse_daemon_args", lambda _argv: SimpleNamespace(command="unexpected")
    )

    assert cli._main_daemon(["unexpected"]) == 2
    assert capsys.readouterr().err == "unknown daemon command\n"
