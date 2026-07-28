"""Deterministic contracts for the daemon CLI's BPF refresh loop.

The daemon composition is exercised with fakes for every process-facing
collaborator.  In particular, the worker runs synchronously with an Event that
allows precisely one periodic iteration; this proves the CLI's health and
cleanup decisions without a socket, a real thread, a BPF map, or elapsed time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

import topos.cli as cli
import topos.providers.net_bpf as net_bpf
from topos.config import BpfSnapshotConfig, ToposConfig


class _OnePeriodicEvent:
    """Permit exactly one loop body, then make the worker stop."""

    def __init__(self, allow_one_wait: bool) -> None:
        self._allow_one_wait = allow_one_wait
        self.wait_calls: list[float] = []
        self.set_called = False

    def wait(self, interval: float) -> bool:
        self.wait_calls.append(interval)
        if self._allow_one_wait:
            self._allow_one_wait = False
            return False
        return True

    def set(self) -> None:
        self.set_called = True


class _SynchronousThread:
    def __init__(self, *, target: Callable[[], None], daemon: bool) -> None:
        assert daemon is True
        self._target = target
        self.started = False
        self.joined = False

    def start(self) -> None:
        self.started = True
        self._target()

    def join(self, *, timeout: float) -> None:
        assert timeout == 5.0
        self.joined = True

    def is_alive(self) -> bool:
        return False


class _FakeCollector:
    def __init__(self, *, cgroup_root: Path | None, config: ToposConfig) -> None:
        self.cgroup_root = cgroup_root or config.cgroup_root
        self.network_providers: tuple[object, ...] = ()


class _FakeBroker:
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


class _FakeApi:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass


@dataclass
class _BridgeScenario:
    periodic: str
    last_valid: object | None


def _wire_daemon(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario: _BridgeScenario,
) -> tuple[dict[str, object], _OnePeriodicEvent]:
    """Install a hermetic daemon composition and return its observations."""
    observed: dict[str, object] = {"refreshes": [], "writes": []}
    frame_stop = _OnePeriodicEvent(False)
    bpf_stop = _OnePeriodicEvent(True)
    events = [frame_stop, bpf_stop]

    def make_event() -> _OnePeriodicEvent:
        return events.pop(0)

    class FakeBridge:
        def __init__(self, root: Path, **kwargs: object) -> None:
            observed["bridge_root"] = root
            observed["bridge_kwargs"] = kwargs
            self.last_valid_snapshot = scenario.last_valid

        def restore_last_known_good(self, state_dir: Path) -> None:
            observed["restored"] = state_dir

        def refresh_and_write(self, map_name: str, state_dir: Path) -> None:
            observed["initial"] = (map_name, state_dir)

        def refresh(self, map_name: str) -> object:
            observed["refreshes"].append(map_name)  # type: ignore[index]
            if scenario.periodic == "bpf-error":
                raise cli.BpfSnapshotError("periodic map unavailable")
            if scenario.periodic == "unexpected":
                raise RuntimeError("periodic decoder bug")
            return {"snapshot": "fresh"}

        def write_snapshot(self, snapshot: object, state_dir: Path) -> None:
            observed["writes"].append((snapshot, state_dir))  # type: ignore[index]

    class FakeProvider:
        def __init__(self, **kwargs: object) -> None:
            observed["provider_kwargs"] = kwargs

    class FakeServer:
        def __init__(self, broker: _FakeBroker) -> None:
            self._broker = broker

        def serve_forever(self) -> None:
            # The synchronous worker has completed its one iteration before the
            # daemon accepts work, so this captures its observable health state.
            observed["health_before_cleanup"] = self._broker.health_registry.snapshot()

        def server_close(self) -> None:
            observed["server_closed"] = True

    def make_broker(*args: object, **kwargs: object) -> _FakeBroker:
        broker = _FakeBroker(*args, **kwargs)
        observed["broker"] = broker
        return broker

    monkeypatch.setattr(cli.threading, "Event", make_event)
    monkeypatch.setattr(cli.threading, "Thread", _SynchronousThread)
    monkeypatch.setattr(cli, "Collector", _FakeCollector)
    monkeypatch.setattr(cli, "FrameBroker", make_broker)
    monkeypatch.setattr(cli, "DaemonApi", _FakeApi)
    monkeypatch.setattr(cli, "BpfSnapshotBridge", FakeBridge)
    monkeypatch.setattr(net_bpf, "BpfProvider", FakeProvider)
    monkeypatch.setattr(
        cli,
        "serve_versioned_unix_socket",
        lambda _path, broker, _api: FakeServer(broker),
    )
    return observed, bpf_stop


@pytest.mark.parametrize(
    ("scenario", "expected_state", "expected_output"),
    (
        (_BridgeScenario("success", None), "healthy", ""),
        (_BridgeScenario("bpf-error", {"prior": "snapshot"}), "degraded", "preserving last valid"),
        (_BridgeScenario("bpf-error", None), "failed", "BPF snapshot refresh failed: periodic map unavailable"),
        (_BridgeScenario("unexpected", None), "failed", "BPF snapshot unexpected error: periodic decoder bug"),
    ),
)
def test_daemon_cli_bpf_root_runs_one_periodic_iteration_with_bounded_failure_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    scenario: _BridgeScenario,
    expected_state: str,
    expected_output: str,
) -> None:
    """An explicit CLI root overrides config and each retry result is visible."""
    config = ToposConfig(
        cgroup_root=tmp_path / "cgroup",
        bpf_snapshot=BpfSnapshotConfig(
            enabled=True,
            root=tmp_path / "config-pins",
            map_name="config_map",
            interval=99.0,
            state_dir=tmp_path / "config-state",
        ),
    )
    observed, bpf_stop = _wire_daemon(monkeypatch, tmp_path, scenario)
    cli_root = tmp_path / "cli-pins"
    cli_state = tmp_path / "cli-state"
    monkeypatch.setattr(cli, "load", lambda _path: config)

    assert cli._main_daemon(
        [
            "serve", "--socket", str(tmp_path / "topos.sock"),
            "--bpf-root", str(cli_root), "--bpf-interval", "1",
            "--bpf-state-dir", str(cli_state),
        ]
    ) == 0

    # CLI arguments select the CLI root/state and the documented default map;
    # interval is floored at five seconds before either refresh path sees it.
    assert observed["bridge_root"] == cli_root
    assert observed["initial"] == ("topos_cgroup_skb", cli_state)
    assert observed["refreshes"] == ["topos_cgroup_skb"]
    assert bpf_stop.wait_calls == [5.0, 5.0]
    assert bpf_stop.set_called is True
    assert observed["provider_kwargs"] == {"bpf_root": cli_root, "state_dir": cli_state}
    assert observed["broker"].events == ["start", "stop", "join"]  # type: ignore[union-attr]
    assert observed["server_closed"] is True

    bridge = observed["health_before_cleanup"].by_name("bpf_snapshot_bridge")  # type: ignore[union-attr]
    assert bridge is not None and bridge.state.value == expected_state
    output = capsys.readouterr().out
    assert "BPF snapshot bridge enabled" in output
    assert expected_output in output
    if scenario.periodic == "success":
        assert observed["writes"] == [({"snapshot": "fresh"}, cli_state)]


def test_daemon_cli_uses_enabled_config_bpf_selection_when_cli_root_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Configured BPF settings remain the fallback when no CLI override is given."""
    config_root = tmp_path / "config-pins"
    config_state = tmp_path / "config-state"
    config = ToposConfig(
        cgroup_root=tmp_path / "cgroup",
        bpf_snapshot=BpfSnapshotConfig(
            enabled=True, root=config_root, map_name="configured_map",
            interval=7.0, state_dir=config_state,
        ),
    )
    observed, bpf_stop = _wire_daemon(monkeypatch, tmp_path, _BridgeScenario("success", None))
    monkeypatch.setattr(cli, "load", lambda _path: config)

    assert cli._main_daemon(["serve", "--socket", str(tmp_path / "topos.sock")]) == 0

    assert observed["bridge_root"] == config_root
    assert observed["initial"] == ("configured_map", config_state)
    assert observed["refreshes"] == ["configured_map"]
    assert bpf_stop.wait_calls == [7.0, 7.0]
