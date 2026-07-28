"""Deterministic lifecycle boundaries for ``topos daemon serve``.

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
        cli, "serve_versioned_unix_socket", lambda _path, _broker, api=None: FakeServer()
    )


def test_daemon_serve_reports_bpf_initialization_failure_and_still_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A BPF setup exception is component-scoped; daemon shutdown remains clean."""
    import topos.cli as cli
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

    assert cli._main_daemon(["serve", "--socket", str(tmp_path / "topos.sock")]) == 0

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
    import topos.cli as cli
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

    assert cli._main_daemon(["serve", "--socket", str(tmp_path / "topos.sock")]) == 0

    output = capsys.readouterr().out
    assert "Daemon-owned paddr session" in output
    assert expected_output in output
    assert observed["closed"] is True
    component = observed["broker"].health_registry.snapshot().by_name("paddr_lifecycle")
    assert component is not None
    assert component.state.value == expected_state
    assert (component.error.error_code if component.error else None) == expected_code
