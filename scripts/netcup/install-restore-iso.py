#!/usr/bin/env python3
"""Upload and boot Netcup ISOs with separate restore and installer handoffs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import netcup_scp_client
from cli_extended import (
    ArgumentSpec,
    CliFailure,
    CliIdentity,
    CliRegistry,
    OptionSpec,
    Requires,
    VerbGroup,
    VerbSpec,
)
from netcup_scp_client import NetcupSCPClient


HERE = Path(__file__).resolve().parent
STATE_DIR = HERE / ".state"
IDENTITY = CliIdentity.resolve(
    name="NETCUP SCP",
    command="install-restore-iso",
    long_name="Netcup SCP ISO installation and restore workflows",
    version_file=HERE / "VERSION",
)
TERMINAL_TASK_STATES = {"FINISHED", "ERROR", "CANCELED", "ROLLBACK"}
ACTIVE_TASK_STATES = {"PENDING", "RUNNING", "WAITING_FOR_CANCEL"}
BOOT_DEVICES = {"HDD", "CDROM", "NETWORK"}
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _argument(name: str, description: str, *, metavar: str | None = None, **kwargs: Any):
    return ArgumentSpec(name, description, metavar=metavar, parser_kwargs=kwargs)


def _option(flags: tuple[str, ...], description: str, *, group: str, metavar: str | None = None, **kwargs: Any):
    return OptionSpec(flags, description, group=group, metavar=metavar, parser_kwargs=kwargs)


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive integer") from None
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _positive_finite(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero") from None
    if number <= 0 or number == float("inf") or number != number:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return number


def _state_path(server_id: int, workflow: str = "restore") -> Path:
    return STATE_DIR / f"{workflow}-{server_id}.json"


def _load_state(path: Path, server_id: int) -> dict[str, Any]:
    if not path.is_file():
        raise CliFailure(f"restore workflow state not found: {path}")
    if path.stat().st_mode & 0o077:
        raise CliFailure(f"restore workflow state must be private (mode 0600): {path}")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CliFailure(f"cannot read restore workflow state: {type(exc).__name__}") from None
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise CliFailure(f"unsupported or malformed restore workflow state: {path}")
    if state.get("server_id") != server_id:
        raise CliFailure(f"state file targets server {state.get('server_id')}, not {server_id}")
    order = state.get("original_boot_order")
    if not isinstance(order, list) or not order or any(item not in BOOT_DEVICES for item in order):
        raise CliFailure("restore state has no valid saved provider boot order")
    if not isinstance(state.get("user_iso_name"), str) or not state["user_iso_name"]:
        raise CliFailure("restore state has no uploaded user ISO name")
    return state


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    state["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _create_client(runtime: Any) -> NetcupSCPClient:
    try:
        netcup_scp_client.load_env_file()
        netcup_scp_client.configure_api(load_environment=False)
    except (SystemExit, OSError, ValueError) as exc:
        raise CliFailure(f"could not load Netcup SCP configuration: {str(exc).removeprefix('ERROR: ')}", exit_code=2) from None
    refresh = os.environ.get("NETCUP_SCP_API_REFRESH_TOKEN")
    if not refresh:
        raise CliFailure("missing NETCUP_SCP_API_REFRESH_TOKEN", exit_code=2, hint="run ./scp-api.py login first")
    runtime.output.register_secret(refresh)
    try:
        access_token = netcup_scp_client.get_access_token(refresh)
    except (RuntimeError, ValueError) as exc:
        raise CliFailure(f"Netcup authentication failed: {exc}", hint="run ./scp-api.py login") from exc
    return NetcupSCPClient(access_token, refresh_token=refresh)


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CliFailure(f"Netcup returned an invalid {label}: expected an object")
    return value


def _rows(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise CliFailure(f"Netcup returned an invalid {label}: expected a list of objects")
    return value


def _server_details(client: NetcupSCPClient, server_id: int) -> dict[str, Any]:
    result = _object(client.get(f"/api/v1/servers/{server_id}"), f"server {server_id} details")
    actual_id = result.get("id")
    if actual_id is not None and actual_id != server_id:
        raise CliFailure(f"Netcup returned server id {actual_id} for requested id {server_id}")
    return result


def _server_name(details: dict[str, Any]) -> str:
    for key in ("name", "nickname", "hostname"):
        value = details.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(details.get("id", "unknown"))


def _guard_mutation(client: NetcupSCPClient, server_id: int, operation: str) -> dict[str, Any]:
    details = _server_details(client, server_id)
    if netcup_scp_client.protected_server_policy_configured():
        netcup_scp_client.assert_server_operation_allowed(server_id, details.get("name"), operation)
    return details


def _boot_order(details: dict[str, Any]) -> list[str]:
    live = details.get("serverLiveInfo")
    order = live.get("bootorder") if isinstance(live, dict) else None
    if not isinstance(order, list) or not order or any(not isinstance(value, str) for value in order):
        raise CliFailure("server details do not contain a usable current boot order; refusing to guess a restore order")
    normalized = [value.upper() for value in order]
    if any(value not in BOOT_DEVICES for value in normalized) or len(set(normalized)) != len(normalized):
        raise CliFailure(f"server reports an unsupported boot order: {order!r}")
    return normalized


def _check_no_active_tasks(client: NetcupSCPClient, server_id: int, operation: str) -> None:
    for state in sorted(ACTIVE_TASK_STATES):
        rows = _rows(client.get("/api/v1/tasks", params={"serverId": server_id, "state": state}), f"{state} tasks")
        active = [row for row in rows if row.get("state") == state]
        if active:
            item = active[0]
            raise CliFailure(
                f"refusing to {operation}: server {server_id} still has task "
                f"{item.get('uuid', '<unknown>')} ({item.get('name', 'unnamed')}) in {state}"
            )


def _task_uuid(result: Any, operation: str) -> str:
    task = _object(result, operation)
    value = task.get("uuid")
    if not isinstance(value, str) or not UUID_RE.fullmatch(value):
        raise CliFailure(f"Netcup {operation} response did not contain a task UUID")
    return value


def _task_progress(task: dict[str, Any]) -> tuple[str, float | None, str, str]:
    state = task.get("state")
    if not isinstance(state, str) or not state.strip():
        raise CliFailure("Netcup task response has no usable state")
    name = task.get("name") if isinstance(task.get("name"), str) else ""
    message = task.get("message") if isinstance(task.get("message"), str) else ""
    percent = None
    progress = task.get("taskProgress")
    candidate = progress.get("progressInPercent") if isinstance(progress, dict) else None
    if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
        if candidate == candidate and candidate not in (float("inf"), float("-inf")):
            percent = float(candidate)
    return state.strip().upper(), percent, name, message


def _wait_task(client: NetcupSCPClient, task_uuid: str, runtime: Any, *, timeout: float | None) -> dict[str, Any]:
    progress = runtime.progress()
    started = time.monotonic()
    last: tuple[Any, ...] | None = None
    while True:
        task = _object(client.get(f"/api/v1/tasks/{task_uuid}"), f"task {task_uuid}")
        state, percent, name, message = _task_progress(task)
        signature = (state, percent, name, message)
        if signature != last:
            shown = f"{state}"
            if percent is not None:
                shown += f" ({percent:.0f}%)"
            if name:
                shown += f" — {name}"
            if message:
                shown += f" — {message}"
            progress.update(shown, current=percent, total=100 if percent is not None else None)
            last = signature
        if state in TERMINAL_TASK_STATES:
            progress.finish(f"Task {state.lower()}: {task_uuid}")
            if state != "FINISHED":
                detail = task.get("responseError") or task.get("message") or "provider did not report a reason"
                raise CliFailure(f"Netcup task {task_uuid} ended in {state}: {detail}")
            return task
        if timeout is not None and time.monotonic() - started > timeout:
            progress.finish(f"Task still running: {task_uuid}")
            raise CliFailure(f"timed out waiting for task {task_uuid}; resume with this workflow state file")
        time.sleep(2)


def _scp_user_id(client: NetcupSCPClient) -> int:
    info = _object(client.get_user_info(), "authenticated user profile")
    value = info.get("id")
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise CliFailure("Netcup user profile did not contain a positive user id")
    return value


def _upload_iso(client: NetcupSCPClient, path: Path, iso_name: str, part_size_mib: int, runtime: Any) -> None:
    user_id = _scp_user_id(client)
    endpoint = f"/api/v1/users/{user_id}/isos/{urllib.parse.quote(iso_name, safe='')}"
    listing = _rows(client.get(f"/api/v1/users/{user_id}/isos"), "uploaded user ISO inventory")
    if any(row.get("key") == iso_name for row in listing):
        raise CliFailure(
            f"user ISO {iso_name!r} already exists; choose a new name. Netcup does not expose a content checksum for reuse"
        )
    prepared = _object(client.post(f"{endpoint}?multipart=true", None), "multipart upload preparation")
    upload_id = prepared.get("uploadId")
    if not isinstance(upload_id, str) or not upload_id:
        raise CliFailure("Netcup multipart preparation did not return uploadId")
    total = path.stat().st_size
    part_bytes = part_size_mib * 1024 * 1024
    part_count = (total + part_bytes - 1) // part_bytes
    progress = runtime.progress()
    parts: list[dict[str, Any]] = []
    offset = 0
    part_number = 1
    try:
        while offset < total:
            length = min(part_bytes, total - offset)
            part_endpoint = f"{endpoint}/{urllib.parse.quote(upload_id, safe='')}/parts/{part_number}"
            reply = _object(client.get(part_endpoint), f"upload part {part_number} URL")
            url = reply.get("url")
            if not isinstance(url, str) or not url:
                raise CliFailure(f"Netcup did not return a URL for upload part {part_number}")
            result = client.upload_file(url, path, offset=offset, size=length)
            etag = result.get("etag") if isinstance(result, dict) else None
            if not isinstance(etag, str) or not etag.strip():
                raise CliFailure(f"upload part {part_number} returned no ETag")
            parts.append({"ETag": etag, "partNumber": part_number})
            offset += length
            progress.update(
                f"Uploaded ISO part {part_number}/{part_count}",
                current=offset,
                total=total,
            )
            part_number += 1
        client.put(f"{endpoint}/{urllib.parse.quote(upload_id, safe='')}", parts)
    except Exception:
        progress.finish("ISO upload incomplete")
        raise
    progress.finish(f"ISO upload complete ({total} bytes, {part_count} parts)")
    refreshed = _rows(client.get(f"/api/v1/users/{user_id}/isos"), "uploaded user ISO inventory")
    matches = [row for row in refreshed if row.get("key") == iso_name]
    if len(matches) != 1:
        raise CliFailure("uploaded ISO was not present exactly once in Netcup's inventory")
    remote_size = matches[0].get("sizeInB")
    if not isinstance(remote_size, int) or isinstance(remote_size, bool) or remote_size != total:
        raise CliFailure(f"uploaded ISO size verification failed: Netcup reports {remote_size!r}, local file is {total}")


def _attached_iso(client: NetcupSCPClient, server_id: int) -> dict[str, Any]:
    return _object(client.get(f"/api/v1/servers/{server_id}/iso"), f"server {server_id} attached ISO")


def _check_iso_attached(attached: dict[str, Any], iso_name: str) -> bool:
    flag = attached.get("isoAttached")
    if not isinstance(flag, bool):
        raise CliFailure("Netcup attached ISO response omitted the isoAttached boolean")
    if not flag:
        return False
    media = attached.get("iso")
    media_name = None
    if isinstance(media, str):
        media_name = media
    elif isinstance(media, dict):
        media_name = media.get("key") or media.get("name") or media.get("userIsoName")
    media_name = media_name or attached.get("userIsoName")
    if media_name is not None and media_name != iso_name:
        raise CliFailure(f"server has a different ISO attached ({media_name!r}); refusing to detach it")
    if media_name is None:
        raise CliFailure("server has an attached ISO, but its identity is not present in the API response")
    return True


def _start(args: Any, runtime: Any) -> int:
    path = Path(args.iso).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise CliFailure(f"ISO must be a non-empty regular file: {path}")
    workflow_operation = getattr(args, "workflow_operation", None)
    state_path = (
        Path(args.state_file).expanduser().resolve()
        if args.state_file
        else _state_path(args.server_id, "install" if workflow_operation == "install" else "restore")
    )
    replace_state = bool(getattr(args, "replace_state", False))
    if state_path.exists() and not replace_state:
        existing = _load_state(state_path, args.server_id)
        if existing.get("phase") != "complete":
            raise CliFailure(
                f"workflow state is unfinished (phase {existing.get('phase')!r}): {state_path}; "
                "inspect it with status and complete or manually recover that provider operation before another start"
            )
        raise CliFailure(
            f"workflow state already exists in phase {existing.get('phase')!r}: {state_path}; "
            "use --replace-state only after reviewing the completed run"
        )
    if state_path.exists() and replace_state:
        previous = _load_state(state_path, args.server_id)
        if previous.get("phase") != "complete":
            raise CliFailure("--replace-state cannot discard an unfinished provider workflow")
    client = _create_client(runtime)
    details = _server_details(client, args.server_id)
    operation = "install an ISO" if getattr(args, "workflow_operation", None) == "install" else "restore an ISO"
    netcup_scp_client.assert_server_operation_allowed(args.server_id, details.get("name"), operation)
    current_order = _boot_order(details)
    attached = _attached_iso(client, args.server_id)
    if attached.get("isoAttached") is True:
        raise CliFailure("a CD-ROM is already attached; detach it with scp-api.py before starting another ISO workflow")
    operation_description = "install ISO" if workflow_operation == "install" else "restore ISO"
    _check_no_active_tasks(client, args.server_id, f"attach an {operation_description}")

    digest = _sha256(path)
    iso_name = args.user_iso_name or f"truenas-restore-{digest[:16]}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.iso"
    if not iso_name.lower().endswith(".iso") or "/" in iso_name or "\\" in iso_name:
        raise CliFailure("user ISO name must be a plain filename ending in .iso")
    state = {
        "schema_version": 1,
        "server_id": args.server_id,
        "server_name": _server_name(details),
        "phase": "preflight",
        "user_iso_name": iso_name,
        "iso_path": str(path),
        "iso_size_bytes": path.stat().st_size,
        "iso_sha256": digest,
        "original_boot_order": current_order,
        "tasks": {},
    }
    if workflow_operation == "install":
        state["operation"] = "installer"
        state["readiness"] = {
            "url": getattr(args, "ready_url", None),
            "text": getattr(args, "ready_text", None),
        }

    runtime.output.info(f"Target: {_server_name(details)} (server {args.server_id})")
    runtime.output.info(f"ISO: {path} ({path.stat().st_size} bytes, SHA-256 {digest})")
    runtime.output.info(f"Uploaded user ISO name: {iso_name}")
    runtime.output.info(f"Saved boot order for final restoration: {','.join(current_order)}")
    if workflow_operation == "install":
        runtime.output.info("The ISO will boot first. The installer must power off the guest on successful completion.")
    else:
        runtime.output.info("The ISO will boot first for restore. Finalization waits for the restore-complete notification.")
    if runtime.dry_run:
        runtime.output.info("Dry run: no ISO was uploaded or attached and the server was not powered.")
        return 0
    if not runtime.confirm(
        f"Upload this ISO, attach it to {_server_name(details)} ({args.server_id}), and boot the server?"
    ):
        return 0

    _write_state(state_path, state)
    state["phase"] = "uploading"
    _write_state(state_path, state)
    _upload_iso(client, path, iso_name, args.part_size_mib, runtime)
    state["phase"] = "uploaded"
    _write_state(state_path, state)

    details = _guard_mutation(client, args.server_id, f"attach an {operation_description}")
    _check_no_active_tasks(client, args.server_id, f"attach an {operation_description}")
    attach = _object(
        client.post(
            f"/api/v1/servers/{args.server_id}/iso",
            {"userIsoName": iso_name, "changeBootDeviceToCdrom": True},
        ),
        "ISO attach response",
    )
    attach_uuid = _task_uuid(attach, "ISO attach")
    state["tasks"]["attach"] = attach_uuid
    state["phase"] = "attaching"
    _write_state(state_path, state)
    _wait_task(client, attach_uuid, runtime, timeout=args.task_timeout_seconds)
    if not _check_iso_attached(_attached_iso(client, args.server_id), iso_name):
        raise CliFailure("Netcup attach task finished but the requested ISO is not attached")
    state["phase"] = "attached"
    _write_state(state_path, state)

    _guard_mutation(client, args.server_id, f"power-cycle into the {operation_description}")
    _check_no_active_tasks(client, args.server_id, f"power-cycle into the {operation_description}")
    power = _object(
        client.patch(
            f"/api/v1/servers/{args.server_id}",
            {"state": "ON"},
            params={"stateOption": "POWERCYCLE"},
        ),
        "power-cycle response",
    )
    power_uuid = _task_uuid(power, "power cycle")
    state["tasks"]["boot"] = power_uuid
    state["phase"] = "booting_restore_iso"
    _write_state(state_path, state)
    _wait_task(client, power_uuid, runtime, timeout=args.task_timeout_seconds)
    state["phase"] = "awaiting_installer_poweroff" if workflow_operation == "install" else "awaiting_restore_completion"
    _write_state(state_path, state)
    if workflow_operation == "install":
        runtime.output.info("Installer ISO is booting. The generic installer workflow will detach it when the guest powers off.")
    else:
        runtime.output.info("Restore ISO is booting. Wait for its Mattermost restore-complete notification.")
        runtime.output.info(f"Then run: ./install-restore-iso.py finish {args.server_id} --yes")
    runtime.output.info(f"Workflow checkpoint: {state_path}")
    return 0


def _live_state(details: dict[str, Any]) -> tuple[str | None, bool | None]:
    live = details.get("serverLiveInfo")
    if not isinstance(live, dict):
        return None, None
    state = live.get("state")
    config_changed = live.get("configChanged")
    return (state.upper() if isinstance(state, str) else None), (config_changed if isinstance(config_changed, bool) else None)


def _wait_guest_poweroff(
    client: NetcupSCPClient,
    server_id: int,
    runtime: Any,
    *,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    saw_running = False
    last_state: tuple[str | None, bool | None] | None = None
    progress = runtime.progress()
    while True:
        details = _server_details(client, server_id)
        state = _live_state(details)
        if state != last_state:
            progress.update(f"Netcup guest state: {state[0] or 'unknown'}; configChanged={state[1]}")
            last_state = state
        if state[0] == "RUNNING" and state[1] is False:
            saw_running = True
        elif saw_running and state[0] in {"OFF", "STOPPED", "SHUTDOWN", "SHUTOFF"}:
            progress.finish("Installer guest powered off; configured handoff condition detected.")
            return
        if time.monotonic() >= deadline:
            progress.finish("Timed out waiting for the installer guest to power off.")
            raise CliFailure(
                f"installer guest did not power off within {timeout:g}s; ISO remains attached and the checkpoint is resumable"
            )
        time.sleep(5)


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, expected_host: str):
        super().__init__()
        self.expected_host = expected_host.casefold()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if target.hostname is None or target.hostname.casefold() != self.expected_host:
            raise CliFailure("readiness URL redirected to a different host; refusing cross-host probe")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _http_ready(url: str, expected_text: str | None, timeout: float) -> bool:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise CliFailure("readiness URL must be an absolute HTTP(S) URL without embedded credentials")
    opener = urllib.request.build_opener(_SameHostRedirect(parsed.hostname))
    request = urllib.request.Request(url, headers={"User-Agent": "netcup-install-iso/1"})
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status < 200 or response.status >= 300:
                return False
            body = response.read(2 * 1024 * 1024)
    except (OSError, urllib.error.URLError, TimeoutError):
        return False
    if expected_text is not None and expected_text.encode("utf-8") not in body:
        return False
    return True


def _wait_http_ready(url: str, expected_text: str | None, runtime: Any, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    progress = runtime.progress()
    while True:
        if _http_ready(url, expected_text, min(10.0, max(1.0, deadline - time.monotonic()))):
            progress.finish(f"Installed system readiness URL responded: {url}")
            return
        if time.monotonic() >= deadline:
            progress.finish("Timed out waiting for the installed system readiness URL.")
            raise CliFailure(
                f"installed system did not satisfy the HTTP readiness check within {timeout:g}s; "
                "provider ISO is detached and boot order is restored; inspect the server console"
            )
        time.sleep(5)


def _install(args: Any, runtime: Any) -> int:
    """Run a generic ISO installation whose guest powers off on success."""
    state_path = Path(args.state_file).expanduser().resolve() if args.state_file else _state_path(args.server_id)
    if runtime.dry_run:
        args.workflow_operation = "install"
        return _start(args, runtime)

    if state_path.exists():
        state = _load_state(state_path, args.server_id)
        if state.get("operation") != "installer":
            raise CliFailure(f"workflow state is not a generic installer run: {state_path}")
        iso_path = Path(args.iso).expanduser().resolve()
        if not iso_path.is_file() or _sha256(iso_path) != state.get("iso_sha256"):
            raise CliFailure("resume requires the same local ISO file whose SHA-256 is stored in the checkpoint")
        readiness = state.get("readiness", {"url": None, "text": None})
        requested_readiness = {"url": args.ready_url, "text": args.ready_text}
        if readiness != requested_readiness:
            raise CliFailure("resume requires the readiness checks recorded in the installer checkpoint")
        if state.get("phase") == "complete":
            runtime.output.info(f"ISO installation workflow already completed: {state_path}")
            return 0
    else:
        args.workflow_operation = "install"
        if _start(args, runtime) != 0:
            return 0
        if not state_path.exists():
            return 0
        state = _load_state(state_path, args.server_id)

    allowed = {
        "awaiting_installer_poweroff",
        "detaching_installer_media",
        "installer_media_detached",
        "restoring_installer_boot_order",
        "installer_boot_order_restored",
        "powering_installed_system",
        "verifying_installed_system",
    }
    if state.get("phase") not in allowed:
        raise CliFailure(
            f"installer workflow is in phase {state.get('phase')!r}; inspect provider state before retrying"
        )

    client = _create_client(runtime)
    if state.get("phase") == "awaiting_installer_poweroff":
        runtime.output.info("Waiting for the installer guest to power off; this is its configured handoff signal.")
        runtime.output.info("The provider ISO stays attached until that shutdown handoff arrives.")
        _wait_guest_poweroff(client, args.server_id, runtime, timeout=args.install_timeout_seconds)
        state["guest_poweroff_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state["phase"] = "detaching_installer_media"
        _write_state(state_path, state)

    if state.get("phase") == "detaching_installer_media":
        details = _guard_mutation(client, args.server_id, "detach a completed installer ISO")
        _check_no_active_tasks(client, args.server_id, "detach the completed installer ISO")
        current, _ = _live_state(details)
        if current not in {"OFF", "STOPPED", "SHUTDOWN", "SHUTOFF"}:
            raise CliFailure(f"guest is {current or 'unknown'}; refusing to detach before installer shutdown")
        attached = _attached_iso(client, args.server_id)
        if _check_iso_attached(attached, state["user_iso_name"]):
            if not runtime.confirm(f"Detach completed installer ISO {state['user_iso_name']!r} from server {args.server_id}?"):
                return 0
            client.delete(f"/api/v1/servers/{args.server_id}/iso")
        if _check_iso_attached(_attached_iso(client, args.server_id), state["user_iso_name"]):
            raise CliFailure("Netcup still reports the installer ISO attached after the detach request")
        state["phase"] = "installer_media_detached"
        _write_state(state_path, state)

    if state.get("phase") == "installer_media_detached":
        state["phase"] = "restoring_installer_boot_order"
        _write_state(state_path, state)
    if state.get("phase") == "restoring_installer_boot_order":
        details = _guard_mutation(client, args.server_id, "restore the saved installer boot order")
        desired = state["original_boot_order"]
        if _boot_order(details) != desired:
            _check_no_active_tasks(client, args.server_id, "restore the saved installer boot order")
            if not runtime.confirm(f"Restore server {args.server_id} boot order to {','.join(desired)}?"):
                return 0
            result = client.patch(f"/api/v1/servers/{args.server_id}", {"bootorder": desired})
            if isinstance(result, dict) and result.get("uuid"):
                uuid = _task_uuid(result, "installer boot-order update")
                state["tasks"]["boot_order"] = uuid
                _write_state(state_path, state)
                _wait_task(client, uuid, runtime, timeout=args.task_timeout_seconds)
            if _boot_order(_server_details(client, args.server_id)) != desired:
                raise CliFailure("Netcup did not confirm the saved installer boot order after the update")
        state["phase"] = "installer_boot_order_restored"
        _write_state(state_path, state)

    if state.get("phase") == "installer_boot_order_restored":
        state["phase"] = "powering_installed_system"
        _write_state(state_path, state)
    if state.get("phase") == "powering_installed_system":
        details = _guard_mutation(client, args.server_id, "boot from the installed system")
        _check_no_active_tasks(client, args.server_id, "boot from the installed system")
        current, _ = _live_state(details)
        if current in {"OFF", "STOPPED", "SHUTDOWN", "SHUTOFF"}:
            if not runtime.confirm(f"Power on server {args.server_id} from its installed disk?"):
                return 0
            result = client.patch(f"/api/v1/servers/{args.server_id}", {"state": "ON"})
            uuid = _task_uuid(result, "installed-system power-on")
            state["tasks"]["installed_system_boot"] = uuid
            _write_state(state_path, state)
            _wait_task(client, uuid, runtime, timeout=args.task_timeout_seconds)
        else:
            raise CliFailure(f"server is {current or 'unknown'}; refusing to start the installed system")
        state["phase"] = "verifying_installed_system"
        _write_state(state_path, state)

    if state.get("phase") == "verifying_installed_system":
        deadline = time.monotonic() + args.server_ready_timeout_seconds
        while True:
            details = _server_details(client, args.server_id)
            current, config_changed = _live_state(details)
            if current == "RUNNING" and config_changed is False:
                break
            if time.monotonic() >= deadline:
                raise CliFailure("Netcup did not confirm RUNNING with configChanged=false after installing the disk")
            time.sleep(5)
        if args.ready_url is not None:
            _wait_http_ready(
                args.ready_url,
                args.ready_text,
                runtime,
                timeout=args.http_ready_timeout_seconds or 900,
            )
        state["phase"] = "complete"
        state["ready_url"] = args.ready_url
        if args.ready_url is not None:
            state["http_verified_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state["installer_completed_at"] = state.get("guest_poweroff_at")
        _write_state(state_path, state)
        if args.ready_url is not None:
            runtime.output.info("Provider ISO detached, saved boot order restored, and HTTP readiness verified.")
        else:
            runtime.output.info("Provider ISO detached, saved boot order restored, and Netcup reports the disk boot running.")
        runtime.output.info(f"Installer workflow completed: {state_path}")
    return 0


def _ssh_verify(host: str, identity: Path, known_hosts: Path, timeout: float, host_key_alias: str | None) -> str:
    if not identity.is_file() or identity.stat().st_mode & 0o077:
        raise CliFailure("SSH identity must exist and have mode 0600 or stricter")
    if not known_hosts.is_file():
        raise CliFailure(f"pinned SSH known_hosts file not found: {known_hosts}")
    ssh = shutil.which("ssh")
    if ssh is None:
        raise CliFailure("ssh is required for final host verification")
    deadline = time.monotonic() + timeout
    last_error = "SSH has not responded yet"
    while time.monotonic() < deadline:
        argv = [
                ssh,
                "-T",
                "-i",
                str(identity),
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                f"UserKnownHostsFile={known_hosts}",
            ]
        if host_key_alias:
            argv.extend(["-o", f"HostKeyAlias={host_key_alias}"])
        argv.extend([
                "-o",
                "ConnectTimeout=8",
                f"root@{host}",
                "id -u && zpool status boot-pool",
            ])
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if result.returncode == 0:
            output = result.stdout
            if output.splitlines() and output.splitlines()[0].strip() == "0" and re.search(r"(?m)^\s*state:\s+ONLINE\s*$", output) and re.search(r"(?m)^errors:\s+No known data errors\s*$", output):
                return output
            raise CliFailure("SSH reached the host, but root or boot-pool health verification failed")
        last_error = (result.stderr or "SSH connection failed").strip().splitlines()[-1]
        time.sleep(5)
    raise CliFailure(f"TrueNAS SSH/pool verification did not succeed within {timeout:g}s: {last_error}")


def _finish_or_recover(args: Any, runtime: Any, *, recovery: bool) -> int:
    state_path = Path(args.state_file).expanduser().resolve() if args.state_file else _state_path(args.server_id, "install")
    state = _load_state(state_path, args.server_id)
    terminal_phase = "recovered" if recovery else "complete"
    if state.get("phase") == "complete":
        runtime.output.info(f"Restore workflow already completed: {state_path}")
        return 0
    if state.get("phase") == "recovered":
        runtime.output.info(f"Disk boot recovery already completed (restore completion was not certified): {state_path}")
        return 0
    if runtime.dry_run:
        action = "attempt disk-boot recovery without asserting restore completion" if recovery else "finalize after the restore-complete assertion"
        runtime.output.info(f"Dry run: would {action} on server {args.server_id}.")
        runtime.output.info(f"Would detach {state['user_iso_name']!r}, restore boot order {','.join(state['original_boot_order'])}, cold-cycle, and verify root SSH/pool health.")
        return 0
    allowed = {
        "awaiting_restore_completion",
        "detaching_media",
        "media_detached",
        "restoring_boot_order",
        "boot_order_restored",
        "powering_disk_boot",
        "verifying_disk_boot",
    }
    if state.get("phase") not in allowed:
        action = "recover" if recovery else "finish"
        raise CliFailure(f"workflow is in phase {state.get('phase')!r}; {action} cannot safely continue from that state")
    expected_operation = "recover" if recovery else "finish"
    operation = state.get("operation")
    if operation not in (None, expected_operation):
        raise CliFailure(
            f"workflow was started as {operation!r}; refusing to continue it as {expected_operation!r}"
        )
    if recovery and operation is None and state.get("phase") != "awaiting_restore_completion":
        raise CliFailure(
            "this checkpoint advanced without a recovery marker; inspect provider state before choosing a recovery action"
        )
    if operation is None:
        state["operation"] = expected_operation
        _write_state(state_path, state)
    if state.get("phase") == "awaiting_restore_completion":
        if recovery:
            state["recovery_started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            state["recovery_reason"] = "restore runtime did not report successful completion"
            state["restore_completion_confirmed_at"] = None
            runtime.output.info(
                f"Starting disk-boot recovery for {state['user_iso_name']!r}; restore completion remains unverified."
            )
        else:
            state["restore_completion_confirmed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            runtime.output.info(f"Restore completion explicitly asserted for {state['user_iso_name']!r}.")
        _write_state(state_path, state)

    client = _create_client(runtime)
    operation_label = "recover disk boot after an incomplete restore ISO run" if recovery else "finish a restore ISO deployment"
    details = _guard_mutation(client, args.server_id, operation_label)
    runtime.output.info(f"{'Recovering' if recovery else 'Finalizing'} {_server_name(details)} (server {args.server_id}).")
    runtime.output.info("Detach provider media, restore the saved boot order, cold-cycle, then verify root SSH and boot-pool health.")

    if state.get("phase") == "awaiting_restore_completion":
        state["phase"] = "detaching_media"
        _write_state(state_path, state)
    if state.get("phase") == "detaching_media":
        _check_no_active_tasks(client, args.server_id, "detach the restore ISO")
        attached = _attached_iso(client, args.server_id)
        if _check_iso_attached(attached, state["user_iso_name"]):
            if not runtime.confirm(f"Detach restore ISO {state['user_iso_name']!r} from server {args.server_id}?"):
                return 0
            client.delete(f"/api/v1/servers/{args.server_id}/iso")
        state["phase"] = "media_detached"
        _write_state(state_path, state)

    if state.get("phase") == "media_detached":
        state["phase"] = "restoring_boot_order"
        _write_state(state_path, state)
    if state.get("phase") == "restoring_boot_order":
        details = _guard_mutation(client, args.server_id, "restore the saved boot order")
        current = _boot_order(details)
        desired = state["original_boot_order"]
        if current != desired:
            if not runtime.confirm(f"Restore server {args.server_id} boot order to {','.join(desired)}?"):
                return 0
            result = client.patch(f"/api/v1/servers/{args.server_id}", {"bootorder": desired})
            if isinstance(result, dict) and result.get("uuid"):
                uuid = _task_uuid(result, "boot-order update")
                state["tasks"]["boot_order"] = uuid
                _write_state(state_path, state)
                _wait_task(client, uuid, runtime, timeout=args.task_timeout_seconds)
        state["phase"] = "boot_order_restored"
        _write_state(state_path, state)

    if state.get("phase") == "boot_order_restored":
        state["phase"] = "powering_disk_boot"
        _write_state(state_path, state)
    if state.get("phase") == "powering_disk_boot":
        details = _guard_mutation(client, args.server_id, "cold-boot the restored disk")
        _check_no_active_tasks(client, args.server_id, "cold-boot the restored disk")
        if not runtime.confirm(f"Cold power-cycle server {args.server_id} to apply media and boot-order changes?"):
            return 0
        live = details.get("serverLiveInfo")
        state_value = live.get("state") if isinstance(live, dict) else details.get("state")
        if isinstance(state_value, str) and state_value.upper() in {"OFF", "STOPPED", "SHUTDOWN"}:
            result = client.patch(f"/api/v1/servers/{args.server_id}", {"state": "ON"})
        else:
            result = client.patch(
                f"/api/v1/servers/{args.server_id}",
                {"state": "ON"},
                params={"stateOption": "POWERCYCLE"},
            )
        uuid = _task_uuid(result, "disk boot power action")
        state["tasks"]["disk_boot"] = uuid
        state["phase"] = "verifying_disk_boot"
        _write_state(state_path, state)
        _wait_task(client, uuid, runtime, timeout=args.task_timeout_seconds)

    if state.get("phase") == "verifying_disk_boot":
        pending_disk_boot = state.get("tasks", {}).get("disk_boot")
        if isinstance(pending_disk_boot, str):
            _wait_task(client, pending_disk_boot, runtime, timeout=args.task_timeout_seconds)
        deadline = time.monotonic() + args.server_ready_timeout_seconds
        while True:
            details = _server_details(client, args.server_id)
            live = details.get("serverLiveInfo")
            config_changed = live.get("configChanged") if isinstance(live, dict) else None
            state_value = live.get("state") if isinstance(live, dict) else details.get("state")
            if config_changed is False and isinstance(state_value, str) and state_value.upper() == "RUNNING":
                break
            if time.monotonic() >= deadline:
                raise CliFailure(
                    "Netcup did not confirm RUNNING with configChanged=false after the cold boot; "
                    "inspect server-details and console before retrying"
                )
            time.sleep(5)
        output = _ssh_verify(
            args.ssh_host,
            Path(args.ssh_identity_file).expanduser().resolve(),
            Path(args.ssh_known_hosts).expanduser().resolve(),
            args.ssh_timeout_seconds,
            args.ssh_host_key_alias,
        )
        state["phase"] = terminal_phase
        state["ssh_verified_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state["ssh_host"] = args.ssh_host
        if recovery:
            state["disk_boot_recovery_verified_at"] = state["ssh_verified_at"]
            state["restore_certified"] = False
        else:
            state["restore_certified"] = True
        _write_state(state_path, state)
        runtime.output.info("Netcup reports RUNNING and configChanged=false.")
        runtime.output.info("Root SSH succeeded; boot-pool is ONLINE with no known data errors.")
        runtime.output.info("Verified guest summary:")
        for line in output.strip().splitlines():
            runtime.output.info(line)
        if recovery:
            runtime.output.info("Disk boot and boot-pool health verified. Restore completion remains uncertified.")
            runtime.output.info(f"Disk-boot recovery completed: {state_path}")
        else:
            runtime.output.info(f"Restore workflow completed: {state_path}")
    return 0


def _finish(args: Any, runtime: Any) -> int:
    return _finish_or_recover(args, runtime, recovery=False)


def _recover(args: Any, runtime: Any) -> int:
    return _finish_or_recover(args, runtime, recovery=True)


def _status(args: Any, runtime: Any) -> int:
    if args.state_file:
        state_path = Path(args.state_file).expanduser().resolve()
    else:
        install_path = _state_path(args.server_id, "install")
        restore_path = _state_path(args.server_id, "restore")
        found = [path for path in (install_path, restore_path) if path.exists()]
        if len(found) > 1:
            raise CliFailure("both installer and restore checkpoints exist; select one with --state-file")
        state_path = found[0] if found else restore_path
    state = _load_state(state_path, args.server_id)
    runtime.output.info(f"Server: {state.get('server_name')} ({args.server_id})")
    runtime.output.info(f"Phase: {state.get('phase')}")
    if state.get("operation"):
        runtime.output.info(f"Operation: {state['operation']}")
    if state.get("restore_certified") is False:
        runtime.output.info("Restore completion: NOT CERTIFIED")
    elif state.get("restore_certified") is True:
        runtime.output.info("Restore completion: certified")
    runtime.output.info(f"ISO: {state.get('user_iso_name')} ({state.get('iso_size_bytes')} bytes)")
    runtime.output.info(f"Saved boot order: {','.join(state['original_boot_order'])}")
    runtime.output.info(f"State file: {state_path}")
    return 0


def build_cli():
    registry = CliRegistry(
        IDENTITY,
        prog="install-restore-iso.py",
        description="Run generic Netcup ISO installs or the TrueNAS golden-disk restore workflow.",
        getting_started=(
            "./install-restore-iso.py start 804027 --iso ../truenas/golden-iso/artifacts/restore.iso",
            "./install-restore-iso.py finish 804027 --yes --ssh-host v1001.vxxu.de --ssh-identity-file ../truenas/golden-iso/vm/root_ed25519 --ssh-known-hosts ../truenas/golden-iso/vm/restore-known_hosts",
        ),
        logging_logger="netcup.install_restore_iso",
        unexpected_exceptions="report",
        expected_exceptions=(OSError, netcup_scp_client.NetcupAPIError),
    )
    registry.register(
        VerbSpec(
            "start",
            "server_id",
            "Upload a restore ISO, attach it, and boot into its unattended restore runtime.",
            group=VerbGroup.MODIFICATION.value,
            mutating=True,
            dry_run=True,
            expensive=True,
            include_json=False,
            include_progress=True,
            arguments=(
                _argument("server_id", "Explicit Netcup server ID to restore.", metavar="SERVER_ID", type=_positive_int),
            ),
            options=(
                _option(("--iso",), "Local bootable restore ISO.", group="ISO UPLOAD", metavar="FILE", required=True, type=Path),
                _option(("--user-iso-name",), "Optional unique filename in Netcup user ISO storage; otherwise derived from the image hash and current UTC time.", group="ISO UPLOAD", metavar="NAME", default=None),
                _option(("--part-size-mib",), "Multipart upload part size in MiB.", group="ISO UPLOAD", metavar="MIB", type=_positive_int, default=64),
                _option(("--state-file",), "Private workflow checkpoint path.", group="RECOVERY", metavar="FILE", type=Path, default=None),
                _option(("--replace-state",), "Replace an existing completed workflow checkpoint.", group="RECOVERY", action="store_true", default=False),
                _option(("--task-timeout-seconds",), "Maximum wait for an SCP task; omit to wait until it ends.", group="STOP CONDITIONS", metavar="SECONDS", type=_positive_finite, default=None),
            ),
            examples=(
                "./install-restore-iso.py start 804027 --iso ../truenas/golden-iso/artifacts/truenas-27-unattended-restore.iso",
                "./install-restore-iso.py start 804027 --iso ./restore.iso --dry-run",
            ),
            handler=_start,
        )
    )
    registry.register(
        VerbSpec(
            "install",
            "server_id",
            "Upload a generic installer ISO, detach provider media after successful guest poweroff, restore boot order, and boot the installed system; optionally probe HTTP readiness.",
            group=VerbGroup.MODIFICATION.value,
            mutating=True,
            dry_run=True,
            expensive=True,
            include_json=False,
            include_progress=True,
            arguments=(
                _argument("server_id", "Netcup server ID to install.", metavar="SERVER_ID", type=_positive_int),
            ),
            options=(
                _option(("--iso",), "Local installer ISO. It must power off the guest after successful installation so provider media can be removed before disk boot.", group="ISO UPLOAD", metavar="FILE", required=True, type=Path),
                _option(("--user-iso-name",), "Optional unique filename in Netcup user ISO storage; otherwise derived from the image hash and current UTC time.", group="ISO UPLOAD", metavar="NAME", default=None),
                _option(("--part-size-mib",), "Multipart upload part size in MiB.", group="ISO UPLOAD", metavar="MIB", type=_positive_int, default=64),
                _option(("--ready-url",), "Optional HTTP(S) URL on the installed guest to probe after disk boot.", group="GUEST READINESS", metavar="URL", default=None),
                _option(("--ready-text",), "Optional text that must appear in the readiness response body; requires --ready-url.", group="GUEST READINESS", metavar="TEXT", default=None),
                _option(("--state-file",), "Private workflow checkpoint path; reuse it to resume an interrupted handoff.", group="RECOVERY", metavar="FILE", type=Path, default=None),
                _option(("--install-timeout-seconds",), "Maximum time to wait for the installer guest to power off.", group="STOP CONDITIONS", metavar="SECONDS", type=_positive_finite, default=3600),
                _option(("--http-ready-timeout-seconds",), "Maximum time to wait for the optional HTTP readiness URL (default 900 seconds).", group="STOP CONDITIONS", metavar="SECONDS", type=_positive_finite, default=None),
                _option(("--server-ready-timeout-seconds",), "Maximum time for Netcup to report RUNNING with configChanged=false.", group="STOP CONDITIONS", metavar="SECONDS", type=_positive_finite, default=300),
                _option(("--task-timeout-seconds",), "Maximum wait for each SCP task; omit to wait until it ends.", group="STOP CONDITIONS", metavar="SECONDS", type=_positive_finite, default=None),
            ),
            constraints=(
                Requires("--ready-text", ("--ready-url",), "response text matching requires an HTTP readiness URL"),
                Requires("--http-ready-timeout-seconds", ("--ready-url",), "HTTP readiness timeout requires an HTTP readiness URL"),
            ),
            examples=(
                "./install-restore-iso.py install 804027 --iso ./TrueNAS-unattended.iso --yes",
                "./install-restore-iso.py install 804027 --iso ./installer.iso --ready-url https://host.example.test/health --ready-text ready --yes",
            ),
            handler=_install,
        )
    )
    registry.register(
        VerbSpec(
            "finish",
            "server_id",
            "After the restore-complete notification, detach the provider ISO, restore saved boot order, cold-boot, and verify TrueNAS.",
            group=VerbGroup.MODIFICATION.value,
            mutating=True,
            dry_run=True,
            expensive=True,
            include_json=False,
            include_progress=True,
            arguments=(
                _argument("server_id", "Netcup server ID recorded by start.", metavar="SERVER_ID", type=_positive_int),
            ),
            options=(
                _option(("--state-file",), "Private workflow checkpoint path.", group="RECOVERY", metavar="FILE", type=Path, default=None),
                _option(("--restore-complete",), "Assert that the restore ISO reported completion and disk verification before removing it.", group="RESTORE HANDOFF", action="store_true", required=True),
                _option(("--ssh-host",), "TrueNAS host or address for final root SSH verification.", group="GUEST VERIFICATION", metavar="HOST", required=True),
                _option(("--ssh-host-key-alias",), "Known-hosts identity to verify when the SSH destination differs from its pinned name.", group="GUEST VERIFICATION", metavar="ALIAS", default=None),
                _option(("--ssh-identity-file",), "Pinned root Ed25519 private key.", group="GUEST VERIFICATION", metavar="FILE", type=Path, required=True),
                _option(("--ssh-known-hosts",), "Known-hosts file containing the pinned TrueNAS host key.", group="GUEST VERIFICATION", metavar="FILE", type=Path, required=True),
                _option(("--ssh-timeout-seconds",), "Maximum time to wait for TrueNAS SSH and boot-pool health.", group="STOP CONDITIONS", metavar="SECONDS", type=_positive_finite, default=900),
                _option(("--server-ready-timeout-seconds",), "Maximum time for Netcup to report RUNNING with configChanged=false.", group="STOP CONDITIONS", metavar="SECONDS", type=_positive_finite, default=300),
                _option(("--task-timeout-seconds",), "Maximum wait for each SCP task; omit to wait until it ends.", group="STOP CONDITIONS", metavar="SECONDS", type=_positive_finite, default=None),
            ),
            examples=(
                "./install-restore-iso.py finish 804027 --restore-complete --ssh-host v1001.vxxu.de --ssh-host-key-alias '[127.0.0.1]:2223' --ssh-identity-file ../truenas/golden-iso/vm/root_ed25519 --ssh-known-hosts ../truenas/golden-iso/vm/restore-known_hosts --yes",
            ),
            handler=_finish,
        )
    )
    registry.register(
        VerbSpec(
            "recover",
            "server_id",
            "After an incomplete ISO run, detach media and try disk boot without asserting restore completion.",
            group=VerbGroup.MODIFICATION.value,
            mutating=True,
            dry_run=True,
            expensive=True,
            include_json=False,
            include_progress=True,
            arguments=(
                _argument("server_id", "Netcup server ID recorded by start.", metavar="SERVER_ID", type=_positive_int),
            ),
            options=(
                _option(("--state-file",), "Private workflow checkpoint path.", group="RECOVERY", metavar="FILE", type=Path, default=None),
                _option(("--try-disk-boot",), "Acknowledge this only attempts disk boot; it does not certify the restore.", group="RECOVERY", action="store_true", required=True),
                _option(("--ssh-host",), "TrueNAS host or address for root SSH verification.", group="GUEST VERIFICATION", metavar="HOST", required=True),
                _option(("--ssh-host-key-alias",), "Known-hosts identity to verify when the SSH destination differs from its pinned name.", group="GUEST VERIFICATION", metavar="ALIAS", default=None),
                _option(("--ssh-identity-file",), "Pinned root Ed25519 private key.", group="GUEST VERIFICATION", metavar="FILE", type=Path, required=True),
                _option(("--ssh-known-hosts",), "Known-hosts file containing the pinned TrueNAS host key.", group="GUEST VERIFICATION", metavar="FILE", type=Path, required=True),
                _option(("--ssh-timeout-seconds",), "Maximum time to wait for TrueNAS SSH and boot-pool health.", group="STOP CONDITIONS", metavar="SECONDS", type=_positive_finite, default=900),
                _option(("--server-ready-timeout-seconds",), "Maximum time for Netcup to report RUNNING with configChanged=false.", group="STOP CONDITIONS", metavar="SECONDS", type=_positive_finite, default=300),
                _option(("--task-timeout-seconds",), "Maximum wait for each SCP task; omit to wait until it ends.", group="STOP CONDITIONS", metavar="SECONDS", type=_positive_finite, default=None),
            ),
            examples=(
                "./install-restore-iso.py recover 804027 --try-disk-boot --ssh-host v1001.vxxu.de --ssh-identity-file ../truenas/golden-iso/vm/root_ed25519 --ssh-known-hosts ../truenas/golden-iso/vm/restore-known_hosts --yes",
            ),
            handler=_recover,
        )
    )
    registry.register(
        VerbSpec(
            "status",
            "server_id",
            "Show the last saved ISO workflow phase and recovery checkpoint.",
            group=VerbGroup.EXPLORATION.value,
            include_json=False,
            include_progress=False,
            arguments=(
                _argument("server_id", "Netcup server ID recorded by start.", metavar="SERVER_ID", type=_positive_int),
            ),
            options=(
                _option(("--state-file",), "Private workflow checkpoint path.", group="RECOVERY", metavar="FILE", type=Path, default=None),
            ),
            examples=("./install-restore-iso.py status 804027",),
            handler=_status,
        )
    )
    return registry.build()


def main(argv: list[str] | None = None) -> int:
    return build_cli().run(argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
