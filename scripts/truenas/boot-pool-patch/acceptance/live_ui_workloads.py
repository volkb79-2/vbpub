#!/usr/bin/env python3
"""Cycle preserved Path A workloads through the TrueNAS UI and verify backing data."""

from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import time
import tomllib

from playwright.sync_api import Error as PlaywrightError
from pwmcp_client import BrowserLease, PwmcpError

from live_ui import observe_rpc_method
from remote import RemoteError, Target


DATASET_RE = re.compile(r"boot-pool/user-data/ui-acceptance-[0-9]{14}[0-9a-f]{6}")
CONTAINER_RE = re.compile(r"uiacceptance[0-9a-f]{8}")
VM_RE = re.compile(r"uiacceptancevm[0-9a-f]{8}")
EXPECTED_MARKER_BYTES = 65_536


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RemoteError(message)


def report(stage: str, detail: str) -> None:
    print(f"[{stage}] {detail}", flush=True)


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RemoteError(f"{name} is required")
    return value


def read_manifest(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RemoteError(f"cannot read UI workload manifest: {type(exc).__name__}") from None
    require(isinstance(value, dict) and value.get("schema_version") == 1,
            "unsupported UI workload manifest schema")
    require(value.get("profile") == "path-a" and value.get("pool") == "boot-pool",
            "UI workload manifest is not for Path A on boot-pool")
    filesystem = value.get("filesystem")
    container = value.get("container")
    vm = value.get("vm")
    zvols = value.get("zvols")
    require(all(isinstance(item, dict) for item in (filesystem, container, vm, zvols)),
            "UI workload manifest is missing its filesystem, container, VM, or zvol records")
    dataset = filesystem.get("dataset")
    mountpoint = filesystem.get("mountpoint")
    probe_file = filesystem.get("probe_file")
    require(isinstance(dataset, str) and DATASET_RE.fullmatch(dataset) is not None,
            "UI workload manifest has an invalid dataset path")
    require(mountpoint == f"/mnt/{dataset}", "UI workload manifest mountpoint does not match its dataset")
    require(probe_file == f"{mountpoint}/ui-disk-probe.bin",
            "UI workload manifest has an unexpected filesystem probe path")
    require(filesystem.get("probe_bytes") == EXPECTED_MARKER_BYTES,
            "UI workload manifest has an unexpected filesystem probe size")
    require(re.fullmatch(r"[0-9a-f]{64}", str(filesystem.get("probe_sha256", ""))) is not None,
            "UI workload manifest has an invalid filesystem probe digest")
    require(isinstance(container.get("name"), str) and CONTAINER_RE.fullmatch(container["name"]),
            "UI workload manifest has an invalid container name")
    require(type(container.get("id")) is int, "UI workload manifest has an invalid container id")
    require(container.get("dataset") == f"boot-pool/user-data/.truenas_containers/containers/{container['name']}",
            "UI workload manifest container dataset does not match its name")
    require(isinstance(vm.get("name"), str) and VM_RE.fullmatch(vm["name"]),
            "UI workload manifest has an invalid VM name")
    require(type(vm.get("id")) is int, "UI workload manifest has an invalid VM id")
    require(vm.get("zvol") == f"{dataset}/vm-disk", "UI workload manifest VM zvol is unexpected")
    require(vm.get("device_path") == f"/dev/zvol/{vm['zvol']}",
            "UI workload manifest VM device path does not match its zvol")
    for key, expected_suffix in (("vm_disk", "vm-disk"), ("cache_device", "cache-device")):
        item = zvols.get(key)
        require(isinstance(item, dict), f"UI workload manifest is missing zvol entry {key}")
        require(item.get("dataset") == f"{dataset}/{expected_suffix}",
                f"UI workload manifest zvol {key} has an unexpected dataset")
        require(item.get("bytes") == EXPECTED_MARKER_BYTES,
                f"UI workload manifest zvol {key} has an unexpected probe size")
        require(re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))) is not None,
                f"UI workload manifest zvol {key} has an invalid digest")
    return value


def one_record(target: Target, method: str, object_id: int, expected_name: str) -> dict:
    rows = target.query(method, [["id", "=", object_id]], timeout=120)
    require(len(rows) == 1, f"{method} returned {len(rows)} records for id {object_id}")
    row = rows[0]
    require(row.get("name") == expected_name,
            f"{method} id {object_id} is {row.get('name')!r}, expected {expected_name!r}")
    return row


def state_of(row: dict) -> str:
    return str((row.get("status") or {}).get("state", "")).upper()


def rpc_request_count(request_lists: tuple[list[dict], ...], method: str) -> int:
    return sum(
        item.get("method") == method
        for requests in request_lists
        for item in requests
    )


def wait_for_rpc_request(page, request_lists: tuple[list[dict], ...], method: str, previous: int) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if rpc_request_count(request_lists, method) > previous:
            return
        page.wait_for_timeout(100)
    raise RemoteError(f"UI did not submit a new {method} request")


def wait_state(
    target: Target, method: str, object_id: int, name: str, expected: str, *, timeout: int = 900,
) -> dict:
    deadline = time.monotonic() + timeout
    last = "unknown"
    while time.monotonic() < deadline:
        row = one_record(target, method, object_id, name)
        last = state_of(row)
        if last == expected:
            return row
        time.sleep(3)
    raise RemoteError(f"{name} did not reach {expected}; last status was {last}")


def file_sha256(target: Target, path: str) -> str:
    digest = target.shell(f"sha256sum {shlex.quote(path)}").split()[0]
    require(re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
            f"could not read a SHA-256 digest for {path}")
    return digest


def zvol_sha256(target: Target, dataset: str) -> str:
    device = f"/dev/zvol/{dataset}"
    target.shell(f"test -b {shlex.quote(device)}")
    output = target.shell(
        f"dd if={shlex.quote(device)} bs=4096 count=16 status=none | sha256sum",
        timeout=90,
    )
    digest = output.split()[0] if output else ""
    require(re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
            f"could not read the probe data from {device}")
    return digest


def verify_backing_data(target: Target, manifest: dict, *, stage: str) -> bool:
    filesystem = manifest["filesystem"]
    dataset = filesystem["dataset"]
    mountpoint = filesystem["mountpoint"]
    mounted = target.shell(f"zfs get -H -o value mounted {shlex.quote(dataset)}")
    filesystem_mounted = mounted == "yes"
    if filesystem_mounted:
        statfs = target.call("filesystem.statfs", mountpoint, timeout=90)
        require(statfs.get("source") == dataset and statfs.get("dest") == mountpoint,
                f"{stage}: filesystem is not accessible from its expected Path A mountpoint: {statfs!r}")
        digest = file_sha256(target, filesystem["probe_file"])
        require(digest == filesystem["probe_sha256"],
                f"{stage}: filesystem probe hash changed: {digest} != {filesystem['probe_sha256']}")
    else:
        report(stage, f"filesystem dataset {dataset} mounted={mounted}; file hash not readable")
    for key, item in manifest["zvols"].items():
        digest = zvol_sha256(target, item["dataset"])
        require(digest == item["sha256"],
                f"{stage}: zvol probe hash changed for {key}: {digest} != {item['sha256']}")
    report(stage, f"zvol probes retain their recorded hashes; filesystem mounted={filesystem_mounted}")
    return filesystem_mounted


def verify_records(target: Target, manifest: dict) -> tuple[dict, dict]:
    container = manifest["container"]
    vm = manifest["vm"]
    container_row = one_record(target, "container.query", container["id"], container["name"])
    vm_row = one_record(target, "vm.query", vm["id"], vm["name"])
    require(container_row.get("dataset") == container["dataset"],
            f"container record dataset is {container_row.get('dataset')!r}, "
            f"expected {container['dataset']!r}")
    if container_row.get("pool") is not None:
        require(container_row["pool"] == "boot-pool",
                f"container record is on {container_row['pool']!r}, not boot-pool")
    vm_devices = target.query("vm.device.query", [["vm", "=", vm["id"]]], timeout=120)
    attached = [
        (device.get("attributes") or {}).get("path", device.get("path"))
        for device in vm_devices
    ]
    require(vm["device_path"] in attached,
            f"VM no longer references its recorded boot-pool zvol: {attached!r}")
    report(
        "inventory",
        f"container {container['name']} state={state_of(container_row)}; "
        f"VM {vm['name']} state={state_of(vm_row)}; VM disk={vm['device_path']}",
    )
    return container_row, vm_row


def matching_row(page, name: str):
    rows = page.get_by_role("row", name=re.compile(re.escape(name)))
    if rows.count() == 1:
        return rows.first
    text = page.get_by_text(re.compile(rf"^{re.escape(name)}(?:\b|$)"))
    try:
        if text.count():
            text.first.wait_for(state="visible", timeout=120_000)
        else:
            rows.first.wait_for(state="visible", timeout=120_000)
    except PlaywrightError:
        # TrueNAS can finish login while its middleware-backed table is still
        # loading. Preserve the visible snapshot below for a useful failure.
        pass
    if rows.count() == 1:
        return rows.first
    if text.count() >= 1:
        candidate = text.first.locator('xpath=ancestor::*[@role="row" or self::tr][1]')
        if candidate.count() == 1:
            return candidate
        # The Containers list is rendered as a custom item without a semantic
        # row. Its nearest button-bearing ancestor contains the item controls.
        candidate = text.first.locator("xpath=ancestor::*[.//button][1]")
        if candidate.count() == 1 and candidate.get_by_role("button").count() >= 1:
            return candidate
    snapshot = page.locator("body").aria_snapshot()
    raise RemoteError(
        f"UI does not expose one row for {name!r}; row count={rows.count()}; "
        f"visible snapshot={snapshot[:5000]!r}"
    )


def perform_ui_action(page, route: str, section: str, name: str, action: str) -> None:
    # TrueNAS 27 labels the VM force-shutdown control "Power Off".
    ui_action = "Power Off" if action == "Force Stop" else action
    page.goto(route, wait_until="domcontentloaded", timeout=120_000)
    page.get_by_text(section, exact=True).first.wait_for(state="visible", timeout=60_000)
    row = matching_row(page, name)
    row.wait_for(state="visible", timeout=60_000)
    action_button = row.get_by_role("button", name=re.compile(rf"^{ui_action}(?:\b|\s)", re.IGNORECASE))
    if action_button.count() == 1 and action_button.is_visible() and action_button.is_enabled():
        action_button.click()
    else:
        icon_name = {"Start": r"^(?:play|play-circle)$", "Stop": r"^(?:stop|stop-circle)$"}.get(ui_action)
        icon_button = row.get_by_role("button", name=re.compile(icon_name, re.IGNORECASE)) if icon_name else None
        if icon_button is not None and icon_button.count() == 1 and icon_button.is_visible() and icon_button.is_enabled():
            icon_button.click()
            report("ui-action", f"submitted {action} for {name} through its UI status control")
            page.wait_for_timeout(500)
            dialogs = page.get_by_role("dialog")
            if dialogs.count() == 1 and dialogs.is_visible():
                confirm = dialogs.get_by_role(
                    "button", name=re.compile(rf"^{ui_action}(?:\b|\s)", re.IGNORECASE)
                )
                if confirm.count() == 1 and confirm.is_visible() and confirm.is_enabled():
                    confirm.click()
            return
        menus = row.get_by_role(
            "button", name=re.compile(r"more|options|actions|menu|chevron-down", re.IGNORECASE)
        )
        if menus.count() != 1 or not menus.is_visible() or not menus.is_enabled():
            raise RemoteError(
                f"{section} row for {name} exposes no unique {action} button or actions menu: "
                f"{row.aria_snapshot()[:4000]!r}"
        )
        menus.click()
        action_pattern = re.compile(rf"^{ui_action}(?:\b|\s)", re.IGNORECASE)
        menuitems = page.get_by_role("menuitem", name=action_pattern)
        buttons = page.get_by_role("button", name=action_pattern)
        deadline = time.monotonic() + 30
        selected = None
        while time.monotonic() < deadline:
            visible = []
            for locator in (menuitems, buttons):
                for index in range(locator.count()):
                    candidate = locator.nth(index)
                    if candidate.is_visible():
                        visible.append(candidate)
            if len(visible) == 1 and visible[0].is_enabled():
                selected = visible[0]
                break
            if len(visible) > 1:
                break
            page.wait_for_timeout(100)
        if selected is None:
            snapshot = page.locator("body").aria_snapshot()
            raise RemoteError(
                f"{section} action menu does not expose one enabled {ui_action} item for {name}; "
                f"menuitems={menuitems.count()}, buttons={buttons.count()}; "
                f"visible snapshot={snapshot[:5000]!r}"
            )
        selected.click()
    page.wait_for_timeout(500)
    dialogs = page.get_by_role("dialog")
    if dialogs.count() == 1 and dialogs.is_visible():
        confirm = dialogs.get_by_role(
            "button", name=re.compile(rf"^{ui_action}(?:\b|\s)", re.IGNORECASE)
        )
        if confirm.count() == 1 and confirm.is_visible() and confirm.is_enabled():
            confirm.click()
        elif dialogs.get_by_role("button").count() > 0:
            raise RemoteError(
                f"{section} {ui_action} confirmation is not uniquely available for {name}: "
                f"{dialogs.aria_snapshot()[:3000]!r}"
            )
    report("ui-action", f"submitted {action} for {name} through {section} UI")


def cycle_workload(
    page, target: Target, *, route: str, section: str, name: str, method: str,
    object_id: int, requests: tuple[list[dict], ...],
) -> None:
    def stop_through_ui(*, allow_force_stop: bool) -> bool:
        stop_method = method.replace("query", "stop")
        previous = rpc_request_count(requests, stop_method)
        perform_ui_action(page, route, section, name, "Stop")
        wait_for_rpc_request(page, requests, stop_method, previous)
        try:
            wait_state(target, method, object_id, name, "STOPPED", timeout=120)
            return False
        except RemoteError as exc:
            if not allow_force_stop or not str(exc).startswith(f"{name} did not reach STOPPED"):
                raise
        # This lane's UI-created VM has no guest OS, so it cannot handle ACPI
        # shutdown. TrueNAS 27 labels the force-stop control "Power Off".
        row = one_record(target, method, object_id, name)
        if state_of(row) == "STOPPED":
            report("ui-workload", f"{name} completed graceful Stop at the shutdown wait boundary")
            return False
        require(state_of(row) == "RUNNING",
                f"{name} cannot be powered off because its status is {state_of(row)!r}")
        perform_ui_action(page, route, section, name, "Force Stop")
        wait_state(target, method, object_id, name, "STOPPED", timeout=120)
        report("ui-workload", f"{name} reached STOPPED through the UI Power Off control")
        return True

    allow_force_stop = method == "vm.query"
    force_stop_used = False
    row = one_record(target, method, object_id, name)
    if state_of(row) == "RUNNING":
        force_stop_used |= stop_through_ui(allow_force_stop=allow_force_stop)
    row = one_record(target, method, object_id, name)
    require(state_of(row) == "STOPPED",
            f"{name} cannot be started because its status is {state_of(row)!r}")
    start_method = method.replace("query", "start")
    previous = rpc_request_count(requests, start_method)
    perform_ui_action(page, route, section, name, "Start")
    wait_for_rpc_request(page, requests, start_method, previous)
    wait_state(target, method, object_id, name, "RUNNING")
    force_stop_used |= stop_through_ui(allow_force_stop=allow_force_stop)
    stop_summary = "Stop/Force Stop" if force_stop_used else "Stop"
    report("ui-workload", f"{name} completed Start → RUNNING → {stop_summary} → STOPPED through the UI")


def main() -> int:
    require(os.environ.get("TRUENAS_STORAGE_PROFILE") == "path-a",
            "TRUENAS_STORAGE_PROFILE must be path-a")
    manifest = read_manifest(Path(required_env("TRUENAS_UI_WORKLOAD_MANIFEST")))
    target = Target.from_environment()
    expected_version = required_env("TRUENAS_EXPECTED_VERSION")
    info = target.call("system.info", timeout=120)
    version = str(info.get("version", ""))
    require(version == expected_version, f"expected TrueNAS {expected_version}, got {version!r}")
    verify_backing_data(target, manifest, stage="before-ui-cycle")
    verify_records(target, manifest)

    secret_path = Path(required_env("TRUENAS_UI_SECRET_FILE"))
    require(secret_path.is_file(), f"TrueNAS UI credential file does not exist: {secret_path}")
    require(not (stat.S_IMODE(secret_path.stat().st_mode) & 0o077),
            "TrueNAS UI credential file must not be accessible by group or others")
    cycle_errors: list[str] = []
    try:
        secrets = tomllib.loads(secret_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RemoteError(f"cannot read TrueNAS UI credentials: {type(exc).__name__}") from None
    admin = secrets.get("truenas_admin", {})
    username = admin.get("username")
    password = admin.get("password")
    require(isinstance(username, str) and isinstance(password, str),
            "TrueNAS UI credentials are missing truenas_admin username/password")
    expected_playwright = required_env("PWMCP_PLAYWRIGHT_VERSION")
    installed_playwright = importlib.metadata.version("playwright")
    require(installed_playwright == expected_playwright,
            f"expected Playwright {expected_playwright}, found {installed_playwright}")

    base_url = required_env("TRUENAS_UI_URL").rstrip("/")
    ws_url = required_env("PWMCP_WS_URL")
    try:
        with BrowserLease.connect(
            ws_url=ws_url,
            lease_seconds=3600,
            label="truenas-live-ui-workloads",
            timeout_ms=120_000,
        ) as lease:
            browser = lease.browser
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 1440, "height": 1000},
            )
            page = context.new_page()
            container_start_requests = observe_rpc_method(page, "container.start")
            container_stop_requests = observe_rpc_method(page, "container.stop")
            vm_start_requests = observe_rpc_method(page, "vm.start")
            vm_stop_requests = observe_rpc_method(page, "vm.stop")
            page.set_default_timeout(30_000)
            page.goto(base_url + "/", wait_until="domcontentloaded", timeout=120_000)
            username_field = page.get_by_role("textbox", name="Username")
            username_field.wait_for(state="visible", timeout=120_000)
            username_field.fill(username)
            page.get_by_role("textbox", name="Password").fill(password)
            page.get_by_role("button", name="Log In", exact=True).click()
            page.wait_for_url(re.compile(r"/ui/(?!signin)"), timeout=120_000)
            report("login", f"authenticated to TrueNAS {version} through PWMCP")

            container = manifest["container"]
            try:
                cycle_workload(
                    page,
                    target,
                    route=base_url + "/ui/containers",
                    section="Containers",
                    name=container["name"],
                    method="container.query",
                    object_id=container["id"],
                    requests=(container_start_requests, container_stop_requests),
                )
            except (RemoteError, PlaywrightError) as exc:
                cycle_errors.append(f"container {container['name']}: {type(exc).__name__}: {exc}")
                report("ui-workload-error", cycle_errors[-1])
            vm = manifest["vm"]
            try:
                cycle_workload(
                    page,
                    target,
                    route=base_url + "/ui/vm",
                    section="Virtual Machines",
                    name=vm["name"],
                    method="vm.query",
                    object_id=vm["id"],
                    requests=(vm_start_requests, vm_stop_requests),
                )
            except (RemoteError, PlaywrightError) as exc:
                cycle_errors.append(f"VM {vm['name']}: {type(exc).__name__}: {exc}")
                report("ui-workload-error", cycle_errors[-1])
            context.close()
    except PwmcpError as exc:
        raise RemoteError(f"PWMCP BrowserLease failed: {type(exc).__name__}: {str(exc)[-1200:]}") from None
    except PlaywrightError as exc:
        raise RemoteError(f"PWMCP UI operation failed: {type(exc).__name__}: {str(exc)[-1600:]}") from None

    verify_records(target, manifest)
    if not verify_backing_data(target, manifest, stage="after-ui-cycle"):
        cycle_errors.append(
            f"UI-created dataset {manifest['filesystem']['dataset']} is not mounted after workload actions"
        )
    if cycle_errors:
        raise RemoteError("workload acceptance failed: " + "; ".join(cycle_errors))
    report("result", f"PASS TrueNAS {version}: container and VM Start/Stop actions worked through the UI")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RemoteError, KeyError, TypeError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
