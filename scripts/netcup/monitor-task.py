#!/usr/bin/env python3
"""Inspect or follow a task created through the Netcup SCP API."""

from __future__ import annotations

import argparse
import logging
import math
import os
import re
import time
from pathlib import Path
from typing import Any

import netcup_scp_client
from cli_extended import (
    ArgumentSpec,
    CliFailure,
    CliIdentity,
    CliRegistry,
    OptionSpec,
    VerbGroup,
    VerbSpec,
)
from netcup_scp_client import NetcupSCPClient

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_TERMINAL_STATES = {"FINISHED", "ERROR", "CANCELED", "ROLLBACK"}
_SETTINGS_EXPECTED_KEYS = {"monitor.poll_interval"}
_SETTINGS_PATH = Path(__file__).resolve().parent / "monitor-task.toml"
_VERSION_PATH = Path(__file__).resolve().parent / "VERSION"


def _read_version() -> str:
    """Read the explicit Netcup CLI family version; never invent a fallback."""

    version = _VERSION_PATH.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
        raise ValueError(f"invalid Netcup CLI version in {_VERSION_PATH}: {version!r}")
    return version


IDENTITY = CliIdentity(
    name="NETCUP SCP",
    command="monitor-task",
    version=_read_version(),
    long_name="Netcup Server Control Panel task monitor",
)


def _task_uuid(value: str) -> str:
    if not _UUID_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "expected a task UUID such as 3a27fe8e-e747-4f3b-80b0-f930c0d0db3f"
        )
    return value


def _settings_error(exc: SystemExit) -> str:
    message = str(exc)
    return message.removeprefix("ERROR: ")


def _load_poll_interval() -> float:
    try:
        settings = netcup_scp_client._load_settings(
            _SETTINGS_PATH, _SETTINGS_EXPECTED_KEYS
        )
    except SystemExit as exc:
        raise CliFailure(_settings_error(exc), exit_code=2) from exc
    except OSError as exc:
        raise CliFailure(f"could not read monitor settings: {exc}") from exc
    except ValueError as exc:
        raise CliFailure(f"monitor settings are malformed: {exc}", exit_code=2) from exc

    interval = settings.get("monitor.poll_interval")
    if isinstance(interval, bool) or not isinstance(interval, (int, float)):
        raise CliFailure(
            f"monitor.poll_interval in {_SETTINGS_PATH} must be a number",
            exit_code=2,
        )
    try:
        interval = float(interval)
    except (OverflowError, ValueError) as exc:
        raise CliFailure(
            f"monitor.poll_interval in {_SETTINGS_PATH} must be a finite number greater than zero",
            exit_code=2,
        ) from exc
    if not math.isfinite(interval) or interval <= 0:
        raise CliFailure(
            f"monitor.poll_interval in {_SETTINGS_PATH} must be a finite number greater than zero",
            exit_code=2,
        )
    return float(interval)


def _create_client(runtime: Any) -> NetcupSCPClient:
    """Load credentials and API configuration only after a real verb dispatch."""

    try:
        netcup_scp_client.load_env_file()
        netcup_scp_client.configure_api(load_environment=False)
    except SystemExit as exc:
        raise CliFailure(_settings_error(exc), exit_code=2) from exc
    except OSError as exc:
        raise CliFailure(f"could not load Netcup API configuration: {exc}") from exc
    except ValueError as exc:
        raise CliFailure(
            f"Netcup API configuration is malformed: {exc}", exit_code=2
        ) from exc

    refresh_token = os.environ.get("NETCUP_SCP_API_REFRESH_TOKEN")
    if not refresh_token:
        raise CliFailure(
            "missing NETCUP_SCP_API_REFRESH_TOKEN",
            exit_code=2,
            hint="run ./scp-api.py login, then retry the task command",
        )

    # The CLI helper can now redact this known secret from provider errors and
    # all shared diagnostics without loading credentials on help/version paths.
    runtime.output.secrets = (*runtime.output.secrets, refresh_token)
    try:
        access_token = netcup_scp_client.get_access_token(refresh_token)
    except (RuntimeError, ValueError) as exc:
        raise CliFailure(
            f"Netcup authentication failed: {exc}",
            hint="run ./scp-api.py login to refresh the local credential",
        ) from exc
    return NetcupSCPClient(access_token, refresh_token=refresh_token)


def _fetch_task(client: NetcupSCPClient, task_uuid: str) -> dict[str, Any]:
    try:
        task = client.get(f"/api/v1/tasks/{task_uuid}")
    except (RuntimeError, ValueError) as exc:
        raise CliFailure(f"could not fetch task {task_uuid}: {exc}") from exc
    if not isinstance(task, dict):
        raise CliFailure(
            "Netcup returned a malformed task response: expected a JSON object, "
            f"got {type(task).__name__}"
        )
    return task


def _task_fields(task: dict[str, Any]) -> tuple[str, str, int | float | None, str]:
    state_value = task.get("state")
    state = state_value.strip() if isinstance(state_value, str) else "unknown"
    state = state or "unknown"

    name_value = task.get("name")
    name = name_value.strip() if isinstance(name_value, str) else ""

    progress: int | float | None = None
    progress_data = task.get("taskProgress")
    if isinstance(progress_data, dict):
        candidate = progress_data.get("progressInPercent")
        if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
            try:
                progress_number = float(candidate)
            except (OverflowError, ValueError):
                progress_number = math.nan
            if math.isfinite(progress_number):
                progress = progress_number

    message_value = task.get("message")
    message = message_value.strip() if isinstance(message_value, str) else ""
    return state, name, progress, message


def _render_task(task: dict[str, Any], runtime: Any) -> None:
    state, name, progress, message = _task_fields(task)
    summary = f"Task state: {state}"
    if progress is not None:
        summary += f" ({progress:.0f}%)"
    if name:
        summary += f" — {name}"
    runtime.output.info(summary)
    if message:
        runtime.output.info(f"Message: {message}")
    response_error = task.get("responseError")
    if response_error:
        safe_error = (
            response_error
            if runtime.debug_raw
            else netcup_scp_client._redact_for_log(response_error)
        )
        runtime.output.warn(f"Task response error: {safe_error}")


def _show(args: Any, runtime: Any) -> int:
    client = _create_client(runtime)
    task = _fetch_task(client, args.task_uuid)
    if runtime.json_mode:
        result = task if runtime.debug_raw else netcup_scp_client._redact_for_log(task)
        runtime.output.primary(result)
    else:
        _render_task(task, runtime)
    return 0


def _watch(args: Any, runtime: Any) -> int:
    interval = args.poll if args.poll is not None else _load_poll_interval()
    if not math.isfinite(interval) or interval <= 0:
        raise CliFailure(
            "--poll must be a finite number greater than zero",
            exit_code=2,
            show_help=True,
        )

    client = _create_client(runtime)
    progress = runtime.progress()
    last_signature: tuple[str, str, int | float | None, str] | None = None
    while True:
        task = _fetch_task(client, args.task_uuid)
        state, name, percent, message = _task_fields(task)
        if state == "unknown":
            raise CliFailure(
                "Netcup returned a task object without a usable state; "
                "stopping instead of polling indefinitely"
            )
        signature = (state, name, percent, message)
        if signature != last_signature:
            rendered = f"Task state: {state}"
            if percent is not None:
                rendered += f" ({percent:.0f}%)"
            if name:
                rendered += f" — {name}"
            if message:
                rendered += f" — {message}"
            progress.update(rendered)
            last_signature = signature

        if state.upper() in _TERMINAL_STATES:
            progress.finish(f"Task finished: {state}")
            response_error = task.get("responseError")
            if response_error:
                safe_error = (
                    response_error
                    if runtime.debug_raw
                    else netcup_scp_client._redact_for_log(response_error)
                )
                runtime.output.warn(f"Task response error: {safe_error}")
            return 0
        time.sleep(interval)


def _with_client_debug(handler):
    """Scope the shared client's debug flags to this CLI invocation."""

    def invoke(args: Any, runtime: Any) -> int:
        previous = (
            netcup_scp_client.DEBUG,
            netcup_scp_client.DEBUG_RAW,
            netcup_scp_client.DEBUG_LOGGER,
        )
        netcup_scp_client.DEBUG = runtime.debug
        netcup_scp_client.DEBUG_RAW = runtime.debug_raw
        netcup_scp_client.DEBUG_LOGGER = logging.getLogger("netcup.monitor_task.client")
        try:
            return handler(args, runtime)
        finally:
            (
                netcup_scp_client.DEBUG,
                netcup_scp_client.DEBUG_RAW,
                netcup_scp_client.DEBUG_LOGGER,
            ) = previous

    return invoke


def build_cli():
    registry = CliRegistry(
        IDENTITY,
        prog="monitor-task.py",
        description="Inspect one Netcup SCP task or follow it until it reaches a terminal state.",
        getting_started=(
            "monitor-task.py show TASK_UUID",
            "monitor-task.py watch TASK_UUID",
        ),
        logging_logger="netcup.monitor_task",
    )
    task_argument = ArgumentSpec(
        "task_uuid",
        "UUID returned when the install or API operation created this task.",
        metavar="TASK_UUID",
        parser_kwargs={"type": _task_uuid},
    )
    registry.register(
        VerbSpec(
            "show",
            "TASK_UUID",
            "Fetch a task once and print its current state or JSON response.",
            group=VerbGroup.EXPLORATION.value,
            examples=(
                "monitor-task.py show 3a27fe8e-e747-4f3b-80b0-f930c0d0db3f",
                "monitor-task.py show 3a27fe8e-e747-4f3b-80b0-f930c0d0db3f --json",
            ),
            include_progress=False,
            arguments=(task_argument,),
            handler=_with_client_debug(_show),
        )
    )
    registry.register(
        VerbSpec(
            "watch",
            "TASK_UUID",
            "Poll a task and report changes until it reaches a terminal state.",
            group=VerbGroup.EXPLORATION.value,
            examples=(
                "monitor-task.py watch 3a27fe8e-e747-4f3b-80b0-f930c0d0db3f",
                "monitor-task.py watch 3a27fe8e-e747-4f3b-80b0-f930c0d0db3f --poll 2",
            ),
            include_json=False,
            arguments=(task_argument,),
            options=(
                OptionSpec(
                    ("--poll",),
                    "Seconds between task queries (default: monitor-task.toml).",
                    group="STOP CONDITIONS",
                    metavar="SECONDS",
                    parser_kwargs={"type": float, "default": None},
                ),
            ),
            handler=_with_client_debug(_watch),
        )
    )
    return registry.build()


def main(argv: list[str] | None = None) -> int:
    return build_cli().run(argv=argv, expected_exceptions=(OSError,))


if __name__ == "__main__":
    raise SystemExit(main())
