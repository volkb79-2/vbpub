"""Deterministic lifecycle boundaries for ``toposd run --self-bind``.

The daemon CLI owns the composition of optional BPF and paddr components.  The
tests deliberately replace every privileged or long-running collaborator: no
socket, BPF map, DAMON sysfs, broker thread, or background refresh loop is
started here.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class _FakeBroker:
    def __init__(self, _frames, *, health_registry, **_kwargs) -> None:
        self.health_registry = health_registry
        self.events: list[str] = []

    def start(self) -> None:
        self.events.append("start")

    def stop(self) -> None:
        self.events.append("stop")

    def join(self, *, timeout: float) -> None:
        assert timeout == 5.0
        self.events.append("join")


class _FakeApi:
    def __init__(self, *_args, **_kwargs) -> None:
        pass


def _wire_one_shot_server(monkeypatch: pytest.MonkeyPatch, cli, observed: dict) -> None:
    class FakeServer:
        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            observed["closed"] = True

    def make_broker(*args, **kwargs):
        broker = _FakeBroker(*args, **kwargs)
        observed["broker"] = broker
        return broker

    monkeypatch.setattr(cli, "FrameBroker", make_broker)
    monkeypatch.setattr(cli, "DaemonApi", _FakeApi)
    monkeypatch.setattr(
        cli, "serve_versioned_unix_socket", lambda _path, _broker, api=None, **_kwargs: FakeServer()
    )


def test_daemon_serve_reports_bpf_initialization_failure_and_still_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A BPF setup exception is component-scoped; daemon shutdown remains clean."""
    import topos.daemon.run as cli
    from conftest import run_toposd_self_bind
    from topos.config import BpfSnapshotConfig, ToposConfig

    config = ToposConfig(
        cgroup_root=tmp_path / "cgroup",
        bpf_snapshot=BpfSnapshotConfig(enabled=True, root=tmp_path / "pins"),
    )
    observed: dict = {}

    class FakeCollector:
        def __init__(self, cgroup_root, config) -> None:
            self.cgroup_root = cgroup_root or config.cgroup_root
            self.network_providers = ()

    class FailingBridge:
        def __init__(self, *_args, **_kwargs) -> None:
            raise RuntimeError("bridge setup denied")

    _wire_one_shot_server(monkeypatch, cli, observed)
    monkeypatch.setattr(cli, "load", lambda _path: config)
    monkeypatch.setattr(cli, "Collector", FakeCollector)
    monkeypatch.setattr(cli, "BpfSnapshotBridge", FailingBridge)

    assert run_toposd_self_bind(tmp_path / "topos.sock") == 0

    output = capsys.readouterr().out
    assert "Failed to start BPF snapshot bridge: bridge setup denied" in output
    assert observed["closed"] is True
    assert observed["broker"].events == ["start", "stop", "join"]
    bpf = observed["broker"].health_registry.snapshot().by_name("bpf_snapshot_bridge")
    assert bpf is not None
    assert bpf.state.value == "failed"
    assert bpf.error is not None and bpf.error.error_code == "bpf_start_failed"


@pytest.mark.parametrize(
    ("outcome_name", "stop_error", "expected_output", "expected_state", "expected_code"),
    (
        ("STARTED", None, "Stopped 2 daemon-owned paddr session", "stopped", None),
        ("ADOPTED", RuntimeError("stop denied"), "Failed to stop paddr session: stop denied", "failed", "paddr_stop_failed"),
    ),
)
def test_daemon_serve_paddr_lifecycle_records_outcome_and_shutdown_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    outcome_name: str,
    stop_error: Exception | None,
    expected_output: str,
    expected_state: str,
    expected_code: str | None,
) -> None:
    """paddr ownership is started/adopted then reported accurately at cleanup."""
    import topos.daemon.run as cli
    from conftest import run_toposd_self_bind
    from topos.config import DamonConfig, ToposConfig
    from topos.daemon.paddr_lifecycle import PaddrLifecycleOutcome
    import topos.daemon.paddr_lifecycle as lifecycle_module

    config = ToposConfig(
        cgroup_root=tmp_path / "cgroup", damon=DamonConfig(paddr_enabled=True)
    )
    observed: dict = {}

    class FakeCollector:
        def __init__(self, cgroup_root, config) -> None:
            self.cgroup_root = cgroup_root or config.cgroup_root
            self.network_providers = ()
            self.damon_root = tmp_path / "damon"

    class FakeLifecycle:
        def __init__(self, **_kwargs) -> None:
            self.started = False
            self.outcome = PaddrLifecycleOutcome[outcome_name]

        def start(self) -> None:
            self.started = True

        def stop(self) -> int:
            if stop_error is not None:
                raise stop_error
            return 2

    _wire_one_shot_server(monkeypatch, cli, observed)
    monkeypatch.setattr(cli, "load", lambda _path: config)
    monkeypatch.setattr(cli, "Collector", FakeCollector)
    monkeypatch.setattr(lifecycle_module, "DaemonPaddrLifecycle", FakeLifecycle)

    assert run_toposd_self_bind(tmp_path / "topos.sock") == 0

    output = capsys.readouterr().out
    assert "Daemon-owned paddr session" in output
    assert expected_output in output
    assert observed["closed"] is True
    component = observed["broker"].health_registry.snapshot().by_name("paddr_lifecycle")
    assert component is not None
    assert component.state.value == expected_state
    assert (component.error.error_code if component.error else None) == expected_code


def test_daemon_serve_exits_two_and_closes_history_when_socket_is_in_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import topos.daemon.run as cli
    from conftest import run_toposd_self_bind
    from topos.config import HistoryConfig, PersistConfig, ToposConfig
    from topos.daemon.broker import SocketInUseError

    config = ToposConfig(
        cgroup_root=tmp_path / "cgroup",
        history=HistoryConfig(
            daemon=PersistConfig(enabled=True, dir=tmp_path / "hist", compression="none")
        ),
    )
    closed: list[bool] = []

    class SpyStore(cli.PersistentHistoryStore):
        def close(self) -> None:
            closed.append(True)
            super().close()

    class FakeCollector:
        def __init__(self, cgroup_root, config) -> None:
            self.cgroup_root = cgroup_root or config.cgroup_root
            self.network_providers = ()

    def in_use(*_args, **_kwargs):
        raise SocketInUseError("socket is served by another daemon")

    observed: dict = {}
    _wire_one_shot_server(monkeypatch, cli, observed)
    monkeypatch.setattr(cli, "serve_versioned_unix_socket", in_use)
    monkeypatch.setattr(cli, "PersistentHistoryStore", SpyStore)
    monkeypatch.setattr(cli, "load", lambda _path: config)
    monkeypatch.setattr(cli, "Collector", FakeCollector)

    assert run_toposd_self_bind(tmp_path / "topos.sock") == 2
    assert "socket is served by another daemon" in capsys.readouterr().err
    assert closed == [True]


def test_run_daemon_adopts_passed_listener_and_keeps_shutdown_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import socket

    import topos.daemon.run as daemon_run
    from topos.config import ToposConfig

    config = ToposConfig(cgroup_root=tmp_path / "cgroup")
    observed: dict = {}
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(tmp_path / "activated.sock"))
    listener.listen()

    class FakeCollector:
        def __init__(self, cgroup_root, config) -> None:
            self.cgroup_root = cgroup_root or config.cgroup_root
            self.config = config
            self.network_providers = ()
            self.damon_root = tmp_path / "damon"

    class FakeHistoryStore:
        def __init__(self, _config) -> None:
            pass

        def close(self) -> None:
            observed["history_closed"] = True

    class FakeServer:
        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            listener.close()
            observed["closed"] = True

    def adopt(passed, _broker, _api, *, owned_path):
        assert passed is listener
        assert owned_path is None
        return FakeServer()

    monkeypatch.setattr(daemon_run, "load", lambda _path: config)
    monkeypatch.setattr(daemon_run, "Collector", FakeCollector)
    monkeypatch.setattr(daemon_run, "PersistentHistoryStore", FakeHistoryStore)
    monkeypatch.setattr(daemon_run, "FrameBroker", _FakeBroker)
    monkeypatch.setattr(daemon_run, "DaemonApi", _FakeApi)
    monkeypatch.setattr(daemon_run, "serve_versioned_listener", adopt)

    assert daemon_run.run_daemon(
        tmp_path / "activated.sock", listener=listener, socket_mode=0o660
    ) == 0
    assert observed["closed"] is True
    assert listener.fileno() == -1
    assert "(activated)" in capsys.readouterr().out


@pytest.mark.parametrize("failure", ("socket-in-use", "unexpected"))
def test_run_daemon_start_failure_closes_passed_fd_and_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    import socket
    from types import SimpleNamespace

    import topos.daemon.run as daemon_run
    from topos.config import HistoryConfig, PersistConfig, ToposConfig
    from topos.daemon.broker import SocketInUseError

    config = ToposConfig(
        cgroup_root=tmp_path / "cgroup",
        history=HistoryConfig(
            daemon=PersistConfig(enabled=True, dir=tmp_path / "history", compression="none")
        ),
    )
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    closed: list[bool] = []

    class FakeCollector:
        def __init__(self, cgroup_root, config) -> None:
            self.cgroup_root = cgroup_root or config.cgroup_root
            self.config = config
            self.network_providers = ()

    class FakeHistoryStore:
        def __init__(self, _config) -> None:
            pass

        def stats(self):
            return SimpleNamespace(degraded=False, recovery_state="empty", segment_count=0)

        def close(self) -> None:
            closed.append(True)

    def fail_adoption(*_args, **_kwargs):
        if failure == "socket-in-use":
            raise SocketInUseError("socket is served by another daemon")
        raise RuntimeError("adoption failed")

    monkeypatch.setattr(daemon_run, "load", lambda _path: config)
    monkeypatch.setattr(daemon_run, "Collector", FakeCollector)
    monkeypatch.setattr(daemon_run, "PersistentHistoryStore", FakeHistoryStore)
    monkeypatch.setattr(daemon_run, "FrameBroker", _FakeBroker)
    monkeypatch.setattr(daemon_run, "DaemonApi", _FakeApi)
    monkeypatch.setattr(daemon_run, "serve_versioned_listener", fail_adoption)

    if failure == "socket-in-use":
        assert daemon_run.run_daemon(
            tmp_path / "activated.sock", listener=listener, socket_mode=0o660
        ) == 2
        assert "socket is served by another daemon" in capsys.readouterr().err
    else:
        with pytest.raises(RuntimeError, match="adoption failed"):
            daemon_run.run_daemon(
                tmp_path / "activated.sock", listener=listener, socket_mode=0o660
            )

    assert listener.fileno() == -1
    assert closed == [True]
