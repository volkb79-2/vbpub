#!/usr/bin/env python3
"""Exercise patched storage workflows in the TrueNAS UI through PWMCP."""

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
import uuid

import tomllib
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect
from playwright.sync_api import sync_playwright

from remote import RemoteError, Target, flatten_datasets
from ui_resume import (
    unexpected_ui_root_snapshots,
    validate_resumed_ui_dataset,
    ui_root_internal_datasets,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RemoteError(message)


def report(stage: str, detail: str) -> None:
    print(f"[{stage}] {detail}", flush=True)


def connect_pwmcp(playwright, ws_url: str):
    """Retry only a pre-session WebSocket handshake reset during worker recycle."""
    for attempt in range(3):
        try:
            return playwright.chromium.connect(ws_url, timeout=120_000)
        except PlaywrightError as exc:
            if "socket hang up" not in str(exc).lower() or attempt == 2:
                raise
            delay = 2 ** attempt
            report("retry", f"PWMCP worker handshake reset; retrying in {delay}s ({attempt + 1}/2)")
            time.sleep(delay)


def inspect_session_timeout_ui(page, username: str) -> None:
    """Record whether the current UI exposes Session Timeout through its navigation."""
    page.set_default_timeout(30_000)
    keywords = re.compile(
        r"preferences|session|timeout|access|advanced|configure|system|account|admin",
        re.IGNORECASE,
    )

    def report_visible(label: str) -> str:
        snapshot = page.locator("body").aria_snapshot()
        lines = [line.strip() for line in snapshot.splitlines() if keywords.search(line)]
        report(label, f"url={page.url}; controls={lines[:100]!r}")
        return snapshot

    try:
        page.get_by_text("Datasets", exact=True).first.wait_for(state="visible", timeout=60_000)
        report_visible("session-timeout-dashboard")

        user_menu = page.get_by_role(
            "button", name=re.compile(rf"^{re.escape(username)}(?:\s|$)")
        )
        if user_menu.count() == 1 and user_menu.is_visible():
            user_menu.click()
            page.wait_for_timeout(500)
            report_visible("session-timeout-user-menu")
            preferences = page.get_by_text("Preferences", exact=True)
            visible_preferences = [
                preferences.nth(index)
                for index in range(preferences.count())
                if preferences.nth(index).is_visible()
            ]
            if len(visible_preferences) == 1:
                visible_preferences[0].click()
                page.wait_for_timeout(1_000)
                snapshot = report_visible("session-timeout-preferences")
                if "Session Timeout" in snapshot:
                    report("session-timeout-ui", "Session Timeout is visible in Preferences")
                    return
        else:
            report("session-timeout-user-menu", f"user menu control count={user_menu.count()}")

        dashboard = page.get_by_role("link", name=re.compile(r"Dashboard$"))
        if dashboard.count() == 1 and dashboard.is_visible():
            dashboard.click()
            page.wait_for_timeout(500)
        system_menu = page.get_by_role(
            "button", name=re.compile(r"^System(?:\s+submenu)?$", re.IGNORECASE)
        )
        if system_menu.count() == 1 and system_menu.is_visible():
            system_menu.click()
            page.wait_for_timeout(500)
            report_visible("session-timeout-system-menu")
            advanced = page.get_by_text("Advanced Settings", exact=True)
            visible_advanced = [
                advanced.nth(index)
                for index in range(advanced.count())
                if advanced.nth(index).is_visible()
            ]
            if len(visible_advanced) == 1:
                visible_advanced[0].click()
                page.wait_for_load_state("domcontentloaded", timeout=30_000)
                page.wait_for_timeout(1_000)
                snapshot = report_visible("session-timeout-advanced-settings")
                if "Session Timeout" in snapshot:
                    report("session-timeout-ui", "Session Timeout is visible on Advanced Settings")
                else:
                    access = page.get_by_text("Access", exact=True)
                    visible_access = [
                        access.nth(index)
                        for index in range(access.count())
                        if access.nth(index).is_visible()
                    ]
                    configure = []
                    for item in visible_access:
                        card = item.locator("xpath=ancestor::mat-card[1]")
                        if card.count() == 1:
                            button = card.get_by_role("button", name="Configure", exact=True)
                            if button.count() == 1 and button.is_visible():
                                configure.append(button)
                    if len(configure) == 1:
                        configure[0].click()
                        page.wait_for_timeout(1_000)
                        snapshot = report_visible("session-timeout-access-settings")
                    result = "Session Timeout is visible" if "Session Timeout" in snapshot else "Session Timeout was not found in inspected settings views"
                    report("session-timeout-ui", result)
            else:
                report("session-timeout-advanced-settings", f"visible link count={len(visible_advanced)}")
        else:
            report("session-timeout-system-menu", f"System submenu control count={system_menu.count()}")
    except PlaywrightError as exc:
        report("session-timeout-ui", f"navigation inspection stopped: {type(exc).__name__}: {str(exc).splitlines()[0]}")


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RemoteError(f"{name} is required")
    return value


def wait_dataset(target: Target, dataset: str, *, present: bool, timeout: int = 120) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = flatten_datasets(target.query("pool.dataset.query", timeout=120))
        exists = dataset in rows
        if exists is present:
            return True
        time.sleep(3)
    return False


def selected_pool(profile: str) -> str:
    return "boot-pool" if profile == "path-a" else "boot-disk-data"


def selected_dataset_parent(profile: str) -> str:
    return "boot-pool/user-data" if profile == "path-a" else "boot-disk-data"


def container_storage_root(profile: str) -> str:
    if profile == "path-a":
        return "boot-pool/user-data/.truenas_containers"
    return "boot-disk-data/.truenas_containers"


def find_container_storage_dataset(target: Target, profile: str, name: str) -> str:
    root = container_storage_root(profile)
    rows = target.run(["zfs", "list", "-H", "-o", "name", "-r", root], timeout=90).splitlines()
    candidates = [
        row for row in rows
        if row.rsplit("/", 1)[-1] == name and "/containers/" in f"/{row}/"
    ]
    require(len(candidates) == 1,
            f"expected one middleware container dataset named {name!r} below {root}, found {candidates!r}")
    return candidates[0]


def zfs_property(target: Target, dataset: str, property_name: str) -> str:
    return target.run(["zfs", "get", "-H", "-p", "-o", "value", property_name, dataset])


def assert_filesystem_backing(target: Target, dataset: str, expected_mountpoint: str) -> int:
    rows = flatten_datasets(target.query("pool.dataset.query", timeout=120))
    row = rows.get(dataset)
    require(row is not None, f"middleware dataset query omits {dataset}")
    require(row.get("type") == "FILESYSTEM", f"{dataset} is not a filesystem dataset: {row.get('type')!r}")
    mountpoint = zfs_property(target, dataset, "mountpoint")
    require(mountpoint == expected_mountpoint,
            f"{dataset} mounts at {mountpoint!r}, expected {expected_mountpoint!r}")
    source = target.run(["findmnt", "-n", "-o", "SOURCE", "--target", expected_mountpoint])
    require(source == dataset, f"{expected_mountpoint} is backed by {source!r}, not {dataset!r}")
    used = int(zfs_property(target, dataset, "used"))
    return used


def record_dialog(page, dialog, label: str, *, limit: int = 2200) -> str:
    snapshot = dialog.aria_snapshot()
    lines = [line.strip() for line in snapshot.splitlines() if line.strip()]
    summary = " | ".join(lines if limit > 2200 else lines[:32])
    report(label, f"visible form: {summary[:limit]}")
    return snapshot


def _redact_rpc_value(value):
    if isinstance(value, dict):
        return {
            key: "<redacted>" if re.search(r"password|secret|token|private.?key", key, re.IGNORECASE)
            else _redact_rpc_value(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_rpc_value(child) for child in value]
    return value


def observe_rpc_method(
    page,
    method: str,
    *,
    log_response: bool = False,
    response_sink: list[dict] | None = None,
) -> list[dict]:
    """Capture sanitized browser WebSocket traffic for one TrueNAS RPC method."""
    requests: list[dict] = []
    request_ids: dict[str, str] = {}

    def walk_dicts(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from walk_dicts(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk_dicts(child)

    def visit(value):
        for item in walk_dicts(value):
            if item.get("method") == method:
                yield item

    def on_websocket(websocket) -> None:
        def on_frame_sent(payload) -> None:
            if isinstance(payload, bytes):
                raw = payload.decode("utf-8", errors="replace")
            elif isinstance(payload, str):
                raw = payload
            else:
                return
            if method not in raw:
                return
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                report("browser-rpc", f"could not decode {method} frame: {raw[:1600]!r}")
                return
            for request in visit(frame):
                sanitized = _redact_rpc_value(request)
                requests.append(sanitized)
                if request.get("id") is not None:
                    request_ids[str(request["id"])] = method
                report("browser-rpc", f"{method} request: {json.dumps(sanitized, sort_keys=True)[:5000]}")

        def on_frame_received(payload) -> None:
            if isinstance(payload, bytes):
                raw = payload.decode("utf-8", errors="replace")
            elif isinstance(payload, str):
                raw = payload
            else:
                return
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                return
            for response in walk_dicts(frame):
                response_id = response.get("id")
                if response_id is None or request_ids.pop(str(response_id), None) != method:
                    continue
                if response.get("error") is not None or log_response:
                    sanitized = _redact_rpc_value(response)
                    if response_sink is not None:
                        response_sink.append(sanitized)
                    report(
                        "browser-rpc-response",
                        f"{method} response: {json.dumps(sanitized, sort_keys=True)[:5000]}",
                    )

        websocket.on("framesent", on_frame_sent)
        websocket.on("framereceived", on_frame_received)

    page.on("websocket", on_websocket)
    return requests


def dataset_create_properties(requests: list[dict], dataset: str) -> dict | None:
    for request in requests:
        if request.get("method") != "pool.dataset.create":
            continue
        params = request.get("params")
        if not isinstance(params, list):
            continue
        for item in params:
            if isinstance(item, dict) and item.get("name") == dataset:
                return item
    return None


def rpc_object_for_name(requests: list[dict], method: str, name: str) -> dict | None:
    for request in requests:
        if request.get("method") != method:
            continue
        params = request.get("params")
        if not isinstance(params, list):
            continue
        for item in params:
            if isinstance(item, dict) and item.get("name") == name:
                return item
    return None


def cleanup_stale_ui_container(
    target: Target, profile: str, container_name: str, image_name: str,
) -> None:
    """Delete only the named, empty, stopped orphan from a failed UI acceptance run."""
    require(profile == "path-a", "stale UI container recovery is valid only for Path A")
    require(
        re.fullmatch(r"uiacceptance[0-9a-f]{8}", container_name) is not None,
        "stale UI container recovery requires an exact uiacceptance<8 lowercase hex> name",
    )
    root = "boot-pool/user-data"
    dataset = f"{root}/.truenas_containers/containers/{container_name}"
    records = target.query("container.query", timeout=120)
    require(len(records) == 1, f"stale UI recovery expected exactly one container record: {records!r}")
    record = records[0]
    require(record.get("name") == container_name,
            f"stale UI recovery found unexpected container name {record.get('name')!r}")
    require(type(record.get("id")) is int, f"stale UI recovery found invalid container id: {record!r}")
    require(record.get("dataset") == dataset,
            f"stale UI recovery found container backing dataset {record.get('dataset')!r}, expected {dataset!r}")
    require((record.get("status") or {}).get("state") == "STOPPED",
            f"stale UI recovery requires a stopped container: {record.get('status')!r}")
    require(target.query("vm.query", timeout=120) == [],
            "stale UI recovery refuses while VM records exist")
    require(target.shell("zpool list -H -o name,health", timeout=90).splitlines() == ["boot-pool\tONLINE"],
            "stale UI recovery requires boot-pool ONLINE with no other imported pools")

    quota = zfs_property(target, root, "quota")
    require(quota.isdecimal() and 1_000_000_000 <= int(quota) < 2**64 - 1,
            f"stale UI recovery requires the existing finite Path A root quota: {quota!r}")
    mountpoint = zfs_property(target, root, "mountpoint")
    require(mountpoint == "/mnt/boot-pool/user-data",
            f"stale UI recovery found unexpected root mountpoint {mountpoint!r}")
    require(zfs_property(target, root, "canmount") == "on",
            "stale UI recovery requires canmount=on for the existing root")
    require(target.run(["findmnt", "-n", "-o", "SOURCE", "--target", mountpoint]) == root,
            "stale UI recovery requires the existing root to be mounted from boot-pool/user-data")

    direct_names = target.run([
        "zfs", "list", "-H", "-t", "filesystem,volume", "-o", "name", "-r", "boot-pool",
    ], timeout=90).splitlines()
    try:
        descendants, image_datasets = ui_root_internal_datasets(
            direct_names, root, image_name, stale_container_name=container_name,
        )
    except ValueError as exc:
        raise RemoteError(str(exc)) from exc
    require(dataset in descendants, f"stale UI recovery cannot find {dataset}")
    require(bool(image_datasets), "stale UI recovery requires the pinned image cache from the failed UI run")
    require(zfs_property(target, dataset, "used") == "0",
            f"stale UI recovery refuses a container dataset with allocated contents: {dataset}")
    dataset_tree = target.run([
        "zfs", "list", "-H", "-t", "filesystem,volume,snapshot", "-o", "name", "-r", dataset,
    ], timeout=45)
    snapshots = [name for name in dataset_tree.splitlines() if "@" in name]
    require(not snapshots, f"stale UI recovery refuses a container dataset with snapshots: {snapshots!r}")

    report(
        "stale-ui-container-recovery",
        f"deleting only stopped empty container {container_name} id={record['id']}; "
        f"preserving root={root} and image-cache-datasets={image_datasets!r}",
    )
    delete_job = target.call("container.delete", record["id"], {
        "force": False,
        "recursive": False,
    }, timeout=120)
    if isinstance(delete_job, int):
        target.wait_job(delete_job, timeout=600, label="stale UI container cleanup")
    remaining_records = target.query("container.query", timeout=120)
    require(not remaining_records,
            f"stale UI container record remains after targeted delete: {remaining_records!r}")
    remaining_names = target.run([
        "zfs", "list", "-H", "-t", "filesystem,volume", "-o", "name", "-r", "boot-pool",
    ], timeout=90).splitlines()
    require(dataset not in remaining_names,
            f"stale UI container dataset remains after targeted delete: {dataset}")
    _, remaining_images = ui_root_internal_datasets(remaining_names, root, image_name)
    require(remaining_images == image_datasets,
            f"targeted cleanup changed the retained image cache: before={image_datasets!r}, after={remaining_images!r}")
    require(zfs_property(target, root, "quota") == quota,
            "targeted cleanup changed the Path A root quota")
    require(target.run(["findmnt", "-n", "-o", "SOURCE", "--target", mountpoint]) == root,
            "targeted cleanup changed the Path A root mount source")
    report("stale-ui-container-recovery", "target container record and dataset removed; Path A root and image cache preserved")


def click_dialog_submit(page, dialog, labels: tuple[str, ...]) -> str:
    for label in labels:
        button = dialog.get_by_role("button", name=label, exact=True)
        if button.count() == 1 and button.is_visible() and button.is_enabled():
            button.click()
            return label
    snapshot = dialog.aria_snapshot()
    raise RemoteError(f"form has no enabled submit button from {labels!r}; ARIA form={snapshot[:2400]!r}")


def submit_result_details(page, dialog) -> tuple[str, list[str]]:
    """Capture visible overlays and field errors when a UI submit does not close."""
    overlays = page.get_by_role("dialog")
    visible: list[str] = []
    for index in range(overlays.count()):
        overlay = overlays.nth(index)
        if not overlay.is_visible():
            continue
        label = overlay.get_attribute("aria-label") or "unnamed dialog"
        text = " ".join(overlay.inner_text(timeout=2_000).split())
        snapshot = overlay.aria_snapshot()
        visible.append(f"{label}: text={text[:1600]!r} aria={snapshot[:2400]!r}")
    errors = [
        text.strip()
        for text in dialog.locator("mat-error").all_text_contents()
        if text.strip()
    ]
    return " | ".join(visible), errors


def fill_labeled_textbox(dialog, labels: tuple[str, ...], value: str, *, required: bool = True) -> bool:
    for label in labels:
        field = dialog.get_by_role("textbox", name=label, exact=True)
        if field.count() == 1 and field.is_visible():
            field.fill(value)
            return True
    if required:
        snapshot = dialog.aria_snapshot()
        raise RemoteError(f"form has no textbox labeled {labels!r}; ARIA form={snapshot[:2400]!r}")
    return False


def inventory_before_ui_mutation(
    target: Target, profile: str, image_name: str, *, resume_existing_root: bool = False,
    resume_existing_ui_dataset: str = "",
) -> bool:
    pool = selected_pool(profile)
    root = selected_dataset_parent(profile)
    rows = flatten_datasets(target.query("pool.dataset.query", timeout=120))
    pool_rows = target.query("pool.query", timeout=120)
    direct_pools = target.shell("zpool list -H -o name,health", timeout=90).splitlines()
    pool_health = {
        line.split("\t", 1)[0]: line.split("\t", 1)[1]
        for line in direct_pools if "\t" in line
    }
    require(pool_health.get("boot-pool") == "ONLINE",
            f"fresh guest direct ZFS inventory omits boot-pool ONLINE: {direct_pools!r}")
    require("boot-disk-data" not in pool_health,
            f"fresh guest already has Path B pool boot-disk-data: {direct_pools!r}")
    try:
        patch_record = json.loads(
            target.shell("cat /root/v27-boot-pool-patch-install.json", timeout=30)
        )
    except (RemoteError, ValueError) as exc:
        raise RemoteError("live-ui requires the registered v27 patch-install record") from exc
    require(
        patch_record.get("root_create_guard") == "root-excluded-from-descendant-readiness-v1",
        f"live-ui requires the v27 root-create guard marker from the current patch bundle: {patch_record!r}",
    )
    if profile == "path-b":
        require(not resume_existing_root,
                "TRUENAS_TEST_RESUME_EXISTING_UI_ROOT is valid only for Path A live-ui")
        require(not resume_existing_ui_dataset,
                "TRUENAS_TEST_RESUME_EXISTING_UI_DATASET is valid only for Path A live-ui")
        require("boot-disk-data" not in rows,
                "fresh Path B target already has boot-disk-data dataset state")
        disk = required_env("TRUENAS_DISK_DEVICE")
        boot_partition = required_env("TRUENAS_BOOT_POOL_PARTITION")
        block_devices = json.loads(target.run([
            "lsblk", "--json", "--tree", "--paths", "--bytes", "--output",
            "PATH,NAME,TYPE,SIZE,RO,RM,PARTN,PARTTYPE,PARTLABEL", disk,
        ], timeout=90))
        middleware_disks = target.query("disk.query", timeout=120)
        report("path-b-device-inventory", {
            "selected_disk": disk,
            "boot_pool_partition": boot_partition,
            "block_devices": block_devices,
            "middleware_disks": [
                {
                    key: row.get(key)
                    for key in ("name", "devname", "identifier", "size", "model")
                    if key in row
                }
                for row in middleware_disks
                if isinstance(row, dict)
            ],
        })
    containers = target.query("container.query", timeout=120)
    vms = target.query("vm.query", timeout=120)
    require(containers == [], f"fresh UI target has existing container records: {containers!r}")
    require(vms == [], f"fresh UI target has existing VM records: {vms!r}")
    # Path B has no selected pool yet: the UI wizard creates it later in this
    # run. Only query datasets beneath a pool that direct ZFS inventory found.
    direct_names = []
    if pool in pool_health:
        direct_names = target.run([
            "zfs", "list", "-H", "-t", "filesystem,volume", "-o", "name", "-r", pool,
        ], timeout=90).splitlines()
    existing_root = profile == "path-a" and root in rows
    if existing_root:
        require(resume_existing_root,
                f"fresh Path A target already has {root}; set the guarded UI resume flag only "
                "for the previously UI-created root")
        try:
            descendants, cached_images = ui_root_internal_datasets(
                direct_names, root, image_name, resumed_ui_dataset=resume_existing_ui_dataset,
            )
        except ValueError as exc:
            raise RemoteError(str(exc)) from exc
        require(
            not any("/containers/" in name for name in descendants),
            f"UI resume requires no existing container datasets: {descendants!r}",
        )
        root_row = rows[root]
        require(root_row.get("type") == "FILESYSTEM",
                f"existing Path A root is not a filesystem dataset: {root_row.get('type')!r}")
        mountpoint = zfs_property(target, root, "mountpoint")
        canmount = zfs_property(target, root, "canmount")
        quota_text = zfs_property(target, root, "quota")
        require(mountpoint == "/mnt/boot-pool/user-data",
                f"UI resume root mountpoint is {mountpoint!r}")
        require(canmount == "on", f"UI resume root canmount is {canmount!r}")
        require(quota_text.isdecimal() and 1_000_000_000 <= int(quota_text) < 2**64 - 1,
                f"UI resume root must retain a finite quota of at least 1 GiB: {quota_text!r}")
        mount_source = target.run(["findmnt", "-n", "-o", "SOURCE", "--target", mountpoint])
        require(mount_source == root,
                f"UI resume root mount source is {mount_source!r}, expected {root!r}")
        root_tree = target.run([
            "zfs", "list", "-H", "-t", "filesystem,volume,snapshot", "-o", "name", "-r", root,
        ], timeout=45)
        root_snapshots = [name for name in root_tree.splitlines() if "@" in name]
        unexpected_snapshots = unexpected_ui_root_snapshots(root_snapshots, cached_images)
        require(not unexpected_snapshots,
                "UI resume refuses snapshots outside the pinned middleware image cache: "
                f"{unexpected_snapshots!r}")
        if resume_existing_ui_dataset:
            prior_dataset = resume_existing_ui_dataset
            prior_dataset_row = rows.get(prior_dataset)
            require(prior_dataset_row is not None and prior_dataset_row.get("type") == "FILESYSTEM",
                    f"explicitly resumed UI dataset is not a middleware filesystem row: {prior_dataset_row!r}")
            prior_mountpoint = f"/mnt/{prior_dataset}"
            require(zfs_property(target, prior_dataset, "mountpoint") == prior_mountpoint,
                    f"explicitly resumed UI dataset has an unexpected mountpoint: {prior_dataset!r}")
            require(zfs_property(target, prior_dataset, "canmount") == "on",
                    f"explicitly resumed UI dataset is not mounted with canmount=on: {prior_dataset!r}")
            require(target.run([
                "findmnt", "-n", "-o", "SOURCE", "--target", prior_mountpoint,
            ]) == prior_dataset,
                f"explicitly resumed UI dataset is not mounted from its own ZFS dataset: {prior_dataset!r}")
            for zvol_name in (f"{prior_dataset}/vm-disk", f"{prior_dataset}/cache-device"):
                require(zvol_name in rows and rows[zvol_name].get("type") == "VOLUME",
                        f"explicitly resumed UI zvol is not a middleware volume row: {zvol_name!r}")
                assert_zvol_backing(target, zvol_name)
                require(zfs_property(target, zvol_name, "volblocksize") == "16384",
                        f"explicitly resumed UI zvol does not have a 16 KiB block size: {zvol_name!r}")
        root_contents = target.run([
            "find", mountpoint, "-mindepth", "1", "-maxdepth", "1", "-printf", "%f\\n",
        ]).splitlines()
        allowed_root_contents = {".truenas_containers"}
        if resume_existing_ui_dataset:
            allowed_root_contents.add(resume_existing_ui_dataset.rsplit("/", 1)[-1])
        require(set(root_contents) <= allowed_root_contents,
                f"UI resume found user data outside the managed container cache: {root_contents!r}")
        require(bool(cached_images) or ".truenas_containers" not in root_contents,
                "UI resume found a container cache directory without a verified pinned image dataset")
    else:
        require(not resume_existing_root,
                f"UI resume flag requires the existing UI-created root {root}")
        require(not resume_existing_ui_dataset,
                f"TRUENAS_TEST_RESUME_EXISTING_UI_DATASET requires the existing UI-created root {root}")
    require(not any("uiacceptance" in name for name in direct_names),
            "read-only inventory found a stale UI acceptance dataset before mutation")
    report(
        "readonly-inventory",
        f"profile={profile} pool={pool} "
        f"{'resuming-verified-ui-root=' + root if existing_root else 'unconfigured-root=' + root} "
        f"direct-pools={pool_health!r} middleware-pool-query={pool_rows!r} "
        f"datasets={len(rows)} containers=0 vms=0 direct-zfs-rows={len(direct_names)} "
        f"cached-ui-images={cached_images if existing_root else []!r}",
    )
    return existing_root


def _select_ui_option(page, dialog, labels: tuple[str, ...], value: str) -> bool:
    for label in labels:
        selector = dialog.get_by_role("combobox", name=re.compile(label, re.IGNORECASE))
        if selector.count() == 1 and selector.is_visible():
            selector.click()
            option = page.get_by_role("option", name=re.compile(rf"^{re.escape(value)}$", re.IGNORECASE))
            option.wait_for(state="visible", timeout=30_000)
            option.click()
            expect(selector).to_contain_text(value, timeout=30_000)
            return True
    return False


def create_path_a_root_in_ui(
    page, base_url: str, target: Target, dataset_create_requests: list[dict]
) -> str:
    """Create and configure the Path A namespace using the dataset UI only."""
    root = "boot-pool/user-data"
    page.goto(base_url + "/ui/datasets/boot-pool", wait_until="domcontentloaded", timeout=120_000)
    add = page.get_by_role("button", name="Add Dataset", exact=True)
    add.wait_for(state="visible", timeout=120_000)
    add.click()
    dialog = page.get_by_role("dialog", name="Slide-in form", exact=True)
    dialog.wait_for(state="visible", timeout=60_000)
    snapshot = record_dialog(page, dialog, "path-a-root-ui-form")
    fill_labeled_textbox(dialog, ("Name",), "user-data")

    advanced = dialog.get_by_role("button", name=re.compile(r"advanced", re.IGNORECASE))
    if advanced.count() == 1 and advanced.is_visible():
        advanced.click()
        basic_options = dialog.get_by_role("button", name=re.compile(r"basic options", re.IGNORECASE))
        basic_options.wait_for(state="visible", timeout=30_000)
    snapshot = record_dialog(page, dialog, "path-a-root-ui-advanced-form", limit=6000)

    mountpoint = dialog.get_by_role(
        "textbox", name=re.compile(r"^(mount.?point|path)$", re.IGNORECASE)
    )
    if mountpoint.count() == 1 and mountpoint.is_visible():
        mountpoint.fill("/mnt/boot-pool/user-data")
        report("path-a-root-ui", "set the root mountpoint in the dataset form")
    else:
        report("path-a-root-ui", "dataset form uses its default mountpoint; verifying it after creation")

    quota = dialog.get_by_role(
        "textbox", name="Quota for this dataset and all children", exact=True
    )
    if quota.count() != 1 or not quota.is_visible():
        snapshot = dialog.aria_snapshot()
        raise RemoteError(
            "Path A dataset UI does not expose the all-children quota control for boot-pool/user-data; "
            f"visible form={snapshot[:2400]!r}"
        )
    quota.fill("2 GiB")
    quota_value_before_blur = quota.input_value()
    quota.press("Tab")
    page.wait_for_timeout(500)
    quota_value_after_blur = quota.input_value()
    quota_dom = quota.evaluate(
        """el => ({value: el.value, type: el.type, name: el.name, id: el.id,
                    ariaLabel: el.getAttribute('aria-label'), dataTest: el.getAttribute('data-test'),
                    parent: el.parentElement?.outerHTML?.slice(0, 1400)})"""
    )
    require(
        quota_value_before_blur == quota_value_after_blur == "2 GiB",
        "Path A all-children quota control did not retain the finite value after blur: "
        f"before={quota_value_before_blur!r}, after={quota_value_after_blur!r}",
    )
    require(
        quota_dom.get("dataTest") == "input-quota",
        "Path A all-children quota label is not bound to the TrueNAS quota field: "
        f"data-test={quota_dom.get('dataTest')!r}",
    )
    report(
        "path-a-quota-control",
        f"filled={quota_value_before_blur!r}; after blur={quota_value_after_blur!r}; "
        f"DOM={json.dumps(quota_dom, sort_keys=True)[:2200]}",
    )

    canmount = dialog.get_by_role("checkbox", name=re.compile(r"can.?mount", re.IGNORECASE))
    if canmount.count() == 1 and canmount.is_visible() and not canmount.is_checked():
        canmount.check()
    else:
        _select_ui_option(page, dialog, ("can.?mount",), "On")

    request_count_before_submit = len(dataset_create_requests)
    click_dialog_submit(page, dialog, ("Save", "Create", "Add"))
    request_deadline = time.monotonic() + 5
    while len(dataset_create_requests) == request_count_before_submit and time.monotonic() < request_deadline:
        page.wait_for_timeout(100)
    create_requests = dataset_create_requests[request_count_before_submit:]
    root_request = next(
        (request for request in create_requests if "user-data" in json.dumps(request)),
        None,
    )
    report(
        "path-a-root-ui-wire",
        f"captured {len(create_requests)} pool.dataset.create request(s); "
        f"root request={json.dumps(root_request, sort_keys=True)[:5000] if root_request else 'missing'}",
    )
    root_properties = dataset_create_properties(create_requests, root)
    validation = page.get_by_role("dialog", name=re.compile(r"validation error", re.IGNORECASE))
    submitted = False
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if validation.count() and validation.first.is_visible():
            break
        if dialog.count() == 0 or not dialog.is_visible():
            submitted = True
            break
        page.wait_for_timeout(250)
    if validation.count() and validation.first.is_visible():
        validation_text = " ".join(validation.first.inner_text(timeout=10_000).split())
        overlays, form_errors = submit_result_details(page, dialog)
        raise RemoteError(
            "Path A dataset UI rejected the root configuration; "
            f"validation={validation_text!r} request={root_request!r} "
            f"overlays={overlays!r} field_errors={form_errors!r}"
        )
    if not submitted:
        overlays, form_errors = submit_result_details(page, dialog)
        root_exists = wait_dataset(target, root, present=True, timeout=20)
        if root_exists:
            report(
                "path-a-root-ui",
                "Save left the UI form visible, but read-only middleware inventory confirms "
                f"the UI-created root exists; overlays={overlays!r} field_errors={form_errors!r}",
            )
            page.goto(base_url + "/ui/datasets/boot-pool", wait_until="domcontentloaded", timeout=120_000)
        else:
            raise RemoteError(
                "Path A dataset UI Save did not close or create boot-pool/user-data within 20 seconds; "
                f"overlays={overlays!r} field_errors={form_errors!r}"
            )
    require(root_properties is not None,
            "TrueNAS UI did not submit pool.dataset.create for boot-pool/user-data")
    ui_quota = root_properties.get("quota")
    require(type(ui_quota) is int and ui_quota >= 1_000_000_000,
            "TrueNAS UI request must submit an explicit finite quota of at least 1 GiB; "
            f"request quota={ui_quota!r}, refquota={root_properties.get('refquota')!r}")
    require(wait_dataset(target, root, present=True),
            "the Path A root created through the dataset UI is missing from middleware query")
    actual_mountpoint = zfs_property(target, root, "mountpoint")
    actual_canmount = zfs_property(target, root, "canmount")
    actual_quota = int(zfs_property(target, root, "quota"))
    require(actual_mountpoint == "/mnt/boot-pool/user-data",
            f"UI-created Path A root mountpoint is {actual_mountpoint!r}")
    require(actual_canmount == "on", f"UI-created Path A root canmount is {actual_canmount!r}")
    require(actual_quota >= 1_000_000_000,
            f"UI-created Path A root quota is not a finite 1 GiB or larger value: {actual_quota}")
    source = target.run(["findmnt", "-n", "-o", "SOURCE", "--target", actual_mountpoint])
    require(source == root,
            f"UI-created Path A root mount source is {source!r}, expected {root!r}")
    choices = target.call("container.pool_choices", timeout=120)
    require("boot-pool" in choices,
            f"patched container pool choices omit boot-pool after UI root setup: {choices!r}")
    report(
        "path-a-root-ui",
        f"created {root}; mountpoint={actual_mountpoint}; canmount={actual_canmount}; "
        f"quota={actual_quota}; findmnt source={source}; container pool choice includes boot-pool",
    )
    return root


def create_path_b_pool_in_ui(
    page,
    base_url: str,
    target: Target,
    pool_create_requests: list[dict],
) -> str:
    """Try the supported pool wizard, accepting only the reserved free-tail partition."""
    body = page.locator("body")
    create = page.get_by_role("button", name=re.compile(r"^create pool$", re.IGNORECASE))
    if create.count() == 0:
        storage_link = page.get_by_text("Storage", exact=True).first
        if storage_link.count() == 0 or not storage_link.is_visible():
            # Return to the authenticated dashboard using the UI so its
            # navigation shell is mounted before opening the Storage section.
            page.goto(base_url + "/", wait_until="domcontentloaded", timeout=120_000)
            page.get_by_text("Datasets", exact=True).first.wait_for(state="visible", timeout=60_000)
            storage_link = page.get_by_text("Storage", exact=True).first
        if storage_link.count() == 1 and storage_link.is_visible():
            storage_link.click()
            create.wait_for(state="visible", timeout=60_000)
        else:
            snapshot = body.aria_snapshot()
            raise RemoteError(
                "Path B UI cannot reach the Storage section from the authenticated UI; "
                f"page={page.url!r} form={snapshot[:2600]!r}"
            )
        create = page.get_by_role("button", name=re.compile(r"^create pool$", re.IGNORECASE))
    if create.count() != 1 or not create.is_visible():
        snapshot = body.aria_snapshot()
        raise RemoteError(
            "Path B UI cannot open the storage pool creation wizard; "
            f"page={page.url!r} form={snapshot[:2600]!r}"
        )
    create.click()
    dialog = page.get_by_role("dialog")
    if dialog.count() == 0:
        dialog = body
    dialog.wait_for(state="visible", timeout=60_000)
    snapshot = record_dialog(page, dialog, "path-b-pool-ui-wizard")

    name = dialog.get_by_role("textbox", name=re.compile(r"name", re.IGNORECASE))
    if name.count() == 1 and name.is_visible():
        name.fill("boot-disk-data")

    # The UI must present the guarded free-tail partition. Never select vda:
    # it contains the running boot-pool and selecting it can destroy TrueNAS.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        visible = " ".join(body.inner_text(timeout=30_000).split())
        if re.search(r"(?<![A-Za-z0-9])(?:/dev/)?vda4(?![0-9])", visible, re.IGNORECASE):
            break
        next_button = page.get_by_role("button", name=re.compile(r"^(next|data|save and go to review)$", re.IGNORECASE))
        if next_button.count() != 1 or not next_button.is_enabled():
            # The wizard route can render its heading before the current
            # step's controls and device inventory finish loading.
            page.wait_for_timeout(500)
            snapshot = dialog.aria_snapshot()
            continue
        next_button.click()
        page.wait_for_timeout(500)
        dialog = page.get_by_role("dialog")
        if dialog.count() == 0:
            dialog = body
        snapshot = record_dialog(page, dialog, "path-b-pool-ui-step")

    visible = " ".join(body.inner_text(timeout=30_000).split())
    if not re.search(r"(?<![A-Za-z0-9])(?:/dev/)?vda4(?![0-9])", visible, re.IGNORECASE):
        raise RemoteError(
            "Path B pool wizard does not offer the free-tail partition vda4. "
            "The test refuses to select whole-disk vda because it contains boot-pool; "
            f"page={page.url!r} visible_text={visible[:1800]!r} last UI form={snapshot[:2600]!r}"
        )

    candidate = page.get_by_role("checkbox", name=re.compile(r"(?:/dev/)?vda4\b", re.IGNORECASE))
    if candidate.count() == 1 and candidate.is_visible():
        candidate.check()
    else:
        exact_device = page.get_by_text(re.compile(r"^(?:/dev/)?vda4$", re.IGNORECASE))
        if exact_device.count() != 1 or not exact_device.is_visible():
            raise RemoteError(
                "Path B pool wizard displays vda4 but does not expose one exact selectable control; "
                f"last UI form={snapshot[:2600]!r}"
            )
        exact_device.click()

    selected = candidate.count() == 1 and candidate.is_checked()
    if not selected:
        selected = bool(re.search(r"(?:selected|assigned).{0,80}vda4|vda4.{0,80}(?:selected|assigned)",
                                  " ".join(body.inner_text().split()), re.IGNORECASE))
    require(selected, "pool wizard did not confirm vda4 as the selected data device")

    for _ in range(8):
        create_final = page.get_by_role("button", name=re.compile(r"^(create pool|create)$", re.IGNORECASE))
        if create_final.count() == 1 and create_final.is_enabled():
            create_final.click()
            break
        next_button = page.get_by_role(
            "button", name=re.compile(r"^(next|save and go to review)$", re.IGNORECASE)
        )
        if next_button.count() != 1 or not next_button.is_enabled():
            snapshot = dialog.aria_snapshot()
            raise RemoteError(f"pool wizard cannot advance with vda4 selected: {snapshot[:2600]!r}")
        next_button.click()
        page.wait_for_timeout(500)
        dialog = page.get_by_role("dialog")
        if dialog.count() == 0:
            dialog = body
        snapshot = record_dialog(page, dialog, "path-b-pool-ui-review")
    else:
        raise RemoteError("Path B pool wizard did not reach its final create action")

    confirmation = page.get_by_role("dialog")
    if confirmation.count() == 1 and confirmation.is_visible():
        confirm = confirmation.get_by_role("button", name=re.compile(r"^(create pool|confirm|create)$", re.IGNORECASE))
        if confirm.count() == 1 and confirm.is_visible() and confirm.is_enabled():
            confirm.click()
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        pools = target.query("pool.query", timeout=120)
        match = next((row for row in pools if row.get("name") == "boot-disk-data"), None)
        if match is not None and match.get("status") == "ONLINE":
            request = rpc_object_for_name(
                pool_create_requests, "pool.create", "boot-disk-data",
            )
            require(request is not None,
                    "boot-disk-data is ONLINE but the browser did not submit pool.create")
            topology = request.get("topology")
            require(isinstance(topology, dict),
                    f"UI pool.create request has no topology object: {request!r}")
            selected_devices: set[str] = set()

            def collect_topology_devices(value) -> None:
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key == "disks" and isinstance(child, list):
                            for device in child:
                                if isinstance(device, str):
                                    selected_devices.add(device.rstrip("/").rsplit("/", 1)[-1])
                        collect_topology_devices(child)
                elif isinstance(value, list):
                    for child in value:
                        collect_topology_devices(child)

            collect_topology_devices(topology)
            require("vda4" in selected_devices and "vda" not in selected_devices,
                    "UI pool.create must submit the reserved vda4 partition and must not submit "
                    f"the whole boot disk: devices={sorted(selected_devices)!r}; request={request!r}")
            choices = target.call("container.pool_choices", timeout=120)
            require("boot-disk-data" in choices,
                    f"container pool choices omit UI-created Path B pool: {choices!r}")
            vdev = target.shell("zpool status -P boot-disk-data", timeout=90)
            require("/dev/vda4" in vdev or "vda4" in vdev,
                    f"UI-created boot-disk-data is not backed by vda4: {vdev!r}")
            report(
                "path-b-pool-ui",
                "browser pool.create submitted boot-disk-data with vda4 only; "
                "middleware registered it ONLINE and container.pool_choices includes it",
            )
            return "boot-disk-data"
        time.sleep(5)
    raise RemoteError("UI pool creation did not register boot-disk-data ONLINE within 600 seconds")


def inspect_path_b_partition_ui(page, base_url: str) -> None:
    """Record disk-screen controls related to partition creation without mutating storage."""
    storage_url = base_url + "/ui/storage"
    try:
        page.goto(storage_url, wait_until="domcontentloaded", timeout=120_000)
        page.get_by_role("button", name=re.compile(r"^create pool$", re.IGNORECASE)).wait_for(
            state="visible", timeout=90_000,
        )
        candidates = []
        for role in ("button", "link"):
            control = page.get_by_role(role, name=re.compile(r"^disks$", re.IGNORECASE))
            if control.count() == 1 and control.is_visible():
                candidates.append((role, control))
        if len(candidates) != 1:
            snapshot = page.locator("body").aria_snapshot()
            report(
                "path-b-partition-ui-inventory",
                "Storage dashboard did not expose exactly one visible Disks navigation control; "
                f"found={len(candidates)} page={page.url!r} form={snapshot[:2500]!r}",
            )
            return
        role, control = candidates[0]
        control.click()
        page.wait_for_load_state("domcontentloaded", timeout=30_000)
        page.wait_for_timeout(700)
        body = page.locator("body")
        body.wait_for(state="visible", timeout=60_000)
        body_text = " ".join(body.inner_text(timeout=30_000).split())
        controls: list[dict[str, str]] = []
        for role in ("button", "link"):
            locator = page.get_by_role(role)
            for index in range(locator.count()):
                item = locator.nth(index)
                if not item.is_visible():
                    continue
                text = " ".join(item.inner_text(timeout=5_000).split())
                aria_label = item.get_attribute("aria-label") or ""
                title = item.get_attribute("title") or ""
                label = " ".join(value for value in (text, aria_label, title) if value)
                if re.search(r"disk|partition|wipe|edit|create", label, re.IGNORECASE):
                    controls.append({"role": role, "label": label[:180]})
        partition_mentions = re.findall(r"[^.]{0,70}partition[^.]{0,100}", body_text, re.IGNORECASE)
        report("path-b-partition-ui-inventory", json.dumps({
            "navigation_role": role,
            "resolved_url": page.url,
            "partition_mentions": partition_mentions[:20],
            "disk_related_controls": controls[:40],
            "screen_text": body_text[:3500],
        }, sort_keys=True))
    except PlaywrightError as exc:
        report(
            "path-b-partition-ui-inventory",
            f"could not inspect the TrueNAS Disks screen: {type(exc).__name__}: {str(exc)[:500]}",
        )
    finally:
        page.goto(storage_url, wait_until="domcontentloaded", timeout=120_000)


def select_dataset_parent(page, base_url: str, profile: str) -> str:
    parent = selected_dataset_parent(profile)

    pool = parent.split("/", 1)[0]
    page.goto(f"{base_url}/ui/datasets/{pool}", wait_until="domcontentloaded", timeout=120_000)
    page.get_by_role("button", name="Add Dataset", exact=True).wait_for(state="visible", timeout=120_000)
    if parent != pool:
        page.get_by_role("tree").get_by_text(parent.rsplit("/", 1)[1], exact=True).first.click()
        selected_path = f"Path: {parent}"
        expect(page.locator("body")).to_contain_text(selected_path, timeout=60_000)
    body = page.locator("body").inner_text(timeout=30_000)
    normalized_body = " ".join(body.split())
    if f"Path: {parent}" not in normalized_body:
        matching_lines = [
            line.strip() for line in body.splitlines()
            if "boot-pool" in line or "user-data" in line
        ]
        raise RemoteError(
            f"datasets page at {page.url} did not select expected parent {parent}; "
            f"visible matching text={matching_lines[:30]!r}"
        )
    require("boot-pool/ROOT" not in body and ".system" not in body,
            "datasets UI exposes protected boot-pool system datasets")
    return parent


def create_dataset_in_ui(page, parent: str, name: str) -> str:
    page.get_by_role("button", name="Add Dataset", exact=True).click()
    dialog = page.get_by_role("dialog", name="Slide-in form")
    dialog.get_by_role("textbox", name="Name").fill(name)
    dialog.get_by_role("button", name="Save", exact=True).click()
    validation = page.get_by_role("dialog", name=re.compile(r"Validation Error", re.IGNORECASE))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if validation.count() == 1 and validation.is_visible():
            details = " ".join(validation.inner_text(timeout=5_000).split())
            raise RemoteError(
                f"TrueNAS UI rejected dataset creation for {parent}/{name}: {details[:1200]}"
            )
        if dialog.count() == 1 and not dialog.is_visible():
            return f"{parent}/{name}"
        page.wait_for_timeout(100)
    dialog.wait_for(state="hidden", timeout=120_000)
    return f"{parent}/{name}"


def open_dataset_in_ui(page, base_url: str, dataset: str) -> None:
    pool = dataset.split("/", 1)[0]
    page.goto(f"{base_url}/ui/datasets/{pool}", wait_until="domcontentloaded", timeout=120_000)
    page.get_by_role("button", name="Add Dataset", exact=True).wait_for(state="visible", timeout=120_000)
    tree = page.get_by_role("tree")
    components = dataset.split("/")[1:]
    selected = pool
    for index, name in enumerate(components):
        node = tree.get_by_text(name, exact=True).first
        try:
            node.wait_for(state="visible", timeout=60_000)
        except PlaywrightError as exc:
            snapshot = tree.aria_snapshot()
            raise RemoteError(
                f"dataset tree did not reveal {name!r} while opening {dataset}; "
                f"selected ancestor={selected!r}; tree={snapshot[:3200]!r}"
            ) from exc
        node.click()
        selected = f"{selected}/{name}"
        expect(page.locator("body")).to_contain_text(f"Path: {selected}", timeout=60_000)
        if index < len(components) - 1:
            item = node.locator("xpath=ancestor-or-self::*[@role='treeitem'][1]")
            if item.count() == 1 and item.get_attribute("aria-expanded") == "false":
                item.focus()
                page.keyboard.press("ArrowRight")
                expect(item).to_have_attribute("aria-expanded", "true", timeout=30_000)


def create_zvol_in_ui(
    page,
    base_url: str,
    parent_dataset: str,
    name: str,
    dataset_create_requests: list[dict],
) -> str:
    open_dataset_in_ui(page, base_url, parent_dataset)
    add_zvol = page.get_by_role("button", name=re.compile(r"add\s+zvol", re.IGNORECASE))
    if add_zvol.count() == 1 and add_zvol.is_visible():
        add_zvol.click()
    else:
        add_menu = page.get_by_role("button", name="Add Dataset", exact=True)
        add_menu.click()
        option = page.get_by_role("menuitem", name=re.compile(r"zvol", re.IGNORECASE))
        if option.count() == 0:
            option = page.get_by_role("option", name=re.compile(r"zvol", re.IGNORECASE))
        option.first.wait_for(state="visible", timeout=30_000)
        option.first.click()

    dialog = page.get_by_role("dialog", name="Slide-in form", exact=True)
    dialog.wait_for(state="visible", timeout=60_000)
    record_dialog(page, dialog, "zvol-ui-form-loading")
    progress = dialog.get_by_role("progressbar")
    if progress.count() == 1:
        progress.wait_for(state="hidden", timeout=60_000)
    record_dialog(page, dialog, "zvol-ui-form-ready", limit=6000)
    fill_labeled_textbox(dialog, ("Name", "Dataset Name"), name)
    size = dialog.get_by_role("textbox", name="Size", exact=True)
    if size.count() != 1:
        snapshot = dialog.aria_snapshot()
        raise RemoteError(f"Zvol form does not expose exactly one size control; ARIA form={snapshot[:2400]!r}")
    size.fill("128 MiB")
    size.press("Tab")
    page.wait_for_timeout(250)
    size_value_after_blur = size.input_value()
    require(
        bool(size_value_after_blur.strip()),
        f"Zvol size textbox lost the explicit 128 MiB value after blur: {size_value_after_blur!r}",
    )
    sparse = dialog.get_by_role("checkbox", name=re.compile(r"sparse", re.IGNORECASE))
    if sparse.count() == 1 and not sparse.is_checked():
        # The v27 size input can leave an Angular overlay active after blur;
        # the checkbox remains keyboard-operable while its pointer is covered.
        sparse.focus()
        sparse.press("Space")
        require(sparse.is_checked(), "keyboard interaction did not enable sparse zvol allocation")

    size_dom = size.evaluate(
        """el => ({value: el.value, type: el.type, invalid: el.getAttribute('aria-invalid'),
                    placeholder: el.getAttribute('placeholder'), dataTest: el.getAttribute('data-test')})"""
    )
    form_errors = [text.strip() for text in dialog.locator("mat-error").all_text_contents() if text.strip()]
    record_dialog(page, dialog, "zvol-ui-ready-form", limit=6000)
    report(
        "zvol-ui-size-control",
        f"after blur value={size_value_after_blur!r}; DOM={json.dumps(size_dom, sort_keys=True)}; "
        f"field errors={form_errors!r}",
    )
    save_button = dialog.get_by_role("button", name="Save", exact=True)
    if save_button.count() == 1 and not save_button.is_enabled():
        form_state = dialog.locator("form").evaluate(
            """form => ({
              submit: [...form.querySelectorAll('button[type="submit"]')].map(el => ({
                text: el.innerText.trim(), disabled: el.disabled,
              })),
              invalid: [...form.querySelectorAll('[aria-invalid="true"]')].map(el => ({
                tag: el.tagName, role: el.getAttribute('role'), label: el.getAttribute('aria-label'),
                name: el.getAttribute('name'), value: el.value ?? null,
                dataTest: el.getAttribute('data-test'),
                context: el.parentElement?.innerText?.trim()?.slice(0, 240),
              })),
              required: [...form.querySelectorAll('[aria-required="true"], [required]')].map(el => ({
                tag: el.tagName, role: el.getAttribute('role'), label: el.getAttribute('aria-label'),
                name: el.getAttribute('name'), value: el.value ?? null,
                dataTest: el.getAttribute('data-test'), disabled: el.disabled ?? false,
              })),
            })"""
        )
        overlays, form_errors = submit_result_details(page, dialog)
        raise RemoteError(
            f"Zvol UI Save is disabled before submit for {parent_dataset}/{name}; "
            f"size_dom={size_dom!r}; form_state={json.dumps(form_state, sort_keys=True)}; "
            f"overlays={overlays!r}; field_errors={form_errors!r}"
        )
    request_count_before_submit = len(dataset_create_requests)
    click_dialog_submit(page, dialog, ("Save", "Create", "Add"))
    dataset = f"{parent_dataset}/{name}"
    validation = page.get_by_role("dialog", name=re.compile(r"validation error", re.IGNORECASE))
    submitted = False
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if validation.count() and validation.first.is_visible():
            break
        if dialog.count() == 0 or not dialog.is_visible():
            submitted = True
            break
        page.wait_for_timeout(250)
    if validation.count() and validation.first.is_visible():
        validation_text = " ".join(validation.first.inner_text(timeout=10_000).split())
        overlays, form_errors = submit_result_details(page, dialog)
        zvol_request = dataset_create_properties(
            dataset_create_requests[request_count_before_submit:], dataset,
        )
        raise RemoteError(
            f"Zvol UI rejected {dataset}; validation={validation_text!r}; request={zvol_request!r}; "
            f"overlays={overlays!r}; field_errors={form_errors!r}; size_dom={size_dom!r}"
        )
    if not submitted:
        overlays, form_errors = submit_result_details(page, dialog)
        zvol_request = dataset_create_properties(
            dataset_create_requests[request_count_before_submit:], dataset,
        )
        raise RemoteError(
            f"Zvol UI did not close the form for {dataset}; request={zvol_request!r}; "
            f"overlays={overlays!r}; field_errors={form_errors!r}; size_dom={size_dom!r}"
        )

    request_deadline = time.monotonic() + 5
    properties = None
    while time.monotonic() < request_deadline:
        properties = dataset_create_properties(dataset_create_requests[request_count_before_submit:], dataset)
        if properties is not None:
            break
        page.wait_for_timeout(100)
    require(properties is not None, f"UI did not send pool.dataset.create for zvol {dataset}")
    require(properties.get("type") == "VOLUME",
            f"UI create request for {dataset} is not a VOLUME: {properties!r}")
    volsize = properties.get("volsize")
    require(
        type(volsize) is int and volsize == 128 * 1024 * 1024,
        f"UI create request for {dataset} has volsize={volsize!r}; expected exactly 134217728 bytes",
    )
    report(
        "zvol-ui-wire",
        f"{dataset} request type={properties.get('type')!r} volsize={volsize} bytes "
        f"after size textbox blur value={size_value_after_blur!r}",
    )
    return dataset


def assert_zvol_backing(target: Target, dataset: str, expected_size: int = 128 * 1024 * 1024) -> int:
    rows = flatten_datasets(target.query("pool.dataset.query", timeout=120))
    row = rows.get(dataset)
    require(row is not None, f"middleware dataset query omits zvol {dataset}")
    require(row.get("type") == "VOLUME", f"{dataset} is not a ZFS volume: {row.get('type')!r}")
    volsize = row.get("volsize", {}).get("parsed")
    require(volsize == expected_size, f"{dataset} has volsize {volsize!r}, expected {expected_size}")
    device = f"/dev/zvol/{dataset}"
    target.shell(f"test -b {shlex.quote(device)}", timeout=45)
    return int(zfs_property(target, dataset, "used"))


def allocate_zvol_probe(target: Target, dataset: str, *, marker: str) -> int:
    device = f"/dev/zvol/{dataset}"
    target.run([
        "dd", "if=/dev/urandom", f"of={device}", "bs=4096", "count=16", "conv=notrunc", "status=none",
    ], timeout=90)
    digest = target.shell(
        f"dd if={shlex.quote(device)} bs=4096 count=16 status=none | sha256sum",
        timeout=60,
    )
    require(bool(re.fullmatch(r"[0-9a-f]{64}  -", digest)),
            f"cannot verify data written to zvol {dataset}")
    used = int(zfs_property(target, dataset, "used"))
    require(used > 0, f"writing {marker} to {dataset} did not allocate ZFS space")
    return used


def ensure_select_value(page, dialog, label: str, value: str) -> None:
    """Keep a valid default, allowing async form defaults to finish loading."""
    selector = dialog.get_by_role("combobox", name=label, exact=True)
    selector.wait_for(state="visible", timeout=60_000)
    try:
        expect(selector).to_contain_text(value, timeout=8_000)
    except AssertionError:
        selector.click()
        option = page.get_by_role("option", name=value, exact=True)
        option.wait_for(state="visible", timeout=60_000)
        option.click()
    expect(selector).to_contain_text(value, timeout=60_000)


def select_single_available_option(page, dialog, label: str) -> str:
    """Select a required UI option only when the UI exposes one unambiguous choice."""
    selector = dialog.get_by_role("combobox", name=label, exact=True)
    require(selector.count() == 1 and selector.is_visible(),
            f"form does not expose one visible {label!r} selector")
    selector.click()
    options = page.get_by_role("option")
    choices = []
    for index in range(options.count()):
        option = options.nth(index)
        if not option.is_visible() or not option.is_enabled():
            continue
        name = " ".join(option.inner_text().split())
        if name:
            choices.append((name, option))
    names = [name for name, _ in choices]
    require(len(choices) == 1,
            f"{label!r} requires one UI choice for this disposable VM test; available={names!r}")
    name, option = choices[0]
    option.click()
    expect(selector).to_contain_text(name, timeout=60_000)
    report("vm-ui-network", f"selected the only available {label} option {name!r}")
    return name


def ensure_combobox_option(page, dialog, labels: tuple[str, ...], value: str) -> bool:
    for label in labels:
        selector = dialog.get_by_role("combobox", name=label, exact=True)
        if selector.count() == 1 and selector.is_visible():
            try:
                expect(selector).to_contain_text(value, timeout=3_000)
                return True
            except AssertionError:
                selector.click()
                option = page.get_by_role("option", name=re.compile(re.escape(value), re.IGNORECASE))
                option.first.wait_for(state="visible", timeout=60_000)
                option.first.click()
                expect(selector).to_contain_text(value, timeout=60_000)
                return True
    return False


def ensure_image_choice(page, dialog, image_name: str, image_version: str) -> None:
    image_pattern = re.compile(r"image", re.IGNORECASE)
    selectors = dialog.get_by_role("combobox", name=image_pattern)
    if selectors.count():
        selector = selectors.first
        selector.click()
        option = page.get_by_role("option", name=re.compile(re.escape(image_name), re.IGNORECASE))
        option.first.wait_for(state="visible", timeout=90_000)
        option.first.click()
        return
    textboxes = dialog.get_by_role("textbox", name=image_pattern)
    if textboxes.count():
        field = textboxes.first
        field_state = field.evaluate(
            "el => ({readOnly: el.readOnly, disabled: el.disabled, value: el.value, dataTest: el.getAttribute('data-test')})"
        )
        if field_state["readOnly"] or field_state["disabled"]:
            # The v27 Add Container form exposes Image as a read-only picker
            # field. Its supported UI path is Browse Catalog → Select Image →
            # Select on the matching image row; filling that textbox can never
            # make a real container submission.
            browse = dialog.get_by_role("button", name="Browse Catalog", exact=True)
            if browse.count() != 1 or not browse.is_enabled():
                snapshot = dialog.aria_snapshot()
                raise RemoteError(
                    "container image textbox is read-only and Browse Catalog is unavailable; "
                    f"field={field_state!r}; ARIA form={snapshot[:2400]!r}"
                )
            browse.click()
            picker_title = page.get_by_text("Select Image", exact=True)
            picker_title.wait_for(state="visible", timeout=90_000)

            dialogs = page.get_by_role("dialog")
            picker = dialogs.last if dialogs.count() >= 2 else dialogs.first
            if picker.count() == 0 or not picker.is_visible():
                picker = page.locator("body")
            record_dialog(page, picker, "container-image-picker", limit=6000)

            # The first Select Image render populates its catalog table
            # asynchronously. Wait for the catalog loading indicators before
            # deciding whether the expected row is absent.
            progressbars = picker.get_by_role("progressbar")
            for index in range(progressbars.count()):
                progressbar = progressbars.nth(index)
                if progressbar.is_visible():
                    progressbar.wait_for(state="hidden", timeout=90_000)

            search = picker.get_by_role("textbox", name=re.compile(r"search", re.IGNORECASE))
            if search.count() == 1 and search.is_visible() and not search.is_disabled():
                search.fill(image_name)
                page.wait_for_timeout(1_000)

            image_tokens = image_name.split(":")
            image_id = image_tokens[0]
            image_tag = image_tokens[1] if len(image_tokens) > 1 else ""
            rows = picker.get_by_role("row")
            candidates = []
            for index in range(rows.count()):
                row = rows.nth(index)
                row_text = " ".join(row.inner_text(timeout=5_000).split())
                folded = row_text.casefold()
                if (
                    image_id.casefold() in folded
                    and (not image_tag or image_tag.casefold() in folded)
                    and image_version.casefold() in folded
                ):
                    candidates.append(row)
            if len(candidates) != 1:
                snapshot = picker.aria_snapshot()
                raise RemoteError(
                    f"image picker did not show one row for {image_name!r} version {image_version!r}; "
                    f"matched_rows={len(candidates)}; ARIA picker={snapshot[:4200]!r}"
                )
            select = candidates[0].get_by_role("button", name="Select", exact=True)
            if select.count() != 1 or not select.is_enabled():
                snapshot = candidates[0].aria_snapshot()
                raise RemoteError(
                    f"image row for {image_name!r} version {image_version!r} "
                    "has no enabled Select action; "
                    f"ARIA row={snapshot[:2400]!r}"
                )
            select.click()
            page.wait_for_timeout(500)
            selected_value = field.input_value().strip()
            require(
                image_id.casefold() in selected_value.casefold()
                and (not image_tag or image_tag.casefold() in selected_value.casefold()),
                f"Browse Catalog selected {selected_value!r}, not {image_name!r}",
            )
            report(
                "container-image-ui",
                f"selected {selected_value!r} version {image_version!r} through Browse Catalog",
            )
            return

        field.fill(image_name)
        option = page.get_by_role("option", name=re.compile(re.escape(image_name), re.IGNORECASE))
        try:
            option.first.wait_for(state="visible", timeout=10_000)
            option.first.click()
        except PlaywrightError:
            field.press("Enter")
        return
    snapshot = dialog.aria_snapshot()
    raise RemoteError(f"container form has no image control; ARIA form={snapshot[:2400]!r}")


def fill_numeric_control(dialog, label: str, value: str) -> bool:
    for role in ("spinbutton", "textbox"):
        field = dialog.get_by_role(role, name=label, exact=True)
        if field.count() == 1 and field.is_visible():
            field.fill(value)
            return True
    return False


def create_container_in_ui(
    page, base_url: str, *, pool: str, name: str, image_name: str, image_version: str,
) -> None:
    page.goto(base_url + "/ui/containers", wait_until="domcontentloaded", timeout=120_000)
    page.get_by_role("button", name="Create New Container", exact=True).click()
    dialog = page.get_by_role("dialog", name="Slide-in form", exact=True)
    dialog.wait_for(state="visible", timeout=120_000)
    record_dialog(page, dialog, "container-ui-form")
    pool_selector = dialog.get_by_role("combobox", name="Pool", exact=True)
    pool_selector.wait_for(state="visible", timeout=60_000)
    require(
        ensure_combobox_option(page, dialog, ("Pool",), pool),
        f"container form has no Pool option for {pool!r}; ARIA form={dialog.aria_snapshot()[:2400]!r}",
    )
    expect(pool_selector).to_contain_text(pool, timeout=60_000)
    require(pool_selector.inner_text().strip() == pool,
            f"container UI selected {pool_selector.inner_text().strip()!r}; expected {pool!r}")
    fill_labeled_textbox(dialog, ("Name", "Container Name"), name)
    ensure_image_choice(page, dialog, image_name, image_version)

    for _ in range(5):
        errors = [text.strip() for text in dialog.locator("mat-error").all_text_contents() if text.strip()]
        require(not errors, f"container UI form has validation errors: {errors}")
        next_button = dialog.get_by_role("button", name="Next", exact=True)
        if next_button.count() != 1 or not next_button.is_enabled():
            break
        next_button.click()
        page.wait_for_timeout(750)
        record_dialog(page, dialog, "container-ui-step")
    click_dialog_submit(page, dialog, ("Create", "Save", "Create Container"))
    creating_dialog = page.get_by_role("dialog", name="Creating Container", exact=True)
    try:
        creating_dialog.wait_for(state="visible", timeout=5_000)
    except PlaywrightError:
        pass
    else:
        creating_dialog.wait_for(state="hidden", timeout=900_000)
    dialog.wait_for(state="hidden", timeout=900_000)


def wait_named_row(target: Target, method: str, name: str, *, timeout: int = 900) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = target.query(method, timeout=120)
        row = next((item for item in rows if item.get("name") == name), None)
        if row is not None:
            return row
        time.sleep(3)
    raise RemoteError(f"{method} did not return the new object named {name!r} within {timeout}s")


def select_existing_zvol_in_vm_wizard(page, dialog, zvol: str) -> None:
    """Select a previously UI-created zvol on the wizard's Disks step."""
    record_dialog(page, dialog, "vm-ui-disks")
    # The panel appears in TrueNAS' ARIA snapshot but is not consistently
    # discoverable as a named region by Chromium's role locator. Anchor on its
    # visible, uniquely named control inside the wizard dialog instead.
    existing_disk_choice = dialog.get_by_role(
        "radio", name="Use existing disk image", exact=True
    )
    try:
        expect(existing_disk_choice).to_have_count(1, timeout=30_000)
    except AssertionError:
        snapshot = dialog.aria_snapshot()
        raise RemoteError(
            "VM wizard does not expose one Use existing disk image radio; "
            f"radio_count={existing_disk_choice.count()} "
            f"form={snapshot[:2600]!r}"
        )
    if not existing_disk_choice.is_checked():
        label = dialog.get_by_text("Use existing disk image", exact=True)
        visible_labels = [
            label.nth(index)
            for index in range(label.count())
            if label.nth(index).is_visible()
        ]
        if len(visible_labels) != 1:
            snapshot = dialog.aria_snapshot()
            raise RemoteError(
                "VM wizard does not expose one visible Use existing disk image label; "
                f"label_count={label.count()} visible_count={len(visible_labels)} "
                f"form={snapshot[:2600]!r}"
            )
        visible_labels[0].click()
    expect(existing_disk_choice).to_be_checked(timeout=15_000)
    report("vm-ui-existing-disk", "selected and verified Use existing disk image")

    disk_type = dialog.get_by_role("combobox", name="Select Disk Type", exact=True)
    if disk_type.count() == 1 and disk_type.is_visible():
        try:
            expect(disk_type).to_contain_text("VirtIO", timeout=3_000)
        except AssertionError:
            disk_type.click()
            page.get_by_role("option", name="VirtIO", exact=True).click()
            expect(disk_type).to_contain_text("VirtIO", timeout=60_000)

    dataset = zvol.removeprefix("/dev/zvol/")
    zvol_selector = dialog.get_by_role("combobox", name="Select Existing Zvol", exact=True)
    if zvol_selector.count() != 1 or not zvol_selector.is_visible():
        snapshot = dialog.aria_snapshot()
        raise RemoteError(
            f"VM wizard does not expose one existing-zvol selector for {dataset}; "
            f"visible form={snapshot[:2600]!r}"
        )
    zvol_selector.click()
    option = page.get_by_role("option", name=dataset, exact=True)
    if option.count() != 1:
        available = page.get_by_role("option").all_text_contents()
        raise RemoteError(
            f"VM wizard does not offer existing zvol {dataset}; available={available[:30]!r}"
        )
    option.click()
    expect(zvol_selector).to_contain_text(dataset, timeout=60_000)
    report("vm-ui-disk", f"selected existing zvol {dataset} in the VM wizard")


def vm_region_next_button(dialog, expected_name: str):
    region = dialog.get_by_role("region", name=re.compile(expected_name, re.IGNORECASE))
    require(
        region.count() == 1,
        f"VM wizard expected one {expected_name!r} region, found {region.count()}: "
        f"{dialog.aria_snapshot()[:3000]!r}",
    )
    button = region.get_by_role("button", name="Next", exact=True)
    require(
        button.count() == 1 and button.is_visible() and button.is_enabled(),
        f"VM wizard {expected_name!r} region has no unique enabled Next button: "
        f"{region.aria_snapshot()[:2400]!r}",
    )
    return button


def active_vm_step_next_button(dialog):
    """Find Next only inside the one active wizard region, excluding edit summaries."""
    regions = dialog.get_by_role("region")
    candidates = []
    snapshots = []
    for index in range(regions.count()):
        region = regions.nth(index)
        snapshot = region.aria_snapshot()
        snapshots.append(snapshot[:900])
        match = re.match(r'\s*- region(?: "([^"]+)")?', snapshot)
        name = match.group(1) if match else ""
        if not name or name.casefold().startswith("pencil "):
            continue
        button = region.get_by_role("button", name="Next", exact=True)
        if button.count() == 1 and button.is_visible() and button.is_enabled():
            candidates.append((name, button))
    require(
        len(candidates) <= 1,
        f"VM wizard has multiple active regions with enabled Next buttons: "
        f"{[name for name, _ in candidates]!r}; regions={snapshots!r}",
    )
    return candidates[0] if candidates else (None, None)


def vm_form_create(page, base_url: str, *, name: str, password: str, zvol: str) -> None:
    page.goto(base_url + "/ui/vm", wait_until="domcontentloaded", timeout=120_000)
    page.get_by_role("button", name="Add VM", exact=True).click()
    dialog = page.get_by_role("dialog", name="Slide-in form", exact=True)
    dialog.wait_for(state="visible", timeout=120_000)
    for label, value in (
        ("Guest Operating System", "Linux"),
        ("System Clock", "Local"),
        ("Boot Method", "UEFI"),
        ("Bind", "0.0.0.0"),
    ):
        ensure_select_value(page, dialog, label, value)
    fill_labeled_textbox(dialog, ("Name",), name)
    fill_labeled_textbox(dialog, ("Password",), password)
    errors = [text.strip() for text in dialog.locator("mat-error").all_text_contents() if text.strip()]
    require(not errors, f"VM wizard first step has validation errors: {errors}")
    next_button = vm_region_next_button(dialog, "Operating System")
    expect(next_button).to_be_enabled(timeout=60_000)
    next_button.click()
    dialog.get_by_text("CPU Mode", exact=True).wait_for(state="visible", timeout=60_000)
    snapshot = record_dialog(page, dialog, "vm-ui-cpu-memory")
    require("Virtual CPUs" in snapshot and "Memory Size" in snapshot,
            "VM wizard does not expose vCPU and memory controls")
    for label, value in (("Virtual CPUs", "1"), ("Memory Size", "256")):
        if not fill_numeric_control(dialog, label, value):
            raise RemoteError(f"VM wizard does not expose an editable {label} control")

    next_button = vm_region_next_button(dialog, "CPU And Memory")
    next_button.click()
    select_existing_zvol_in_vm_wizard(page, dialog, zvol)

    for _ in range(5):
        step_name, next_button = active_vm_step_next_button(dialog)
        if next_button is None:
            attach_nic = dialog.get_by_role("combobox", name="Attach NIC", exact=True)
            if attach_nic.count() == 1 and attach_nic.is_visible():
                select_single_available_option(page, dialog, "Attach NIC")
                continue
            break
        report("vm-ui-step", f"advancing the active {step_name} wizard region")
        next_button.click()
        page.wait_for_timeout(750)
        record_dialog(page, dialog, "vm-ui-step")
    errors = [text.strip() for text in dialog.locator("mat-error").all_text_contents() if text.strip()]
    require(not errors, f"VM wizard has validation errors before submission: {errors}")
    click_dialog_submit(page, dialog, ("Create", "Save", "Add"))
    dialog.wait_for(state="hidden", timeout=120_000)


def main() -> int:
    target = Target.from_environment()
    profile = required_env("TRUENAS_STORAGE_PROFILE")
    if profile not in {"path-a", "path-b"}:
        raise RemoteError("TRUENAS_STORAGE_PROFILE must be path-a or path-b")
    resume_existing_root = os.environ.get("TRUENAS_TEST_RESUME_EXISTING_UI_ROOT", "")
    if resume_existing_root not in {"", "1"}:
        raise RemoteError("TRUENAS_TEST_RESUME_EXISTING_UI_ROOT must be unset or 1")
    if resume_existing_root == "1" and profile != "path-a":
        raise RemoteError("TRUENAS_TEST_RESUME_EXISTING_UI_ROOT=1 is only valid for Path A")
    try:
        resume_existing_ui_dataset = validate_resumed_ui_dataset(
            os.environ.get("TRUENAS_TEST_RESUME_EXISTING_UI_DATASET", "").strip()
        )
    except ValueError as exc:
        raise RemoteError(str(exc)) from exc
    if resume_existing_ui_dataset and (resume_existing_root != "1" or profile != "path-a"):
        raise RemoteError(
            "TRUENAS_TEST_RESUME_EXISTING_UI_DATASET requires Path A and "
            "TRUENAS_TEST_RESUME_EXISTING_UI_ROOT=1"
        )
    stale_container_cleanup = os.environ.get("TRUENAS_TEST_CLEANUP_STALE_UI_CONTAINER", "")
    if stale_container_cleanup and re.fullmatch(r"uiacceptance[0-9a-f]{8}", stale_container_cleanup) is None:
        raise RemoteError(
            "TRUENAS_TEST_CLEANUP_STALE_UI_CONTAINER must be an exact uiacceptance<8 lowercase hex> name"
        )
    if stale_container_cleanup and (profile != "path-a" or resume_existing_root != "1"):
        raise RemoteError(
            "TRUENAS_TEST_CLEANUP_STALE_UI_CONTAINER requires Path A and "
            "TRUENAS_TEST_RESUME_EXISTING_UI_ROOT=1"
        )
    expected_version = required_env("TRUENAS_EXPECTED_VERSION")
    if os.environ.get("TRUENAS_TEST_ALLOW_MUTATIONS") != "1":
        raise RemoteError("set TRUENAS_TEST_ALLOW_MUTATIONS=1 only for a disposable TrueNAS guest")

    base_url = required_env("TRUENAS_UI_URL").rstrip("/")
    ws_url = required_env("PWMCP_WS_URL")
    expected_playwright = required_env("PWMCP_PLAYWRIGHT_VERSION")
    secret_path = Path(required_env("TRUENAS_UI_SECRET_FILE"))
    if not secret_path.is_file():
        raise RemoteError(f"TrueNAS UI credential file does not exist: {secret_path}")
    mode = stat.S_IMODE(secret_path.stat().st_mode)
    if mode & 0o077:
        raise RemoteError("TrueNAS UI credential file must not be accessible by group or others")
    try:
        secrets = tomllib.loads(secret_path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RemoteError(f"cannot read UI credential TOML: {type(exc).__name__}") from None
    admin = secrets.get("truenas_admin", {})
    username = admin.get("username")
    password = admin.get("password")
    if not isinstance(username, str) or not username or not isinstance(password, str) or not password:
        raise RemoteError("TrueNAS UI credential TOML is missing truenas_admin username/password")

    playwright_version = importlib.metadata.version("playwright")
    require(playwright_version == expected_playwright,
            f"PWMCP Playwright client version {expected_playwright} required, found {playwright_version}")
    info = target.call("system.info")
    version = str(info.get("version", ""))
    require(version == expected_version, f"expected TrueNAS {expected_version}, got {version!r}")

    suffix = time.strftime("%Y%m%d%H%M%S", time.gmtime()) + uuid.uuid4().hex[:6]
    dataset_parent = selected_dataset_parent(profile)
    pool = selected_pool(profile)
    dataset_name = f"ui-acceptance-{suffix}"
    created_dataset = f"{dataset_parent}/{dataset_name}"
    mountpoint = f"/mnt/{created_dataset}"
    vm_name = f"uiacceptancevm{suffix[-8:]}"
    container_name = f"uiacceptance{suffix[-8:]}"
    vm_zvol = ""
    cache_zvol = ""
    container_dataset = ""
    dataset_created = False
    vm_id: int | None = None
    container_id: int | None = None
    temporary_pool_name: str | None = None
    cache_attached = False
    cleanup_errors: list[str] = []
    browser = None
    page = None
    ui_stage = "before-browser"
    image_name = "alpine:3.24:amd64:default"
    image_rows = target.call("container.image.query_registry", timeout=120)
    image_row = next((row for row in image_rows if row.get("name") == image_name), None)
    require(image_row is not None and image_row.get("versions"),
            f"container image registry does not offer the pinned UI test image {image_name}")
    image_version = sorted(row["version"] for row in image_row["versions"])[-1]
    report(
        "container-image-registry",
        f"target UI image entry: {json.dumps({'name': image_row.get('name'), 'version': image_version}, sort_keys=True)}",
    )
    if stale_container_cleanup:
        cleanup_stale_ui_container(
            target, profile, stale_container_cleanup, image_name,
        )
    existing_ui_root = inventory_before_ui_mutation(
        target, profile, image_name, resume_existing_root=resume_existing_root == "1",
        resume_existing_ui_dataset=resume_existing_ui_dataset,
    )

    try:
        with sync_playwright() as playwright:
            ui_stage = "connect-pwmcp"
            report("browser", "connecting to PWMCP Playwright endpoint")
            browser = connect_pwmcp(playwright, ws_url)
            report("browser", "connected to PWMCP")
            context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            dataset_create_requests = observe_rpc_method(page, "pool.dataset.create")
            pool_create_requests = observe_rpc_method(page, "pool.create", log_response=True)
            observe_rpc_method(page, "pool.dataset.query")
            observe_rpc_method(page, "pool.dataset.recommended_zvol_blocksize", log_response=True)
            observe_rpc_method(page, "pool.dataset.compression_choices", log_response=True)
            observe_rpc_method(page, "disk.query", log_response=True)
            observe_rpc_method(page, "container.image.query_registry", log_response=True)
            container_create_requests = observe_rpc_method(page, "container.create")
            page.set_default_timeout(30_000)
            ui_stage = "open-signin"
            report("navigation", f"opening TrueNAS sign-in at {base_url}")
            page.goto(base_url + "/", wait_until="domcontentloaded", timeout=120_000)
            report("navigation", f"sign-in page loaded at {page.url}")
            ui_stage = "signin-username"
            username_field = page.get_by_role("textbox", name="Username")
            report("login", "waiting for the username field to become enabled")
            username_field.wait_for(state="visible", timeout=120_000)
            expect(username_field).to_be_enabled(timeout=120_000)
            username_field.fill(username)
            ui_stage = "signin-password"
            password_field = page.get_by_role("textbox", name="Password")
            report("login", "username entered; waiting for the password field to become enabled")
            expect(password_field).to_be_enabled(timeout=120_000)
            password_field.fill(password)
            ui_stage = "signin-submit"
            report("login", "credentials entered; submitting login")
            page.get_by_role("button", name="Log In", exact=True).click()
            ui_stage = "signin-complete"
            page.wait_for_url(re.compile(r"/ui/(?!signin)"), timeout=120_000)
            report("login", f"login redirected to {page.url}")
            ui_stage = "sidebar-ready"
            page.wait_for_load_state("domcontentloaded", timeout=30_000)
            page.wait_for_timeout(5_000)
            dashboard_text = page.locator("body").inner_text(timeout=30_000)
            require("Datasets" in dashboard_text,
                    "authenticated dashboard does not expose the Datasets navigation text")
            report("login", f"PWMCP browser authenticated to TrueNAS {version}")
            inspect_session_timeout_ui(page, username)

            for label, route in (
                ("Datasets", "/ui/datasets"),
                ("Containers", "/ui/containers"),
                ("Virtual Machines", "/ui/vm"),
                ("Shares", "/ui/sharing"),
            ):
                ui_stage = f"page-{label.lower().replace(' ', '-')}"
                report("page", f"opening {label} route")
                page.goto(base_url + route, wait_until="domcontentloaded", timeout=120_000)
                page.get_by_text(label, exact=True).first.wait_for(state="visible", timeout=60_000)
                report("page", f"{label} route loaded")

            ui_stage = "profile-storage-setup"
            if profile == "path-a":
                if existing_ui_root:
                    report(
                        "path-a-root-ui",
                        "resuming from the verified UI-created root and its pinned image cache",
                    )
                else:
                    create_path_a_root_in_ui(page, base_url, target, dataset_create_requests)
            else:
                inspect_path_b_partition_ui(page, base_url)
                pool = create_path_b_pool_in_ui(page, base_url, target, pool_create_requests)

            ui_stage = "dataset-parent"
            parent = select_dataset_parent(page, base_url, profile)
            ui_stage = "dataset-create"
            created_dataset = create_dataset_in_ui(page, parent, dataset_name)
            dataset_created = True
            require(wait_dataset(target, created_dataset, present=True),
                    f"dataset created in UI is missing from middleware query: {created_dataset}")
            dataset_used_before = assert_filesystem_backing(target, created_dataset, mountpoint)
            probe_file = f"{mountpoint}/ui-disk-probe.bin"
            target.run([
                "dd", "if=/dev/urandom", f"of={probe_file}", "bs=4096", "count=16", "status=none",
            ], timeout=90)
            probe_bytes = int(target.run(["wc", "-c", probe_file]).split()[0])
            dataset_used_after = assert_filesystem_backing(target, created_dataset, mountpoint)
            require(probe_bytes == 65_536 and dataset_used_after > dataset_used_before,
                    f"dataset write did not increase ZFS use: before={dataset_used_before}, "
                    f"after={dataset_used_after}, bytes={probe_bytes}")
            report(
                "dataset-ui",
                f"created {created_dataset}; findmnt source matches; 65536-byte probe increased "
                f"ZFS used from {dataset_used_before} to {dataset_used_after}",
            )

            ui_stage = "vm-zvol-create"
            vm_zvol = create_zvol_in_ui(
                page, base_url, created_dataset, "vm-disk", dataset_create_requests,
            )
            require(wait_dataset(target, vm_zvol, present=True), f"UI-created VM zvol is missing: {vm_zvol}")
            vm_zvol_used = assert_zvol_backing(target, vm_zvol)
            vm_zvol_used = allocate_zvol_probe(target, vm_zvol, marker="VM disk probe")
            report("zvol-ui", f"created {vm_zvol}; 65536 bytes written; ZFS used={vm_zvol_used}")

            ui_stage = "cache-zvol-create"
            cache_zvol = create_zvol_in_ui(
                page, base_url, created_dataset, "cache-device", dataset_create_requests,
            )
            require(wait_dataset(target, cache_zvol, present=True),
                    f"UI-created cache-device zvol is missing: {cache_zvol}")
            cache_zvol_used = assert_zvol_backing(target, cache_zvol)
            require(cache_zvol_used >= 0, f"invalid ZFS used value for {cache_zvol}: {cache_zvol_used}")

            ui_stage = "cache-vdev-use"
            temporary_pool_name = f"patchcache{suffix[-10:]}"
            temporary_pool_file = f"{mountpoint}/cache-target.img"
            target.run(["truncate", "-s", "256M", temporary_pool_file], timeout=90)
            target.shell(
                "zpool create -f -o cachefile=none -o altroot=/mnt -O mountpoint=none "
                f"{shlex.quote(temporary_pool_name)} {shlex.quote(temporary_pool_file)}",
                timeout=120,
            )
            cache_device = f"/dev/zvol/{cache_zvol}"
            target.shell(
                f"zpool add {shlex.quote(temporary_pool_name)} cache {shlex.quote(cache_device)}",
                timeout=120,
            )
            cache_attached = True
            cache_status = target.shell(f"zpool status -P {shlex.quote(temporary_pool_name)}")
            require(cache_device in cache_status and "cache" in cache_status.lower(),
                    "temporary pool status does not show the UI-created zvol as a cache vdev")
            cache_pool_health = target.shell(
                f"zpool list -H -o health {shlex.quote(temporary_pool_name)}"
            )
            require(cache_pool_health == "ONLINE",
                    f"temporary cache target pool health is {cache_pool_health!r}")
            target.shell(
                f"zpool remove {shlex.quote(temporary_pool_name)} {shlex.quote(cache_device)}",
                timeout=120,
            )
            cache_attached = False
            require(cache_device not in target.shell(f"zpool status -P {shlex.quote(temporary_pool_name)}"),
                    "cache vdev removal did not remove the UI-created zvol")
            cache_zvol_used = int(zfs_property(target, cache_zvol, "used"))
            require(cache_zvol_used > 0, f"cache vdev writes did not allocate space on {cache_zvol}")
            target.shell(f"zpool export {shlex.quote(temporary_pool_name)}", timeout=120)
            remaining_pools = target.shell("zpool list -H -o name").splitlines()
            require(temporary_pool_name not in remaining_pools,
                    f"temporary cache pool {temporary_pool_name} remains imported")
            report(
                "cache-device",
                f"UI-created {cache_zvol} stored ZFS data and served as a cache vdev for a "
                f"temporary file-backed pool; add/status/remove/export passed (ZFS used={cache_zvol_used})",
            )
            temporary_pool_name = None

            ui_stage = "container-create"
            report("container-ui", f"submitting a container on pool {pool} using {image_name}")
            create_container_in_ui(
                page, base_url, pool=pool, name=container_name, image_name=image_name,
                image_version=image_version,
            )
            submitted_container = rpc_object_for_name(
                container_create_requests, "container.create", container_name,
            )
            require(submitted_container is not None,
                    f"UI did not submit container.create for {container_name}")
            submitted_image = submitted_container.get("image")
            require(submitted_container.get("pool") == pool,
                    f"UI container.create submitted pool {submitted_container.get('pool')!r}, expected {pool!r}")
            require(
                isinstance(submitted_image, dict)
                and submitted_image.get("name") == image_name
                and submitted_image.get("version") == image_version,
                f"UI container.create submitted unexpected image {submitted_image!r}; "
                f"expected {image_name!r} version {image_version!r}",
            )
            report(
                "container-ui-wire",
                f"UI request submitted pool={pool}, image={image_name}, version={image_version}",
            )
            container_row = wait_named_row(target, "container.query", container_name)
            container_id = int(container_row["id"])
            container_root = container_storage_root(profile)
            container_dataset = find_container_storage_dataset(target, profile, container_name)
            container_zfs_row = target.run([
                "zfs", "list", "-H", "-p", "-o", "name,used,refer",
                "-t", "filesystem,volume", container_dataset,
            ], timeout=90).split("\t")
            require(
                len(container_zfs_row) == 3 and container_zfs_row[0] == container_dataset,
                f"direct ZFS inventory did not return one row for {container_dataset}: {container_zfs_row!r}",
            )
            container_used, container_referenced = map(int, container_zfs_row[1:])
            require(container_referenced > 0,
                    f"UI-created container dataset {container_dataset} references no ZFS data")
            image_dataset = f"{container_root}/images/{image_name}:{image_version}"
            image_zfs_row = target.run([
                "zfs", "list", "-H", "-p", "-o", "name,used", "-t", "filesystem,volume", image_dataset,
            ], timeout=90).split("\t")
            require(
                len(image_zfs_row) == 2 and image_zfs_row[0] == image_dataset and int(image_zfs_row[1]) > 0,
                f"UI-selected image has no allocated backing dataset on {pool}: {image_zfs_row!r}",
            )
            visible_datasets = flatten_datasets(target.query("pool.dataset.query", timeout=120))
            hidden_internal = [
                item for item in visible_datasets
                if item == container_root or item.startswith(container_root + "/")
            ]
            require(not hidden_internal,
                    f"dataset UI/API query exposes container-managed datasets: {hidden_internal[:8]!r}")
            report(
                "container-ui",
                f"created container {container_name} on {pool}; direct ZFS dataset {container_dataset} "
                f"used={container_used} bytes, referenced={container_referenced} bytes; image dataset "
                f"{image_dataset} uses {int(image_zfs_row[1])} bytes; selected pool and storage root match",
            )

            ui_stage = "vm-create"
            report("vm-ui", f"submitting VM wizard {vm_name}")
            vm_form_create(
                page, base_url, name=vm_name, password=uuid.uuid4().hex[:8],
                zvol=f"/dev/zvol/{vm_zvol}",
            )
            vm_row = wait_named_row(target, "vm.query", vm_name, timeout=120)
            vm_id = int(vm_row["id"])
            vm_devices = target.query("vm.device.query", [["vm", "=", vm_id]], timeout=120)
            attached_paths = [
                (device.get("attributes") or {}).get("path", device.get("path"))
                for device in vm_devices
            ]
            require(f"/dev/zvol/{vm_zvol}" in attached_paths,
                    f"VM created through the UI is not attached to its user-data zvol: {attached_paths!r}")
            vm_zvol_used = int(zfs_property(target, vm_zvol, "used"))
            require(vm_zvol_used > 0, f"VM zvol {vm_zvol} has no allocated ZFS storage")
            report(
                "vm-ui",
                f"UI wizard created VM {vm_name} with existing zvol /dev/zvol/{vm_zvol}; "
                f"zvol is on {pool} and uses {vm_zvol_used} bytes",
            )

            ui_stage = "shares-page"
            report("shares-ui", "opening SMB and NFS sections")
            page.goto(base_url + "/ui/sharing", wait_until="domcontentloaded", timeout=120_000)
            page.get_by_role("heading", name=re.compile(r"Windows \(SMB\) Shares")).wait_for(
                state="visible", timeout=60_000
            )
            page.get_by_role("heading", name=re.compile(r"UNIX \(NFS\) Shares")).wait_for(
                state="visible", timeout=60_000
            )
            share_text = page.locator("body").inner_text(timeout=30_000)
            require("Windows (SMB) Shares" in share_text and "UNIX (NFS) Shares" in share_text,
                    "sharing UI does not expose both SMB and NFS sections")
            require(page.get_by_role("button", name=re.compile(r"\bAdd\b")).count() >= 2,
                    "sharing UI does not expose the SMB and NFS Add actions")
            report("shares-ui", "SMB and NFS share pages/actions are available")
            context.close()

    except PlaywrightError as exc:
        detail = str(exc).strip().replace("\n", " ")[-1600:]
        raise RemoteError(
            f"PWMCP Playwright UI interaction failed during {ui_stage}: "
            f"{type(exc).__name__}: {detail}"
        ) from None
    finally:
        if browser is not None:
            try:
                browser.close()
            except PlaywrightError:
                pass
        if container_id is not None:
            try:
                current = target.query("container.query", [["id", "=", container_id]], timeout=60)
                if current and (current[0].get("status") or {}).get("state") == "RUNNING":
                    stop_job = target.call("container.stop", container_id, {
                        "force": False,
                        "force_after_timeout": True,
                    }, timeout=120)
                    if isinstance(stop_job, int):
                        target.wait_job(stop_job, timeout=600, label="UI container stop")
                delete_job = target.call("container.delete", container_id, {
                    "force": False,
                    "recursive": False,
                }, timeout=120)
                if isinstance(delete_job, int):
                    target.wait_job(delete_job, timeout=600, label="UI container delete")
                if target.query("container.query", [["id", "=", container_id]], timeout=60):
                    cleanup_errors.append(f"UI container {container_id} remains after delete")
                if container_dataset and target.run(
                    ["zfs", "list", "-H", "-o", "name", container_dataset], check=False,
                ) == container_dataset:
                    cleanup_errors.append(f"UI container dataset {container_dataset} remains after delete")
            except Exception as exc:
                cleanup_errors.append(f"UI container {container_id}: {exc}")
        if vm_id is not None:
            try:
                current = target.query("vm.query", [["id", "=", vm_id]], timeout=60)
                if current and (current[0].get("status") or {}).get("state") == "RUNNING":
                    stop_job = target.call("vm.stop", vm_id, {"force": False}, timeout=120)
                    if isinstance(stop_job, int):
                        target.wait_job(stop_job, timeout=300, label="UI VM stop")
                target.call("vm.delete", vm_id, timeout=120)
                if target.query("vm.query", [["id", "=", vm_id]], timeout=60):
                    cleanup_errors.append(f"UI VM {vm_id} remains after delete")
            except Exception as exc:
                cleanup_errors.append(f"UI VM {vm_id}: {exc}")
        if temporary_pool_name is not None:
            try:
                pools = target.shell("zpool list -H -o name").splitlines()
                if temporary_pool_name in pools:
                    if cache_attached and cache_zvol:
                        target.shell(
                            f"zpool remove {shlex.quote(temporary_pool_name)} "
                            f"{shlex.quote('/dev/zvol/' + cache_zvol)}",
                            timeout=120,
                        )
                    target.shell(f"zpool export {shlex.quote(temporary_pool_name)}", timeout=120)
                if temporary_pool_name in target.shell("zpool list -H -o name").splitlines():
                    cleanup_errors.append(f"temporary cache pool {temporary_pool_name} remains imported")
            except Exception as exc:
                cleanup_errors.append(f"temporary cache pool {temporary_pool_name}: {exc}")
        if dataset_created:
            try:
                existing = flatten_datasets(target.query("pool.dataset.query", timeout=120))
                if created_dataset in existing:
                    target.call("pool.dataset.delete", created_dataset, {"recursive": True}, timeout=120)
                remaining = flatten_datasets(target.query("pool.dataset.query", timeout=120))
                leftovers = [name for name in remaining if name == created_dataset or name.startswith(created_dataset + "/")]
                if leftovers:
                    cleanup_errors.append(f"UI test dataset remains after cleanup: {leftovers[:8]!r}")
                direct = target.run(
                    ["zfs", "list", "-H", "-t", "filesystem,volume", "-o", "name", "-r", created_dataset],
                    check=False,
                )
                if direct and created_dataset in direct.splitlines():
                    cleanup_errors.append(f"UI test ZFS tree remains after cleanup: {created_dataset}")
                report("cleanup", f"removed UI test tree {created_dataset}")
            except Exception as exc:
                cleanup_errors.append(f"UI test dataset cleanup failed: {exc}")
        if cleanup_errors:
            report("cleanup-error", "; ".join(cleanup_errors))

    if cleanup_errors:
        raise RemoteError("cleanup failed: " + "; ".join(cleanup_errors))
    report("result", f"PASS TrueNAS {version} {profile} UI")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RemoteError, KeyError, TypeError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
