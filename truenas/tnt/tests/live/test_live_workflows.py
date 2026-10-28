"""Explicit, namespaced acceptance probe for an operator-provided v27 host.

This module is selected only with ``-m live``. It never skips: selecting it
without all prerequisites is a failure, and it creates and removes only
resources whose names begin with ``tnt-live-``.
"""
from __future__ import annotations

import base64
import os
import secrets
import struct
import time
import uuid
from pathlib import Path

import pytest

from tnt.actions import apply_plan, make_plan
from tnt.profile import Secret, read_credential, resolve_paths
from tnt.host_store import load_host_store
from tnt.session import open_session
from tnt.workflows import validate_ssh_public_keys


pytestmark = pytest.mark.live


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    assert value, f"live lane requires {name}"
    return value


def _apply(session, action, confirmation: str | None = None):
    plan = make_plan(action, session.rpc)
    print(plan.render(), flush=True)
    return apply_plan(plan, session.rpc, allow_write=True, typed_confirmation=confirmation)


def _wait_for_state(workflows, family: str, name: str, state: str) -> dict:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        row = getattr(workflows, family)(name)
        observed = row.get("status", {}).get("state") if isinstance(row.get("status"), dict) else None
        if isinstance(observed, str) and observed.casefold() == state.casefold():
            return row
        time.sleep(1)
    raise AssertionError(f"{family} {name!r} did not reach {state} within 45 seconds")


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
            ("user.query", "username", user), ("container.query", "name", prefix), ("vm.query", "name", prefix),
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
            "password_disabled": True, "smb": False,
        }))
        _apply(session, workflows.user_update_action(user, {"full_name": f"tnt live {suffix} updated"}))
        _apply(session, workflows.user_ssh_key_action(user, validate_ssh_public_keys(_public_test_key())))
        _apply(session, workflows.user_password_action(user, Secret(secrets.token_urlsafe(32))))

        owned.add("container")
        _apply(session, workflows.container_create_action({
            "name": prefix, "pool": pool_name,
            "image": {"name": image_name, "version": image_version},
            "description": prefix, "autostart": False,
        }))
        _apply(session, workflows.container_update_action(prefix, {"autostart": True}))
        _apply(session, workflows.container_lifecycle_action(prefix, "start"))
        _wait_for_state(workflows, "container", prefix, "RUNNING")
        _apply(session, workflows.container_lifecycle_action(prefix, "restart"), prefix)
        _apply(session, workflows.container_lifecycle_action(prefix, "stop"), prefix)

        owned.add("vm")
        _apply(session, workflows.vm_create_action({"name": prefix, "memory": memory_mib, "vcpus": 1,
                                                    "autostart": False}))
        _apply(session, workflows.vm_device_create_action(prefix, {
            "dtype": "DISK", "create_zvol": True, "zvol_name": disk_zvol, "zvol_volsize": disk_size,
        }))
        _apply(session, workflows.vm_update_action(prefix, {"memory": memory_mib}))
        workflows.vm_console(prefix)
        _apply(session, workflows.vm_lifecycle_action(prefix, "start"))
        _wait_for_state(workflows, "vm", prefix, "RUNNING")
        _apply(session, workflows.vm_lifecycle_action(prefix, "stop"), prefix)
        _wait_for_state(workflows, "vm", prefix, "STOPPED")

        assert workflows.dataset(dataset)["id"] == dataset
        assert workflows.dataset(clone)["id"] == clone
        assert workflows.user(user)["username"] == user
        assert workflows.container(prefix)["name"] == prefix
        assert workflows.vm(prefix)["name"] == prefix
    finally:
        # Each cleanup operation is itself a guarded plan. Continue through all
        # resources so one host-side cleanup failure cannot strand the rest.
        def cleanup(label: str, operation) -> None:
            try:
                operation()
            except Exception as exc:
                cleanup_errors.append(label + ": " + type(exc).__name__)

        def cleanup_vm() -> None:
            if "vm" in owned and _exists(session, "vm.query", "name", prefix):
                row = workflows.vm(prefix)
                state = row.get("status", {}).get("state") if isinstance(row.get("status"), dict) else None
                if state and state.casefold() != "stopped":
                    _apply(session, workflows.vm_lifecycle_action(prefix, "stop"), prefix)
                _apply(session, workflows.vm_delete_action(prefix, delete_zvols=True), prefix)

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
