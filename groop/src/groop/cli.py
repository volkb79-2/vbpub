from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from groop import __version__
from groop.bpf_gate import report_to_jsonable, render_report, run_bpf_gate
from groop.collect.collector import Collector
from groop.collect.cgroup import _validate_slice_name
from groop.collect.dockerjoin import ContainerResolveError, resolve_container_key
from groop.config import BpfSnapshotConfig, load
from groop.damon.control import APPROVAL_TEXT, DamonControlError, RootRequired, stop_owned_sessions
from groop.damon.passive import DEFAULT_DAMON_ROOT
from groop.damon.paddr import paddr_confirmation_text, plan_start_paddr_session, start_planned_paddr_session
from groop.daemon.client import DaemonClientError, DaemonConnectError, DaemonProtocolError, DaemonResponseError, current_frame
from groop.daemon.deploy import (
    DEFAULT_DAEMON_GROUP,
    DEFAULT_DAEMON_SOCKET,
    DEFAULT_SERVICE_DEST,
    DEFAULT_TMPFILES_DEST,
    build_install_plan,
    install_plan_to_jsonable,
    preflight_daemon_deployment,
    preflight_report_to_jsonable,
    render_install_plan_text,
    render_preflight_text,
)
from groop.daemon import (
    BpfSnapshotBridge,
    BpfSnapshotError,
    FrameBroker,
    FrameBrokerError,
)
from groop.daemon.api import ApiLimits, DaemonApi, serve_versioned_unix_socket
from groop.daemon.component_health import (
    ComponentError,
    ComponentHealthRegistry,
)
from groop.model import frame_to_jsonable
from groop.record.live import live_frame_stream
from groop.record.headless import run_headless_record
from groop.record.replay import ReplayDriver, format_frame_summary
from groop.record.writer import RecordWriter
from groop.registry import METRIC_GROUPS, parse_metrics_selector
from groop.snapshot import inspect_bundle


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="groop")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--once", action="store_true", help="collect one frame and exit")
    parser.add_argument("--record", type=Path, default=None, help="record live frames to JSONL or JSONL.zst")
    parser.add_argument("--replay", type=Path, default=None, help="replay frames from a JSONL or JSONL.zst recording")
    parser.add_argument(
        "--attach",
        nargs="?",
        const=DEFAULT_DAEMON_SOCKET,
        type=Path,
        default=None,
        help=f"attach to daemon frames over a Unix socket (default: {DEFAULT_DAEMON_SOCKET})",
    )
    parser.add_argument("--speed", type=float, default=1.0, help="replay speed multiplier")
    parser.add_argument("--step", action="store_true", help="step through replay without wall-clock pacing")
    parser.add_argument("--json", action="store_true", help="emit JSON for --once")
    parser.add_argument("--pretty-json", action="store_true", help="pretty-print JSON output")
    parser.add_argument("--config", type=Path, default=None, help="load config from PATH instead of the default XDG location")
    parser.add_argument("--profile", type=str, default=None, help="override the active UI column profile for this run")
    parser.add_argument("--cgroup-root", type=Path, default=None, help="cgroup v2 root for live or fixture collection")
    parser.add_argument("--headless", action="store_true", help="run without UI (requires --record FILE)")
    parser.add_argument("--interval", type=float, default=None, help="collector interval override in seconds")
    parser.add_argument("--duration", type=float, default=None, help="stop after S seconds")
    parser.add_argument("--frames", type=int, default=None, help="stop after K frames")
    parser.add_argument("--ui-smoke", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--entities", action="append", default=None, type=str, dest="entities",
                        help="include entities matching GLOB (repeatable, fnmatch against EntityKey path)")
    parser.add_argument("--slice", type=str, default=None,
                        help="include a *.slice (or other) entity key and everything under it")
    parser.add_argument("--metrics", type=str, default="full",
                        help="metric output mode: full (default), compact (keep only memory/PSI/refault families), "
                             "or a comma-separated list of metric names and/or family names from the registry")
    parser.add_argument("--container", action="append", default=None, type=str, dest="container_selectors",
                        help="include entities matching a docker container name/prefix (repeatable)")
    return parser.parse_args(argv)


def parse_damon_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="groop damon")
    subparsers = parser.add_subparsers(dest="command", required=True)
    stop_parser = subparsers.add_parser("stop", help="stop groop-owned DAMON sessions")
    stop_parser.add_argument("--all-mine", action="store_true", help="stop every groop-owned DAMON session")
    stop_parser.add_argument("--damon-root", type=Path, default=DEFAULT_DAMON_ROOT, help="DAMON kdamonds sysfs root")
    stop_parser.add_argument("--state-dir", type=Path, default=None, help="groop state dir containing DAMON ownership markers")
    stop_parser.add_argument("--allow-non-root-fixture", action="store_true", help=argparse.SUPPRESS)
    paddr_parser = subparsers.add_parser("paddr", help="manage host paddr DAMON sessions")
    paddr_subparsers = paddr_parser.add_subparsers(dest="paddr_command", required=True)
    paddr_start = paddr_subparsers.add_parser("start", help="start a groop-owned host paddr session")
    paddr_start.add_argument("--damon-root", type=Path, default=DEFAULT_DAMON_ROOT, help="DAMON kdamonds sysfs root")
    paddr_start.add_argument("--state-dir", type=Path, default=None, help="groop state dir containing DAMON ownership markers")
    paddr_start.add_argument("--config", type=Path, default=None, help="load config from PATH instead of the default XDG location")
    paddr_start.add_argument("--confirm", type=str, default=None, help=f"type {APPROVAL_TEXT} to apply sysfs writes")
    paddr_start.add_argument("--allow-non-root-fixture", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def parse_snapshot_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="groop snapshot")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect", help="inspect a groop incident snapshot bundle")
    inspect_parser.add_argument("file", type=Path, help="snapshot .tar or .tar.zst bundle")
    return parser.parse_args(argv)


def parse_daemon_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="groop daemon")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="serve read-only frames over a Unix socket")
    serve.add_argument("--socket", type=Path, required=True, help="Unix socket path")
    serve.add_argument("--config", type=Path, default=None, help="load config from PATH instead of the default XDG location")
    serve.add_argument("--cgroup-root", type=Path, default=None, help="cgroup v2 root for live or fixture collection")
    serve.add_argument("--history-size", type=int, default=120, help="bounded in-memory frame history")
    serve.add_argument("--bpf-root", type=Path, default=None, help="BPF pin root for periodic snapshot refresh (disabled by default)")
    serve.add_argument("--bpf-interval", type=float, default=30.0, help="BPF snapshot refresh interval in seconds (default: 30.0)")
    serve.add_argument("--bpf-state-dir", type=Path, default=None, help="BPF snapshot output directory (default: /run/groop/bpf)")
    preflight = subparsers.add_parser("preflight", help="check daemon deployment readiness")
    preflight.add_argument(
        "--socket",
        type=Path,
        default=DEFAULT_DAEMON_SOCKET,
        help="daemon socket path to inspect",
    )
    preflight.add_argument(
        "--group",
        type=str,
        default=DEFAULT_DAEMON_GROUP,
        help="expected daemon socket group",
    )
    preflight.add_argument("--json", action="store_true", help="emit JSON instead of text")
    install_plan = subparsers.add_parser("install-plan", help="show a safe install plan for the packaged daemon templates")
    install_plan.add_argument(
        "--socket",
        type=Path,
        default=DEFAULT_DAEMON_SOCKET,
        help="daemon socket path for the planned deployment",
    )
    install_plan.add_argument(
        "--group",
        type=str,
        default=DEFAULT_DAEMON_GROUP,
        help="expected daemon socket group",
    )
    install_plan.add_argument(
        "--service-dest",
        type=Path,
        default=DEFAULT_SERVICE_DEST,
        help="destination path for the systemd service unit",
    )
    install_plan.add_argument(
        "--tmpfiles-dest",
        type=Path,
        default=DEFAULT_TMPFILES_DEST,
        help="destination path for the tmpfiles configuration",
    )
    install_plan.add_argument("--json", action="store_true", help="emit JSON instead of text")
    current = subparsers.add_parser("current", help="print one canonical frame from the daemon socket as JSON")
    current.add_argument(
        "--socket",
        type=Path,
        default=DEFAULT_DAEMON_SOCKET,
        help=f"daemon socket path (default: {DEFAULT_DAEMON_SOCKET})",
    )
    current.add_argument("--json", action="store_true", help="emit JSON (default)")
    current.add_argument("--pretty-json", action="store_true", help="pretty-print the frame JSON")
    status = subparsers.add_parser("status", help="check daemon deployment and protocol status")
    status.add_argument(
        "--socket",
        type=Path,
        default=DEFAULT_DAEMON_SOCKET,
        help=f"daemon socket path (default: {DEFAULT_DAEMON_SOCKET})",
    )
    status.add_argument(
        "--group",
        type=str,
        default=DEFAULT_DAEMON_GROUP,
        help=f"expected daemon group (default: {DEFAULT_DAEMON_GROUP})",
    )
    status.add_argument("--json", action="store_true", help="emit JSON instead of text")
    status.add_argument("--pretty-json", action="store_true", help="pretty-print JSON output")
    health = subparsers.add_parser("health", help="print daemon component health snapshot")
    health.add_argument(
        "--socket",
        type=Path,
        default=DEFAULT_DAEMON_SOCKET,
        help=f"daemon socket path (default: {DEFAULT_DAEMON_SOCKET})",
    )
    health.add_argument("--json", action="store_true", help="emit JSON (default)")
    health.add_argument("--pretty-json", action="store_true", help="pretty-print the health JSON")
    return parser.parse_args(argv)


def parse_mcp_args(argv: list[str]) -> argparse.Namespace:
    """Parse the deliberately small, stdio-only MCP command surface."""
    parser = argparse.ArgumentParser(prog="groop mcp")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="serve read-only daemon data over MCP stdio")
    serve.add_argument(
        "--socket",
        type=Path,
        default=DEFAULT_DAEMON_SOCKET,
        help=f"daemon socket path (default: {DEFAULT_DAEMON_SOCKET})",
    )
    serve.add_argument(
        "--redact-above",
        choices=("public", "operational", "sensitive"),
        default=None,
        metavar="LEVEL",
        help="replace metric values more sensitive than LEVEL with a typed redaction marker",
    )
    return parser.parse_args(argv)


def parse_gateway_args(argv: list[str]) -> argparse.Namespace:
    """Parse the separately-run HTTP gateway command.

    Principals are supplied by the operator because the trusted reverse proxy
    owns authentication; this process only maps its verified identity header to
    a closed sensitivity ceiling.
    """
    parser = argparse.ArgumentParser(prog="groop gateway")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="serve authenticated versioned reads over HTTP")
    serve.add_argument(
        "--daemon-socket",
        type=Path,
        default=DEFAULT_DAEMON_SOCKET,
        help=f"versioned daemon Unix socket (default: {DEFAULT_DAEMON_SOCKET})",
    )
    serve.add_argument("--host", type=str, default="127.0.0.1", help="HTTP bind address (default: 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8080, help="HTTP port (default: 8080)")
    serve.add_argument(
        "--principal",
        action="append",
        required=True,
        metavar="NAME:CEILING",
        help="trusted-proxy principal and ceiling: public, operational, or sensitive (repeatable)",
    )
    serve.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help="explicitly permit a non-loopback bind; still trusts identities only from a local proxy",
    )
    return parser.parse_args(argv)


def parse_bpf_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="groop bpf")
    subparsers = parser.add_subparsers(dest="command", required=True)
    gate = subparsers.add_parser("gate", help="run the safe BPF measurement gate")
    gate.add_argument("--proc-root", type=Path, default=Path("/proc"), help="procfs root for the safe baseline probe")
    gate.add_argument("--pin-root", type=Path, default=Path("/sys/fs/bpf/groop"), help="expected groop BPF pin root")
    gate.add_argument("--json", action="store_true", help="emit JSON")
    return parser.parse_args(argv)


def parse_inspect_files_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="groop inspect-files")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan", help="plan a read-only file/log inspection (no content reads)")
    plan.add_argument("--kind", type=str, required=True, help="inspection kind: docker-json-log, systemd-journal, cgroup-files")
    plan.add_argument("--target", type=str, default=None, help="inspection target: container id/name, systemd unit, or cgroup path")
    plan.add_argument("--container", type=str, default=None, help="container name or prefix to resolve (alternative to --target)")
    plan.add_argument("--inspect-files", action="store_true", help="enable file inspection planning mode")
    plan.add_argument("--admin", action="store_true", help="enable admin mode for inspection planning")
    plan.add_argument("--json", action="store_true", help="emit JSON plan instead of text")
    read_parser = subparsers.add_parser("read", help="read bounded file/log content (requires --inspect-files and --admin)")
    read_parser.add_argument("--kind", type=str, required=True, help="inspection kind: docker-json-log, systemd-journal, cgroup-files")
    read_parser.add_argument("--target", type=str, default=None, help="inspection target: container id (64 hex) for docker, systemd unit for journal, cgroup path for cgroup")
    read_parser.add_argument("--container", type=str, default=None, help="container name or prefix to resolve (alternative to --target)")
    read_parser.add_argument("--inspect-files", action="store_true", help="enable file inspection mode")
    read_parser.add_argument("--admin", action="store_true", help="enable admin mode")
    read_parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    read_parser.add_argument("--max-bytes", type=int, default=65536, help="maximum bytes to read (default: 65536)")
    read_parser.add_argument("--max-lines", type=int, default=5000, help="maximum lines to read (default: 5000)")
    return parser.parse_args(argv)


def _print_frame_json(frame, pretty: bool) -> None:
    payload = frame_to_jsonable(frame)
    print(json.dumps(payload, indent=2 if pretty else None, separators=None if pretty else (",", ":"), sort_keys=True))


def _validate_metrics_mode(metrics: str) -> None:
    """Validate --metrics value. Raises SystemExit(2) on invalid values.

    Accepts 'full', 'compact', or a comma-separated selector list that
    parse_metrics_selector can resolve. Empty selectors and unknown tokens
    are rejected.
    """
    if metrics in ("full", "compact"):
        return
    try:
        parse_metrics_selector(metrics)
    except ValueError as exc:
        print(f"invalid --metrics: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


def _filter_kwargs(args: argparse.Namespace) -> dict[str, object]:
    """Extract entity/metric filtering kwargs for Collector from parsed args.

    Caller must validate --slice and --metrics before calling this. Converts the
    argparse list/groups into the tuple/frozenset form the Collector expects.
    """
    slice_names: tuple[str, ...] | None = None
    if args.slice is not None:
        slice_names = (args.slice,)
    entities_globs: tuple[str, ...] | None = None
    if args.entities is not None:
        entities_globs = tuple(args.entities)
    container_selectors: tuple[str, ...] | None = None
    if args.container_selectors is not None:
        container_selectors = tuple(args.container_selectors)
    return {
        "entities_globs": entities_globs,
        "slice_names": slice_names,
        "container_selectors": container_selectors,
        "metrics_mode": args.metrics,
    }


def _format_daemon_error(exc: DaemonClientError, socket_path: Path) -> str:
    """Format a daemon client error with actionable guidance.

    Preserves the original exception text and adds concise next-step
    commands based on whether the socket is the default or custom,
    and on the error type.
    """
    lines = [str(exc)]
    if isinstance(exc, DaemonConnectError):
        lines.append("")
        if socket_path == DEFAULT_DAEMON_SOCKET:
            lines.append("Try: groop daemon preflight")
            lines.append("If the daemon is not installed: groop daemon install-plan")
        else:
            lines.append(f"Try: groop daemon preflight --socket {socket_path}")
    elif isinstance(exc, (DaemonProtocolError, DaemonResponseError)):
        lines.append("")
        lines.append("Check that the process at the socket is a compatible groop daemon")
        lines.append("and review the daemon logs for errors.")
    return "\n".join(lines)


def _attach_frame_source(socket_path: Path, *, poll_interval_s: float) -> Iterator:
    interval_s = max(0.1, poll_interval_s)
    while True:
        yield current_frame(socket_path)
        time.sleep(interval_s)


def _replay_frame_source(driver: ReplayDriver, *, speed: float, step: bool) -> Iterator:
    for replay_frame in driver.play(speed=speed, step=step):
        yield replay_frame.frame


def _run_ui(
    frame_source,
    *,
    config,
    cgroup_root: Path | None,
    smoke: bool,
    profile: str | None,
    source_label: str = "LIVE",
    replay_driver: ReplayDriver | None = None,
    replay_step: bool = False,
    replay_speed: float = 1.0,
) -> int:
    try:
        from groop.ui.app import run_ui
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("textual"):
            if smoke:
                print("textual is required for --ui-smoke", file=sys.stderr)
                return 2
            return -1
        raise
    result = run_ui(
        frame_source,
        config=config,
        cgroup_root=cgroup_root,
        smoke=smoke,
        profile=profile,
        source_label=source_label,
        replay_driver=replay_driver,
        replay_step=replay_step,
        replay_speed=replay_speed,
    )
    if isinstance(result, str):
        print(result)
    return 0


def _default_squeeze_log_path(target: str) -> Path:
    """Build a default squeeze log path under ``/var/log/groop/squeeze/``.

    Uses a timestamp and the target cgroup path's last component for a
    descriptive filename, following the convention of groop audit log
    locations (``/var/log/groop/``).
    """
    import datetime

    ts = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    leaf = Path(target).name if target and target != "/" else "root"
    return Path("/var/log/groop/squeeze") / f"squeeze-{leaf}-{ts}.jsonl"


def parse_squeeze_args(argv: list[str]) -> argparse.Namespace:
    """Parse arguments for ``groop squeeze``.

    Uses subcommand-free flat arguments (like ``groop --once``), not
    subcommands (unlike ``groop action preview/execute``).
    """
    parser = argparse.ArgumentParser(prog="groop squeeze")
    parser.add_argument("--target", type=str, required=True, help="cgroup path to squeeze")
    parser.add_argument("--admin", action="store_true", help="enable admin mode (required)")
    parser.add_argument("--confirm", type=str, default="", help="type SQUEEZE to confirm the measurement")
    parser.add_argument("--step", type=str, default="256M", help="step size (default: 256M)")
    parser.add_argument("--delay", type=float, default=15.0, help="seconds between steps (default: 15)")
    parser.add_argument("--floor", type=str, default="1G", help="never set memory.high below this (default: 1G)")
    parser.add_argument("--start", type=str, default=None, help="initial memory.high (default: current rounded up to step)")
    parser.add_argument("--relax-to", type=str, default="max", help="restore memory.high to this on exit (default: max)")
    parser.add_argument("--psi-some-limit", type=float, default=10.0, help="stop when PSI some avg10 > PCT (default: 10)")
    parser.add_argument("--psi-full-limit", type=float, default=5.0, help="stop when PSI full avg10 > PCT (default: 5)")
    parser.add_argument("--rf-limit", type=int, default=200, help="stop when refaults/s > N (default: 200)")
    parser.add_argument("--log", type=Path, default=None, help="path to JSONL log")
    parser.add_argument("--json", action="store_true", help="emit JSON result instead of text")
    parser.add_argument("--force", action="store_true", help="allow target with memory.min > 0")
    parser.add_argument("--audit-path", type=Path, default=Path("/var/log/groop/actions.jsonl"), help="audit log path")
    return parser.parse_args(argv)


def _main_squeeze(argv: list[str]) -> int:
    from groop.actions.squeeze import (
        SqueezeConfig,
        parse_size,
        render_squeeze_result,
        run_squeeze_gated,
        squeeze_result_to_jsonable,
    )

    args = parse_squeeze_args(argv)

    try:
        step_bytes = parse_size(args.step)
        floor_bytes = parse_size(args.floor)
        start_bytes = parse_size(args.start) if args.start else None
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.delay is not None and args.delay < 1.0:
        print("error: --delay should be at least 1 second (PSI avg10 window is 10s)", file=sys.stderr)
        return 2

    log_path = args.log if args.log is not None else _default_squeeze_log_path(args.target)

    config = SqueezeConfig(
        target=args.target,
        step=step_bytes,
        delay=args.delay,
        floor=floor_bytes,
        start=start_bytes,
        relax_to=args.relax_to,
        psi_some_limit=max(0.0, args.psi_some_limit),
        psi_full_limit=max(0.0, args.psi_full_limit),
        rf_limit=max(0, args.rf_limit),
        force=args.force,
        log_path=log_path,
        audit_path=args.audit_path,
        admin=args.admin,
        confirm=args.confirm,
    )

    result = run_squeeze_gated(config)

    if result.stop_reason == "error":
        print(render_squeeze_result(result), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(squeeze_result_to_jsonable(result), sort_keys=True))
    else:
        print(render_squeeze_result(result))

    if result.stop_reason == "interrupted":
        return 130
    return 0


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv[:1] == ["damon"]:
        return _main_damon(raw_argv[1:])
    if raw_argv[:1] == ["snapshot"]:
        return _main_snapshot(raw_argv[1:])
    if raw_argv[:1] == ["daemon"]:
        return _main_daemon(raw_argv[1:])
    if raw_argv[:1] == ["mcp"]:
        return _main_mcp(raw_argv[1:])
    if raw_argv[:1] == ["gateway"]:
        return _main_gateway(raw_argv[1:])
    if raw_argv[:1] == ["bpf"]:
        return _main_bpf(raw_argv[1:])
    if raw_argv[:1] == ["action"]:
        return _main_action(raw_argv[1:])
    if raw_argv[:1] == ["inspect-files"]:
        return _main_inspect_files(raw_argv[1:])
    if raw_argv[:1] == ["report"]:
        return _main_report(raw_argv[1:])
    if raw_argv[:1] == ["query"]:
        return _main_query(raw_argv[1:])
    if raw_argv[:1] == ["squeeze"]:
        return _main_squeeze(raw_argv[1:])
    args = parse_args(raw_argv)
    config = load(args.config)
    if args.headless and args.replay is not None:
        print("--headless is not supported with --replay", file=sys.stderr)
        return 2
    if args.headless and args.record is None:
        print("--headless requires --record FILE", file=sys.stderr)
        return 2
    if args.record is not None and args.replay is not None:
        print("choose either --record or --replay", file=sys.stderr)
        return 2
    if args.headless and args.attach is not None:
        print("--headless is not supported with --attach", file=sys.stderr)
        return 2
    if args.duration is not None and args.frames is not None:
        print("--duration and --frames are mutually exclusive", file=sys.stderr)
        return 2
    # Validate --slice early so bad values are rejected before collector work
    if args.slice is not None:
        try:
            _validate_slice_name(args.slice)
        except ValueError as exc:
            print(f"invalid --slice: {exc}", file=sys.stderr)
            return 2
    # Validate --metrics early (must be 'full', 'compact', or a valid selector list)
    try:
        _validate_metrics_mode(args.metrics)
    except SystemExit:
        return 2
    if args.attach is not None:
        if args.replay is not None:
            print("choose either --attach or --replay", file=sys.stderr)
            return 2
        if args.step or args.speed != 1.0:
            print("--attach does not accept replay pacing flags", file=sys.stderr)
            return 2
        if args.cgroup_root is not None:
            print("--attach does not accept --cgroup-root", file=sys.stderr)
            return 2
        if args.record is not None:
            print("--attach does not support --record in this build", file=sys.stderr)
            return 2
        if args.json and not args.once:
            print("--json is supported with --attach only when --once is also set", file=sys.stderr)
            return 2
        if args.entities is not None or args.slice is not None or args.metrics != "full" or args.container_selectors is not None:
            print("--attach does not accept --entities/--slice/--metrics/--container", file=sys.stderr)
            return 2
        try:
            if args.once:
                if not args.json:
                    print("groop --attach implements --once --json for canonical daemon frames", file=sys.stderr)
                    return 2
                frame = current_frame(args.attach)
                _print_frame_json(frame, args.pretty_json)
                return 0
            ui_code = _run_ui(
                _attach_frame_source(args.attach, poll_interval_s=config.interval),
                config=config,
                cgroup_root=None,
                smoke=args.ui_smoke,
                profile=args.profile,
                source_label="ATTACH",
            )
            if ui_code == 0:
                return 0
            print("textual is not installed; use --once --json or install UI dependencies", file=sys.stderr)
            return 2
        except DaemonClientError as exc:
            print(_format_daemon_error(exc, args.attach), file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            return 0
    if args.replay is not None:
        if args.entities is not None or args.slice is not None or args.metrics != "full" or args.container_selectors is not None:
            print("--replay does not accept --entities/--slice/--metrics/--container", file=sys.stderr)
            return 2
        driver = ReplayDriver.from_path(args.replay, config=config)
        ui_code = _run_ui(
            (),
            config=config,
            cgroup_root=args.cgroup_root,
            smoke=args.ui_smoke,
            profile=args.profile,
            source_label="REPLAY",
            replay_driver=driver,
            replay_step=args.step,
            replay_speed=args.speed,
        )
        if ui_code == 0:
            return 0
        for replay_frame in driver.play(speed=args.speed, step=args.step):
            print(format_frame_summary(replay_frame))
        return 0
    if args.record is not None:
        if args.json and not args.once:
            print("--json is supported with --record only when --once is also set", file=sys.stderr)
            return 2
        collector = Collector(cgroup_root=args.cgroup_root, config=config, **_filter_kwargs(args))  # type: ignore[arg-type]
        try:
            with RecordWriter(args.record, config=collector.config) as writer:
                if args.headless:
                    # When --once is given with --headless, collect one frame
                    # and exit like the normal --record --once path.
                    if args.once:
                        stream = live_frame_stream(collector, writer=writer)
                        frame = next(stream)
                        if args.json:
                            _print_frame_json(frame, args.pretty_json)
                        return 0
                    exit_code = run_headless_record(
                        collector,
                        writer,
                        interval=args.interval,
                        duration=args.duration,
                        max_frames=args.frames,
                    )
                    return exit_code
                stream = live_frame_stream(collector, writer=writer)
                if args.once:
                    frame = next(stream)
                    if args.json:
                        _print_frame_json(frame, args.pretty_json)
                    return 0
                ui_code = _run_ui(
                    stream,
                    config=config,
                    cgroup_root=args.cgroup_root,
                    smoke=args.ui_smoke,
                    profile=args.profile,
                    source_label="LIVE",
                )
                if ui_code == 0:
                    return 0
                print("textual is not installed; use --once --json or install UI dependencies", file=sys.stderr)
                return 2
        except ContainerResolveError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            return 0
    if not args.once and not args.json:
        collector = Collector(cgroup_root=args.cgroup_root, config=config, **_filter_kwargs(args))  # type: ignore[arg-type]
        ui_code = _run_ui(
            live_frame_stream(collector),
            config=config,
            cgroup_root=args.cgroup_root,
            smoke=args.ui_smoke,
            profile=args.profile,
            source_label="LIVE",
        )
        if ui_code == 0:
            return 0
        print("textual is not installed; use --once --json or install UI dependencies", file=sys.stderr)
        return 2
    if not args.once or not args.json:
        print("groop implements --once --json for live collection and --replay for frame playback", file=sys.stderr)
        return 2
    try:
        frame = Collector(cgroup_root=args.cgroup_root, config=config, **_filter_kwargs(args)).collect_once()  # type: ignore[arg-type]
    except ContainerResolveError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_frame_json(frame, args.pretty_json)
    return 0


def _main_damon(argv: list[str]) -> int:
    args = parse_damon_args(argv)
    if args.command == "stop":
        if not args.all_mine:
            print("groop damon stop requires --all-mine", file=sys.stderr)
            return 2
        try:
            stopped = stop_owned_sessions(
                damon_root=args.damon_root,
                state_dir=args.state_dir,
                all_mine=True,
                require_root=not args.allow_non_root_fixture,
            )
        except RootRequired as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except DamonControlError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"stopped {stopped} groop-owned DAMON session(s)")
        return 0
    if args.command == "paddr" and args.paddr_command == "start":
        config = load(args.config)
        try:
            plan = plan_start_paddr_session(
                damon_root=args.damon_root,
                state_dir=args.state_dir,
                config=config.damon,
                require_root=not args.allow_non_root_fixture,
            )
            if args.confirm is None:
                print(paddr_confirmation_text(plan))
                return 2
            session = start_planned_paddr_session(
                plan,
                confirmed_text=args.confirm,
                require_root=not args.allow_non_root_fixture,
            )
        except RootRequired as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except DamonControlError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"started groop-owned paddr DAMON session on kdamond {session.kdamond_idx}")
        return 0
    print("unknown damon command", file=sys.stderr)
    return 2


def _main_snapshot(argv: list[str]) -> int:
    args = parse_snapshot_args(argv)
    if args.command == "inspect":
        try:
            print(inspect_bundle(args.file))
        except (OSError, RuntimeError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    print("unknown snapshot command", file=sys.stderr)
    return 2


def parse_action_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="groop action")
    subparsers = parser.add_subparsers(dest="command", required=True)
    preview = subparsers.add_parser("preview", help="preview an admin action without executing it")
    preview.add_argument("--kind", type=str, required=True, help="action kind, e.g. docker-restart, systemd-stop")
    preview.add_argument("--target", type=str, default=None, help="action target, e.g. container name or systemd unit")
    preview.add_argument("--container", type=str, default=None, help="container name or prefix to resolve (alternative to --target)")
    preview.add_argument("--admin", action="store_true", help="enable admin preview mode")
    preview.add_argument("--json", action="store_true", help="emit JSON preview instead of text")
    preview.add_argument("--audit-log", type=Path, default=None, help="path to append-only JSONL audit log")
    preview.add_argument("--property", type=str, default=None, help="property for systemd-set-property (e.g. memory.high)")
    preview.add_argument("--value", type=str, default=None, help="value for systemd-set-property (e.g. max or byte count)")
    preview.add_argument("--mode", type=str, default=None, help="persistence mode: runtime|persistent (default: auto-detect)")
    # P72 kill-specific preview arguments
    preview.add_argument("--signal", type=str, default=None, help="signal name for kill action (e.g. TERM, KILL)")
    preview.add_argument("--force", action="store_true", help="allow KILL signal (required for --signal KILL)")
    # P72 update-specific preview arguments
    preview.add_argument("--memory", type=str, default=None, help="memory limit for docker-update (e.g. 512M, 2G)")
    preview.add_argument("--cpus", type=str, default=None, help="CPU count for docker-update")
    preview.add_argument("--below-current", action="store_true", dest="below_current",
                         help="allow memory limit below current usage (may OOM)")
    execute = subparsers.add_parser("execute", help="execute a gated admin action (start/stop/restart/kill/update only)")
    execute.add_argument("--kind", type=str, required=True, help="action kind, e.g. docker-restart, systemd-stop")
    execute.add_argument("--target", type=str, default=None, help="action target, e.g. container name or systemd unit")
    execute.add_argument("--container", type=str, default=None, help="container name or prefix to resolve (alternative to --target)")
    execute.add_argument("--admin", action="store_true", help="enable admin execution mode (required)")
    execute.add_argument("--confirm", type=str, default="", help="type EXECUTE/KILL/UPDATE to confirm the action")
    execute.add_argument("--json", action="store_true", help="emit JSON result instead of text")
    execute.add_argument("--timeout", type=float, default=30.0, help="subprocess timeout in seconds (default 30.0)")
    execute.add_argument("--property", type=str, default=None, help="property for systemd-set-property (e.g. memory.high)")
    execute.add_argument("--value", type=str, default=None, help="value for systemd-set-property (e.g. max or byte count)")
    execute.add_argument("--mode", type=str, default=None, help="persistence mode: runtime|persistent (default: auto-detect)")
    # P72 kill-specific execute arguments
    execute.add_argument("--signal", type=str, default=None, help="signal name for kill action (e.g. TERM, KILL)")
    execute.add_argument("--force", action="store_true", help="allow KILL signal (required for --signal KILL)")
    # P72 update-specific execute arguments
    execute.add_argument("--memory", type=str, default=None, help="memory limit for docker-update (e.g. 512M, 2G)")
    execute.add_argument("--cpus", type=str, default=None, help="CPU count for docker-update")
    execute.add_argument("--below-current", action="store_true", dest="below_current",
                         help="allow memory limit below current usage (may OOM)")
    return parser.parse_args(argv)


def _main_action(argv: list[str]) -> int:
    from groop.actions.audit import AuditLog
    from groop.actions.execute import ExecuteResult, execute_plan
    from groop.actions.governance import (
        SetPropertyPlan,
        render_set_property_preview,
        set_property_plan_to_jsonable,
    )
    from groop.actions.kill_ops import (
        KillPlan,
        render_kill_preview,
        kill_plan_to_jsonable,
    )
    from groop.actions.update_ops import (
        UpdatePlan,
        render_update_preview,
        update_plan_to_jsonable,
    )
    from groop.actions.preview import ActionPlan, DisabledPlan, build_admin_preview

    args = parse_action_args(argv)

    try:
        resolved_target = _resolve_mutual_exclusive_target(
            args.target, args.container, "action"
        )
    except (ValueError, ContainerResolveError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.command == "preview":
        try:
            result = build_admin_preview(
                args.kind, resolved_target,
                admin=args.admin,
                property_name=args.property,
                property_value=args.value,
                persistence=args.mode,
                # P72 kill arguments
                signal=args.signal,
                force=args.force,
                # P72 update arguments
                memory=args.memory,
                cpus=args.cpus,
                below_current=args.below_current,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        if isinstance(result, DisabledPlan):
            print(result.message, file=sys.stderr)
            return 2

        if isinstance(result, SetPropertyPlan):
            if args.audit_log is not None:
                AuditLog(args.audit_log).record(
                    kind=result.kind,
                    target=result.unit,
                    argv=result.argv,
                    admin=args.admin,
                )
            if args.json:
                print(
                    json.dumps(
                        set_property_plan_to_jsonable(result),
                        sort_keys=True,
                    )
                )
            else:
                print(render_set_property_preview(result))
            return 0

        if isinstance(result, KillPlan):
            if args.audit_log is not None:
                AuditLog(args.audit_log).record(
                    kind=result.kind,
                    target=result.target,
                    argv=result.argv,
                    admin=args.admin,
                )
            if args.json:
                print(
                    json.dumps(
                        kill_plan_to_jsonable(result),
                        sort_keys=True,
                    )
                )
            else:
                print(render_kill_preview(result))
            return 0

        if isinstance(result, UpdatePlan):
            if args.audit_log is not None:
                AuditLog(args.audit_log).record(
                    kind=result.kind,
                    target=result.target,
                    argv=result.argv,
                    admin=args.admin,
                )
            if args.json:
                print(
                    json.dumps(
                        update_plan_to_jsonable(result),
                        sort_keys=True,
                    )
                )
            else:
                print(render_update_preview(result))
            return 0

        if not isinstance(result, ActionPlan):
            print("unexpected action preview result", file=sys.stderr)
            return 2

        argv_list = list(result.argv)
        if args.audit_log is not None:
            AuditLog(args.audit_log).record(
                kind=result.kind.value,
                target=result.target,
                argv=result.argv,
                admin=args.admin,
            )

        if args.json:
            print(
                json.dumps(
                    {
                        "argv": argv_list,
                        "description": result.description,
                        "kind": result.kind.value,
                        "mode": result.mode,
                        "target": result.target,
                    },
                    sort_keys=True,
                )
            )
        else:
            print(f"Action: {result.kind.value}")
            print(f"Target: {result.target}")
            print(f"Command argv: {argv_list}")
            print(f"Description: {result.description}")
            print("Mode: preview only; no command was executed")
        return 0

    if args.command == "execute":
        if args.kind == "systemd-set-property" and args.property is not None and args.value is not None:
            from groop.actions.execute import execute_set_property
            result = execute_set_property(
                resolved_target,
                property_name=args.property,
                property_value=args.value,
                persistence=args.mode,
                admin=args.admin,
                confirm=args.confirm,
                timeout=args.timeout,
            )
        elif args.kind in ("docker-kill", "systemd-kill"):
            from groop.actions.execute import execute_kill
            from groop.actions import owner_safety
            result = execute_kill(
                args.kind,
                resolved_target,
                signal=args.signal or "TERM",
                force=args.force,
                admin=args.admin,
                confirm=args.confirm,
                timeout=args.timeout,
                owner_inspect=owner_safety.default_owner_inspect,
            )
        elif args.kind == "docker-update":
            from groop.actions.execute import execute_update
            from groop.actions import owner_safety
            result = execute_update(
                resolved_target,
                memory=args.memory,
                cpus=args.cpus,
                below_current=args.below_current,
                admin=args.admin,
                confirm=args.confirm,
                timeout=args.timeout,
                owner_inspect=owner_safety.default_owner_inspect,
            )
        else:
            from groop.actions import owner_safety
            result = execute_plan(
                args.kind,
                resolved_target,
                admin=args.admin,
                confirm=args.confirm,
                timeout=args.timeout,
                owner_inspect=owner_safety.default_owner_inspect,
            )

        if args.json:
            from groop.actions.execute import result_to_jsonable
            print(json.dumps(result_to_jsonable(result), sort_keys=True))
        else:
            from groop.actions.execute import render_result_text
            print(render_result_text(result))

        # Exit codes: 0 for success, 1 for nonzero/timeout/runner_failure, 2 for refusal
        if result.outcome == "success":
            return 0
        if result.outcome == "refusal":
            return 2
        return 1

    print("unknown action command", file=sys.stderr)
    return 2



def parse_report_args(argv: list[str]) -> argparse.Namespace:
    """Parse groop report [--window last:Ns|all|auto] [--group-by slice|entity] FILE.

    Emits a human-readable ASCII table by default; ``--json`` emits the
    machine-readable JSON contract instead (``--json``/``--table`` are
    mutually exclusive; requesting both is a usage error, exit 2).

    Also accepts repeatable ``--assert GROUP:METRIC:STAT<=VALUE`` (or ``>=``)
    for threshold gating (P61), whose exit code (1 on breach) does not depend
    on the chosen presentation.
    """
    parser = argparse.ArgumentParser(prog="groop report")
    parser.add_argument("file", type=Path, help="JSONL or JSONL.zst recording to analyze")
    parser.add_argument(
        "--window", type=str, default="all",
        help="time window: 'all', 'last:Ns', or 'auto' (default: all)",
    )
    parser.add_argument(
        "--stability-gauge", type=str, default="ram",
        help="primary gauge for --window auto (default: ram)",
    )
    parser.add_argument(
        "--stability-cov", type=str, default="0.05",
        help="maximum population CoV for --window auto (default: 0.05)",
    )
    parser.add_argument(
        "--min-frames", type=str, default="3",
        help="minimum frames in a --window auto suffix (default: 3)",
    )
    parser.add_argument(
        "--group-by", type=str, default="entity", choices=["slice", "entity"],
        help="aggregation grain: 'entity' (per-EntityKey) or 'slice' (per-*.slice ancestor)",
    )
    fmt_group = parser.add_mutually_exclusive_group()
    fmt_group.add_argument(
        "--json", action="store_true",
        help="emit machine-readable JSON (default: human-readable ASCII table)",
    )
    fmt_group.add_argument(
        "--table", action="store_true",
        help="emit human-readable ASCII table (default; explicit form conflicts with --json)",
    )
    parser.add_argument(
        "--assert", action="append", type=str, default=None,
        dest="assert_specs",
        help="threshold assertion GROUP:METRIC:STAT<=VALUE or >=VALUE (repeatable)",
    )
    return parser.parse_args(argv)


def _main_report(argv: list[str]) -> int:
    """Implement groop report — load recording, compute profile, emit JSON."""
    try:
        args = parse_report_args(argv)
    except SystemExit as exc:
        return int(str(exc.code)) if exc.code is not None else 2

    from groop.render import render_report
    from groop.report import (
        Assertion,
        AssertionResult,
        compute_report_with_selection,
        evaluate_assertions,
        format_report,
        parse_assert_spec,
        report_to_jsonable,
    )

    try:
        stability_cov = float(args.stability_cov)
    except ValueError:
        print("invalid --stability-cov — must be a finite non-negative number", file=sys.stderr)
        return 2
    try:
        min_frames = int(args.min_frames)
    except ValueError:
        print("invalid --min-frames — must be a positive integer", file=sys.stderr)
        return 2

    # Handle reader errors: corrupt/truncated recordings, missing zstandard,
    # missing file, or any unexpected failure — all produce exit 2 with
    # a bounded message (never a raw exception traceback).
    try:
        computation = compute_report_with_selection(
            args.file,
            window_spec=args.window,
            group_by=args.group_by,
            stability_gauge=args.stability_gauge,
            stability_cov=stability_cov,
            min_frames=min_frames,
        )
    except FileNotFoundError:
        print(f"file not found: {args.file}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        msg = str(exc)
        if "zstandard" in msg:
            print(msg, file=sys.stderr)
            return 2
        print(str(exc), file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error reading {args.file}: {exc.strerror}", file=sys.stderr)
        return 2
    except Exception:
        # Backstop only: every known damaged-input failure is typed above and
        # names its failure class. Reaching here means an unclassified fault,
        # so it still must not put a traceback across the CLI boundary.
        print(f"unexpected error reading {args.file}", file=sys.stderr)
        return 2

    profiles = computation.profiles

    # Parse and evaluate assertions (P61)
    assertions: list[Assertion] = []
    if args.assert_specs:
        for spec in args.assert_specs:
            try:
                assertions.append(parse_assert_spec(spec))
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 2

    assertion_results: list[AssertionResult] | None = None
    if assertions:
        assertion_results = evaluate_assertions(profiles, assertions)

    if args.json:
        print(
            format_report(
                profiles,
                assertions=assertion_results,
                window_selection=computation.window_selection,
            )
        )
    else:
        print(
            render_report(
                report_to_jsonable(
                    profiles,
                    assertions=assertion_results,
                    window_selection=computation.window_selection,
                )
            )
        )

    # Exit code: 1 when any assertion is breached
    if assertion_results:
        for r in assertion_results:
            if not r.passed:
                return 1
    return 0


def parse_query_args(argv: list[str]) -> argparse.Namespace:
    """Parse ``groop query FILE`` — the executable surface over the P88 engine.

    Reads a P2 recording through the unified ``FrameSource`` and emits one
    deterministic result (current | raw | summary) as a human-readable ASCII
    table by default, or as JSON with ``--json`` (``--json``/``--table`` are
    mutually exclusive; requesting both is a usage error, exit 2).
    """
    parser = argparse.ArgumentParser(prog="groop query")
    parser.add_argument("file", type=Path, help="JSONL or JSONL.zst recording to query")
    parser.add_argument(
        "--shape", choices=["current", "raw", "summary"], default="summary",
        help="result shape (default: summary)",
    )
    parser.add_argument(
        "--metric", action="append", dest="metrics", default=None, metavar="NAME[:SEMANTIC]",
        help="metric to include, optionally with a value semantic (repeatable, required)",
    )
    parser.add_argument(
        "--window", default="all", help="time window: 'all' or 'last:Ns' (default: all)",
    )
    parser.add_argument(
        "--entities", action="append", dest="globs", default=None, metavar="GLOB",
        help="select entities whose key matches a glob (repeatable)",
    )
    parser.add_argument(
        "--select", action="append", dest="select_keys", default=None, metavar="KEY",
        help="select an exact entity key (repeatable)",
    )
    parser.add_argument("--slice", default=None, help="select an entity subtree by slice key")
    parser.add_argument(
        "--projection", choices=["hierarchy", "flat"], default="flat",
        help="row projection (default: flat)",
    )
    parser.add_argument(
        "--visibility", choices=["all", "available"], default="all",
        help="metric visibility (default: all)",
    )
    parser.add_argument(
        "--sort", default=None, metavar="METRIC[:STAT][:asc|desc]",
        help="ranking key for current/summary shapes",
    )
    parser.add_argument("--max-rows", type=int, default=None, dest="max_rows")
    parser.add_argument("--max-points", type=int, default=None, dest="max_points")
    parser.add_argument("--max-bytes", type=int, default=None, dest="max_bytes")
    parser.add_argument(
        "--on-exceed", choices=["error", "truncate"], default="error", dest="on_exceed",
        help="behaviour when a hard bound is exceeded (default: error)",
    )
    fmt_group = parser.add_mutually_exclusive_group()
    fmt_group.add_argument(
        "--json", action="store_true",
        help="emit machine-readable JSON (default: human-readable ASCII table)",
    )
    fmt_group.add_argument(
        "--table", action="store_true",
        help="emit human-readable ASCII table (default; explicit form conflicts with --json)",
    )
    parser.add_argument("--pretty", action="store_true", help="pretty-print the JSON result")
    return parser.parse_args(argv)


def _main_query(argv: list[str]) -> int:
    """Implement groop query — load a recording, run the P88 engine, emit a result."""
    try:
        args = parse_query_args(argv)
    except SystemExit as exc:
        return int(str(exc.code)) if exc.code is not None else 2

    from groop.render import render_query
    from groop.query import (
        Caps,
        MetricRef,
        Query,
        QueryError,
        Selector,
        format_result,
        run_query,
    )
    from groop.query.engine import (
        DEFAULT_MAX_BYTES,
        DEFAULT_MAX_POINTS,
        DEFAULT_MAX_ROWS,
        _parse_metric_token,
        _parse_sort_token,
    )
    from groop.query.source import RecordingFrameSource

    if not args.metrics:
        print("groop query requires at least one --metric", file=sys.stderr)
        return 2

    caps = Caps(
        max_rows=args.max_rows if args.max_rows is not None else DEFAULT_MAX_ROWS,
        max_points=args.max_points if args.max_points is not None else DEFAULT_MAX_POINTS,
        max_bytes=args.max_bytes if args.max_bytes is not None else DEFAULT_MAX_BYTES,
        on_exceed=args.on_exceed,
    )
    query = Query(
        shape=args.shape,
        metrics=tuple(_parse_metric_token(m) for m in args.metrics),
        window_spec=args.window,
        selector=Selector(
            keys=tuple(args.select_keys or ()),
            globs=tuple(args.globs or ()),
            slice=args.slice,
        ),
        projection=args.projection,
        visibility=args.visibility,
        sort=_parse_sort_token(args.sort) if args.sort is not None else None,
        caps=caps,
    )

    try:
        result = run_query(RecordingFrameSource(args.file), query)
    except QueryError as exc:
        # Typed engine error (bad field, incompatible combo, bound exceeded).
        print(str(exc), file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"file not found: {args.file}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error reading {args.file}: {exc.strerror}", file=sys.stderr)
        return 2
    except Exception:
        print(f"unexpected error querying {args.file}", file=sys.stderr)
        return 2

    if args.json:
        print(format_result(result, pretty=args.pretty))
    else:
        print(render_query(result.to_jsonable()))
    return 0


def _resolve_container_target(name_or_prefix: str) -> str:
    """Resolve a --container name/prefix to an EntityKey via a live collector sweep.

    Returns the resolved cgroup-path key that ``--target`` would accept.
    The collector collects one frame to ensure Docker metadata is enriched.
    """
    config = load(None)
    collector = Collector(config=config)
    frame = collector.collect_once()
    # frame.entities is dict[EntityKey, EntityFrame]; extract Entity objects
    entities = {k: ef.entity for k, ef in frame.entities.items()}
    key = resolve_container_key(name_or_prefix, entities)
    return key


def _resolve_mutual_exclusive_target(target: str | None, container: str | None, command: str) -> str:
    """Resolve --target / --container mutual exclusivity.

    Returns the effective target string. Raises ValueError on misuse
    (both given or neither given) and ContainerResolveError on
    name-resolution failure.

    .. note::

        **P55/P57 composition (P59, merged):** The top-level ``--container``
        collection-path selector is wired differently — it resolves inside the
        collector's ``collect_once()`` against the current sweep's post-enrich
        entities, not via this helper. See handoff/P59-container-entity-selector-composition.md.

        **P56 composition:** If/when P56 (``groop squeeze --target``) is
        merged, accept ``--container`` as an alternative to
        ``--target CGROUP_PATH``, resolving before P56's own root /
        ``memory.min`` / ``--force`` checks run.
    """
    if target is not None and container is not None:
        raise ValueError(
            f"choose either --target or --container for {command}"
        )
    if container is not None:
        return _resolve_container_target(container)
    if target is not None:
        return target
    raise ValueError(
        f"{command} requires either --target or --container"
    )


def _main_inspect_files(argv: list[str]) -> int:
    from groop.inspect_files.plan import (
        DisabledInspector,
        InspectFilesPlan,
        build_gated_inspect_plan,
    )
    from groop.inspect_files.reader import (
        InspectFilesReadError,
        InspectFilesReadResult,
        ReadDenied,
        build_inspect_read,
    )

    args = parse_inspect_files_args(argv)

    # Resolve --container to --target before validation
    try:
        resolved_target = _resolve_mutual_exclusive_target(
            args.target, args.container, "inspect-files"
        )
    except (ValueError, ContainerResolveError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.command == "plan":
        try:
            result = build_gated_inspect_plan(
                args.kind, resolved_target,
                inspect_files=args.inspect_files,
                admin=args.admin,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        if isinstance(result, DisabledInspector):
            print(result.message, file=sys.stderr)
            return 2
        if not isinstance(result, InspectFilesPlan):
            print("unexpected inspection plan result", file=sys.stderr)
            return 2

        if args.json:
            print(json.dumps(result.to_jsonable(), sort_keys=True))
        else:
            print(result.to_text())
        return 0

    if args.command == "read":
        try:
            result = build_inspect_read(
                args.kind, resolved_target,
                inspect_files=args.inspect_files,
                admin=args.admin,
                max_bytes=args.max_bytes,
                max_lines=args.max_lines,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        if isinstance(result, ReadDenied):
            print(result.message, file=sys.stderr)
            return 2

        if isinstance(result, InspectFilesReadError):
            print(result.to_text(), file=sys.stderr)
            return 1

        if not isinstance(result, InspectFilesReadResult):
            print("unexpected inspection read result", file=sys.stderr)
            return 2

        if args.json:
            print(json.dumps(result.to_jsonable(), sort_keys=True))
        else:
            print(result.to_text())
        return 0

    print("unknown inspect-files command", file=sys.stderr)
    return 2


def _main_bpf(argv: list[str]) -> int:
    args = parse_bpf_args(argv)
    if args.command == "gate":
        report = run_bpf_gate(proc_root=args.proc_root, pin_root=args.pin_root)
        if args.json:
            print(json.dumps(report_to_jsonable(report), sort_keys=True))
        else:
            print(render_report(report))
        return 0
    print("unknown bpf command", file=sys.stderr)
    return 2


def _main_mcp(argv: list[str]) -> int:
    """Dispatch MCP only after the user explicitly selected its optional extra."""
    args = parse_mcp_args(argv)
    if args.command != "serve":
        return 2
    # Importing this frontend is intentionally confined to this command path;
    # server.py itself delays the optional SDK import until it starts serving.
    from groop.daemon.api import Sensitivity
    from groop.mcp.server import run_server

    redact_above = Sensitivity(args.redact_above) if args.redact_above is not None else None
    return run_server(args.socket, redact_above=redact_above)


def _main_gateway(argv: list[str]) -> int:
    """Run the explicitly configured, authenticated HTTP read gateway."""
    from groop.daemon.http_gateway import (
        GatewayAuthConfig,
        GatewayConfig,
        GatewayStartupError,
        serve_versioned_http_gateway,
    )

    args = parse_gateway_args(argv)
    if args.command != "serve":
        print("unknown gateway command", file=sys.stderr)
        return 2
    principals: dict[str, str] = {}
    for item in args.principal:
        if item.count(":") != 1:
            print("--principal must have NAME:CEILING form", file=sys.stderr)
            return 2
        name, ceiling = item.split(":", 1)
        if name in principals:
            print("--principal names must be unique", file=sys.stderr)
            return 2
        principals[name] = ceiling
    try:
        gateway = serve_versioned_http_gateway(
            args.daemon_socket,
            config=GatewayConfig(
                host=args.host,
                port=args.port,
                auth=GatewayAuthConfig(principals),
                allow_non_loopback=args.allow_non_loopback,
            ),
        )
    except GatewayStartupError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    host, port = gateway.server_address
    print(f"serving authenticated groop reads on http://{host}:{port}", flush=True)
    try:
        gateway.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        gateway.server_close()
    return 0


def _main_daemon(argv: list[str]) -> int:
    args = parse_daemon_args(argv)
    if args.command == "serve":
        config = load(args.config)
        collector = Collector(cgroup_root=args.cgroup_root, config=config)

        # Create component health registry and wire it into the broker
        health_registry = ComponentHealthRegistry()
        health_registry.mark_starting("collector", detail="collector initialized")

        frame_stop = threading.Event()
        broker = FrameBroker(
            live_frame_stream(collector, stop_event=frame_stop),
            history_size=args.history_size,
            health_registry=health_registry,
            stop_callback=frame_stop.set,
        )
        api = DaemonApi(
            broker,
            limits=ApiLimits(history_capacity=args.history_size),
            health_registry=health_registry,
        )
        server = serve_versioned_unix_socket(args.socket, broker, api)
        print(f"serving read-only groop frames on {args.socket}", flush=True)

        # Default-disabled components
        health_registry.mark_disabled("bpf_snapshot_bridge", detail="disabled by default")
        health_registry.mark_disabled("paddr_lifecycle", detail="disabled by default")

        # BPF snapshot bridge (disabled by default)
        bpf_bridge: BpfSnapshotBridge | None = None
        bpf_thread: threading.Thread | None = None
        _bpf_stop = threading.Event()

        bpf_enabled = False
        bpf_root: Path | None = None
        bpf_interval = 30.0

        # Check CLI args first, then config, then stay disabled
        if args.bpf_root is not None:
            bpf_enabled = True
            bpf_root = args.bpf_root
            bpf_interval = max(5.0, args.bpf_interval)
        elif config.bpf_snapshot.enabled and config.bpf_snapshot.root is not None:
            bpf_enabled = True
            bpf_root = config.bpf_snapshot.root
            bpf_interval = max(5.0, config.bpf_snapshot.interval)

        if bpf_enabled and bpf_root is not None:
            health_registry.mark_starting("bpf_snapshot_bridge", detail="starting BPF snapshot bridge")
            try:
                map_name = config.bpf_snapshot.map_name if not args.bpf_root else "groop_cgroup_skb"
                state_dir: Path = (
                    args.bpf_state_dir
                    if args.bpf_state_dir is not None
                    else config.bpf_snapshot.state_dir
                )
                bpf_bridge = BpfSnapshotBridge(
                    bpf_root,
                    command_runner=None,
                    cgroup_root=collector.cgroup_root,
                )
                # Restore last known good snapshot from disk if available
                bpf_bridge.restore_last_known_good(state_dir)

                # Integrate BpfProvider at highest rank into the Collector
                from groop.providers.net_bpf import BpfProvider as BpfProv

                bpf_provider = BpfProv(
                    bpf_root=bpf_root, state_dir=state_dir
                )
                # Rebuild network_providers with BPF first, then existing
                existing_providers = collector.network_providers or ()
                collector.network_providers = (
                    bpf_provider,
                    *existing_providers,
                )

                print(
                    f"BPF snapshot bridge enabled: root={bpf_root} "
                    f"map={map_name} interval={bpf_interval}s "
                    f"state_dir={state_dir}",
                    flush=True,
                )

                # Perform an immediate pre-thread refresh (best-effort)
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
                                message="initial BPF refresh failed",
                                error_code="bpf_initial_refresh_failed",
                            ),
                        )
                    else:
                        health_registry.record_failure(
                            "bpf_snapshot_bridge",
                            detail="initial BPF refresh failed; no valid snapshot",
                            error=ComponentError(
                                message="initial BPF refresh failed",
                                error_code="bpf_initial_refresh_failed",
                            ),
                        )
                    print(
                        f"BPF snapshot initial refresh failed "
                        f"(continuing with periodic retry): {exc}",
                        flush=True,
                    )

                def _bpf_refresh_loop() -> None:
                    while not _bpf_stop.wait(bpf_interval):
                        try:
                            snapshot = bpf_bridge.refresh(map_name)
                            bpf_bridge.write_snapshot(snapshot, state_dir)
                            health_registry.record_success(
                                "bpf_snapshot_bridge",
                                detail="BPF refresh succeeded",
                            )
                        except BpfSnapshotError as exc:
                            last = bpf_bridge.last_valid_snapshot
                            if last is not None:
                                health_registry.record_degraded(
                                    "bpf_snapshot_bridge",
                                    detail="BPF refresh failed; preserving last valid snapshot",
                                    error=ComponentError(
                                        message="BPF refresh failed",
                                        error_code="bpf_refresh_failed",
                                    ),
                                )
                                print(
                                    f"BPF snapshot refresh failed "
                                    f"(preserving last valid): {exc}",
                                    flush=True,
                                )
                            else:
                                health_registry.record_failure(
                                    "bpf_snapshot_bridge",
                                    detail="BPF refresh failed; no valid snapshot",
                                    error=ComponentError(
                                        message="BPF refresh failed",
                                        error_code="bpf_refresh_failed",
                                    ),
                                )
                                print(
                                    f"BPF snapshot refresh failed: {exc}",
                                    flush=True,
                                )
                        except Exception as exc:
                            health_registry.record_failure(
                                "bpf_snapshot_bridge",
                                detail="BPF refresh encountered an unexpected error",
                                error=ComponentError(
                                    message="BPF refresh encountered an unexpected error",
                                    error_code="bpf_unexpected_error",
                                ),
                            )
                            print(
                                f"BPF snapshot unexpected error: {exc}",
                                flush=True,
                            )

                bpf_thread = threading.Thread(
                    target=_bpf_refresh_loop, daemon=True
                )
                bpf_thread.start()
            except Exception as exc:
                health_registry.record_failure(
                    "bpf_snapshot_bridge",
                    detail="failed to start BPF snapshot bridge",
                    error=ComponentError(
                        message="failed to start BPF snapshot bridge",
                        error_code="bpf_start_failed",
                    ),
                )
                print(
                    f"Failed to start BPF snapshot bridge: {exc}",
                    flush=True,
                )

        # Daemon-owned paddr lifecycle (disabled by default via [damon] paddr_enabled)
        from groop.daemon.paddr_lifecycle import (
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
                    case PaddrLifecycleOutcome.DISABLED:
                        pass  # not reached since we check paddr_enabled above
            except Exception as exc:
                health_registry.record_failure(
                    "paddr_lifecycle",
                    detail="paddr lifecycle start failed",
                    error=ComponentError(
                        message="paddr lifecycle start failed",
                        error_code=(
                            "paddr_start_failed"
                            if isinstance(exc, PaddrLifecycleStartError)
                            else "paddr_unexpected_start_error"
                        ),
                    ),
                )
                print(
                    f"Paddr lifecycle start failed "
                    f"(daemon continues without paddr): {exc}",
                    flush=True,
                )

        try:
            # Start only after all collector providers and daemon-owned
            # components are configured. The socket is not accepting handler
            # work until serve_forever starts below.
            broker.start()
            server.serve_forever()
        except KeyboardInterrupt:
            return 0
        finally:
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
                        message="collector producer did not stop cleanly",
                        error_code="collector_shutdown_failed",
                    ),
                )
                print(f"Collector producer shutdown failed: {exc}", flush=True)
            if bpf_thread is not None:
                health_registry.mark_stopping("bpf_snapshot_bridge", detail="shutting down BPF bridge")
                _bpf_stop.set()
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
                        print(
                            f"Stopped {stopped} daemon-owned paddr "
                            f"session",
                            flush=True,
                        )
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
                            message="paddr lifecycle stop failed",
                            error_code="paddr_stop_failed",
                        ),
                    )
                    print(
                        f"Failed to stop paddr session: {exc}",
                        flush=True,
                    )
            if collector_stopped:
                health_registry.mark_stopped("collector", detail="collector stopped")
            server.server_close()
    if args.command == "preflight":
        try:
            report = preflight_daemon_deployment(args.socket, group_name=args.group)
        except (OSError, RuntimeError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(preflight_report_to_jsonable(report), sort_keys=True))
        else:
            print(render_preflight_text(report))
        return 0 if report.usable else 1
    if args.command == "install-plan":
        try:
            plan = build_install_plan(
                socket_path=args.socket,
                group_name=args.group,
                service_dest=args.service_dest,
                tmpfiles_dest=args.tmpfiles_dest,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(install_plan_to_jsonable(plan), sort_keys=True))
        else:
            print(render_install_plan_text(plan))
        return 0
    if args.command == "current":
        from groop.daemon.client import DaemonClient

        try:
            frame = DaemonClient(args.socket).current_frame()
        except DaemonClientError as exc:
            print(_format_daemon_error(exc, args.socket), file=sys.stderr)
            return 2
        _print_frame_json(frame, args.pretty_json)
        return 0
    if args.command == "status":
        from groop.daemon.status import build_daemon_status

        try:
            report = build_daemon_status(args.socket, group_name=args.group)
        except (OSError, RuntimeError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if args.json or args.pretty_json:
            print(
                json.dumps(
                    report.to_jsonable(),
                    indent=2 if args.pretty_json else None,
                    separators=None if args.pretty_json else (",", ":"),
                    sort_keys=True,
                )
            )
        else:
            print(report.to_text())
        return 0 if report.ok else 1
    if args.command == "health":
        from groop.daemon.client import DaemonClient

        try:
            health = DaemonClient(args.socket).request_health()
        except DaemonClientError as exc:
            print(_format_daemon_error(exc, args.socket), file=sys.stderr)
            return 2
        if args.pretty_json:
            print(json.dumps(health.to_jsonable(), indent=2, sort_keys=True))
        else:
            print(json.dumps(health.to_jsonable(), sort_keys=True, separators=(",", ":")))
        return 0
    print("unknown daemon command", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
