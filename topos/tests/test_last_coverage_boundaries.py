"""Deterministic contracts for the final uncommon coverage paths.

These tests stay at public/composition seams: fake lifecycle, filesystem, and
UI collaborators expose observable outcomes without starting privileged
services or waiting on wall-clock time.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import fixture_frame
from topos.config import DamonConfig, ToposConfig
from topos.daemon.bpf_snapshot import _pop_int
from topos.daemon.client import DaemonClient
from topos.daemon.paddr_lifecycle import PaddrLifecycleOutcome
from topos.inspect_files.catalog import InspectFilesKind
from topos.inspect_files.reader import build_inspect_read
from topos.report import _RateSamples, compute_profile


def test_daemon_serve_reports_started_paddr_before_clean_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A lifecycle that starts a session is reported healthy before serving."""
    import topos.cli as cli
    import topos.daemon.paddr_lifecycle as lifecycle_module

    config = ToposConfig(cgroup_root=tmp_path / "cgroup", damon=DamonConfig(paddr_enabled=True))

    class Collector:
        def __init__(self, cgroup_root: Path | None, config: ToposConfig) -> None:
            self.cgroup_root = cgroup_root or config.cgroup_root
            self.network_providers = ()
            self.damon_root = tmp_path / "damon"

    class Broker:
        def __init__(self, _frames: object, *, health_registry: object, **_kwargs: object) -> None:
            self.health_registry = health_registry

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def join(self, *, timeout: float) -> None:
            assert timeout == 5.0

    class Lifecycle:
        outcome = PaddrLifecycleOutcome.STARTED
        started = True

        def __init__(self, **_kwargs: object) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self) -> int:
            return 1

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
    assert "Daemon-owned paddr session started" in capsys.readouterr().out


def test_nonintegral_float_identifier_is_rejected() -> None:
    """JSON float identifiers must not be truncated into a different cgroup id."""
    assert _pop_int({"cgroup_id": 7.5}, "cgroup_id", 0) is None


def test_health_component_error_without_code_preserves_none() -> None:
    """A valid optional error code remains absent rather than becoming a sentinel."""
    components = [
        {
            "name": name,
            "state": "healthy",
            "detail": "ready",
            "last_attempt_ts": None,
            "last_success_ts": None,
            "consecutive_failures": 0,
            "state_change_count": 0,
            "error": None,
        }
        for name in ("collector", "bpf_snapshot_bridge", "paddr_lifecycle")
    ]
    components[0]["error"] = {"message": "collector delayed"}

    health = DaemonClient(Path("/fixture/daemon.sock"))._parse_health_payload(
        {"schema_version": 1, "capability": "health-v1", "components": components}
    )
    assert health.snapshots[0].error is not None
    assert health.snapshots[0].error.error_code is None


def test_inspect_error_that_cannot_fit_is_marked_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A denied file never overflows the aggregate rendered-output budget."""
    import topos.inspect_files.reader as reader

    container_id = "a" * 64
    path = tmp_path / "containers" / container_id / f"{container_id}-json.log"
    max_bytes = len(str(path)) + 6  # header fits; the rendered error cannot.
    monkeypatch.setattr(reader, "_confine_and_open", lambda *_args: (_ for _ in ()).throw(ValueError("denied")))

    result = build_inspect_read(
        InspectFilesKind.DOCKER_JSON_LOG.value,
        container_id,
        inspect_files=True,
        admin=True,
        max_bytes=max_bytes,
        fixture_root=tmp_path,
        is_root=lambda: True,
    )
    assert result.truncated_bytes is True
    assert result.content == ""


def test_profile_retains_rate_only_metric_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rate-only groups contribute their computed samples and sample count."""
    import topos.report as report_module

    monkeypatch.setattr(
        report_module,
        "_group_frames",
        lambda _frames, _group_by: {"entity": {"cpu_pct": _RateSamples(values=[3.0, 5.0])}},
    )

    profiles = compute_profile([fixture_frame()])
    assert profiles[0].sample_count == 2
    assert profiles[0].rates["cpu_pct"]["max"] == 5.0


def test_ui_smoke_waits_for_a_later_frame_without_wall_clock_sleep(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """The smoke loop accepts a frame delivered after its first event-loop turn."""
    import topos.ui.app as app_module

    class App:
        frames_received = 0
        view_mode = "tree"
        profile_name = "auto"

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        def run_test(self, **_kwargs: object) -> object:
            app = self

            class Pilot:
                async def pause(self) -> None:
                    app.frames_received += 1

            class Context:
                async def __aenter__(self) -> Pilot:
                    return Pilot()

                async def __aexit__(self, *_args: object) -> None:
                    return None

            return Context()

    monkeypatch.setattr(app_module, "ToposApp", App)
    monkeypatch.setattr(app_module.asyncio, "get_event_loop", lambda: SimpleNamespace(time=lambda: 0.0))

    result = asyncio.run(
        app_module._run_ui_smoke(
            (), config=ToposConfig(), cgroup_root=Path("/cgroup"), proc_root=Path("/proc"),
            profile=None, source_label="LIVE", replay_driver=None, replay_step=False,
            replay_speed=1.0,
        )
    )
    assert result == "ui smoke ok frames=1 view=tree profile=auto"
