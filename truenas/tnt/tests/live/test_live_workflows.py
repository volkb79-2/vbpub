"""Explicit, namespaced acceptance probe for an operator-provided v27 host.

This module is selected only with ``-m live``. It never skips: selecting it
without all prerequisites is a failure, and it creates and removes only
resources with a unique ``tnt-live-`` namespace (VM names use the required
ASCII-alphanumeric ``tntlive`` prefix).
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import pty
import secrets
import select
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import time
import uuid
from contextlib import contextmanager

import pytest
from textual.widgets import DataTable
from tnt.actions import apply_plan, make_plan
from tnt.host_store import load_host_store
from tnt.keyfiles import write_api_key_file
from tnt.menu import walk
from tnt.profile import Secret, read_credential, resolve_paths
from tnt.session import open_session
from tnt.tui.app import TntApp
from tnt.tui.core_views import CoreReadView
from tnt.tui.hosts import HostPicker
from tnt.workflows import validate_ssh_public_keys

pytestmark = pytest.mark.live


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    assert value, f"live lane requires {name}"
    return value


def _apply(session, action, confirmation: str | None = None, *, progress_events: list | None = None):
    plan = make_plan(action, session.rpc)
    print(plan.render(), flush=True)
    callback = progress_events.append if progress_events is not None else None
    return apply_plan(plan, session.rpc, allow_write=True, typed_confirmation=confirmation, job_progress=callback)


def _cli_json(profile_name: str, *arguments: str):
    command = [sys.executable, "-m", "tnt", *arguments,
               "--host", profile_name, "--insecure", "--json"]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    assert completed.returncode == 0, f"live tnt CLI command exited with status {completed.returncode}"
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise AssertionError("live tnt CLI command did not return valid JSON") from None


async def _wait_for_pilot(pilot, predicate, message: str) -> None:
    for _ in range(200):
        await pilot.pause(0.05)
        if predicate():
            return
    raise AssertionError(message)


def _view_widget(view, widget_id: str):
    return next((widget for widget in view.walk_children() if widget.id == widget_id), None)


async def _wait_for_host_switch(pilot, app, content, host_name: str) -> None:
    for _ in range(900):
        await pilot.pause(0.05)
        if (app.session is not None and app.session.profile.name == host_name and app.current == "dashboard"
                and content.children and isinstance(content.children[0], CoreReadView)):
            return
        if len(app.screen.query("#worker-drain-wait")):
            await pilot.click("#worker-drain-wait")
    raise AssertionError(f"TUI did not finish switching to host alias {host_name!r}")


def _wait_for_state(workflows, family: str, name: str, state: str) -> dict:
    deadline = time.monotonic() + 120
    observed = None
    while time.monotonic() < deadline:
        row = getattr(workflows, family)(name)
        observed = row.get("status", {}).get("state") if isinstance(row.get("status"), dict) else None
        if isinstance(observed, str) and observed.casefold() == state.casefold():
            return row
        time.sleep(1)
    raise AssertionError(f"{family} {name!r} did not reach {state} within 120 seconds; last state={observed!r}")


def _public_test_key() -> str:
    key_type = b"ssh-ed25519"
    blob = struct.pack(">I", len(key_type)) + key_type + bytes(range(32))
    return "ssh-ed25519 " + base64.b64encode(blob).decode("ascii") + " tnt-live-test"


def _query_rows(session, method: str, filters=()) -> list[dict]:
    rows = session.rpc.call(method, list(filters), {})
    assert isinstance(rows, list) and all(isinstance(row, dict) for row in rows), (
        f"{method} returned an invalid row collection"
    )
    return rows


def _exists(session, method: str, field: str, name: str) -> bool:
    rows = _query_rows(session, method, [[field, "=", name]])
    return any(isinstance(row, dict) and row.get(field) == name for row in rows)


def test_v27_core_workflows_against_explicit_profile():
    assert os.environ.get("TNT_LIVE") == "1", "selecting the live lane requires TNT_LIVE=1"
    profile_name = _required("TNT_LIVE_PROFILE")
    pool_name = _required("TNT_LIVE_POOL")
    group_text = _required("TNT_LIVE_GROUP_ID")
    image_name = _required("TNT_LIVE_CONTAINER_IMAGE_NAME")
    image_version = _required("TNT_LIVE_CONTAINER_IMAGE_VERSION")
    memory_text = _required("TNT_LIVE_VM_MEMORY_MIB")
    disk_size_text = _required("TNT_LIVE_VM_DISK_BYTES")
    try:
        group_id = int(group_text)
        memory_mib = int(memory_text)
        disk_size = int(disk_size_text)
    except ValueError:
        raise AssertionError("TNT_LIVE_GROUP_ID, TNT_LIVE_VM_MEMORY_MIB, and TNT_LIVE_VM_DISK_BYTES must be integers") from None
    assert group_id > 0, "TNT_LIVE_GROUP_ID must be positive"
    assert memory_mib >= 20, "TNT_LIVE_VM_MEMORY_MIB must be at least 20"
    assert disk_size > 0, "TNT_LIVE_VM_DISK_BYTES must be positive"

    paths = resolve_paths()
    profiles, _, entries = load_host_store(paths, require_secrets=True)
    assert profile_name in profiles, f"profile {profile_name!r} is absent from {paths.config}"
    profile = profiles[profile_name]
    session = open_session(profile, secret=read_credential(profile, entries), allow_write=True,
                           insecure_ack=os.environ.get("TNT_LIVE_INSECURE") == "1")
    workflows = session.workflows
    suffix = uuid.uuid4().hex[:12]
    prefix = f"tnt-live-{suffix}"
    vm_name = f"tntlive{suffix}"
    dataset = f"{pool_name}/{prefix}"
    clone = f"{pool_name}/{prefix}-clone"
    disk_zvol = f"{pool_name}/{prefix}-disk"
    user = prefix
    cleanup_errors: list[str] = []
    owned: set[str] = set()

    try:
        pools = _query_rows(session, "pool.query")
        assert any(isinstance(row, dict) and row.get("name") == pool_name for row in pools), (
            f"TNT_LIVE_POOL {pool_name!r} is not a data pool visible to this profile"
        )
        assert pool_name != "boot-pool", "TNT_LIVE_POOL must be a disposable data pool, not boot-pool"
        groups = _query_rows(session, "group.query", [["id", "=", group_id]])
        assert any(isinstance(row, dict) and row.get("id") == group_id for row in groups), (
            f"TNT_LIVE_GROUP_ID {group_id} is not a local group visible to this profile"
        )
        targets = (
            ("pool.dataset.query", "id", dataset), ("pool.dataset.query", "id", clone),
            ("pool.dataset.query", "id", disk_zvol), ("pool.snapshot.query", "id", f"{dataset}@{prefix}"),
            ("user.query", "username", user), ("container.query", "name", prefix), ("vm.query", "name", vm_name),
        )
        for method, field, name in targets:
            assert not _exists(session, method, field, name), f"refusing to reuse pre-existing live test resource {name!r}"

        owned.add("dataset")
        _apply(session, workflows.dataset_create_action({"name": dataset, "type": "FILESYSTEM", "comments": prefix}))
        _apply(session, workflows.dataset_update_action(dataset, {"comments": f"{prefix} updated"}))
        owned.add("snapshot")
        _apply(session, workflows.snapshot_create_action({"dataset": dataset, "name": prefix}))
        snapshot = f"{dataset}@{prefix}"
        _apply(session, workflows.snapshot_hold_action(snapshot))
        _apply(session, workflows.snapshot_release_action(snapshot))
        owned.add("clone")
        _apply(session, workflows.snapshot_clone_action(snapshot, clone))
        _apply(session, workflows.snapshot_rollback_action(snapshot), snapshot)

        owned.add("user")
        _apply(session, workflows.user_create_action({
            "username": user, "full_name": f"tnt live {suffix}", "group": group_id,
            # TrueNAS requires an existing writable home before accepting sshpubkey updates.
            "home": f"/mnt/{dataset}", "home_create": True,
            "password_disabled": True, "smb": False,
        }))
        _apply(session, workflows.user_update_action(user, {"full_name": f"tnt live {suffix} updated"}))
        _apply(session, workflows.user_ssh_key_action(user, validate_ssh_public_keys(_public_test_key())))
        _apply(session, workflows.user_access_action(user, {"password_disabled": False}), user)
        _apply(session, workflows.user_password_action(user, Secret(secrets.token_urlsafe(32))))

        owned.add("container")
        container_job_progress: list[dict] = []
        _apply(session, workflows.container_create_action({
            "name": prefix, "pool": pool_name,
            "image": {"name": image_name, "version": image_version},
            "description": prefix, "autostart": False,
        }), progress_events=container_job_progress)
        container_job = next((row for row in session.job_registry.snapshot()
                              if row["method"] == "container.create"), None)
        assert container_job is not None and container_job["state"] == "SUCCESS", (
            "container create job did not reach a recorded successful state"
        )
        assert any(event["kind"] in {"state", "progress"} for event in container_job["events"]), (
            "container create job did not record lifecycle progress"
        )
        assert any(isinstance(event, dict) for event in container_job_progress), (
            "container create job did not invoke the progress callback"
        )
        _apply(session, workflows.container_update_action(prefix, {"autostart": True}))
        _apply(session, workflows.container_lifecycle_action(prefix, "start"))
        _wait_for_state(workflows, "container", prefix, "RUNNING")
        _apply(session, workflows.container_lifecycle_action(prefix, "restart"), prefix)
        _apply(session, workflows.container_lifecycle_action(prefix, "stop"), prefix)

        vm_supported = session.rpc.call("vm.supports_virtualization")
        assert isinstance(vm_supported, bool), "vm.supports_virtualization must return a boolean"
        if vm_supported:
            owned.add("vm")
            _apply(session, workflows.vm_create_action({"name": vm_name, "memory": memory_mib, "vcpus": 1,
                                                        "autostart": False}))
            _apply(session, workflows.vm_device_create_action(vm_name, {
                "dtype": "DISK", "create_zvol": True, "zvol_name": disk_zvol, "zvol_volsize": disk_size,
            }))
            _apply(session, workflows.vm_update_action(vm_name, {"memory": memory_mib}))
            workflows.vm_console(vm_name)
            _apply(session, workflows.vm_lifecycle_action(vm_name, "start"))
            _wait_for_state(workflows, "vm", vm_name, "RUNNING")
            _apply(session, workflows.vm_lifecycle_action(vm_name, "stop"), vm_name)
            _wait_for_state(workflows, "vm", vm_name, "STOPPED")

        assert workflows.dataset(dataset)["id"] == dataset
        assert workflows.dataset(clone)["id"] == clone
        assert workflows.user(user)["username"] == user
        assert workflows.container(prefix)["name"] == prefix
        if vm_supported:
            assert workflows.vm(vm_name)["name"] == vm_name
    finally:
        # Each cleanup operation is itself a guarded plan. Continue through all
        # resources so one host-side cleanup failure cannot strand the rest.
        def cleanup(label: str, operation) -> None:
            try:
                operation()
            except Exception as exc:
                cleanup_errors.append(label + ": " + type(exc).__name__)

        def cleanup_vm() -> None:
            if "vm" in owned and _exists(session, "vm.query", "name", vm_name):
                row = workflows.vm(vm_name)
                state = row.get("status", {}).get("state") if isinstance(row.get("status"), dict) else None
                if state and state.casefold() != "stopped":
                    _apply(session, workflows.vm_lifecycle_action(vm_name, "stop"), vm_name)
                _apply(session, workflows.vm_delete_action(vm_name, delete_zvols=True), vm_name)

        def cleanup_container() -> None:
            if "container" in owned and _exists(session, "container.query", "name", prefix):
                row = workflows.container(prefix)
                state = row.get("status", {}).get("state") if isinstance(row.get("status"), dict) else None
                if state and state.casefold() != "stopped":
                    _apply(session, workflows.container_lifecycle_action(prefix, "stop"), prefix)
                _apply(session, workflows.container_delete_action(prefix), prefix)

        def cleanup_user() -> None:
            if "user" in owned and _exists(session, "user.query", "username", user):
                _apply(session, workflows.user_delete_action(user), user)

        def cleanup_clone() -> None:
            if "clone" in owned and _exists(session, "pool.dataset.query", "id", clone):
                _apply(session, workflows.dataset_delete_action(clone, recursive=True), clone)

        def cleanup_snapshot() -> None:
            snapshot = f"{dataset}@{prefix}"
            if "snapshot" in owned and _exists(session, "pool.snapshot.query", "id", snapshot):
                _apply(session, workflows.snapshot_delete_action(snapshot), snapshot)

        def cleanup_dataset() -> None:
            if "dataset" in owned and _exists(session, "pool.dataset.query", "id", dataset):
                _apply(session, workflows.dataset_delete_action(dataset, recursive=True), dataset)

        cleanup("VM cleanup", cleanup_vm)
        cleanup("container cleanup", cleanup_container)
        cleanup("user cleanup", cleanup_user)
        cleanup("clone cleanup", cleanup_clone)
        cleanup("snapshot cleanup", cleanup_snapshot)
        cleanup("dataset cleanup", cleanup_dataset)
        try:
            session.close()
        except Exception as exc:
            cleanup_errors.append("session close: " + type(exc).__name__)
        assert not cleanup_errors, "live cleanup failed: " + "; ".join(cleanup_errors)


def test_v27_m2_views_shares_data_protection_api_keys_tui_cli_and_ssh():
    assert os.environ.get("TNT_LIVE") == "1", "selecting the live lane requires TNT_LIVE=1"
    profile_name = _required("TNT_LIVE_PROFILE")
    pool_name = _required("TNT_LIVE_POOL")
    paths = resolve_paths()
    profiles, _, entries = load_host_store(paths, require_secrets=True)
    assert profile_name in profiles, "the selected live profile is absent"
    profile = profiles[profile_name]
    assert profile_name + "-alt" in profiles, "the live config must include its second same-host alias"
    alias = profiles[profile_name + "-alt"]
    assert alias.url == profile.url, "the secondary profile must target the same appliance"
    assert pool_name != "boot-pool", "M2 live checks require the disposable data pool"
    insecure_ack = os.environ.get("TNT_LIVE_INSECURE") == "1"
    session = open_session(profile, secret=read_credential(profile, entries), allow_write=True,
                           insecure_ack=insecure_ack)
    workflows = session.workflows
    suffix = uuid.uuid4().hex[:12]
    prefix = f"tnt-live-m2-{suffix}"
    dataset = f"{pool_name}/{prefix}"
    path = f"/mnt/{dataset}"
    smb_name = prefix + "-smb"
    key_name = prefix + "-key"
    renamed_key = prefix + "-renamed"
    key_path = paths.config.parent / f"{prefix}.api-key"
    config_export_path = paths.config.parent / f"{prefix}-config-export.tar"
    ssh_home = paths.config.parent / f"{prefix}-ssh-home"
    owned: set[str] = set()
    cleanup_errors: list[str] = []
    config_before = paths.config.read_bytes()
    secrets_before = paths.secrets.read_bytes()
    try:
        assert any(row.get("name") == pool_name for row in _query_rows(session, "pool.query")), (
            "the selected pool is not visible to the live profile"
        )

        # The same schema-checked models back these screens and their CLI verbs.
        assert session.read_views.dashboard()["host"]["version"] == "27.0.0-RC.1"
        alerts = session.read_views.alerts()
        session.read_views.jobs(limit=50)
        session.read_views.services()
        storage = session.read_views.storage_health()
        assert any(row.get("name") == pool_name for row in storage["pools"])
        network = workflows.network_show()
        assert set(network) == {"interfaces", "addresses", "routes", "dns", "default_gateways"}
        workflows.apps()
        audit_rows = workflows.audit_entries(service="MIDDLEWARE", limit=100, offset=0)
        workflows.replication_tasks()
        cloud_tasks = workflows.cloudsync_tasks()
        for row in cloud_tasks:
            for field in ("credentials", "encryption_password", "encryption_salt"):
                if field in row and row[field] not in (None, "***"):
                    raise AssertionError("cloud-sync credential data was not masked")
        api_credential = read_credential(profile, entries).reveal()
        safe_payloads = json.dumps([alerts, audit_rows, cloud_tasks], sort_keys=True, default=str)
        if api_credential and api_credential in safe_payloads:
            raise AssertionError("a read-only view returned the active API credential")
        assert session.rpc.call("system.version")
        assert session.rpc.call("system.state")

        # Verify the CLI JSON readers through fresh real API sessions.
        cli_views = (
            ("dashboard",), ("alerts", "list"), ("jobs", "list"), ("services",),
            ("storage", "health"), ("network", "show"), ("app", "list"),
            ("audit", "--service", "MIDDLEWARE", "--limit", "100", "--offset", "0"),
            ("apikey", "list"), ("task", "replication", "list"), ("task", "cloudsync", "list"),
        )
        for command in cli_views:
            result = _cli_json(profile_name, *command)
            assert isinstance(result, (dict, list)), "live CLI reader returned an unexpected JSON shape"

        if config_export_path.exists():
            raise AssertionError("refusing to reuse the private configuration-export path")
        export_command = [sys.executable, "-m", "tnt", "system", "config-export", str(config_export_path),
                          "--host", profile_name, "--insecure", "--allow-write"]
        export_result = subprocess.run(export_command, capture_output=True, text=True, timeout=120, check=False)
        assert export_result.returncode == 0, "live configuration export command failed"
        assert config_export_path.is_file() and stat.S_IMODE(config_export_path.stat().st_mode) == 0o600, (
            "live configuration export did not create a private mode-0600 file"
        )
        assert tarfile.is_tarfile(config_export_path), "live configuration export was not a tar archive"
        config_export_path.unlink()

        owned.add("dataset")
        _apply(session, workflows.dataset_create_action({
            "name": dataset, "type": "FILESYSTEM", "comments": prefix,
        }))
        assert workflows.dataset(dataset)["id"] == dataset

        owned.add("smb")
        _apply(session, workflows.share_smb_create_action({
            "name": smb_name, "path": path, "purpose": "DEFAULT_SHARE", "enabled": True,
            "readonly": True, "comment": prefix,
            "options": {"hostsallow": ["127.0.0.1"], "hostsdeny": []},
        }))
        smb = workflows.share_smb(smb_name)
        assert isinstance(smb.get("acl"), dict), "SMB ACL read did not return an object"
        smb_delete = workflows.share_smb_delete_action(smb_name)
        _apply(session, smb_delete, smb_delete.confirm_target)
        assert workflows.dataset(dataset)["id"] == dataset, "deleting a share removed its underlying dataset"

        owned.add("nfs")
        _apply(session, workflows.share_nfs_create_action({
            "path": path, "networks": ["127.0.0.1/32"], "hosts": [], "maproot_user": "root",
            "security": ["SYS"], "ro": True,
        }))
        assert workflows.share_nfs(path)["path"] == path
        nfs_delete = workflows.share_nfs_delete_action(path)
        _apply(session, nfs_delete, nfs_delete.confirm_target)
        assert workflows.dataset(dataset)["id"] == dataset, "deleting a share removed its underlying dataset"

        owned.add("snapshot-task")
        _apply(session, workflows.snapshot_task_create_action({
            "dataset": dataset,
            "schedule": {"minute": "00", "hour": "02", "dom": "*", "month": "*", "dow": "*"},
            "lifetime_value": 1, "lifetime_unit": "DAY", "naming_schema": prefix + "-%Y-%m-%d_%H-%M",
            "allow_empty": True, "enabled": True,
        }))
        task_rows = [row for row in workflows.snapshot_tasks() if row.get("dataset") == dataset]
        assert len(task_rows) == 1, "periodic snapshot task was not visible after create"
        assert task_rows[0].get("enabled") is True, "periodic snapshot task was not enabled"
        task_id = task_rows[0].get("id")
        assert isinstance(task_id, int) and not isinstance(task_id, bool), "snapshot task ID is invalid"
        _apply(session, workflows.snapshot_task_run_action(task_id))
        task_snapshots = []
        snapshot_deadline = time.monotonic() + 60
        while time.monotonic() < snapshot_deadline:
            task_snapshots = [row for row in _query_rows(session, "pool.snapshot.query", [["dataset", "=", dataset]])
                              if isinstance(row.get("id"), str)
                              and row["id"].startswith(dataset + "@" + prefix + "-")]
            if task_snapshots:
                break
            time.sleep(1)
        assert task_snapshots, "snapshot task run did not create its scheduled snapshot within 60 seconds"
        _apply(session, workflows.snapshot_task_enable_action(task_id, False))
        assert workflows.snapshot_task(task_id)["enabled"] is False
        task_delete = workflows.snapshot_task_delete_action(task_id)
        _apply(session, task_delete, task_delete.confirm_target)
        assert not any(row.get("dataset") == dataset for row in workflows.snapshot_tasks())

        # Store the one-time API-key response immediately in an exclusive 0600 file.
        # The secret value is never printed, asserted in a message, or added to host config.
        if key_path.exists():
            raise AssertionError("refusing to reuse the private API-key output path")
        owned.add("api-key")
        key_result = _apply(session, workflows.api_key_create_action(key_name, profile.username))
        key_entry = key_result[0] if key_result else None
        raw_key = key_entry.get("key") if isinstance(key_entry, dict) else None
        if not isinstance(raw_key, str) or not raw_key:
            raise AssertionError("TrueNAS did not return the one-time API-key value")
        write_api_key_file(str(key_path), raw_key)
        key_result = None
        key_entry = None
        raw_key = None
        if stat.S_IMODE(key_path.stat().st_mode) != 0o600 or not key_path.read_bytes().strip():
            raise AssertionError("the one-time API key was not saved to a non-empty mode-0600 file")
        created_rows = [row for row in workflows.api_keys() if row.get("name") == key_name]
        assert len(created_rows) == 1, "created API key was not visible in the safe key listing"
        key_id = created_rows[0]["id"]
        listed = _cli_json(profile_name, "apikey", "list")
        assert isinstance(listed, list) and any(row.get("name") == key_name for row in listed), (
            "CLI key list did not show the temporary key metadata"
        )
        assert all(not (set(row) & {"key", "client_key", "salt", "stored_key", "server_key"})
                   for row in listed if isinstance(row, dict)), "CLI key list exposed verifier material"
        _apply(session, workflows.api_key_rename_action(key_id, renamed_key))
        assert any(row.get("name") == renamed_key for row in workflows.api_keys())
        revoke = workflows.api_key_revoke_action(key_id)
        _apply(session, revoke, revoke.confirm_target)
        assert not any(row.get("id") == key_id for row in workflows.api_keys())
        owned.remove("api-key")
        key_path.unlink()

        # Pilot exercises live TUI loading, same-host alias switching, and the SSH hand-off boundary.
        tui_session = open_session(profile, secret=read_credential(profile, entries), allow_write=False,
                                   insecure_ack=insecure_ack)
        app = TntApp(tui_session, hosts=profiles, default_host=profile_name, secret_entries=entries,
                     host_paths=paths, insecure_ack=insecure_ack)
        menu_items = {item.key: item for _, item in walk()}

        async def drive_tui() -> None:
            async with app.run_test(size=(180, 55)) as pilot:
                for key in ("dashboard", "alerts", "jobs", "services", "storage/health",
                            "sharing", "snapshot-tasks", "apps", "network", "audit", "api-keys"):
                    content = app.query_one("#content")
                    previous = tuple(content.children)
                    app.show(menu_items[key])
                    await _wait_for_pilot(
                        pilot, lambda content=content, previous=previous: bool(content.children)
                        and content.children[0] not in previous,
                        f"TUI {key} view did not replace the previous view")
                    view = content.children[0]
                    if isinstance(view, CoreReadView):
                        await _wait_for_pilot(pilot, lambda view=view: view.last_success is not None,
                                              "TUI read-only overview did not load")
                    elif key == "network":
                        await _wait_for_pilot(pilot, lambda view=view: (note := _view_widget(view, "network-note")) is not None
                                              and "status loaded" in str(note.render()).casefold(),
                            "TUI network view did not load")
                    elif key == "audit":
                        await _wait_for_pilot(pilot, lambda view=view: (note := _view_widget(view, "audit-note")) is not None
                                              and "audit event(s)" in str(note.render()),
                                              "TUI audit view did not load")
                    elif key == "api-keys":
                        await _wait_for_pilot(pilot, lambda view=view: (note := _view_widget(view, "apikey-note")) is not None
                                              and "API key(s)" in str(note.render()),
                                              "TUI API-key view did not load")
                    else:
                        await _wait_for_pilot(pilot, lambda view=view: (note := _view_widget(view, "workflow-note")) is not None
                                              and " loaded." in str(note.render()),
                                              "TUI workflow view did not load")

                content = app.query_one("#content")
                previous = tuple(content.children)
                app.show(menu_items["dashboard"])
                await _wait_for_pilot(
                    pilot, lambda: bool(content.children) and content.children[0] not in previous,
                    "TUI dashboard did not remount before host switching")
                dashboard = content.children[0]
                await _wait_for_pilot(
                    pilot, lambda dashboard=dashboard: isinstance(dashboard, CoreReadView)
                    and dashboard.last_success is not None,
                    "TUI dashboard did not finish loading before host switching")
                await pilot.press("h")
                await _wait_for_pilot(pilot, lambda: any(isinstance(child, HostPicker)
                                                          for child in content.children),
                                      "TUI host picker did not open")
                picker = app.query_one("HostPicker")
                table = picker.query_one("#hosts-table", DataTable)
                host_rows = [str(key.value) for key in table.rows]
                table.move_cursor(row=host_rows.index(alias.name), column=0)
                await pilot.click("#hosts-switch")
                await _wait_for_host_switch(pilot, app, content, alias.name)
                assert app.current == "dashboard", "TUI did not restore the active dashboard after host switch"
                dashboard = content.children[0]
                await _wait_for_pilot(
                    pilot, lambda dashboard=dashboard: isinstance(dashboard, CoreReadView)
                    and dashboard.last_success is not None,
                    "TUI dashboard did not load after switching to the same-host alias")

                await pilot.press("h")
                await _wait_for_pilot(pilot, lambda: any(isinstance(child, HostPicker)
                                                          for child in content.children),
                                      "TUI host picker did not reopen")
                picker = app.query_one("HostPicker")
                table = picker.query_one("#hosts-table", DataTable)
                host_rows = [str(key.value) for key in table.rows]
                table.move_cursor(row=host_rows.index(profile_name), column=0)
                await pilot.click("#hosts-switch")
                await _wait_for_host_switch(pilot, app, content, profile_name)
                dashboard = content.children[0]
                await _wait_for_pilot(
                    pilot, lambda dashboard=dashboard: isinstance(dashboard, CoreReadView)
                    and dashboard.last_success is not None,
                    "TUI dashboard did not load after switching back to the original host alias")

                suspension: list[str] = []
                handed_off: list[str] = []

                @contextmanager
                def fake_suspend():
                    suspension.append("suspended")
                    try:
                        yield
                    finally:
                        suspension.append("restored")

                import tnt.ssh as ssh_module
                with pytest.MonkeyPatch.context() as patch:
                    patch.setattr(ssh_module, "run_interactive_ssh",
                                  lambda selected: handed_off.append(selected.name) or 0)
                    patch.setattr(app, "suspend", fake_suspend)
                    await pilot.press("s")
                assert suspension == ["suspended", "restored"], "TUI did not restore after SSH hand-off"
                assert handed_off == [profile_name], "TUI SSH action did not use the selected host"

        try:
            asyncio.run(drive_tui())
        finally:
            app.session_controller.close()

        # An actual PTY hand-off verifies strict pinned-host checking and that HOME/.ssh/config is ignored.
        if not profile.ssh_key_files or not profile.ssh_known_hosts:
            raise AssertionError("the live profile lacks its pinned SSH identity or known-hosts file")
        ssh_home.mkdir(mode=0o700)
        ssh_dir = ssh_home / ".ssh"
        ssh_dir.mkdir(mode=0o700)
        marker = ssh_home / "unexpected-user-ssh-config"
        ssh_config = ssh_dir / "config"
        ssh_config.write_text(f"Host *\n    ProxyCommand /usr/bin/touch {marker}\n", encoding="utf-8")
        ssh_config.chmod(0o600)
        hostile_env = dict(os.environ)
        hostile_env["HOME"] = str(ssh_home)
        master, slave = pty.openpty()
        process = subprocess.Popen(
            [sys.executable, "-m", "tnt", "ssh", profile_name, "--config", str(paths.config)],
            stdin=slave, stdout=slave, stderr=slave, env=hostile_env, close_fds=True,
        )
        os.close(slave)
        try:
            os.write(master, b"exit\n")
            deadline = time.monotonic() + 60
            while process.poll() is None and time.monotonic() < deadline:
                readable, _, _ = select.select([master], [], [], 0.2)
                if readable:
                    try:
                        os.read(master, 4096)  # discard terminal output; it may contain host banners
                    except OSError:
                        break
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                raise AssertionError("interactive SSH did not return within 60 seconds")
            assert process.returncode == 0, "interactive SSH did not preserve a successful exit status"
            if marker.exists():
                raise AssertionError("OpenSSH unexpectedly read the hostile user SSH configuration")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            os.close(master)
            shutil.rmtree(ssh_home, ignore_errors=True)

        assert paths.config.read_bytes() == config_before, "live M2 checks changed the non-secret host config"
        assert paths.secrets.read_bytes() == secrets_before, "live M2 checks changed the paired secrets config"
    finally:
        def cleanup(label: str, operation) -> None:
            try:
                operation()
            except Exception as exc:
                cleanup_errors.append(label + ": " + type(exc).__name__)

        def cleanup_api_key() -> None:
            rows = [row for row in workflows.api_keys() if row.get("name") in {key_name, renamed_key}]
            for row in rows:
                action = workflows.api_key_revoke_action(row["id"])
                _apply(session, action, action.confirm_target)
            try:
                key_path.unlink()
            except FileNotFoundError:
                pass

        def cleanup_config_export() -> None:
            try:
                config_export_path.unlink()
            except FileNotFoundError:
                pass

        def cleanup_snapshot_tasks() -> None:
            rows = [row for row in workflows.snapshot_tasks() if row.get("dataset") == dataset]
            for row in rows:
                action = workflows.snapshot_task_delete_action(row["id"])
                _apply(session, action, action.confirm_target)

        def cleanup_shares() -> None:
            if _exists(session, "sharing.nfs.query", "path", path):
                action = workflows.share_nfs_delete_action(path)
                _apply(session, action, action.confirm_target)
            if _exists(session, "sharing.smb.query", "name", smb_name):
                action = workflows.share_smb_delete_action(smb_name)
                _apply(session, action, action.confirm_target)

        def cleanup_dataset() -> None:
            if "dataset" in owned and _exists(session, "pool.dataset.query", "id", dataset):
                snapshots = [row for row in _query_rows(session, "pool.snapshot.query")
                             if isinstance(row.get("id"), str) and row["id"].startswith(dataset + "@")]
                for row in snapshots:
                    action = workflows.snapshot_delete_action(row["id"])
                    _apply(session, action, action.confirm_target)
                _apply(session, workflows.dataset_delete_action(dataset, recursive=True), dataset)

        cleanup("API-key cleanup", cleanup_api_key)
        cleanup("configuration-export cleanup", cleanup_config_export)
        cleanup("snapshot-task cleanup", cleanup_snapshot_tasks)
        cleanup("share cleanup", cleanup_shares)
        cleanup("dataset cleanup", cleanup_dataset)
        try:
            session.close()
        except Exception as exc:
            cleanup_errors.append("session close: " + type(exc).__name__)
        assert not cleanup_errors, "M2 live cleanup failed: " + "; ".join(cleanup_errors)
