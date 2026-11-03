#!/usr/bin/env python3
"""Exercise boot-pool zvols as cache, log, and special vdevs on disposable pools."""

from __future__ import annotations

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

from live_ui import (
    assert_zvol_backing,
    connect_pwmcp,
    create_dataset_in_ui,
    create_path_a_root_in_ui,
    create_zvol_in_ui,
    dataset_create_properties,
    flatten_datasets,
    inventory_before_ui_mutation,
    observe_rpc_method,
    report,
    require,
    selected_dataset_parent,
    select_dataset_parent,
    wait_dataset,
    zfs_property,
)
from remote import RemoteError, Target
from vdev_metrics import parse_vdev_write_counters


EXTRA_DISK_SERIALS = (
    "TNEXTRA001",
    "TNEXTRA002",
    "TNEXTRA003",
    "TNEXTRA004",
)
ZVOL_BYTES = 128 * 1024 * 1024


def ui_secrets() -> tuple[str, str]:
    secret_path = Path(os.environ.get("TRUENAS_UI_SECRET_FILE", ""))
    if not secret_path.is_file():
        raise RemoteError("TRUENAS_UI_SECRET_FILE must point to the private TrueNAS UI credential file")
    if stat.S_IMODE(secret_path.stat().st_mode) & 0o077:
        raise RemoteError("TrueNAS UI credential file must not be accessible by group or others")
    try:
        secrets = tomllib.loads(secret_path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        raise RemoteError("cannot read the TrueNAS UI credential file") from None
    admin = secrets.get("truenas_admin", {})
    username, password = admin.get("username"), admin.get("password")
    if not isinstance(username, str) or not username or not isinstance(password, str) or not password:
        raise RemoteError("TrueNAS UI credential file is missing truenas_admin username/password")
    return username, password


def ui_login(page, base_url: str, username: str, password: str) -> None:
    page.set_default_timeout(30_000)
    page.goto(base_url + "/", wait_until="domcontentloaded", timeout=120_000)
    user_field = page.get_by_role("textbox", name="Username")
    user_field.wait_for(state="visible", timeout=120_000)
    expect(user_field).to_be_enabled(timeout=120_000)
    user_field.fill(username)
    password_field = page.get_by_role("textbox", name="Password")
    expect(password_field).to_be_enabled(timeout=120_000)
    password_field.fill(password)
    page.get_by_role("button", name="Log In", exact=True).click()
    page.wait_for_url(re.compile(r"/ui/(?!signin)"), timeout=120_000)
    page.wait_for_load_state("domcontentloaded", timeout=30_000)
    page.get_by_text("Datasets", exact=True).first.wait_for(state="visible", timeout=60_000)


def free_extra_disks(target: Target) -> dict[str, dict]:
    rows = target.query("disk.query", timeout=120)
    matches: dict[str, list[dict]] = {serial: [] for serial in EXTRA_DISK_SERIALS}
    for row in rows:
        if isinstance(row, dict) and row.get("serial") in matches:
            matches[row["serial"]].append(row)
    missing = [serial for serial, found in matches.items() if len(found) != 1]
    require(not missing, f"disk.query must return one row for each managed sparse test disk; missing/duplicate={missing!r}")

    resolved: dict[str, dict] = {}
    for serial, found in matches.items():
        row = found[0]
        require(row.get("pool") in (None, ""),
                f"managed sparse test disk {serial} is already assigned to pool {row.get('pool')!r}")
        require(isinstance(row.get("name"), str) and row["name"],
                f"disk.query did not return an API disk name for {serial}: {row!r}")
        # disk.query reports devname as a kernel device name (for example,
        # "vda"), not as an absolute /dev path. Pool creation uses the
        # middleware disk name; the pool topology/status checks below verify
        # the resolved device path after creation.
        require(isinstance(row.get("devname"), str) and bool(row["devname"]),
                f"disk.query did not return a kernel device name for {serial}: {row!r}")
        size = row.get("size")
        minimum = 3 * 1024**3 if serial == "TNEXTRA001" else 1024**3
        require(type(size) is int and size >= minimum,
                f"managed sparse test disk {serial} has size {size!r}; expected at least {minimum}")
        resolved[serial] = row

    report("disk-query", json.dumps([
        {key: resolved[serial].get(key) for key in ("serial", "name", "devname", "size", "pool")}
        for serial in EXTRA_DISK_SERIALS
    ], sort_keys=True))
    return resolved


def pool_create_control(container):
    name = re.compile(r"^create pool$", re.IGNORECASE)
    for role in ("button", "link"):
        candidate = container.get_by_role(role, name=name)
        if candidate.count() == 1 and candidate.is_visible():
            return candidate, role
    return None, None


def inspect_aux_vdev_selectors(
    page,
    base_url: str,
    zvol_names: dict[str, str],
    disk_responses: list[dict],
) -> dict[str, object]:
    """Walk the pool wizard through auxiliary roles without submitting a pool."""
    page.goto(base_url + "/ui/storage", wait_until="domcontentloaded", timeout=120_000)
    create, create_role = pool_create_control(page)
    if create is None:
        for role in ("button", "link"):
            candidate = page.get_by_role(
                role, name=re.compile(r"^create pool$", re.IGNORECASE),
            )
            try:
                candidate.wait_for(state="visible", timeout=45_000)
            except PlaywrightError:
                continue
            if candidate.count() == 1:
                create, create_role = candidate, role
                break
    require(create is not None, "Storage Dashboard did not expose a Create Pool control")
    report("ui-pool-create-control", f"wizard opened using role={create_role}")
    create.click()
    page.get_by_role("heading", name=re.compile(r"^pool creation wizard$", re.IGNORECASE)).wait_for(
        state="visible", timeout=60_000,
    )
    wizard = page.get_by_role("main")
    wizard.wait_for(state="visible", timeout=30_000)
    pool_name = wizard.get_by_role("textbox", name=re.compile(r"^name$", re.IGNORECASE))
    require(pool_name.count() == 1 and pool_name.is_visible(),
            "Pool Creation Wizard did not expose its Name field")
    pool_name.fill("vdevprobe" + uuid.uuid4().hex[:10])
    next_button = wizard.get_by_role("button", name=re.compile(r"^next$", re.IGNORECASE))
    require(next_button.count() == 1 and next_button.is_enabled(),
            f"Pool Creation Wizard cannot advance from General Info: {wizard.inner_text()!r}")
    next_button.click()
    page.wait_for_timeout(500)

    rows: list[dict] = []

    def collect_rows(value) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("name"), str) and isinstance(value.get("devname"), str):
                rows.append({
                    key: value.get(key)
                    for key in ("name", "devname", "serial", "pool", "size")
                    if key in value
                })
            for child in value.values():
                collect_rows(child)
        elif isinstance(value, list):
            for child in value:
                collect_rows(child)

    disk_deadline = time.monotonic() + 30
    while not disk_responses and time.monotonic() < disk_deadline:
        page.wait_for_timeout(100)
    for response in disk_responses:
        collect_rows(response)
    unique_rows = {
        (row.get("name"), row.get("devname")): row
        for row in rows
    }
    require(unique_rows, "PWMCP pool wizard did not return any browser disk.query rows")
    data_disk_rows = [row for row in unique_rows.values() if row.get("serial") == "TNEXTRA004"]
    require(len(data_disk_rows) == 1,
            f"pool wizard disk.query must return one TNEXTRA004 row: {data_disk_rows!r}")
    data_disk = data_disk_rows[0]
    require(data_disk.get("pool") in (None, ""),
            f"UI topology probe data disk is already assigned: {data_disk!r}")
    disk_names = [
        value for value in (data_disk.get("name"), data_disk.get("devname"), data_disk.get("serial"))
        if isinstance(value, str) and value
    ]

    snapshots: list[dict[str, str]] = []
    role_steps_seen: set[str] = set()
    visible_zvols: set[str] = set()
    data_disk_selected = False
    data_disk_attempts: list[str] = []
    selection_diagnostics: list[dict[str, object]] = []
    wizard_steps = ("general info", "data", "log", "spare", "cache", "special", "dedup", "review")

    def active_step(snapshot: str) -> str | None:
        match = re.search(r'- button "([^"\n]+)" \[disabled\] \[expanded\]', snapshot)
        if match is None:
            return None
        label = match.group(1).lower().replace("pencil ", "")
        return next((step for step in wizard_steps if step in label), None)

    for _ in range(8):
        text = " ".join(wizard.inner_text(timeout=30_000).split())
        snapshot = wizard.aria_snapshot()
        step = active_step(snapshot)
        relevant_lines = [
            line.strip()
            for line in snapshot.splitlines()
            if re.search(
                r"Layout|Manual Disk|Automated Disk|Available Disks|Unassigned Disks|"
                r"No disks|TNEXTRA|/dev/vd[a-z]|Cache|Log|Special|Metadata|Next|Create Pool",
                line,
                re.IGNORECASE,
            )
        ]
        step_snapshot = {
            "active_step": step or "unknown",
            "text": text[:1600],
            "aria_relevant": " | ".join(relevant_lines[:50])[:2400],
        }
        snapshots.append(step_snapshot)
        if step in {"cache", "log", "special"}:
            role_steps_seen.add(step)
        for role_name, dataset_name in zvol_names.items():
            leaf = dataset_name.rsplit("/", 1)[-1]
            if dataset_name in text or leaf in text or f"/dev/zvol/{dataset_name}" in text:
                visible_zvols.add(role_name)

        if step == "data" and not data_disk_selected:
            layout = wizard.get_by_role("combobox", name=re.compile(r"^layout$", re.IGNORECASE))
            layout_count = layout.count()
            layout_selected = False
            layout_before = None
            layout_after = None
            if layout_count == 1 and layout.is_visible():
                layout_before = layout.evaluate(
                    """el => ({
                        tag: el.tagName,
                        role: el.getAttribute('role'),
                        value: el.value ?? null,
                        ariaValueText: el.getAttribute('aria-valuetext'),
                        selected: el.selectedOptions?.[0]?.textContent?.trim() ?? null,
                        displayed: el.querySelector('.mat-mdc-select-value-text, .mat-select-value-text')
                            ?.textContent?.trim() ?? null,
                        outerHTML: el.outerHTML.slice(0, 1800),
                    })"""
                )
                if layout_before.get("tag") == "SELECT":
                    layout.select_option(label="Stripe")
                else:
                    layout.click()
                    stripe = page.get_by_role(
                        "option", name=re.compile(r"^stripe$", re.IGNORECASE),
                    )
                    if stripe.count() == 1 and stripe.is_visible() and stripe.is_enabled():
                        stripe.click()
                page.wait_for_timeout(400)
                layout_after = layout.evaluate(
                    """el => ({
                        tag: el.tagName,
                        role: el.getAttribute('role'),
                        value: el.value ?? null,
                        ariaValueText: el.getAttribute('aria-valuetext'),
                        selected: el.selectedOptions?.[0]?.textContent?.trim() ?? null,
                        displayed: el.querySelector('.mat-mdc-select-value-text, .mat-select-value-text')
                            ?.textContent?.trim() ?? null,
                        outerHTML: el.outerHTML.slice(0, 1800),
                    })"""
                )
                selection = " ".join(
                    str(layout_after.get(key) or "")
                    for key in ("value", "ariaValueText", "selected", "displayed")
                )
                layout_selected = "stripe" in selection.lower()
            selection_diagnostics.append({
                "control": "data-layout-stripe",
                "combobox_count": layout_count,
                "selected": layout_selected,
                "before": layout_before,
                "after": layout_after,
            })
            manual = wizard.get_by_role(
                "button", name=re.compile(r"manual disk selection", re.IGNORECASE),
            )
            manual_count = manual.count()
            selection_diagnostics.append({
                "control": "manual-disk-selection",
                "count": manual_count,
                "visible": manual_count == 1 and manual.is_visible(),
                "enabled": manual_count == 1 and manual.is_enabled(),
                "disabled_attribute": (
                    manual.get_attribute("disabled") if manual_count == 1 else None
                ),
                "aria_disabled": (
                    manual.get_attribute("aria-disabled") if manual_count == 1 else None
                ),
            })
            for disk_name in disk_names:
                escaped_name = re.escape(disk_name)
                for control_role in ("checkbox", "radio"):
                    candidate = wizard.get_by_role(
                        control_role,
                        name=re.compile(escaped_name, re.IGNORECASE),
                    )
                    count = candidate.count()
                    diagnostic = {
                        "control": control_role,
                        "name": disk_name,
                        "count": count,
                        "visible": count == 1 and candidate.is_visible(),
                        "enabled": count == 1 and candidate.is_enabled(),
                    }
                    if count == 1 and candidate.is_visible() and candidate.is_enabled():
                        candidate.check()
                        data_disk_selected = candidate.is_checked()
                        diagnostic["checked_after_action"] = data_disk_selected
                        if data_disk_selected:
                            data_disk_attempts.append(
                                f"selected {disk_name!r} through role={control_role}"
                            )
                            selection_diagnostics.append(diagnostic)
                            break
                    selection_diagnostics.append(diagnostic)
                if data_disk_selected:
                    break

                row = wizard.get_by_role("row", name=re.compile(escaped_name, re.IGNORECASE))
                row_count = row.count()
                if row_count == 1 and row.is_visible():
                    row_checkbox = row.get_by_role("checkbox")
                    checkbox_count = row_checkbox.count()
                    diagnostic = {
                        "control": "row-checkbox",
                        "name": disk_name,
                        "row_count": row_count,
                        "checkbox_count": checkbox_count,
                        "visible": checkbox_count == 1 and row_checkbox.is_visible(),
                        "enabled": checkbox_count == 1 and row_checkbox.is_enabled(),
                    }
                    if checkbox_count == 1 and row_checkbox.is_visible() and row_checkbox.is_enabled():
                        row_checkbox.check()
                        data_disk_selected = row_checkbox.is_checked()
                        diagnostic["checked_after_action"] = data_disk_selected
                        selection_diagnostics.append(diagnostic)
                        if data_disk_selected:
                            data_disk_attempts.append(f"selected {disk_name!r} through its disk-row checkbox")
                            break
                    selection_diagnostics.append(diagnostic)
            if data_disk_selected:
                page.wait_for_timeout(300)

        if step == "review":
            break
        next_button = wizard.get_by_role("button", name=re.compile(r"^next$", re.IGNORECASE))
        if next_button.count() != 1 or not next_button.is_enabled():
            break
        next_button.click()
        changed = False
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            page.wait_for_timeout(250)
            if active_step(wizard.aria_snapshot()) != step:
                changed = True
                break
        if not changed:
            break

    # Optional role steps are independently selectable in the wizard stepper.
    # Visit them directly even when the required data layout has no disk
    # selected, so this probe can determine whether boot-pool zvols are offered
    # for each role without ever submitting a pool.
    for role_step in ("log", "cache", "special"):
        if role_step in role_steps_seen:
            continue
        step_control = wizard.get_by_role(
            "button",
            name=re.compile(rf"^{role_step}\s+\(optional\)$", re.IGNORECASE),
        )
        diagnostic = {
            "control": f"wizard-step-{role_step}",
            "count": step_control.count(),
            "visible": step_control.count() == 1 and step_control.is_visible(),
            "enabled": step_control.count() == 1 and step_control.is_enabled(),
        }
        if step_control.count() == 1:
            diagnostic["disabled_attribute"] = step_control.get_attribute("disabled")
            diagnostic["aria_disabled"] = step_control.get_attribute("aria-disabled")
        if (
            step_control.count() == 1 and step_control.is_visible()
            and step_control.is_enabled()
            and step_control.get_attribute("aria-disabled") != "true"
            and step_control.get_attribute("disabled") is None
        ):
            step_control.click()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                page.wait_for_timeout(200)
                if active_step(wizard.aria_snapshot()) == role_step:
                    break
        active = active_step(wizard.aria_snapshot())
        diagnostic["active_after_action"] = active
        selection_diagnostics.append(diagnostic)
        if active == role_step:
            role_steps_seen.add(role_step)
            text = " ".join(wizard.inner_text(timeout=30_000).split())
            snapshot = wizard.aria_snapshot()
            relevant_lines = [
                line.strip()
                for line in snapshot.splitlines()
                if re.search(
                    r"Layout|Manual Disk|Automated Disk|Available Disks|Unassigned Disks|"
                    r"No disks|TNEXTRA|/dev/vd[a-z]|Cache|Log|Special|Metadata|Next|Create Pool",
                    line,
                    re.IGNORECASE,
                )
            ]
            snapshots.append({
                "active_step": active,
                "text": text[:1600],
                "aria_relevant": " | ".join(relevant_lines[:50])[:2400],
            })
            for role_name, dataset_name in zvol_names.items():
                leaf = dataset_name.rsplit("/", 1)[-1]
                if dataset_name in text or leaf in text or f"/dev/zvol/{dataset_name}" in text:
                    visible_zvols.add(role_name)

    expected_roles = {"cache", "log", "special"}
    missing_roles = sorted(expected_roles - role_steps_seen)
    report("ui-aux-vdev-selectors", json.dumps({
        "data_disk": {key: data_disk.get(key) for key in ("serial", "name", "devname", "size", "pool")},
        "data_disk_selected": data_disk_selected,
        "data_disk_selection_attempts": data_disk_attempts,
        "wizard_role_steps_seen": sorted(role_steps_seen),
        "wizard_role_labels_missing": missing_roles,
        "selection_diagnostics": selection_diagnostics,
        "boot_pool_zvols_visible_by_name": sorted(visible_zvols),
        "browser_disk_query_rows": list(unique_rows.values()),
    }, sort_keys=True))
    for snapshot in snapshots:
        report("ui-pool-wizard-step", json.dumps(snapshot, sort_keys=True))
    if visible_zvols:
        report(
            "ui-zvol-vdev-selection",
            "the wizard displayed these boot-pool zvol names in its form: "
            + ", ".join(sorted(visible_zvols)),
        )
    else:
        report(
            "ui-zvol-vdev-selection",
            "the wizard did not display the UI-created boot-pool zvols by name; "
            "the browser disk inventory therefore offers no boot-pool zvol for an auxiliary role",
        )
    page.goto(base_url + "/ui/storage", wait_until="domcontentloaded", timeout=120_000)
    create, create_role = pool_create_control(page)
    if create is None:
        for role in ("button", "link"):
            candidate = page.get_by_role(
                role, name=re.compile(r"^create pool$", re.IGNORECASE),
            )
            try:
                candidate.wait_for(state="visible", timeout=45_000)
            except PlaywrightError:
                continue
            if candidate.count() == 1:
                create, create_role = candidate, role
                break
    require(create is not None, "Storage Dashboard did not return to a Create Pool control")
    report("ui-pool-inventory-navigation", f"returned to Storage Dashboard with role={create_role}")
    return {
        "data_disk_selected": data_disk_selected,
        "data_disk": data_disk,
        "data_disk_selection_attempts": data_disk_attempts,
        "wizard_role_steps_seen": sorted(role_steps_seen),
        "wizard_role_labels_missing": missing_roles,
        "boot_pool_zvols_visible_by_name": sorted(visible_zvols),
    }


def target_preflight(target: Target, expected_version: str) -> dict[str, dict]:
    info = target.call("system.info")
    version = str(info.get("version", ""))
    require(version == expected_version, f"expected TrueNAS {expected_version}, got {version!r}")
    require(os.environ.get("TRUENAS_STORAGE_PROFILE") == "path-a",
            "live-vdev-topology is scoped to a fresh Path A guest")
    require(os.environ.get("TRUENAS_TEST_ALLOW_MUTATIONS") == "1",
            "set TRUENAS_TEST_ALLOW_MUTATIONS=1 only for the disposable topology guest")

    try:
        patch_record = json.loads(target.shell("cat /root/v27-boot-pool-patch-install.json", timeout=30))
    except (RemoteError, ValueError) as exc:
        raise RemoteError("live-vdev-topology requires the registered v27 patch-install record") from exc
    require(patch_record.get("root_create_guard") == "root-excluded-from-descendant-readiness-v1",
            f"unexpected v27 patch-install record: {patch_record!r}")

    health = target.shell("zpool list -H -o name,health", timeout=90).splitlines()
    require(health == ["boot-pool\tONLINE"],
            f"fresh topology guest must have only boot-pool ONLINE before test: {health!r}")
    rows = flatten_datasets(target.query("pool.dataset.query", timeout=120))
    resume_existing_root = os.environ.get("TRUENAS_TEST_RESUME_EXISTING_UI_ROOT", "")
    require(resume_existing_root in {"", "1"},
            "TRUENAS_TEST_RESUME_EXISTING_UI_ROOT must be unset or 1")
    root = selected_dataset_parent("path-a")
    if root in rows:
        require(resume_existing_root == "1",
                f"fresh topology guest already has {root}; set the guarded UI root resume flag only "
                "after a registered read-only inventory confirms it")
    else:
        require(resume_existing_root != "1",
                f"UI root resume flag requires the previously UI-created root {root}")
    require(target.query("container.query", timeout=120) == [],
            "fresh topology guest has container records")
    require(target.query("vm.query", timeout=120) == [],
            "fresh topology guest has VM records")
    require(target.shell("test ! -e /dev/vda4 && printf absent") == "absent",
            "Path A topology guest unexpectedly has a Path B partition")
    return free_extra_disks(target)


def pool_query(target: Target, name: str) -> dict | None:
    rows = target.query("pool.query", [["name", "=", name]], timeout=120)
    require(isinstance(rows, list), f"pool.query returned a non-list for {name}: {rows!r}")
    require(len(rows) <= 1, f"pool.query returned duplicate pool rows for {name}: {rows!r}")
    return rows[0] if rows else None


def wait_pool(target: Target, name: str, *, online: bool, timeout: int = 600) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = pool_query(target, name)
        if (row is not None and row.get("status") == "ONLINE") if online else row is None:
            return row
        time.sleep(3)
    expected = "ONLINE" if online else "absent"
    raise RemoteError(f"pool.query did not report {name} {expected} within {timeout}s")


def complete_job(target: Target, result, label: str, *, timeout: int = 900) -> None:
    if type(result) is int:
        target.wait_job(result, timeout=timeout, label=label)
    elif result is not None:
        report("api-job-result", f"{label} returned {json.dumps(result, sort_keys=True)[:1800]}")


def create_pool(target: Target, name: str, vdev_type: str, disk_names: list[str]) -> dict:
    before = pool_query(target, name)
    require(before is None, f"refusing to create test pool over existing pool {name}: {before!r}")
    payload = {
        "name": name,
        "topology": {"data": [{"type": vdev_type, "disks": disk_names}]},
    }
    report("pool-create-api-request", json.dumps(payload, sort_keys=True))
    result = target.call("pool.create", payload, timeout=180)
    complete_job(target, result, f"pool.create {name}")
    row = wait_pool(target, name, online=True)
    require(row is not None, f"pool.create did not register {name}")
    report("pool-create-api", f"created {name} using data topology {vdev_type}; id={row.get('id')}")
    return row


def add_auxiliary_vdevs(target: Target, pool: dict, name: str, topology: dict) -> str:
    require(type(pool.get("id")) is int, f"pool.query did not return an integer id for {name}: {pool!r}")
    require("force_topology" not in topology, "test must use native topology policy, not force_topology")
    request = {"topology": topology}
    report("pool-update-api-request", f"pool={name} {json.dumps(request, sort_keys=True)}")
    result = target.call("pool.update", pool["id"], request, timeout=180)
    complete_job(target, result, f"pool.update {name}")
    row = wait_pool(target, name, online=True)
    require(row is not None, f"pool.update removed or failed to register {name}")
    status = target.shell(f"zpool status -P {name}", timeout=90)
    expected_devices = [
        device
        for role in ("cache", "log", "special")
        for vdev in topology.get(role, [])
        for device in vdev.get("disks", [])
    ]
    missing = [device for device in expected_devices if device not in status]
    require(not missing, f"zpool status -P {name} omits API-selected auxiliary devices {missing!r}: {status}")
    require(target.shell(f"zpool list -H -o health {name}", timeout=90) == "ONLINE",
            f"{name} is not healthy after adding auxiliary vdevs")
    api_topology = json.dumps(row.get("topology") or {}, sort_keys=True)
    for role in ("cache", "log", "special"):
        if topology.get(role):
            require(role in api_topology,
                    f"pool.query topology omits configured {role} vdev for {name}: {api_topology}")
    report("pool-topology-api", f"{name} ONLINE; pool.query={api_topology}; zpool status verified {expected_devices!r}")
    return status


def exercise_metadata_and_sync_write(
    target: Target,
    pool_name: str,
    suffix: str,
    log_device: str,
    special_devices: list[str],
) -> None:
    dataset = f"{pool_name}/metadata-probe"
    created = target.call("pool.dataset.create", {
        "name": dataset,
        "type": "FILESYSTEM",
        "create_ancestors": False,
    }, timeout=120)
    require(isinstance(created, dict) and created.get("id") == dataset,
            f"API dataset create returned an unexpected result for {dataset}: {created!r}")
    updated = target.call("pool.dataset.update", dataset, {"sync": "ALWAYS"}, timeout=120)
    require(updated is not None, f"API dataset update did not persist sync=ALWAYS on {dataset}")
    require(zfs_property(target, dataset, "sync").lower() == "always",
            f"{dataset} does not have sync=always for the SLOG probe")
    mountpoint = zfs_property(target, dataset, "mountpoint")
    require(mountpoint.startswith("/mnt/"), f"unexpected test dataset mountpoint {mountpoint!r}")
    args = " ".join(shlex.quote(value) for value in [mountpoint, pool_name, log_device, *special_devices])
    script = (
        "python3 - " + args + " <<'PY'\n"
        "import json,os,subprocess,sys,time\n"
        "root,pool,log_device,*special_devices=sys.argv[1:]\n"
        "sampler=subprocess.Popen(['zpool','iostat','-v','-y','-P','-p','-n',pool,'1','8'],\n"
        "    stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)\n"
        "try:\n"
        "    time.sleep(0.25)\n"
        "    total=0\n"
        "    for index in range(128):\n"
        "        directory=os.path.join(root,f'dir-{index:03d}')\n"
        "        os.mkdir(directory)\n"
        "        with open(os.path.join(directory,'probe.bin'),'wb') as stream:\n"
        "            stream.write(os.urandom(8192))\n"
        "            stream.flush()\n"
        "            os.fsync(stream.fileno())\n"
        "            total += stream.tell()\n"
        "    iostat,_=sampler.communicate(timeout=20)\n"
        "except BaseException:\n"
        "    sampler.terminate()\n"
        "    sampler.wait(timeout=5)\n"
        "    raise\n"
        "if sampler.returncode != 0:\n"
        "    raise SystemExit(f'zpool iostat failed ({sampler.returncode}): {iostat}')\n"
        "print(json.dumps({'bytes':total,'iostat':iostat}))\n"
        "PY"
    )
    probe = json.loads(target.shell(script, timeout=150))
    expected_bytes = 128 * 8192
    require(probe.get("bytes") == expected_bytes,
            f"sync=always SLOG probe wrote {probe.get('bytes')!r} bytes, expected {expected_bytes}")
    devices = [log_device, *special_devices]
    try:
        counters = parse_vdev_write_counters(probe.get("iostat", ""), devices)
    except ValueError as exc:
        raise RemoteError(f"cannot verify per-vdev writes for {pool_name}: {exc}") from exc
    for device in devices:
        operations, byte_count = counters[device]
        require(operations > 0 and byte_count > 0,
                f"sync/metadata probe did not write to expected vdev {device}: "
                f"ops={operations} bytes={byte_count}; iostat={probe.get('iostat')!r}")
    target.shell("sync", timeout=90)
    status = target.shell(f"zpool status -P {pool_name}", timeout=90)
    require("logs" in status.lower(), f"SLOG vdev is absent from {pool_name} status after sync probe")
    require("special" in status.lower(), f"metadata special vdev is absent from {pool_name} status")
    report(
        "metadata-slog-probe",
        f"{pool_name} wrote and fsynced {expected_bytes} bytes; per-vdev iostat writes={counters!r}",
    )


def export_destroy_pool(target: Target, name: str) -> None:
    row = pool_query(target, name)
    if row is None:
        return
    require(type(row.get("id")) is int, f"cannot safely clean up {name} without its pool id: {row!r}")
    result = target.call("pool.export", row["id"], {"cascade": True, "destroy": True}, timeout=180)
    complete_job(target, result, f"pool.export destroy {name}", timeout=900)
    if wait_pool(target, name, online=False, timeout=180) is not None:
        raise RemoteError(f"disposable pool {name} remains registered after API destroy")
    require(name not in target.shell("zpool list -H -o name", timeout=90).splitlines(),
            f"disposable pool {name} remains imported after API destroy")
    report("pool-cleanup", f"API pool.export destroyed disposable pool {name}")


def cleanup_dataset(target: Target, dataset: str) -> None:
    rows = flatten_datasets(target.query("pool.dataset.query", timeout=120))
    if dataset not in rows:
        return
    require(dataset.startswith("boot-pool/user-data/vdev-acceptance-"),
            f"refusing cleanup outside this lane's generated Path A test tree: {dataset!r}")
    target.call("pool.dataset.delete", dataset, {"recursive": True}, timeout=180)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        remaining = flatten_datasets(target.query("pool.dataset.query", timeout=120))
        leftovers = [name for name in remaining if name == dataset or name.startswith(dataset + "/")]
        if not leftovers:
            report("dataset-cleanup", f"removed UI-created topology test tree {dataset}")
            return
        time.sleep(3)
    raise RemoteError(f"UI-created topology test tree remains after cleanup: {leftovers!r}")


def verify_pool_list_in_ui(
    page, base_url: str, pool_names: list[str], username: str, password: str,
) -> None:
    page.goto(base_url + "/ui/storage", wait_until="domcontentloaded", timeout=120_000)
    username_field = page.get_by_role("textbox", name="Username")
    username_visible = False
    try:
        username_field.wait_for(state="visible", timeout=3_000)
        username_visible = True
    except PlaywrightError:
        pass
    if (re.search(r"/ui/(?:signin/?)?$", page.url, re.IGNORECASE)
            or username_visible):
        report("ui-session", "the API test exceeded the UI session idle timer; signing in again")
        ui_login(page, base_url, username, password)
        page.goto(base_url + "/ui/storage", wait_until="domcontentloaded", timeout=120_000)
    create_control, create_role = pool_create_control(page)
    if create_control is None:
        for role in ("button", "link"):
            candidate = page.get_by_role(
                role, name=re.compile(r"^create pool$", re.IGNORECASE),
            )
            try:
                candidate.wait_for(state="visible", timeout=45_000)
            except PlaywrightError:
                continue
            if candidate.count() == 1:
                create_control, create_role = candidate, role
                break
    if create_control is None:
        body = page.locator("body")
        main = page.get_by_role("main")
        snapshot = main.aria_snapshot() if main.count() == 1 else body.aria_snapshot()
        report("ui-pool-inventory-navigation-failure", json.dumps({
            "url": page.url,
            "username_field_visible": username_visible,
            "body_text": " ".join(body.inner_text(timeout=30_000).split())[:2400],
            "main_snapshot": snapshot[:5000],
        }, sort_keys=True))
        raise RemoteError(f"TrueNAS Storage UI did not expose the pool inventory at {page.url!r}")
    report("ui-pool-inventory-navigation", f"pool inventory available through role={create_role}")
    for name in pool_names:
        page.get_by_text(name, exact=True).first.wait_for(state="visible", timeout=60_000)
    body = " ".join(page.locator("body").inner_text(timeout=30_000).split())
    report("ui-pool-inventory", f"storage page after API pool setup: {body[:5000]}")
    for name in pool_names:
        require(name in body, f"TrueNAS Storage UI does not show API-created pool {name!r}")


def main() -> int:
    target = Target.from_environment()
    expected_version = os.environ.get("TRUENAS_EXPECTED_VERSION", "")
    if not expected_version:
        raise RemoteError("TRUENAS_EXPECTED_VERSION is required")
    base_url = os.environ.get("TRUENAS_UI_URL", "").rstrip("/")
    ws_url = os.environ.get("PWMCP_WS_URL", "")
    expected_playwright = os.environ.get("PWMCP_PLAYWRIGHT_VERSION", "")
    if not base_url or ws_url != "ws://pwmcp:3000/" or not expected_playwright:
        raise RemoteError("managed TrueNAS UI URL, PWMCP endpoint, and pinned Playwright version are required")
    try:
        from importlib.metadata import version

        actual_playwright = version("playwright")
    except Exception as exc:
        raise RemoteError(f"cannot read installed Playwright version: {type(exc).__name__}") from None
    require(actual_playwright == expected_playwright,
            f"PWMCP Playwright version {expected_playwright} required, found {actual_playwright}")

    disks = target_preflight(target, expected_version)
    user, password = ui_secrets()
    suffix = time.strftime("%Y%m%d%H%M%S", time.gmtime()) + uuid.uuid4().hex[:6]
    root = selected_dataset_parent("path-a")
    dataset = f"{root}/vdev-acceptance-{suffix}"
    pool_single = f"vdsingle{suffix[-8:]}"
    pool_raidz = f"vdraidz1{suffix[-8:]}"
    pool_names = [pool_single, pool_raidz]
    created_dataset = False
    created_pools: list[str] = []
    cleanup_errors: list[str] = []
    topology_errors: list[str] = []
    api_refusals: list[str] = []
    topology_successes: list[str] = []
    ui_probe: dict[str, object] | None = None
    browser = None
    context = None
    page = None
    dataset_requests: list[dict] = []
    disk_responses: list[dict] = []

    zvol_names = {
        name: f"{dataset}/{name}"
        for name in (
            "single-cache", "single-log", "single-special",
            "raidz-cache", "raidz-log", "raidz-special-a", "raidz-special-b",
        )
    }

    try:
        with sync_playwright() as playwright:
            report("browser", "connecting to the managed PWMCP Playwright endpoint")
            browser = connect_pwmcp(playwright, ws_url)
            context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            dataset_requests = observe_rpc_method(page, "pool.dataset.create")
            observe_rpc_method(page, "disk.query", log_response=True, response_sink=disk_responses)
            ui_login(page, base_url, user, password)
            report("login", f"PWMCP authenticated to TrueNAS {expected_version}")

            existing_ui_root = inventory_before_ui_mutation(
                target,
                "path-a",
                "alpine:3.24:amd64:default",
                resume_existing_root=os.environ.get("TRUENAS_TEST_RESUME_EXISTING_UI_ROOT") == "1",
            )
            if existing_ui_root:
                report("path-a-root-ui", "resuming the directly verified UI-created root")
            else:
                create_path_a_root_in_ui(page, base_url, target, dataset_requests)
            parent = select_dataset_parent(page, base_url, "path-a")
            created = create_dataset_in_ui(page, parent, dataset.rsplit("/", 1)[1])
            created_dataset = True
            require(created == dataset, f"UI created unexpected dataset path {created!r}")
            require(wait_dataset(target, dataset, present=True), f"UI-created topology dataset is absent: {dataset}")
            for role_name, zvol in zvol_names.items():
                before = len(dataset_requests)
                created_zvol = create_zvol_in_ui(
                    page, base_url, dataset, role_name, dataset_requests,
                )
                require(created_zvol == zvol, f"UI created unexpected zvol {created_zvol!r}; expected {zvol!r}")
                require(wait_dataset(target, zvol, present=True), f"UI-created zvol is absent from middleware: {zvol}")
                row = flatten_datasets(target.query("pool.dataset.query", timeout=120)).get(zvol)
                require(row is not None and row.get("type") == "VOLUME",
                        f"UI-created auxiliary zvol is not a middleware VOLUME: {zvol!r} {row!r}")
                require(row.get("volsize", {}).get("parsed") == ZVOL_BYTES,
                        f"UI-created zvol has wrong volsize: {zvol!r} {row.get('volsize')!r}")
                require(zfs_property(target, zvol, "volblocksize") == "16384",
                        f"UI-created zvol has unexpected volblocksize: {zvol!r}")
                api_request = dataset_create_properties(dataset_requests[before:], zvol)
                require(api_request is not None and api_request.get("type") == "VOLUME",
                        f"PWMCP did not submit pool.dataset.create for {zvol}: {api_request!r}")
                assert_zvol_backing(target, zvol, expected_size=ZVOL_BYTES)
                zvol_device = f"/dev/zvol/{zvol}"
                volmode = zfs_property(target, zvol, "volmode")
                device_state = target.shell(
                    f"if [ -b {shlex.quote(zvol_device)} ]; then "
                    f"readlink -f {shlex.quote(zvol_device)}; "
                    f"stat -Lc '%F:%t:%T' {shlex.quote(zvol_device)}; "
                    f"elif [ -e {shlex.quote(zvol_device)} ]; then "
                    f"stat -c '%F' {shlex.quote(zvol_device)}; "
                    "else printf 'missing'; fi",
                    timeout=30,
                )
                report("zvol-device", json.dumps({
                    "dataset": zvol,
                    "device": zvol_device,
                    "volmode": volmode,
                    "device_state": device_state,
                }, sort_keys=True))
                report("zvol-ui", f"PWMCP created {zvol} as a 128 MiB sparse zvol in {root}")

            ui_probe = inspect_aux_vdev_selectors(page, base_url, zvol_names, disk_responses)
            if ui_probe.get("wizard_role_labels_missing") != []:
                raise RemoteError(
                    "UI probe could not reach all auxiliary-vdev role screens; "
                    "stopping before API pool mutations"
                )

            extra_disk1 = disks["TNEXTRA001"]["name"]
            raidz_disks = [disks[serial]["name"] for serial in EXTRA_DISK_SERIALS[1:]]
            created_pools.append(pool_single)
            single = create_pool(target, pool_single, "STRIPE", [extra_disk1])
            created_pools.append(pool_raidz)
            raidz = create_pool(target, pool_raidz, "RAIDZ1", raidz_disks)

            single_topology = {
                "cache": [{"type": "STRIPE", "disks": [f"/dev/zvol/{zvol_names['single-cache']}"]}],
                "log": [{"type": "STRIPE", "disks": [f"/dev/zvol/{zvol_names['single-log']}"]}],
                "special": [{"type": "STRIPE", "disks": [f"/dev/zvol/{zvol_names['single-special']}"]}],
            }
            raidz_topology = {
                "cache": [{"type": "STRIPE", "disks": [f"/dev/zvol/{zvol_names['raidz-cache']}"]}],
                "log": [{"type": "STRIPE", "disks": [f"/dev/zvol/{zvol_names['raidz-log']}"]}],
                "special": [{
                    "type": "MIRROR",
                    "disks": [
                        f"/dev/zvol/{zvol_names['raidz-special-a']}",
                        f"/dev/zvol/{zvol_names['raidz-special-b']}",
                    ],
                }],
            }

            for pool, pool_name, topology, log_device, special_devices in (
                (
                    single,
                    pool_single,
                    single_topology,
                    f"/dev/zvol/{zvol_names['single-log']}",
                    [f"/dev/zvol/{zvol_names['single-special']}"],
                ),
                (
                    raidz,
                    pool_raidz,
                    raidz_topology,
                    f"/dev/zvol/{zvol_names['raidz-log']}",
                    [
                        f"/dev/zvol/{zvol_names['raidz-special-a']}",
                        f"/dev/zvol/{zvol_names['raidz-special-b']}",
                    ],
                ),
            ):
                try:
                    add_auxiliary_vdevs(target, pool, pool_name, topology)
                    exercise_metadata_and_sync_write(
                        target,
                        pool_name,
                        suffix[-8:],
                        log_device,
                        special_devices,
                    )
                    topology_successes.append(pool_name)
                except Exception as exc:
                    report("pool-topology-api-refusal", f"{pool_name}: {type(exc).__name__}: {exc}")
                    row = pool_query(target, pool_name)
                    status = target.shell(f"zpool status -P {pool_name}", timeout=90)
                    health = target.shell(f"zpool list -H -o health {pool_name}", timeout=90)
                    require(row is not None and row.get("status") == "ONLINE" and row.get("healthy") is True,
                            f"{pool_name} is not ONLINE after the auxiliary-vdev attempt: {row!r}")
                    require(health == "ONLINE",
                            f"{pool_name} lost pool health after the auxiliary-vdev attempt: {health!r}")
                    topology_state = row.get("topology") or {}
                    role_state = {
                        role: topology_state.get(role, [])
                        for role in ("cache", "log", "special")
                    }
                    refusal = "The following disks were not found in system" in str(exc)
                    if refusal:
                        require(
                            all(not role_state[role] for role in role_state),
                            f"{pool_name} changed auxiliary topology after disk-inventory refusal: {role_state!r}",
                        )
                        api_refusals.append(f"{pool_name}: {exc}")
                    else:
                        topology_errors.append(f"{pool_name}: {exc}")
                    report("pool-topology-after-attempt", json.dumps({
                        "pool": pool_name,
                        "pool_query": row,
                        "zpool_status": status,
                        "health": health,
                        "auxiliary_roles": role_state,
                        "expected_disk_inventory_refusal": refusal,
                    }, sort_keys=True))

            verify_pool_list_in_ui(page, base_url, pool_names, user, password)
            if topology_errors:
                report("topology-partial-result", json.dumps({
                    "api_role_errors": topology_errors,
                    "api_role_refusals": api_refusals,
                    "ui_probe": ui_probe,
                }, sort_keys=True))
            elif api_refusals:
                report("topology-capability-gap", json.dumps({
                    "api_role_refusals": api_refusals,
                    "api_role_successes": topology_successes,
                    "ui_probe": ui_probe,
                    "meaning": (
                        "TrueNAS middleware rejected boot-pool zvols as pool vdev devices; "
                        "the pool wizard also omitted them from its disk inventory."
                    ),
                }, sort_keys=True))
            else:
                report(
                    "topology-result",
                    "API created single-disk and RAIDZ1 data pools. Each used distinct UI-created boot-pool zvols "
                    "simultaneously as cache, log, and special. UI storage inventory showed both pools.",
                )
    finally:
        if context is not None:
            try:
                context.close()
            except PlaywrightError:
                pass
        if browser is not None:
            try:
                browser.close()
            except PlaywrightError:
                pass
        for pool_name in reversed(created_pools):
            try:
                export_destroy_pool(target, pool_name)
            except Exception as exc:
                cleanup_errors.append(f"pool {pool_name}: {exc}")
        if created_dataset and not cleanup_errors:
            try:
                cleanup_dataset(target, dataset)
            except Exception as exc:
                cleanup_errors.append(f"dataset {dataset}: {exc}")
        if cleanup_errors:
            report("cleanup-error", "; ".join(cleanup_errors))

    if cleanup_errors:
        raise RemoteError("cleanup failed: " + "; ".join(cleanup_errors))
    final_errors: list[str] = []
    if ui_probe is None:
        final_errors.append("PWMCP auxiliary-vdev UI probe did not return a result")
    else:
        missing_ui_roles = ui_probe.get("wizard_role_labels_missing")
        if missing_ui_roles != []:
            final_errors.append(
                f"TrueNAS Pool Creation Wizard did not reach all auxiliary role screens: {missing_ui_roles!r}"
            )
    if topology_errors:
        final_errors.append("API auxiliary-vdev attempts failed: " + "; ".join(topology_errors))
    if api_refusals and topology_successes:
        final_errors.append(
            "API auxiliary-vdev acceptance differed between target pools: "
            + "; ".join(api_refusals + topology_successes)
        )
    require(not final_errors, "; ".join(final_errors))
    remaining = target.shell("zpool list -H -o name,health", timeout=90).splitlines()
    require(remaining == ["boot-pool\tONLINE"],
            f"topology pools remain after cleanup or boot-pool changed health: {remaining!r}")
    require(pool_single not in target.shell("zpool list -H -o name", timeout=90).splitlines(),
            f"single-disk probe pool remains after cleanup: {pool_single}")
    require(pool_raidz not in target.shell("zpool list -H -o name", timeout=90).splitlines(),
            f"RAIDZ1 probe pool remains after cleanup: {pool_raidz}")
    if api_refusals:
        report(
            "result",
            f"PASS diagnostic probe TrueNAS {expected_version} Path A: boot-pool zvol vdevs "
            "were refused safely and remain an open capability requirement",
        )
    else:
        report("result", f"PASS TrueNAS {expected_version} Path A vdev topology")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RemoteError, KeyError, TypeError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
