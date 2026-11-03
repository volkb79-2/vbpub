#!/usr/bin/env python3
"""Explore and operate the TrueNAS v27/v28 JSON-RPC WebSocket API."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from cli_extended import (
    ArgumentSpec,
    CliFailure,
    CliIdentity,
    CliRegistry,
    OptionSpec,
    VerbGroup,
    VerbSpec,
)

import truenas_api_client as api_module
from truenas_api_client import TrueNASAPIClient, TrueNASAPIError


IDENTITY = CliIdentity.resolve(
    name="TrueNAS",
    command="truenas-api",
    long_name="TrueNAS API exploration and operations client",
    version_file=Path(__file__).resolve().with_name("VERSION"),
)
_METHOD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_EVENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_JOB_TERMINAL_STATES = {"SUCCESS", "FAILED", "ABORTED"}


def _argument(name: str, description: str, *, metavar: str | None = None, **kwargs: Any):
    return ArgumentSpec(name, description, metavar=metavar, parser_kwargs=kwargs)


def _option(
    flags: tuple[str, ...],
    description: str,
    *,
    group: str,
    metavar: str | None = None,
    **kwargs: Any,
):
    return OptionSpec(
        flags,
        description,
        group=group,
        metavar=metavar,
        parser_kwargs=kwargs,
    )


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _positive_finite(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return parsed


def _method(value: str) -> str:
    if not _METHOD_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("expected a TrueNAS method such as system.info")
    return value


def _event(value: str) -> str:
    if not _EVENT_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("expected a TrueNAS event such as vm.query")
    return value


def _create_client(args: Any, runtime: Any) -> TrueNASAPIClient:
    """Load local credentials and settings only after a real command dispatch."""

    try:
        api_module.load_env_file()
        config_path = Path(args.config) if getattr(args, "config", None) else None
        settings = api_module.load_settings(config_path)
        api_key = api_module.api_key_from_environment()
    except (OSError, ValueError, TrueNASAPIError) as exc:
        raise CliFailure(str(exc), exit_code=2) from exc
    runtime.output.register_secret(api_key)
    return TrueNASAPIClient(settings, api_key)


def _json_compatible(value: Any) -> Any:
    """Normalize rich values decoded by the upstream client for CLI output."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    return value


def _emit(value: Any, runtime: Any) -> None:
    normalized = _json_compatible(value)
    if runtime.json_mode:
        runtime.output.primary_json(normalized)
    else:
        print(json.dumps(normalized, indent=2, sort_keys=True), flush=True)


def _job_progress(method: str, progress: Any):
    last: tuple[Any, ...] | None = None

    def on_update(job: dict[str, Any]) -> None:
        nonlocal last
        state_value = job.get("state")
        state = state_value.strip().upper() if isinstance(state_value, str) else "RUNNING"
        details = job.get("progress")
        percent: int | float | None = None
        description = ""
        if isinstance(details, dict):
            candidate = details.get("percent")
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                percent = candidate
            text_value = details.get("description")
            if isinstance(text_value, str):
                description = text_value.strip()
        signature = (state, percent, description)
        if signature == last:
            return
        last = signature
        message = f"{method}: {state}"
        if description:
            message += f" — {description}"
        progress.update(message, current=percent, total=100 if percent is not None else None)

    return on_update


def cmd_info(args: Any, runtime: Any) -> int:
    with _create_client(args, runtime) as client:
        _emit(client.call("system.info"), runtime)
    return 0


def cmd_methods(args: Any, runtime: Any) -> int:
    with _create_client(args, runtime) as client:
        methods = client.call("core.get_methods")
    if args.filter:
        needle = args.filter.casefold()
        if isinstance(methods, dict):
            methods = {key: value for key, value in methods.items() if needle in str(key).casefold()}
        elif isinstance(methods, list):
            methods = [
                value
                for value in methods
                if needle in json.dumps(_json_compatible(value)).casefold()
            ]
        else:
            raise CliFailure(
                "TrueNAS returned an unexpected core.get_methods shape; "
                f"expected an object or list, got {type(methods).__name__}"
            )
    _emit(methods, runtime)
    return 0


def cmd_vms(args: Any, runtime: Any) -> int:
    with _create_client(args, runtime) as client:
        _emit(client.call("vm.query"), runtime)
    return 0


def cmd_jobs(args: Any, runtime: Any) -> int:
    filters = [["id", "=", args.job_id]] if args.job_id is not None else []
    with _create_client(args, runtime) as client:
        _emit(client.call("core.get_jobs", filters), runtime)
    return 0


def cmd_call(args: Any, runtime: Any) -> int:
    try:
        params = json.loads(args.params_json)
    except json.JSONDecodeError as exc:
        raise CliFailure(f"PARAMS_JSON is not valid JSON: {exc}", exit_code=2) from exc
    if not isinstance(params, list):
        raise CliFailure("PARAMS_JSON must be a JSON array of positional API parameters", exit_code=2)

    plan = {"method": args.method, "params": params, "effect": args.effect, "job": args.job}
    if runtime.dry_run:
        _emit({"result": "planned", **plan}, runtime)
        return 0
    if args.effect == "mutating" and not runtime.confirm(
        f"Call {args.method} as a mutating TrueNAS API operation?"
    ):
        return 0

    with _create_client(args, runtime) as client:
        if args.job:
            progress = runtime.progress()
            try:
                result = client.call_job(
                    args.method,
                    *params,
                    on_update=_job_progress(args.method, progress),
                )
            except TrueNASAPIError:
                raise
            else:
                progress.finish(f"{args.method} completed")
        else:
            result = client.call(args.method, *params)
    _emit(result, runtime)
    return 0


def _job_snapshot(client: TrueNASAPIClient, job_id: int) -> dict[str, Any]:
    result = client.call("core.get_jobs", [["id", "=", job_id]])
    if not isinstance(result, list):
        raise CliFailure(
            "TrueNAS returned an unexpected core.get_jobs shape: expected a list, "
            f"got {type(result).__name__}"
        )
    matches = [item for item in result if isinstance(item, dict) and item.get("id") == job_id]
    if not matches:
        raise CliFailure(f"TrueNAS has no visible job with id {job_id}")
    if len(matches) != 1:
        raise CliFailure(f"TrueNAS returned multiple jobs with id {job_id}")
    return matches[0]


def cmd_watch_job(args: Any, runtime: Any) -> int:
    progress = runtime.progress()
    stop_waiting = threading.Event()
    current: dict[str, Any] = {}
    last_signature: tuple[Any, ...] | None = None

    def report() -> None:
        nonlocal last_signature
        state_value = current.get("state")
        state = state_value.strip().upper() if isinstance(state_value, str) else "UNKNOWN"
        details = current.get("progress")
        percent = details.get("percent") if isinstance(details, dict) else None
        description = details.get("description") if isinstance(details, dict) else None
        signature = (state, percent, description)
        if signature == last_signature:
            return
        last_signature = signature
        message = f"Job {args.job_id}: {state}"
        if isinstance(percent, (int, float)) and not isinstance(percent, bool):
            message += f" ({percent:.0f}%)"
        if isinstance(description, str) and description.strip():
            message += f" — {description.strip()}"
        progress.update(
            message,
            current=percent if isinstance(percent, (int, float)) and not isinstance(percent, bool) else None,
            total=100 if isinstance(percent, (int, float)) and not isinstance(percent, bool) else None,
        )
        if state in _JOB_TERMINAL_STATES:
            stop_waiting.set()

    def on_event(message_type: str, **message: Any) -> None:
        fields = message.get("fields")
        if not isinstance(fields, dict) or fields.get("id") != args.job_id:
            return
        current.update(fields)
        report()

    with _create_client(args, runtime) as client:
        subscription_id = client.subscribe("core.get_jobs", on_event, sync=True)
        try:
            current.update(_job_snapshot(client, args.job_id))
            report()
            if not stop_waiting.is_set():
                stop_waiting.wait()
            final = _job_snapshot(client, args.job_id)
        finally:
            client.unsubscribe(subscription_id)

    final_state = final.get("state")
    progress.finish(f"Job {args.job_id} finished: {final_state or 'unknown state'}")
    _emit(final, runtime)
    return 0 if final_state == "SUCCESS" else 1


def cmd_watch_event(args: Any, runtime: Any) -> int:
    progress = runtime.progress()
    completed = threading.Event()

    def on_event(message_type: str, **message: Any) -> None:
        record = _json_compatible({"event": args.event, "message_type": message_type, **message})
        if runtime.json_mode:
            runtime.output.primary_json(record)
            return
        fields = record.get("fields")
        detail = json.dumps(fields, sort_keys=True) if fields is not None else ""
        progress.update(f"{args.event}: {message_type}" + (f" — {detail}" if detail else ""))

    with _create_client(args, runtime) as client:
        subscription_id = client.subscribe(args.event, on_event, sync=True)
        try:
            completed.wait(args.timeout_seconds)
        except KeyboardInterrupt:
            runtime.output.info("Stopped listening for events.")
        finally:
            client.unsubscribe(subscription_id)
    progress.finish(f"Stopped listening for {args.event}")
    return 0


def build_cli():
    registry = CliRegistry(
        IDENTITY,
        prog="truenas-api.py",
        description="Explore and operate the TrueNAS v27/v28 WebSocket API.",
        getting_started=(
            "./truenas-api.py info",
            "./truenas-api.py vms",
            "./truenas-api.py methods --filter vm.",
        ),
        logging_logger="truenas.api",
        unexpected_exceptions="report",
        expected_exceptions=(TrueNASAPIError, OSError, ValueError),
        global_options=(
            _option(
                ("--config",),
                "TrueNAS settings TOML (default: truenas.toml or TRUENAS_CONFIG)",
                group="LOCAL CONFIGURATION",
                metavar="FILE",
                default=None,
            ),
        ),
    )

    registry.register(
        VerbSpec(
            "info",
            None,
            "show system.info",
            group=VerbGroup.EXPLORATION.value,
            examples=("./truenas-api.py info",),
            handler=cmd_info,
            summary_description="show system version and host information",
        )
    )
    registry.register(
        VerbSpec(
            "methods",
            "[filter]",
            "list API methods, optionally filtered by name",
            group=VerbGroup.EXPLORATION.value,
            arguments=(_argument("filter", "case-insensitive local method-name filter", nargs="?", default=None),),
            examples=("./truenas-api.py methods", "./truenas-api.py methods vm."),
            handler=cmd_methods,
            summary_description="inspect the server's current API method registry",
        )
    )
    registry.register(
        VerbSpec(
            "vms",
            None,
            "list configured virtual machines",
            group=VerbGroup.EXPLORATION.value,
            examples=("./truenas-api.py vms",),
            handler=cmd_vms,
            summary_description="list VM configuration and runtime state",
        )
    )
    registry.register(
        VerbSpec(
            "jobs",
            "[job_id]",
            "show active and recent API jobs",
            group=VerbGroup.EXPLORATION.value,
            arguments=(_argument("job_id", "filter by numeric TrueNAS job ID", type=_positive_int, nargs="?", default=None),),
            examples=("./truenas-api.py jobs", "./truenas-api.py jobs 153"),
            handler=cmd_jobs,
            summary_description="query core.get_jobs",
        )
    )
    registry.register(
        VerbSpec(
            "watch-job",
            "job_id",
            "follow one job's progress and terminal state over WebSocket events",
            group=VerbGroup.EXPLORATION.value,
            arguments=(_argument("job_id", "numeric TrueNAS job ID", type=_positive_int),),
            examples=("./truenas-api.py watch-job 153",),
            include_progress=True,
            handler=cmd_watch_job,
            summary_description="stream state/progress changes until job completion",
        )
    )
    registry.register(
        VerbSpec(
            "watch-event",
            "EVENT",
            "stream one TrueNAS event until interrupted or timed out",
            group=VerbGroup.EXPLORATION.value,
            arguments=(_argument("event", "event name such as vm.query or core.get_jobs", metavar="EVENT", type=_event),),
            options=(
                _option(("--timeout-seconds",), "stop listening after this many seconds; omitted means wait until Ctrl-C", group="EVENT MONITORING", type=_positive_finite, default=None),
            ),
            examples=("./truenas-api.py watch-event vm.query", "./truenas-api.py watch-event vm.query --timeout-seconds 60 --json"),
            include_progress=True,
            handler=cmd_watch_event,
            summary_description="subscribe to any event exposed by the server",
        )
    )
    registry.register(
        VerbSpec(
            "call",
            "METHOD [PARAMS_JSON]",
            "Call an arbitrary API method with positional JSON parameters. PARAMS_JSON is a JSON array, passed as positional API parameters. Declare the operation effect: read calls do not prompt; mutating calls require confirmation. This declaration is not inferred or checked against the server's method schema. With --job, follow a long-running job's progress and completion events.",
            group=VerbGroup.MODIFICATION.value,
            arguments=(
                _argument("method", "TrueNAS API method name", metavar="METHOD", type=_method),
                _argument("params_json", "JSON array of positional parameters", metavar="PARAMS_JSON", nargs="?", default="[]"),
            ),
            options=(
                _option(("--effect",), "declare whether the method is read-only or mutating", group="CALL SAFETY", choices=("read", "mutating"), required=True),
                _option(("--job",), "treat the method as a long-running job and report progress", group="CALL OPTIONS", action="store_true", default=False),
            ),
            examples=(
                "./truenas-api.py call vm.query --effect read",
                "./truenas-api.py call vm.device.convert '{\"source\":\"/mnt/tank/images/debian.qcow2\",\"destination\":\"/dev/zvol/tank/vms/debian\"}' --effect mutating --job",
            ),
            mutating=True,
            dry_run=True,
            include_progress=True,
            handler=cmd_call,
            summary_description="invoke a server method, with explicit effect and optional job monitoring",
        )
    )
    return registry.build()


def main(argv: list[str] | None = None) -> int:
    return build_cli().run(argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
