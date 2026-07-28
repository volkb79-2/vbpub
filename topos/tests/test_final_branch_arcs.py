"""Real, deterministic contracts for the final global-coverage branch arcs."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from topos.config import DamonConfig, ToposConfig
from topos.daemon.bpf_snapshot import _pop_int
from topos.daemon.paddr_lifecycle import PaddrLifecycleOutcome
from topos.report import _RateSamples, compute_profile


def test_non_numeric_bpf_identifier_is_rejected_without_coercion() -> None:
    """A string identifier is neither an integral JSON number nor a cgroup id."""
    assert _pop_int({"cgroup_id": "7"}, "cgroup_id", 0) is None


def test_confined_open_closes_leaf_if_buffer_wrapper_cannot_be_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed ``fdopen`` leaves raw-descriptor ownership with the reader."""
    import topos.inspect_files.reader as reader

    allow_root = tmp_path / "allowed"
    allow_root.mkdir()
    leaf = allow_root / "metrics"
    leaf.write_text("safe\n", encoding="utf-8")
    closed: list[int] = []
    real_close = os.close

    def record_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    def refuse_wrapper(fd: int, _mode: str):
        raise OSError("simulated wrapper allocation failure")

    monkeypatch.setattr(reader.os, "close", record_close)
    monkeypatch.setattr(reader.os, "fdopen", refuse_wrapper)

    with pytest.raises(OSError, match="simulated wrapper allocation failure"):
        reader._confine_and_open(leaf, allow_root)

    # The leaf descriptor is distinct from the root descriptor and must be
    # closed on this failure path rather than leaked into the process.
    assert len(closed) == 2


def test_profile_processes_multiple_rate_metrics_in_one_group(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every rate metric contributes to a rate-only group's profile."""
    import topos.report as report_module

    monkeypatch.setattr(
        report_module,
        "_group_frames",
        lambda _frames, _group_by: {
            "entity": {
                "cpu_pct": _RateSamples(values=[3.0, 5.0]),
                "io_r_bps": _RateSamples(values=[7.0, 11.0]),
            }
        },
    )

    profiles = compute_profile([SimpleNamespace(ts=1.0)])

    assert profiles[0].sample_count == 2
    assert set(profiles[0].rates) == {"cpu_pct", "io_r_bps"}
    assert profiles[0].rates["io_r_bps"]["max"] == 11.0


def test_daemon_serve_continues_if_lifecycle_reports_disabled_after_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The daemon remains available if a lifecycle collaborator becomes disabled."""
    import topos.cli as cli
    import topos.daemon.paddr_lifecycle as lifecycle_module

    config = ToposConfig(cgroup_root=tmp_path / "cgroup", damon=DamonConfig(paddr_enabled=True))

    class Collector:
        def __init__(self, cgroup_root: Path | None, config: ToposConfig) -> None:
            self.cgroup_root = cgroup_root or config.cgroup_root
            self.network_providers = ()
            self.damon_root = tmp_path / "damon"

    class Broker:
        def __init__(self, _frames: object, **_kwargs: object) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def join(self, *, timeout: float) -> None:
            assert timeout == 5.0

    class Lifecycle:
        outcome = PaddrLifecycleOutcome.DISABLED
        started = False

        def __init__(self, **_kwargs: object) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self) -> int:
            return 0

    class Server:
        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            return None

    monkeypatch.setattr(cli, "load", lambda _path: config)
    monkeypatch.setattr(cli, "Collector", Collector)
    monkeypatch.setattr(cli, "FrameBroker", Broker)
    monkeypatch.setattr(cli, "DaemonApi", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli, "serve_versioned_unix_socket", lambda *_args, **_kwargs: Server())
    monkeypatch.setattr(lifecycle_module, "DaemonPaddrLifecycle", Lifecycle)

    assert cli._main_daemon(["serve", "--socket", str(tmp_path / "topos.sock")]) == 0


def test_ui_smoke_retries_until_its_monotonic_deadline_without_a_frame(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frame-free UI smoke fails closed after cooperative event-loop turns."""
    import topos.ui.app as app_module

    class App:
        frames_received = 0
        view_mode = "tree"
        profile_name = "auto"

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        def run_test(self, **_kwargs: object) -> object:
            class Pilot:
                async def pause(self) -> None:
                    return None

            class Context:
                async def __aenter__(self) -> Pilot:
                    return Pilot()

                async def __aexit__(self, *_args: object) -> None:
                    return None

            return Context()

    clock_values = iter((0.0, 1.0, 11.0))
    monkeypatch.setattr(app_module, "ToposApp", App)
    monkeypatch.setattr(
        app_module.asyncio, "get_event_loop", lambda: SimpleNamespace(time=lambda: next(clock_values))
    )

    with pytest.raises(RuntimeError, match="ui smoke did not receive a frame"):
        asyncio.run(
            app_module._run_ui_smoke(
                (), config=ToposConfig(), cgroup_root=Path("/cgroup"), proc_root=Path("/proc"),
                profile=None, source_label="LIVE", replay_driver=None, replay_step=False,
                replay_speed=1.0,
            )
        )
