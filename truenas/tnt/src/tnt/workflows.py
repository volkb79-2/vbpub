"""Shared query and action builders for the CLI and TUI core workflows."""
from __future__ import annotations

import base64
import binascii
import re
import struct
import threading
from typing import Any, Mapping

from .actions import Action, Kind, RpcCall
from .errors import Refused, TransportError
from .policy import _ambiguous_path, _names_boot_pool, _strings, refusal_for
from .profile import Secret
from .transport import GuardedRpc


class Workflows:
    """Domain queries and plan descriptions built only from a session's guarded RPC object."""

    def __init__(self, rpc: GuardedRpc):
        self.rpc = rpc

    # -- SMB and NFS shares -----------------------------------------------------------------------------
    def shares_smb(self) -> list[dict[str, Any]]:
        rows = _rows(self.rpc.call("sharing.smb.query", [], {}), "sharing.smb.query")
        for row in rows:
            name = row.get("name")
            if isinstance(name, str):
                row["acl"] = self.rpc.call("sharing.smb.getacl", {"share_name": name})
        return rows

    def share_smb(self, name: str) -> dict[str, Any]:
        row = self._named("sharing.smb.query", name, "SMB share")
        row["acl"] = self.rpc.call("sharing.smb.getacl", {"share_name": name})
        return row

    def shares_nfs(self) -> list[dict[str, Any]]:
        return _rows(self.rpc.call("sharing.nfs.query", [], {}), "sharing.nfs.query")

    def share_nfs(self, path: str) -> dict[str, Any]:
        if not isinstance(path, str) or not path.strip():
            raise Refused("workflow-input", "NFS share path is required")
        rows = _rows(self.rpc.call("sharing.nfs.query", [["path", "=", path]], {}), "sharing.nfs.query")
        if len(rows) != 1 or rows[0].get("path") != path:
            raise Refused("workflow-lookup", f"expected exactly one NFS share at {path!r}; found {len(rows)}")
        return rows[0]

    def share_smb_create_action(self, data: Mapping[str, Any]) -> Action:
        payload = dict(data)
        name = self._required_string(payload, "name", "SMB share name")
        path = self._required_string(payload, "path", "SMB share path")
        purpose = payload.get("purpose", "DEFAULT_SHARE")
        if purpose not in {"DEFAULT_SHARE", "LEGACY_SHARE", "TIMEMACHINE_SHARE", "MULTIPROTOCOL_SHARE",
                           "TIME_LOCKED_SHARE", "PRIVATE_DATASETS_SHARE", "VEEAM_REPOSITORY_SHARE", "FCP_SHARE"}:
            raise Refused("smb-purpose", "purpose must be a supported v27 SMB share preset")
        self._validate_share_path(path)
        options = dict(payload.pop("options", {}) or {})
        guest = payload.pop("guest", None)
        if guest is not None:
            if not isinstance(guest, bool):
                raise Refused("smb-guest", "guest must be true or false")
            if guest and purpose != "LEGACY_SHARE":
                raise Refused("smb-guest", "v27 exposes guest access only for LEGACY_SHARE purpose")
            if purpose == "LEGACY_SHARE":
                options["guestok"] = guest
        for key in ("hostsallow", "hostsdeny"):
            if key in payload:
                options[key] = payload.pop(key)
        if not isinstance(options.get("hostsallow", []), list) or not isinstance(options.get("hostsdeny", []), list):
            raise Refused("smb-hosts", "hostsallow and hostsdeny must be arrays of strings")
        if any(not isinstance(item, str) or not item.strip() for key in ("hostsallow", "hostsdeny") for item in options.get(key, [])):
            raise Refused("smb-hosts", "hostsallow and hostsdeny entries must be non-empty strings")
        payload["purpose"] = purpose
        payload["options"] = options
        payload["path"] = path
        # Probe now to translate only a confirmed remote missing-path response into the actionable refusal.
        self._remote_path_stat(path)
        return Action("TNT-42", Kind.MUTATE, f"Create SMB share {name}",
                      (RpcCall("sharing.smb.create", (payload,)),),
                      (RpcCall("filesystem.stat", (path,)), RpcCall("sharing.smb.query", ([["name", "=", name]], {}))))

    def share_nfs_create_action(self, data: Mapping[str, Any]) -> Action:
        payload = dict(data)
        path = self._required_string(payload, "path", "NFS share path")
        self._validate_share_path(path)
        self._remote_path_stat(path)
        payload["path"] = path
        if "maprootuser" in payload and "maproot_user" not in payload:
            payload["maproot_user"] = payload.pop("maprootuser")
        security = payload.get("security", [])
        if not isinstance(security, list) or any(item not in {"SYS", "KRB5", "KRB5I", "KRB5P"} for item in security):
            raise Refused("nfs-security", "security must contain only SYS, KRB5, KRB5I, or KRB5P")
        return Action("TNT-42", Kind.MUTATE, f"Create NFS share {path}",
                      (RpcCall("sharing.nfs.create", (payload,)),),
                      (RpcCall("filesystem.stat", (path,)), RpcCall("sharing.nfs.query", ([["path", "=", path]], {}))))

    def share_smb_delete_action(self, name: str) -> Action:
        row = self._named("sharing.smb.query", name, "SMB share")
        return Action("TNT-42", Kind.DESTRUCTIVE, f"Delete SMB share {name} (share only; data is retained)",
                      (RpcCall("sharing.smb.delete", (row["id"],)),),
                      (RpcCall("sharing.smb.query", ([["name", "=", name]], {})),), name)

    def share_nfs_delete_action(self, path: str) -> Action:
        row = self.share_nfs(path)
        return Action("TNT-42", Kind.DESTRUCTIVE, f"Delete NFS share {path} (share only; data is retained)",
                      (RpcCall("sharing.nfs.delete", (row["id"],)),),
                      (RpcCall("sharing.nfs.query", ([["path", "=", path]], {})),), path)

    # -- scheduled protection tasks --------------------------------------------------------------------
    def snapshot_tasks(self) -> list[dict[str, Any]]:
        return _rows(self.rpc.call("pool.snapshottask.query", [], {}), "pool.snapshottask.query")

    def snapshot_task(self, task_id: int) -> dict[str, Any]:
        return self._task_by_id("pool.snapshottask.get_instance", task_id, "periodic snapshot task")

    def snapshot_task_create_action(self, data: Mapping[str, Any]) -> Action:
        payload = dict(data)
        dataset = self._required_string(payload, "dataset", "snapshot task dataset")
        self._refuse_boot_pool("pool.snapshottask.create", (payload,))
        schedule = payload.get("schedule")
        schedule_keys = {"minute", "hour", "dom", "month", "dow", "begin", "end"}
        if (not isinstance(schedule, Mapping) or not schedule
                or set(schedule) - schedule_keys
                or any(not isinstance(value, str) or not value.strip() for value in schedule.values())):
            raise Refused("snapshot-task-schedule", "provide a non-empty v27 cron schedule with string values")
        lifetime = payload.get("lifetime_value")
        if not isinstance(lifetime, int) or isinstance(lifetime, bool) or lifetime < 1:
            raise Refused("snapshot-task-retention", "lifetime_value must be a positive integer")
        if payload.get("lifetime_unit") not in {"HOUR", "DAY", "WEEK", "MONTH", "YEAR"}:
            raise Refused("snapshot-task-retention", "lifetime_unit must be HOUR, DAY, WEEK, MONTH, or YEAR")
        naming_schema = payload.get("naming_schema")
        if not isinstance(naming_schema, str) or not naming_schema.strip() or not (
                "%s" in naming_schema or all(token in naming_schema for token in ("%Y", "%m", "%d", "%H", "%M"))):
            raise Refused("snapshot-task-naming-schema", "naming_schema must contain %Y, %m, %d, %H, and %M, or %s")
        allowed = {"dataset", "recursive", "lifetime_value", "lifetime_unit", "enabled", "exclude",
                   "naming_schema", "allow_empty", "schedule"}
        if set(payload) - allowed:
            raise Refused("snapshot-task-fields", f"unsupported v27 snapshot task fields: {sorted(set(payload) - allowed)}")
        self.rpc.call("pool.dataset.get_instance", dataset)
        return Action("TNT-43", Kind.MUTATE, f"Create periodic snapshot task for {dataset}",
                      (RpcCall("pool.snapshottask.create", (payload,)),),
                      (RpcCall("pool.dataset.get_instance", (dataset,)),))

    def snapshot_task_enable_action(self, task_id: int, enabled: bool) -> Action:
        if not isinstance(enabled, bool):
            raise Refused("snapshot-task-enabled", "enabled must be true or false")
        row = self.snapshot_task(task_id)
        return Action("TNT-43", Kind.MUTATE, f"{'Enable' if enabled else 'Disable'} snapshot task {row['dataset']} (ID {task_id})",
                      (RpcCall("pool.snapshottask.update", (task_id, {"enabled": enabled})),),
                      (RpcCall("pool.snapshottask.get_instance", (task_id,)),))

    def snapshot_task_run_action(self, task_id: int) -> Action:
        row = self.snapshot_task(task_id)
        return Action("TNT-43", Kind.MUTATE, f"Run snapshot task for {row['dataset']} now",
                      (RpcCall("pool.snapshottask.run", (task_id,), job=True),),
                      (RpcCall("pool.snapshottask.get_instance", (task_id,)),))

    def snapshot_task_delete_action(self, task_id: int) -> Action:
        row = self.snapshot_task(task_id)
        target = f"{row['dataset']} [{task_id}]"
        return Action("TNT-43", Kind.DESTRUCTIVE, f"Delete periodic snapshot task for {row['dataset']}",
                      (RpcCall("pool.snapshottask.delete", (task_id,)),),
                      (RpcCall("pool.snapshottask.get_instance", (task_id,)),), target)

    def replication_tasks(self) -> list[dict[str, Any]]:
        rows = _rows(self.rpc.call("replication.query", [], {}), "replication.query")
        return [_safe_value(row) for row in rows]

    def replication_task(self, name: str) -> dict[str, Any]:
        return _safe_value(self._named("replication.query", name, "replication task"))

    def replication_enable_action(self, name: str, enabled: bool) -> Action:
        if not isinstance(enabled, bool):
            raise Refused("replication-enabled", "enabled must be true or false")
        row = self._named("replication.query", name, "replication task")
        return Action("TNT-43", Kind.MUTATE, f"{'Enable' if enabled else 'Disable'} replication task {name}",
                      (RpcCall("replication.update", (row["id"], {"enabled": enabled})),),
                      (RpcCall("replication.get_instance", (row["id"],)),))

    def replication_run_action(self, name: str) -> Action:
        row = self._named("replication.query", name, "replication task")
        sources = row.get("source_datasets")
        target = row.get("target_dataset")
        if not isinstance(sources, list) or not sources or not isinstance(target, str) or not target:
            raise Refused("replication-target", "replication task does not report its source datasets and target")
        source_text = ", ".join(str(source) for source in sources)
        risk = " (allow_from_scratch may remove destination snapshots)" if row.get("allow_from_scratch") is True else ""
        return Action("TNT-43", Kind.DESTRUCTIVE, f"Run replication {name}: {source_text} -> {target}{risk}",
                      (RpcCall("replication.run", (row["id"],), job=True),),
                      (RpcCall("replication.get_instance", (row["id"],)),), name)

    def cloudsync_tasks(self) -> list[dict[str, Any]]:
        rows = _rows(self.rpc.call("cloudsync.query", [], {}), "cloudsync.query")
        return [_safe_value(row) for row in rows]

    def cloudsync_task(self, description: str) -> dict[str, Any]:
        if not isinstance(description, str) or not description.strip():
            raise Refused("workflow-input", "cloud-sync task description is required")
        rows = _rows(self.rpc.call("cloudsync.query", [["description", "=", description]], {}), "cloudsync.query")
        matches = [row for row in rows if row.get("description") == description]
        if len(matches) != 1:
            raise Refused("workflow-lookup", f"expected exactly one cloud-sync task named {description!r}; found {len(matches)}")
        return _safe_value(matches[0])

    def cloudsync_enable_action(self, description: str, enabled: bool) -> Action:
        if not isinstance(enabled, bool):
            raise Refused("cloudsync-enabled", "enabled must be true or false")
        row = self.cloudsync_task(description)
        return Action("TNT-43", Kind.MUTATE, f"{'Enable' if enabled else 'Disable'} cloud-sync task {description}",
                      (RpcCall("cloudsync.update", (row["id"], {"enabled": enabled})),),
                      (RpcCall("cloudsync.get_instance", (row["id"],)),))

    def cloudsync_run_action(self, description: str) -> Action:
        row = self.cloudsync_task(description)
        risk = " (MOVE deletes local source files after transfer)" if row.get("transfer_mode") == "MOVE" else ""
        return Action("TNT-43", Kind.DESTRUCTIVE, f"Run cloud-sync task {description}{risk}",
                      (RpcCall("cloudsync.sync", (row["id"], {"dry_run": False}), job=True),),
                      (RpcCall("cloudsync.get_instance", (row["id"],)),), description)

    def _task_by_id(self, method: str, task_id: int, label: str) -> dict[str, Any]:
        if not isinstance(task_id, int) or isinstance(task_id, bool) or task_id < 1:
            raise Refused("workflow-input", f"{label} ID must be a positive integer")
        row = self.rpc.call(method, task_id)
        if not isinstance(row, Mapping) or row.get("id") != task_id:
            raise Refused("unexpected-response", f"{method} returned an invalid {label}")
        return dict(row)

    def _validate_share_path(self, path: str) -> None:
        self._refuse_boot_pool("share", (path,))
        if not path.startswith("/mnt/") or path == "/mnt/":
            raise Refused("share-path", "share path must be an absolute path below /mnt/ on a data pool")

    def _remote_path_stat(self, path: str) -> dict[str, Any]:
        try:
            result = self.rpc.call("filesystem.stat", path)
        except TransportError as exc:
            message = str(exc).casefold()
            if any(marker in message for marker in ("no such file", "does not exist", "not found", "enoent")):
                raise Refused("share-path", f"share path {path!r} does not exist on the TrueNAS host; tnt never creates paths implicitly") from None
            raise
        if not isinstance(result, Mapping) or not isinstance(result.get("realpath"), str):
            raise Refused("unexpected-response", "filesystem.stat returned no canonical path for the share")
        if _names_boot_pool(result["realpath"]):
            raise Refused("refusal-list", "share path resolves under boot-pool, which is never shared from here")
        return dict(result)

    # -- users -------------------------------------------------------------------------------------------
    def users(self) -> list[dict[str, Any]]:
        return [_redact_public_user(row) for row in _rows(self.rpc.call("user.query", [], {}), "user.query")]

    def user(self, username: str) -> dict[str, Any]:
        row = self._named("user.query", username, "user")
        return _redact_public_user(row)

    def user_create_action(self, data: Mapping[str, Any]) -> Action:
        payload = dict(data)
        username = self._required_string(payload, "username", "username")
        self._required_string(payload, "full_name", "full name")
        if payload.get("group_create") is not True and (
                not isinstance(payload.get("group"), int) or isinstance(payload.get("group"), bool)):
            raise Refused("user-create", "provide a primary group ID or set group_create=true")
        if "password" in payload and not isinstance(payload["password"], Secret):
            raise Refused("secret-input", "user passwords must be provided as a protected Secret value")
        if not payload.get("password_disabled") and not payload.get("random_password") and "password" not in payload:
            raise Refused("user-create", "provide a protected password or set password_disabled=true")
        return Action(
            "TNT-41", Kind.MUTATE, f"Create user {username}",
            (RpcCall("user.create", (payload,)),),
            (RpcCall("user.query", ([["username", "=", username]], {})),),
        )

    def user_update_action(self, username: str, data: Mapping[str, Any], *, builtin: bool = False) -> Action:
        row, current = self._editable_user(username, builtin=builtin)
        if not data:
            raise Refused("user-update", "at least one user property is required")
        if "password" in data or "sshpubkey" in data:
            raise Refused("user-update", "use the dedicated password or SSH-key workflow for credential changes")
        return Action(
            "TNT-41", Kind.MUTATE, f"Update user {username}",
            (RpcCall("user.update", (row["id"], dict(data))),),
            (RpcCall("user.query", ([["username", "=", username]], {})),),
        )

    def user_delete_action(self, username: str) -> Action:
        row, current = self._editable_user(username, builtin=True)
        if row.get("builtin") is True or username.casefold() in {"root", "admin"}:
            raise Refused("builtin-user", "builtin and root/admin accounts are never deleted")
        if username.casefold() == current.casefold():
            raise Refused("current-user", "the user currently logged in to tnt cannot be deleted")
        return Action(
            "TNT-41", Kind.DESTRUCTIVE, f"Delete user {username}",
            (RpcCall("user.delete", (row["id"], {})),),
            (RpcCall("user.query", ([["username", "=", username]], {})),), username,
        )

    def user_password_action(self, username: str, password: Secret, *, confirm_self: bool = False,
                             builtin: bool = False) -> Action:
        if not isinstance(password, Secret):
            raise Refused("secret-input", "password must be provided as a protected Secret value")
        row, current = self._editable_user(username, builtin=builtin)
        is_current = username.casefold() == current.casefold()
        if is_current and not confirm_self:
            raise Refused("current-user", "changing the current login password needs the extra confirmation flag")
        data = {"username": username, "old_password": None, "new_password": password}
        kind = Kind.DESTRUCTIVE if is_current else Kind.MUTATE
        return Action(
            "TNT-41", kind, f"Set password for user {username}",
            (RpcCall("user.set_password", (data,)),),
            (RpcCall("user.query", ([["username", "=", username]], {})), RpcCall("auth.me")),
            username if is_current else None,
        )

    def user_ssh_key_action(self, username: str, keys: list[str], *, replace: bool = True,
                            remove: bool = False, confirm_self: bool = False, builtin: bool = False) -> Action:
        row, current = self._editable_user(username, builtin=builtin)
        if username.casefold() == current.casefold() and not confirm_self:
            raise Refused("current-user", "changing the current login SSH key needs the extra confirmation flag")
        existing = row.get("sshpubkey") or ""
        if remove:
            value = ""
        else:
            validated = validate_ssh_public_keys("\n".join(keys))
            if replace or not existing:
                value = "\n".join(validated)
            else:
                value = "\n".join(dict.fromkeys([*existing.splitlines(), *validated]))
        return Action(
            "TNT-41", Kind.MUTATE, f"{'Remove' if remove else 'Set'} SSH public key for user {username}",
            (RpcCall("user.update", (row["id"], {"sshpubkey": Secret(value)})),),
            (RpcCall("user.query", ([["username", "=", username]], {})),),
        )

    def _editable_user(self, username: str, *, builtin: bool) -> tuple[dict[str, Any], str]:
        row = self._named("user.query", username, "user")
        if row.get("local") is False:
            raise Refused("directory-user", "directory-service users cannot be changed through this workflow")
        if row.get("builtin") is True and not builtin:
            raise Refused("builtin-user", "this account is built in; pass --builtin to edit it")
        me = self.rpc.call("auth.me")
        if not isinstance(me, Mapping) or not isinstance(me.get("pw_name"), str):
            raise Refused("login-identity", "the current login user could not be determined safely")
        return row, me["pw_name"]

    # -- containers --------------------------------------------------------------------------------------
    def containers(self) -> list[dict[str, Any]]:
        rows = _rows(self.rpc.call("container.query", [], {}), "container.query")
        return self._container_rows_with_metrics(rows)

    def container(self, name: str) -> dict[str, Any]:
        return self._container_rows_with_metrics([self._named("container.query", name, "container")])[0]

    def container_metrics(self, *, timeout: float = 2.5) -> tuple[dict[str, Any], str]:
        """Collect one v27 container.metrics event; timeout remains explicit, never a zero reading."""
        values: dict[str, Any] = {}
        received = threading.Event()

        def on_metrics(message: str, **event: Any) -> None:
            if message.upper() not in {"ADDED", "CHANGED"}:
                return
            fields = event.get("fields")
            if not isinstance(fields, Mapping):
                return
            for ident, metrics in fields.items():
                if isinstance(metrics, Mapping):
                    values[str(ident)] = dict(metrics)
            if values:
                received.set()

        subscription = self.rpc.subscribe_event("container.metrics", on_metrics)
        try:
            if not received.wait(timeout):
                return values, "timeout"
            return values, "available"
        finally:
            self.rpc.unsubscribe_event(subscription)

    def _container_rows_with_metrics(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        states = [row.get("status", {}).get("state") if isinstance(row.get("status"), Mapping) else None
                  for row in rows]
        running = [row for row, state in zip(rows, states) if isinstance(state, str) and state.upper() == "RUNNING"]
        error = None
        if not rows:
            metrics, status = {}, "no-containers"
        elif not running and all(isinstance(state, str) and state.upper() in {"STOPPED", "SUSPENDED"} for state in states):
            metrics, status = {}, "no-running-containers"
        elif not running:
            metrics, status = {}, "state-unknown"
        else:
            try:
                metrics, status = self.container_metrics()
            except Exception as exc:
                metrics, status, error = {}, "error", str(exc)
        result = []
        for raw in rows:
            row = dict(raw)
            sample = metrics.get(str(row.get("id")))
            state = row.get("status", {}).get("state") if isinstance(row.get("status"), Mapping) else None
            state = state.upper() if isinstance(state, str) else None
            row["metrics"] = sample
            if sample is not None:
                row["metrics_status"] = "available"
            elif state == "RUNNING":
                row["metrics_status"] = "no-sample" if status == "available" else status
            elif state in {"STOPPED", "SUSPENDED"}:
                row["metrics_status"] = "no-running-containers"
            else:
                row["metrics_status"] = "state-unknown"
            row["v27_unavailable_fields"] = ["image", "cpu_count", "memory_limit"]
            if error is not None:
                row["metrics_error"] = error
            result.append(row)
        return result

    def container_create_action(self, data: Mapping[str, Any]) -> Action:
        payload = dict(data)
        name = self._required_string(payload, "name", "container name")
        if not isinstance(payload.get("image"), Mapping) or not payload["image"].get("name") or not payload["image"].get("version"):
            raise Refused("container-create", "v27 container.create requires image.name and image.version")
        self._refuse_boot_pool("container.create", (payload,))
        return Action("TNT-39", Kind.MUTATE, f"Create container {name}",
                      (RpcCall("container.create", (payload,), job=True),),
                      (RpcCall("container.query", ([["name", "=", name]], {})),))

    def container_update_action(self, name: str, data: Mapping[str, Any]) -> Action:
        row = self._named("container.query", name, "container")
        if not data:
            raise Refused("container-update", "at least one supported container property is required")
        if set(data) - {"autostart", "cpuset"}:
            raise Refused("container-update", "v27 supports container autostart and cpuset updates; image and memory limits are unavailable")
        return Action("TNT-39", Kind.MUTATE, f"Update container {name}",
                      (RpcCall("container.update", (row["id"], dict(data))),),
                      (RpcCall("container.query", ([["name", "=", name]], {})),))

    def container_lifecycle_action(self, name: str, operation: str) -> Action:
        row = self._named("container.query", name, "container")
        if operation == "restart":
            return Action("TNT-39", Kind.DESTRUCTIVE, f"Restart container {name}",
                          (RpcCall("container.stop", (row["id"], {}), job=True), RpcCall("container.start", (row["id"],))),
                          (RpcCall("container.query", ([["name", "=", name]], {})),), name)
        if operation not in {"start", "stop"}:
            raise Refused("container-operation", f"unsupported container operation {operation!r}")
        destructive = operation == "stop"
        return Action("TNT-39", Kind.DESTRUCTIVE if destructive else Kind.MUTATE,
                      f"{operation.title()} container {name}",
                      (RpcCall(f"container.{operation}", (row["id"], {}), job=True) if destructive else
                       RpcCall(f"container.{operation}", (row["id"],)),),
                      (RpcCall("container.query", ([["name", "=", name]], {})),), name if destructive else None)

    def container_delete_action(self, name: str) -> Action:
        row = self._named("container.query", name, "container")
        return Action("TNT-39", Kind.DESTRUCTIVE, f"Delete container {name}",
                      (RpcCall("container.delete", (row["id"], {}), job=True),),
                      (RpcCall("container.query", ([["name", "=", name]], {})),), name)

    # -- virtual machines --------------------------------------------------------------------------------
    def vms(self) -> list[dict[str, Any]]:
        return _rows(self.rpc.call("vm.query", [], {}), "vm.query")

    def vm(self, name: str) -> dict[str, Any]:
        return self._named("vm.query", name, "VM")

    def vm_console(self, name: str) -> dict[str, Any]:
        row = self._named("vm.query", name, "VM")
        console = self.rpc.call("vm.get_console", row["id"])
        devices = _rows(self.rpc.call("vm.get_display_devices", row["id"]), "vm.get_display_devices")
        web_display = self.rpc.call("vm.get_display_web_uri", row["id"], "", {})
        if not isinstance(console, str) or not isinstance(web_display, Mapping):
            raise Refused("unexpected-response", "VM console information did not match the v27 response shape")
        displays = []
        for device in devices:
            attributes = device.get("attributes")
            if not isinstance(attributes, Mapping):
                raise Refused("unexpected-response", "VM display device omitted its attributes")
            host = attributes.get("bind")
            port = attributes.get("port")
            web_port = attributes.get("web_port")
            displays.append({
                "type": attributes.get("type"),
                "host": host,
                "port": port,
                "endpoint": _host_port(host, port),
                "web_port": web_port,
                "web_endpoint": _host_port(host, web_port),
                "web": attributes.get("web"),
                "password_configured": bool(attributes.get("password_configured") or attributes.get("password")),
            })
        return {"console": console, "displays": displays, "web_display": dict(web_display)}

    def vm_console_action(self, name: str) -> Action:
        row = self._named("vm.query", name, "VM")
        vm_id = row["id"]
        return Action(
            "TNT-40", Kind.READ, f"Read console and display information for VM {name}",
            (RpcCall("vm.get_console", (vm_id,)), RpcCall("vm.get_display_devices", (vm_id,)),
             RpcCall("vm.get_display_web_uri", (vm_id, "", {}))),
        )

    def vm_create_action(self, data: Mapping[str, Any]) -> Action:
        payload = dict(data)
        name = self._required_string(payload, "name", "VM name")
        if "devices" in payload:
            raise Refused("vm-create", "v27 excludes device lists from vm.create; add disks, ISO media, or NICs with vm.device.create")
        if not isinstance(payload.get("memory"), int) or isinstance(payload["memory"], bool) or payload["memory"] < 20:
            raise Refused("vm-create", "v27 VM creation requires memory of at least 20 MiB")
        self._refuse_boot_pool("vm.create", (payload,))
        return Action("TNT-40", Kind.MUTATE, f"Create VM {name}", (RpcCall("vm.create", (payload,)),),
                      (RpcCall("vm.query", ([["name", "=", name]], {})),))

    def vm_update_action(self, name: str, data: Mapping[str, Any]) -> Action:
        row = self._named("vm.query", name, "VM")
        if not data:
            raise Refused("vm-update", "at least one VM property is required")
        self._refuse_boot_pool("vm.update", (row["id"], dict(data)))
        return Action("TNT-40", Kind.MUTATE, f"Update VM {name}",
                      (RpcCall("vm.update", (row["id"], dict(data))),),
                      (RpcCall("vm.query", ([["name", "=", name]], {})),))

    def vm_device_create_action(self, name: str, attributes: Mapping[str, Any]) -> Action:
        row = self._named("vm.query", name, "VM")
        payload = {"vm": row["id"], "attributes": dict(attributes)}
        self.validate_vm_device_attributes(payload["attributes"])
        return Action("TNT-40", Kind.MUTATE, f"Add {payload['attributes']['dtype']} device to VM {name}",
                      (RpcCall("vm.device.create", (payload,)),),
                      (RpcCall("vm.query", ([["name", "=", name]], {})),))

    @classmethod
    def validate_vm_device_attributes(cls, attributes: Mapping[str, Any]) -> None:
        cls._refuse_boot_pool("vm.device.create", (dict(attributes),))
        dtype = attributes.get("dtype")
        if dtype not in {"DISK", "CDROM", "NIC", "DISPLAY", "PCI", "RAW", "USB"}:
            raise Refused("vm-device", "a supported v27 device dtype is required")
        if dtype == "DISK" and attributes.get("create_zvol") is True:
            size = attributes.get("zvol_volsize")
            if not isinstance(attributes.get("zvol_name"), str) or not attributes["zvol_name"].strip():
                raise Refused("vm-device", "a zvol name is required when create_zvol=true")
            if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
                raise Refused("vm-device", "zvol_volsize must be a positive integer number of bytes")
        if dtype == "CDROM" and (not isinstance(attributes.get("path"), str) or not attributes["path"].startswith("/mnt/")):
            raise Refused("vm-device", "a v27 installer ISO path must start with /mnt/")

    def vm_lifecycle_action(self, name: str, operation: str) -> Action:
        row = self._named("vm.query", name, "VM")
        if operation not in {"start", "stop", "poweroff", "restart", "suspend", "resume"}:
            raise Refused("vm-operation", f"unsupported VM operation {operation!r}")
        destructive = operation in {"stop", "poweroff"}
        args = (row["id"], {}) if operation in {"stop", "start"} else (row["id"],)
        return Action("TNT-40", Kind.DESTRUCTIVE if destructive else Kind.MUTATE,
                      f"{operation.title()} VM {name}",
                      (RpcCall(f"vm.{operation}", args, job=operation in {"stop", "restart"}),),
                      (RpcCall("vm.query", ([["name", "=", name]], {})),), name if destructive else None)

    def vm_delete_action(self, name: str, *, delete_zvols: bool = False) -> Action:
        row = self._named("vm.query", name, "VM")
        return Action("TNT-40", Kind.DESTRUCTIVE, f"Delete VM {name}",
                      (RpcCall("vm.delete", (row["id"], {"zvols": delete_zvols, "force": False})),),
                      (RpcCall("vm.query", ([["name", "=", name]], {})),), name)

    def _named(self, method: str, name: str, kind: str) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip():
            raise Refused("workflow-input", f"{kind} name is required")
        rows = _rows(self.rpc.call(method, [["name" if kind != "user" else "username", "=", name]], {}), method)
        matches = [row for row in rows if isinstance(row, Mapping) and row.get("name" if kind != "user" else "username") == name]
        if len(matches) != 1:
            raise Refused("workflow-lookup", f"expected exactly one {kind} named {name!r}; found {len(matches)}")
        return dict(matches[0])

    # -- datasets ----------------------------------------------------------------------------------------
    def datasets(self) -> list[dict[str, Any]]:
        """Read the v27 dataset tree with its usage and service attachment details."""
        return _rows(self.rpc.call("pool.dataset.details"), "pool.dataset.details")

    def dataset(self, name: str) -> dict[str, Any]:
        return self.rpc.call("pool.dataset.get_instance", name)

    def dataset_create_action(self, data: Mapping[str, Any]) -> Action:
        payload = dict(data)
        self._refuse_boot_pool("pool.dataset.create", (payload,))
        name = self._required_string(payload, "name", "dataset name")
        return Action(
            "TNT-12", Kind.MUTATE, f"Create dataset {name}",
            (RpcCall("pool.dataset.create", (payload,)),),
            (RpcCall("pool.dataset.query", ([['id', '=', name]], {})),),
        )

    def dataset_update_action(self, name: str, data: Mapping[str, Any]) -> Action:
        self._refuse_boot_pool("pool.dataset.update", (name, dict(data)))
        if not name.strip() or not data:
            raise Refused("dataset-update", "dataset name and at least one property are required")
        return Action(
            "TNT-12", Kind.MUTATE, f"Update dataset {name}",
            (RpcCall("pool.dataset.update", (name, dict(data))),),
            (RpcCall("pool.dataset.get_instance", (name,)),),
        )

    def dataset_delete_action(self, name: str, *, recursive: bool = False) -> Action:
        if not name.strip():
            raise Refused("dataset-delete", "dataset name is required")
        self._refuse_boot_pool("pool.dataset.delete", (name, {"recursive": recursive}))
        probes = (
            RpcCall("pool.dataset.details"),
            RpcCall("container.query", ([], {})),
            RpcCall("vm.query", ([], {})),
        )
        _check_dataset_delete_probe(name, recursive, [self.rpc.call(call.method, *call.params) for call in probes])

        return Action(
            "TNT-12", Kind.DESTRUCTIVE, f"Delete dataset {name}",
            (RpcCall("pool.dataset.delete", (name, {"recursive": recursive})),),
            probes, name,
        )

    # -- snapshots ---------------------------------------------------------------------------------------
    def snapshots(self, dataset: str | None = None) -> list[dict[str, Any]]:
        filters = [] if dataset is None else [["dataset", "=", dataset]]
        return _rows(self.rpc.call("pool.snapshot.query", filters, {}), "pool.snapshot.query")

    def snapshot(self, name: str) -> dict[str, Any]:
        return self.rpc.call("pool.snapshot.get_instance", name)

    def snapshot_create_action(self, data: Mapping[str, Any]) -> Action:
        payload = dict(data)
        dataset = self._required_string(payload, "dataset", "dataset name")
        self._refuse_boot_pool("pool.snapshot.create", (payload,))
        if not isinstance(payload.get("name"), str) or not payload["name"].strip():
            raise Refused("snapshot-create", "snapshot name is required")
        return Action(
            "TNT-38", Kind.MUTATE, f"Create snapshot {dataset}@{payload['name']}",
            (RpcCall("pool.snapshot.create", (payload,)),),
            (RpcCall("pool.dataset.get_instance", (dataset,)),),
        )

    def snapshot_clone_action(self, snapshot: str, dataset_dst: str) -> Action:
        self._refuse_boot_pool("pool.snapshot.clone", ({"snapshot": snapshot, "dataset_dst": dataset_dst},))
        data = {"snapshot": snapshot, "dataset_dst": dataset_dst}
        return Action(
            "TNT-38", Kind.MUTATE, f"Clone {snapshot} to {dataset_dst}",
            (RpcCall("pool.snapshot.clone", (data,)),),
            (
                RpcCall("pool.snapshot.get_instance", (snapshot,)),
                RpcCall("pool.dataset.query", ([['id', '=', dataset_dst]], {})),
            ),
        )

    def snapshot_hold_action(self, name: str) -> Action:
        return self._snapshot_flag_action(name, "hold")

    def snapshot_release_action(self, name: str) -> Action:
        return self._snapshot_flag_action(name, "release")

    def snapshot_rollback_action(self, name: str) -> Action:
        snapshot = self._required_string({"name": name}, "name", "snapshot name")
        self._refuse_boot_pool("pool.snapshot.rollback", (snapshot, {}))
        dataset = snapshot.split("@", 1)[0]
        return Action(
            "TNT-38", Kind.DESTRUCTIVE, f"Rollback dataset to {snapshot}",
            (RpcCall("pool.snapshot.rollback", (snapshot, {})),),
            (RpcCall("pool.snapshot.get_instance", (snapshot,)), RpcCall("pool.dataset.get_instance", (dataset,))),
            snapshot,
        )

    def snapshot_delete_action(self, name: str, *, recursive: bool = False) -> Action:
        snapshot = self._required_string({"name": name}, "name", "snapshot name")
        self._refuse_boot_pool("pool.snapshot.delete", (snapshot, {"recursive": recursive}))
        return Action(
            "TNT-38", Kind.DESTRUCTIVE, f"Delete snapshot {snapshot}",
            (RpcCall("pool.snapshot.delete", (snapshot, {"recursive": recursive})),),
            (RpcCall("pool.snapshot.get_instance", (snapshot,)),),
            snapshot,
        )

    def _snapshot_flag_action(self, name: str, operation: str) -> Action:
        snapshot = self._required_string({"name": name}, "name", "snapshot name")
        self._refuse_boot_pool(f"pool.snapshot.{operation}", (snapshot, {}))
        return Action(
            "TNT-38", Kind.MUTATE, f"{operation.title()} snapshot {snapshot}",
            (RpcCall(f"pool.snapshot.{operation}", (snapshot, {})),),
            (RpcCall("pool.snapshot.get_instance", (snapshot,)),),
        )

    @staticmethod
    def _required_string(data: Mapping[str, Any], key: str, label: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise Refused("workflow-input", f"{label} is required")
        return value.strip()

    @staticmethod
    def _refuse_boot_pool(method: str, params: Any) -> None:
        values = list(_strings(params))
        if any(_ambiguous_path(value) for value in values):
            raise Refused("refusal-list", f"{method}: a path with '..' is ambiguous and refused")
        if any(_names_boot_pool(value) for value in values):
            raise Refused("refusal-list", f"{method}: boot-pool and its datasets are never changed from here")


def _flatten(rows: Any):
    if isinstance(rows, Mapping):
        yield rows
        children = rows.get("children")
        if isinstance(children, list):
            yield from _flatten(children)
    elif isinstance(rows, (list, tuple)):
        for row in rows:
            yield from _flatten(row)


def _running_names(rows: Any) -> set[str]:
    names = set()
    for row in rows:
        status = row.get("status") if isinstance(row, Mapping) else None
        state = status.get("state") if isinstance(status, Mapping) else status
        if isinstance(state, str) and state.upper() in {"RUNNING", "SUSPENDED", "STARTING"}:
            name = row.get("name")
            if isinstance(name, str):
                names.add(name)
    return names


def _attachment_names(attachments: Any) -> set[str]:
    if not isinstance(attachments, list):
        return set()
    return {item["name"] for item in attachments if isinstance(item, Mapping) and isinstance(item.get("name"), str)}


def _check_dataset_delete_probe(name: str, recursive: bool, results: Any) -> None:
    if not isinstance(results, (list, tuple)) or len(results) != 3:
        raise Refused("dataset-delete", "dataset deletion requires live dataset and workload probes")
    details, containers, vms = results
    rows = list(_flatten(details or []))
    target = next((row for row in rows if row.get("id") == name), None)
    if target is None:
        raise Refused("dataset-delete", f"dataset {name!r} is not present in the v27 dataset details")

    children = [row.get("id") for row in rows if isinstance(row.get("id"), str) and row["id"].startswith(f"{name}/")]
    mounted = target.get("mounted") is True
    running_containers = _running_names(containers or [])
    running_vms = _running_names(vms or [])
    attached_containers = _attachment_names(target.get("containers")) & running_containers
    attached_vms = _attachment_names(target.get("vms")) & running_vms
    if (children or mounted or attached_containers or attached_vms) and not recursive:
        reasons = []
        if children:
            reasons.append("has child datasets")
        if mounted:
            reasons.append("is mounted")
        if attached_containers:
            reasons.append(f"is used by running container(s) {', '.join(sorted(attached_containers))}")
        if attached_vms:
            reasons.append(f"is used by running VM(s) {', '.join(sorted(attached_vms))}")
        raise Refused("dataset-in-use", f"dataset {name!r} {'; '.join(reasons)}; pass --recursive and type its full name")


_SSH_KEY_TYPES = frozenset({
    "ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com",
})


def _host_port(host: Any, port: Any) -> str | None:
    if not isinstance(host, str) or not host:
        return None
    formatted_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    if port is None:
        return f"{formatted_host}:auto-assigned"
    if not isinstance(port, int) or isinstance(port, bool):
        return None
    return f"{formatted_host}:{port}"


def validate_ssh_public_keys(text: str) -> list[str]:
    """Validate one or more OpenSSH public-key lines without accepting private-key material."""
    if not isinstance(text, str) or not text.strip():
        raise Refused("ssh-key", "at least one SSH public key is required")
    keys = []
    for line_number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if line.startswith("-----BEGIN ") or "PRIVATE KEY" in line:
            raise Refused("ssh-key", f"line {line_number} is private-key material; provide an OpenSSH .pub key")
        fields = line.split()
        if len(fields) < 2 or fields[0] not in _SSH_KEY_TYPES:
            raise Refused("ssh-key", f"line {line_number} must contain a supported OpenSSH public-key type and base64 data")
        try:
            blob = base64.b64decode(fields[1], validate=True)
        except (binascii.Error, ValueError):
            raise Refused("ssh-key", f"line {line_number} public-key data is not valid base64") from None
        if len(blob) < 4:
            raise Refused("ssh-key", f"line {line_number} public-key data is too short")
        type_size = struct.unpack(">I", blob[:4])[0]
        if type_size > len(blob) - 4:
            raise Refused("ssh-key", f"line {line_number} public-key data is malformed")
        embedded_type = blob[4:4 + type_size].decode("ascii", errors="replace")
        if embedded_type != fields[0]:
            raise Refused("ssh-key", f"line {line_number} key type does not match its encoded public key")
        keys.append(" ".join(fields))
    if not keys:
        raise Refused("ssh-key", "at least one SSH public key is required")
    return keys


def _safe_value(value: Any, key: Any = None) -> Any:
    sensitive = {"password", "old_password", "new_password", "api_key", "sshpubkey", "private_key", "unixhash", "smbhash",
                 "credentials", "credential", "token", "secret", "secret_access_key", "access_key", "client_secret",
                 "encryption_password", "encryption_salt"}
    key_text = key.casefold() if isinstance(key, str) else ""
    if isinstance(value, Secret) or key_text in sensitive or any(marker in key_text for marker in ("password", "secret", "credential", "token")):
        return "***"
    if isinstance(value, Mapping):
        return {name: _safe_value(child, name) for name, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_value(child) for child in value]
    return value


def _redact_public_user(row: Mapping[str, Any]) -> dict[str, Any]:
    return _safe_value(row)


def _rows(value: Any, method: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
        raise Refused("unexpected-response", f"{method} returned an invalid row collection")
    return [dict(row) for row in value]


def validate_plan_preconditions(action: Action, probe_results: Any) -> None:
    """Enforce data-use checks on both plan creation and apply, after fresh probe reads."""
    deletions = [call for call in action.calls if call.method == "pool.dataset.delete"]
    for call in deletions:
        if len(call.params) < 2 or not isinstance(call.params[0], str) or not isinstance(call.params[1], Mapping):
            raise Refused("dataset-delete", "dataset deletion call has an invalid target or options")
        options = call.params[1]
        recursive = options.get("recursive", False) is True
        _check_dataset_delete_probe(call.params[0], recursive, probe_results)
    creates = {
        "pool.dataset.create": ("pool.dataset.query", "name", "dataset"),
        "container.create": ("container.query", "name", "container"),
        "vm.create": ("vm.query", "name", "VM"),
        "user.create": ("user.query", "username", "user"),
    }
    for call in action.calls:
        requirement = creates.get(call.method)
        if requirement is None:
            continue
        probe_method, key, kind = requirement
        if len(call.params) != 1 or not isinstance(call.params[0], Mapping) or len(action.probe) != 1 or action.probe[0].method != probe_method:
            raise Refused("create-probe", f"{call.method} requires a matching {probe_method} uniqueness probe")
        expected = call.params[0].get(key)
        results = probe_results[0] if isinstance(probe_results, (list, tuple)) and len(probe_results) == 1 else None
        if not isinstance(results, list):
            raise Refused("create-probe", f"{kind} creation requires a live uniqueness probe")
        matches = [row for row in results if isinstance(row, Mapping) and row.get(key) == expected]
        if matches:
            raise Refused("already-exists", f"{kind} {expected!r} already exists; choose another name")

    share_creates = {
        "sharing.smb.create": ("sharing.smb.query", "name", "SMB share"),
        "sharing.nfs.create": ("sharing.nfs.query", "path", "NFS share"),
    }
    for call in action.calls:
        requirement = share_creates.get(call.method)
        if requirement is None:
            continue
        query_method, key, kind = requirement
        if (len(call.params) != 1 or not isinstance(call.params[0], Mapping) or len(action.probe) != 2
                or action.probe[0].method != "filesystem.stat" or action.probe[1].method != query_method):
            raise Refused("share-create-probe", f"{call.method} requires remote path and uniqueness probes")
        path_probe, matches = probe_results
        path = call.params[0].get("path")
        if (not isinstance(path_probe, Mapping) or not isinstance(path_probe.get("realpath"), str)
                or _names_boot_pool(path_probe["realpath"])):
            raise Refused("share-path", "share path is missing, unresolved, or resolves under boot-pool")
        if path_probe["realpath"] != path:
            raise Refused("share-path", f"share path resolves to {path_probe['realpath']!r}; use the canonical path explicitly")
        if not isinstance(matches, list):
            raise Refused("share-create-probe", f"{kind} creation requires a live uniqueness probe")
        expected = call.params[0].get(key)
        if any(isinstance(row, Mapping) and row.get(key) == expected for row in matches):
            raise Refused("already-exists", f"{kind} {expected!r} already exists")

    # ID-based updates and lifecycle calls must still resolve to the same named
    # object at both plan time and apply time. The plan fingerprint binds the
    # returned row; this comparison also prevents a stale or mismatched ID from
    # being paired with an unrelated broad query.
    target_calls = {
        "user.update": ("user.query", "username", 0),
        "user.delete": ("user.query", "username", 0),
        "user.set_password": ("user.query", "username", None),
        "container.update": ("container.query", "name", 0),
        "container.start": ("container.query", "name", 0),
        "container.stop": ("container.query", "name", 0),
        "container.delete": ("container.query", "name", 0),
        "vm.update": ("vm.query", "name", 0),
        "vm.device.create": ("vm.query", "name", None),
        "vm.start": ("vm.query", "name", 0),
        "vm.stop": ("vm.query", "name", 0),
        "vm.poweroff": ("vm.query", "name", 0),
        "vm.restart": ("vm.query", "name", 0),
        "vm.suspend": ("vm.query", "name", 0),
        "vm.resume": ("vm.query", "name", 0),
        "vm.delete": ("vm.query", "name", 0),
    }
    targeted = [call for call in action.calls if call.method in target_calls]
    if targeted:
        password_targeted = any(call.method == "user.set_password" for call in targeted)
        expected_probe_count = 2 if password_targeted else 1
        if len(action.probe) != expected_probe_count or not isinstance(probe_results, (list, tuple)) or len(probe_results) != expected_probe_count:
            raise Refused("target-probe", "an update or lifecycle action requires one named-object probe")
        probe = action.probe[0]
        query_method, key, _ = target_calls[targeted[0].method]
        if (probe.method != query_method or len(probe.params) != 2 or probe.params[1] != {} or
                password_targeted and action.probe[1].method != "auth.me"):
            raise Refused("target-probe", "the named-object probe does not match the action family")
        filters = probe.params[0]
        if not isinstance(filters, list) or len(filters) != 1 or not isinstance(filters[0], list) or len(filters[0]) != 3:
            raise Refused("target-probe", "the named-object probe must use one exact equality filter")
        filter_key, operator, target_name = filters[0]
        if filter_key != key or operator != "=" or not isinstance(target_name, str) or not target_name:
            raise Refused("target-probe", "the named-object probe must identify the exact target name")
        if any(target_calls[call.method][0] != query_method or target_calls[call.method][1] != key for call in targeted):
            raise Refused("target-probe", "one action cannot combine writes to different object families")
        rows = probe_results[0]
        if not isinstance(rows, list):
            raise Refused("target-probe", "the target lookup did not return a row list")
        matches = [row for row in rows if isinstance(row, Mapping) and row.get(key) == target_name]
        if len(matches) != 1 or matches[0].get("id") is None:
            raise Refused("target-probe", f"expected exactly one current target named {target_name!r}")
        current_id = matches[0]["id"]
        for call in targeted:
            id_index = target_calls[call.method][2]
            if id_index is None:
                payload = call.params[0] if call.params else None
                if not isinstance(payload, Mapping):
                    raise Refused("target-probe", "the action payload does not identify its current target")
                if call.method == "user.set_password" and payload.get("username") != target_name:
                    raise Refused("target-probe", "the password target does not match its lookup")
                if call.method == "user.set_password":
                    login = probe_results[1]
                    login_name = login.get("pw_name") if isinstance(login, Mapping) else None
                    if not isinstance(login_name, str) or not login_name:
                        raise Refused("login-identity", "the current login user could not be determined safely")
                    is_current = login_name.casefold() == target_name.casefold()
                    expected_kind = Kind.DESTRUCTIVE if is_current else Kind.MUTATE
                    expected_confirmation = target_name if is_current else None
                    if action.kind is not expected_kind or action.confirm_target != expected_confirmation:
                        raise Refused("password-classification", "password change confirmation does not match whether the target is the current login user")
                if call.method == "vm.device.create" and payload.get("vm") != current_id:
                    raise Refused("target-probe", "the VM device target does not match its lookup")
            elif not call.params or call.params[id_index] != current_id:
                raise Refused("target-probe", "the action ID does not match the current named object")
