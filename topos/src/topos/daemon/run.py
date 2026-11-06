"""Run the read-only topos daemon on an activated or explicitly bound socket."""

from __future__ import annotations

import socket
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

from topos.collect.collector import Collector
from topos.config import load
from topos.damon.passive import DEFAULT_DAMON_ROOT
from topos.daemon import (
    BpfSnapshotBridge,
    BpfSnapshotError,
    FrameBroker,
    FrameBrokerError,
    PersistentHistoryStore,
)
from topos.daemon.api import ApiLimits, DaemonApi, serve_versioned_listener, serve_versioned_unix_socket
from topos.daemon.broker import SocketInUseError
from topos.daemon.component_health import ComponentError, ComponentHealthRegistry
from topos.daemon.lifecycle import ShutdownCoordinator
from topos.model import Frame
from topos.record.live import live_frame_stream


def _persisting_frame_stream(source: Iterator[Frame], store: PersistentHistoryStore) -> Iterator[Frame]:
    """Persist each frame once as it flows to the broker."""
    for frame in source:
        store.append(frame)
        yield frame


def run_daemon(
    socket_path: Path,
    *,
    listener: socket.socket | None,
    socket_mode: int,
    topos_config: Path | None = None,
    cgroup_root: Path | None = None,
    history_size: int = 120,
    bpf_root: Path | None = None,
    bpf_interval: float = 30.0,
    bpf_state_dir: Path | None = None,
) -> int:
    """Run the daemon until shutdown, serving a passed fd or self-binding.

    ``listener`` transfers ownership of an activated listening socket to this
    function. ``None`` requests the explicit development self-bind path.
    """
    config = load(topos_config)
    collector = Collector(cgroup_root=cgroup_root, config=config)

    health_registry = ComponentHealthRegistry()
    health_registry.mark_starting("collector", detail="collector initialized")

    history_store = PersistentHistoryStore(config.history.daemon)
    if config.history.daemon.enabled:
        history_stats = history_store.stats()
        if history_stats.degraded:
            health_registry.record_degraded(
                "persistent_history",
                detail=f"persistent history degraded at startup: {history_stats.degraded_reason}",
                error=ComponentError(
                    message="persistent history degraded at startup",
                    error_code="persistent_history_degraded",
                ),
            )
        else:
            health_registry.record_success(
                "persistent_history",
                detail=f"recovery_state={history_stats.recovery_state} segments={history_stats.segment_count}",
            )
    else:
        health_registry.mark_disabled("persistent_history", detail="disabled by default (see P91-REPORT.md)")

    frame_stop = threading.Event()
    frame_stream: Iterator[Frame] = live_frame_stream(collector, stop_event=frame_stop)
    if config.history.daemon.enabled:
        frame_stream = _persisting_frame_stream(frame_stream, history_store)

    shutdown = ShutdownCoordinator()
    broker = FrameBroker(
        frame_stream,
        history_size=history_size,
        health_registry=health_registry,
        stop_callback=frame_stop.set,
        terminal_callback=shutdown.on_producer_terminal,
    )
    api = DaemonApi(
        broker,
        limits=ApiLimits(history_capacity=history_size),
        health_registry=health_registry,
    )

    try:
        if listener is None:
            server = serve_versioned_unix_socket(socket_path, broker, api, mode=socket_mode)
            start_mode = "self-bind"
        else:
            server = serve_versioned_listener(listener, broker, api, owned_path=None)
            start_mode = "activated"
    except SocketInUseError as exc:
        if listener is not None:
            listener.close()
        if config.history.daemon.enabled:
            history_store.close()
        print(f"topos daemon: {exc}", file=sys.stderr, flush=True)
        return 2
    except Exception:
        if listener is not None:
            listener.close()
        if config.history.daemon.enabled:
            history_store.close()
        raise

    shutdown.bind_server(server)
    # The real address: systemd's socket path when activated (which may differ from the
    # configured one), else the path this process just bound.
    bound_address = listener.getsockname() if listener is not None else str(socket_path)
    print(f"serving read-only topos frames on {bound_address} ({start_mode})", flush=True)

    health_registry.mark_disabled("bpf_snapshot_bridge", detail="disabled by default")
    health_registry.mark_disabled("paddr_lifecycle", detail="disabled by default")

    bpf_bridge: BpfSnapshotBridge | None = None
    bpf_thread: threading.Thread | None = None
    bpf_stop = threading.Event()

    bpf_enabled = False
    selected_bpf_root: Path | None = None
    selected_bpf_interval = 30.0
    if bpf_root is not None:
        bpf_enabled = True
        selected_bpf_root = bpf_root
        selected_bpf_interval = max(5.0, bpf_interval)
    elif config.bpf_snapshot.enabled and config.bpf_snapshot.root is not None:
        bpf_enabled = True
        selected_bpf_root = config.bpf_snapshot.root
        selected_bpf_interval = max(5.0, config.bpf_snapshot.interval)

    if bpf_enabled and selected_bpf_root is not None:
        health_registry.mark_starting("bpf_snapshot_bridge", detail="starting BPF snapshot bridge")
        try:
            map_name = config.bpf_snapshot.map_name if bpf_root is None else "topos_cgroup_skb"
            state_dir = bpf_state_dir if bpf_state_dir is not None else config.bpf_snapshot.state_dir
            bpf_bridge = BpfSnapshotBridge(
                selected_bpf_root,
                command_runner=None,
                cgroup_root=collector.cgroup_root,
            )
            bpf_bridge.restore_last_known_good(state_dir)

            from topos.providers.net_bpf import BpfProvider as BpfProv
            from topos.providers.net_bpf import staleness_limit_s

            bpf_provider = BpfProv(
                bpf_root=selected_bpf_root,
                state_dir=state_dir,
                max_age_s=staleness_limit_s(selected_bpf_interval),
            )
            existing_providers = collector.network_providers or ()
            collector.network_providers = (bpf_provider, *existing_providers)

            print(
                f"BPF snapshot bridge enabled: root={selected_bpf_root} "
                f"map={map_name} interval={selected_bpf_interval}s state_dir={state_dir}",
                flush=True,
            )

            try:
                bpf_bridge.refresh_and_write(map_name, state_dir)
                print("BPF snapshot refreshed on startup", flush=True)
                health_registry.record_success("bpf_snapshot_bridge", detail="initial BPF refresh succeeded")
            except BpfSnapshotError as exc:
                if bpf_bridge.last_valid_snapshot is not None:
                    health_registry.record_degraded(
                        "bpf_snapshot_bridge",
                        detail="initial BPF refresh failed; preserving last valid snapshot",
                        error=ComponentError(
                            message=f"initial BPF refresh failed: {exc}",
                            error_code="bpf_initial_refresh_failed",
                        ),
                    )
                else:
                    health_registry.record_failure(
                        "bpf_snapshot_bridge",
                        detail="initial BPF refresh failed; no valid snapshot",
                        error=ComponentError(
                            message=f"initial BPF refresh failed: {exc}",
                            error_code="bpf_initial_refresh_failed",
                        ),
                    )
                print(
                    f"BPF snapshot initial refresh failed (continuing with periodic retry): {exc}",
                    flush=True,
                )

            def _bpf_refresh_loop() -> None:
                while not bpf_stop.wait(selected_bpf_interval):
                    try:
                        snapshot = bpf_bridge.refresh(map_name)
                        bpf_bridge.write_snapshot(snapshot, state_dir)
                        health_registry.record_success("bpf_snapshot_bridge", detail="BPF refresh succeeded")
                    except BpfSnapshotError as exc:
                        last = bpf_bridge.last_valid_snapshot
                        if last is not None:
                            health_registry.record_degraded(
                                "bpf_snapshot_bridge",
                                detail="BPF refresh failed; preserving last valid snapshot",
                                error=ComponentError(
                                    message=f"BPF refresh failed: {exc}",
                                    error_code="bpf_refresh_failed",
                                ),
                            )
                            print(f"BPF snapshot refresh failed (preserving last valid): {exc}", flush=True)
                        else:
                            health_registry.record_failure(
                                "bpf_snapshot_bridge",
                                detail="BPF refresh failed; no valid snapshot",
                                error=ComponentError(
                                    message=f"BPF refresh failed: {exc}",
                                    error_code="bpf_refresh_failed",
                                ),
                            )
                            print(f"BPF snapshot refresh failed: {exc}", flush=True)
                    except Exception as exc:
                        health_registry.record_failure(
                            "bpf_snapshot_bridge",
                            detail="BPF refresh encountered an unexpected error",
                            error=ComponentError(
                                message=f"BPF refresh encountered an unexpected error: {exc}",
                                error_code="bpf_unexpected_error",
                            ),
                        )
                        print(f"BPF snapshot unexpected error: {exc}", flush=True)

            bpf_thread = threading.Thread(target=_bpf_refresh_loop, daemon=True)
            bpf_thread.start()
        except Exception as exc:
            health_registry.record_failure(
                "bpf_snapshot_bridge",
                detail="failed to start BPF snapshot bridge",
                error=ComponentError(
                    message=f"failed to start BPF snapshot bridge: {exc}",
                    error_code="bpf_start_failed",
                ),
            )
            print(f"Failed to start BPF snapshot bridge: {exc}", flush=True)

    from topos.daemon.paddr_lifecycle import (
        DaemonPaddrLifecycle,
        PaddrLifecycleOutcome,
        PaddrLifecycleStartError,
    )

    paddr_lifecycle = DaemonPaddrLifecycle(
        config=config.damon,
        damon_root=getattr(collector, "damon_root", DEFAULT_DAMON_ROOT),
    )
    if config.damon.paddr_enabled:
        health_registry.mark_starting("paddr_lifecycle", detail="starting paddr lifecycle")
        try:
            paddr_lifecycle.start()
            match paddr_lifecycle.outcome:
                case PaddrLifecycleOutcome.STARTED:
                    health_registry.record_success("paddr_lifecycle", detail="paddr session started")
                    print("Daemon-owned paddr session started", flush=True)
                case PaddrLifecycleOutcome.ADOPTED:
                    health_registry.record_success("paddr_lifecycle", detail="paddr session adopted")
                    print("Daemon-owned paddr session adopted", flush=True)
        except Exception as exc:
            health_registry.record_failure(
                "paddr_lifecycle",
                detail="paddr lifecycle start failed",
                error=ComponentError(
                    message=f"paddr lifecycle start failed: {exc}",
                    error_code=(
                        "paddr_start_failed"
                        if isinstance(exc, PaddrLifecycleStartError)
                        else "paddr_unexpected_start_error"
                    ),
                ),
            )
            print(f"Paddr lifecycle start failed (daemon continues without paddr): {exc}", flush=True)

    restore_signals = shutdown.install_signal_handlers()
    try:
        broker.start()
        server.serve_forever()
    except KeyboardInterrupt:
        shutdown.operator_interrupt()
    finally:
        restore_signals()
        if shutdown.producer_failed:
            print(f"topos daemon exiting: {shutdown.reason}", file=sys.stderr, flush=True)
        broker.stop()
        collector_stopped = False
        try:
            broker.join(timeout=5.0)
            collector_stopped = True
        except FrameBrokerError as exc:
            health_registry.record_failure(
                "collector",
                detail="collector producer did not stop cleanly",
                error=ComponentError(
                    message=f"collector producer did not stop cleanly: {exc}",
                    error_code="collector_shutdown_failed",
                ),
            )
            print(f"Collector producer shutdown failed: {exc}", flush=True)
        if bpf_thread is not None:
            health_registry.mark_stopping("bpf_snapshot_bridge", detail="shutting down BPF bridge")
            bpf_stop.set()
            bpf_thread.join(timeout=5.0)
            if bpf_thread.is_alive():
                health_registry.record_failure(
                    "bpf_snapshot_bridge",
                    detail="BPF bridge did not stop before the shutdown deadline",
                    error=ComponentError(
                        message="BPF bridge shutdown timed out",
                        error_code="bpf_shutdown_timeout",
                    ),
                )
            else:
                health_registry.mark_stopped("bpf_snapshot_bridge", detail="BPF bridge stopped")
        if paddr_lifecycle.started:
            adopted = paddr_lifecycle.outcome is PaddrLifecycleOutcome.ADOPTED
            health_registry.mark_stopping("paddr_lifecycle", detail="stopping paddr lifecycle")
            try:
                stopped = paddr_lifecycle.stop()
                if stopped:
                    print(f"Stopped {stopped} daemon-owned paddr session", flush=True)
                health_registry.mark_stopped(
                    "paddr_lifecycle",
                    detail=(
                        "daemon lifecycle stopped; adopted paddr session remains active"
                        if adopted
                        else "paddr lifecycle stopped"
                    ),
                )
            except Exception as exc:
                health_registry.record_failure(
                    "paddr_lifecycle",
                    detail="paddr lifecycle stop failed",
                    error=ComponentError(
                        message=f"paddr lifecycle stop failed: {exc}",
                        error_code="paddr_stop_failed",
                    ),
                )
                print(f"Failed to stop paddr session: {exc}", flush=True)
        if collector_stopped:
            health_registry.mark_stopped("collector", detail="collector stopped")
        if config.history.daemon.enabled:
            health_registry.mark_stopping("persistent_history", detail="flushing persistent history")
            history_store.close()
            health_registry.mark_stopped("persistent_history", detail="persistent history flushed")
        server.server_close()
    return 1 if shutdown.producer_failed else 0


__all__ = ["run_daemon"]
