"""Test support: a minimal fake TrueNAS JSON-RPC 2.0 websocket server.

It speaks just enough of the middleware protocol for ``truenas_api_client``: ``core.set_options``,
``auth.mechanism_choices``, ``auth.login_ex`` with the API-key SCRAM and password flows, ``system.version``,
``system.info``, ``core.get_methods``, ``disk.query`` and ``disk.details``.
It listens on loopback only and records every request, so tests can assert which methods were called.
This module imports ``websockets``: it is for tests only and is never imported by the shipped CLI or TUI.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
from typing import Any, Callable
from urllib.parse import urlencode

from websockets.sync.server import ServerConnection, serve

from .adapters.v27 import V27
from .profile import Profile, Secret

GIB = 1024 ** 3
CALL_ERROR = -32001  # truenas_api_client.JSONRPCError.TRUENAS_CALL_ERROR
FAKE_KEY = "1-fakefakefakefakefakefakefakefake"


def standard_disks() -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Four disks: a boot disk with a free tail (ACTION-MATRIX B-01), a pool disk, an empty disk, one with no partition data."""
    queried = [
        {"name": "sda", "size": 512 * GIB, "model": "BOOTDISK", "serial": "BOOT0001", "type": "SSD", "pool": "boot-pool"},
        {"name": "sdb", "size": 1024 * GIB, "model": "DATADISK", "serial": "DATA0002", "type": "HDD", "pool": "tank"},
        {"name": "sdc", "size": 256 * GIB, "model": "SPARE", "serial": "SPARE003", "type": "SSD", "pool": None},
        {"name": "sdd", "size": 128 * GIB, "model": "OPAQUE", "serial": "OPAQ0004", "type": "SSD", "pool": None},
    ]
    parts = {
        "sda": [{"name": "sda1", "size": 1 << 20, "start": 2048}, {"name": "sda2", "size": 512 << 20, "start": 4096},
                {"name": "sda3", "size": 100 * GIB, "start": 1052672}],
        "sdb": [{"name": "sdb1", "size": 1024 * GIB - (1 << 20), "start": 2048}],
        "sdc": [],
    }
    return queried, parts


class FakeServer:
    def __init__(self, *, version: str = "TrueNAS-27.0.0-RC.1", username: str = "tnt", api_key: str = FAKE_KEY,
                 password: str = "fake-password-material",
                 disks: list[dict[str, Any]] | None = None, parts: dict[str, list[dict[str, Any]]] | None = None,
                 methods: list[str] | None = None, fail: dict[str, str] | None = None, details_as_flat_list: bool = False):
        std_disks, std_parts = standard_disks()
        self.version, self.username, self.api_key, self.password = version, username, api_key, password
        self.disks = std_disks if disks is None else disks
        self.parts = std_parts if parts is None else parts
        self.methods = sorted(V27.supported_methods()) if methods is None else methods
        self.fail = dict(fail or {})
        self.pools: Any = [{"id": 1, "name": "tank", "topology": {"data": [{"type": "DISK", "disk": "sdb", "path": "/dev/sdb1"}]}}]
        self.boot_state: Any = {"name": "boot-pool", "topology": {"data": [{"type": "DISK", "disk": "sda3", "path": "/dev/sda3"}]}}
        self.details_as_flat_list = details_as_flat_list
        self.requests: list[dict[str, Any]] = []
        self.logins = 0
        self.datasets: dict[str, dict[str, Any]] = {"tank": self._make_dataset("tank", type_="FILESYSTEM")}
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.snapshot_tasks: dict[int, dict[str, Any]] = {}
        self.replication_tasks: dict[int, dict[str, Any]] = {
            10: {"id": 10, "name": "offsite", "enabled": True, "source_datasets": ["tank/home"],
                 "target_dataset": "archive/home", "direction": "PUSH", "transport": "SSH",
                 "allow_from_scratch": False, "state": {"state": "FINISHED"}},
        }
        self.cloudsync_tasks: dict[int, dict[str, Any]] = {
            20: {"id": 20, "description": "offsite-copy", "enabled": True, "path": "/mnt/tank/home",
                 "direction": "PUSH", "transfer_mode": "COPY", "credentials": {
                     "id": 4, "name": "backup-account", "provider": "S3",
                     "attributes": {"secret_access_key": "synthetic-cloud-secret"}},
                 "encryption_password": "synthetic-cloud-secret", "encryption_salt": "synthetic-cloud-secret",
                 "job": None, "locked": False},
        }
        self.apps: dict[str, dict[str, Any]] = {
            "example-app": {"id": "example-app", "name": "Example App", "state": "STOPPED",
                            "upgrade_available": False, "latest_version": "1.0.0", "latest_app_version": "1.0.0",
                            "image_updates_available": False, "custom_app": False, "migrated": False,
                            "human_version": "1.0.0", "version": "1.0.0", "metadata": {},
                            "active_workloads": {"containers": 0, "used_ports": [], "used_host_ips": [],
                                                 "container_details": [], "volumes": [], "images": [], "networks": []},
                            "notes": None, "action_required": False, "portals": {}}
        }
        self.app_delete_options: dict[str, Any] | None = None
        self.audit_entries: list[dict[str, Any]] = [
            {"audit_id": "audit-1", "message_timestamp": 1793181600, "timestamp": "2026-10-28T10:00:00Z",
             "address": "192.0.2.44", "username": "operator", "session": None, "service": "MIDDLEWARE",
             "service_data": {"note": "password=synthetic-audit-secret"}, "event": "USER_LOGIN",
             "event_data": {"access_token": "synthetic-audit-token", "status": "ok"}, "success": True},
            {"audit_id": "audit-2", "message_timestamp": 1793178000, "timestamp": "2026-10-28T09:00:00Z",
             "address": "192.0.2.44", "username": "operator", "session": None, "service": "MIDDLEWARE",
             "service_data": {}, "event": "USER_LOGOUT", "event_data": {"status": "ok"}, "success": True},
        ]
        self.containers: dict[int, dict[str, Any]] = {}
        self.vms: dict[int, dict[str, Any]] = {}
        self.vm_devices: dict[int, dict[str, Any]] = {}
        self.smb_shares: dict[int, dict[str, Any]] = {}
        self.nfs_shares: dict[int, dict[str, Any]] = {}
        self.share_paths = {"/mnt/tank/share", "/mnt/tank/export"}
        self.interfaces: list[dict[str, Any]] = [{
            "id": "enp1s0", "name": "enp1s0", "fake": False, "type": "PHYSICAL",
            "state": {"name": "enp1s0", "orig_name": "enp1s0", "description": "Primary interface", "mtu": 1500,
                      "cloned": False, "flags": ["UP", "RUNNING"], "nd6_flags": [], "capabilities": [],
                      "link_state": "UP", "media_type": "Ethernet", "media_subtype": "1000baseT",
                      "active_media_type": "Ethernet", "active_media_subtype": "1000baseT", "supported_media": [],
                      "media_options": [], "link_address": "02:00:00:00:00:01", "hardware_link_address": "02:00:00:00:00:01",
                      "aliases": [{"type": "INET", "address": "192.0.2.10", "netmask": 24}]},
            "aliases": [{"type": "INET", "address": "192.0.2.10", "netmask": 24}],
            "ipv4_dhcp": False, "ipv6_auto": False, "description": "Primary interface", "mtu": 1500,
        }]
        self.routes: list[dict[str, Any]] = [
            {"network": "0.0.0.0", "netmask": "0.0.0.0", "gateway": "192.0.2.1", "interface": "enp1s0",
             "flags": [], "table_id": 254, "scope": 0, "preferred_source": "192.0.2.10"},
            {"network": "192.0.2.0", "netmask": "255.255.255.0", "gateway": None, "interface": "enp1s0",
             "flags": [], "table_id": 254, "scope": 253, "preferred_source": "192.0.2.10"},
        ]
        self.network_summary: dict[str, Any] = {"ips": {"enp1s0": {"IPV4": ["192.0.2.10/24"]}},
                                                "default_routes": ["192.0.2.1"], "nameservers": ["192.0.2.53"]}
        self._next_share_id = 1
        self._next_snapshot_task_id = 1
        self.users: dict[int, dict[str, Any]] = {
            0: {"id": 0, "uid": 0, "username": "root", "full_name": "root", "builtin": True, "local": True,
                "shell": "/usr/bin/zsh", "home": "/root", "groups": [], "sshpubkey": None, "password_disabled": False},
            1000: {"id": 1000, "uid": 1000, "username": username, "full_name": username, "builtin": False, "local": True,
                   "shell": "/usr/bin/zsh", "home": f"/home/{username}", "groups": [], "sshpubkey": None, "password_disabled": False},
        }
        self.user_passwords: dict[str, str] = {username: password}
        self.groups: dict[int, dict[str, Any]] = {1: {"id": 1, "gid": 1, "name": "wheel", "builtin": True}}
        self._next_container_id = 1
        self._next_vm_id = 1
        self._next_device_id = 1
        self._next_user_id = 1001
        self._next_job_id = 1
        self._subscription_id = 1
        self._scram: dict[str, dict[str, Any]] = {}
        self._server: Any = None
        self._thread: threading.Thread | None = None

    # -- lifecycle ----------------------------------------------------------------------------------------
    def start(self) -> "FakeServer":
        self._server = serve(self._handle, "127.0.0.1", 0)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)

    def __enter__(self) -> "FakeServer":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    @property
    def port(self) -> int:
        return self._server.socket.getsockname()[1]

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/api/current"

    def profile(self, name: str = "fake", **kw: Any) -> Profile:
        auth = kw.pop("auth", "api_key")
        api_key = kw.pop("api_key", Secret(self.api_key) if auth == "api_key" else None)
        password = kw.pop("password", Secret(self.password) if auth == "password" else None)
        return Profile(name=name, url=self.url, username=self.username, tls="verify", auth=auth,
                       api_key=api_key, password=password, **kw)

    def secret(self) -> Secret:
        return Secret(self.api_key)

    def called(self) -> list[str]:
        return [r["method"] for r in self.requests]

    # -- protocol -----------------------------------------------------------------------------------------
    def _handle(self, ws: ServerConnection) -> None:
        authed = False
        for raw in ws:
            msg = json.loads(raw)
            self.requests.append(msg)
            method, params = msg["method"], msg.get("params") or []
            try:
                if method in self.fail:
                    raise _RpcError(CALL_ERROR, self.fail[method])
                if method == "auth.login_ex":
                    result, authed = self._login(params, authed)
                elif method in ("core.set_options", "auth.mechanism_choices"):
                    result = ["SCRAM", "API_KEY_PLAIN", "PASSWORD_PLAIN"] if method == "auth.mechanism_choices" else {"legacy_jobs": False}
                elif method == "core.subscribe" and params and params[0] == "core.get_jobs":
                    result = {"legacy_jobs": False}
                elif method == "core.unsubscribe" and not authed:
                    result = True
                elif not authed:
                    raise _RpcError(CALL_ERROR, "Not authenticated")
                else:
                    result = self._dispatch(method, params)
                    if method in {"container.create", "container.stop", "container.delete", "vm.stop", "vm.restart",
                                  "pool.snapshottask.run", "replication.run", "cloudsync.sync",
                                  "app.start", "app.stop", "app.delete"}:
                        self._send_job(ws, msg["id"], result)
                        continue
                reply: dict[str, Any] = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
            except _RpcError as exc:
                error: dict[str, Any] = {"code": exc.code, "message": exc.message}
                if exc.code == CALL_ERROR:  # the client reads these fields from a call error
                    error["data"] = {"reason": exc.message, "error": 22, "trace": None, "extra": None}
                reply = {"jsonrpc": "2.0", "id": msg["id"], "error": error}
            ws.send(json.dumps(reply))
            if authed and method not in self.fail and method == "core.subscribe" and params and params[0] == "container.metrics":
                self._send_container_metrics(ws)

    def _send_job(self, ws: ServerConnection, request_id: Any, result: Any) -> None:
        job_id = str(self._next_job_id)
        self._next_job_id += 1
        ws.send(json.dumps({"jsonrpc": "2.0", "method": "collection_update", "params": {
            "collection": "core.get_jobs", "msg": "added", "id": job_id,
            "fields": {"id": job_id, "state": "RUNNING", "message_ids": [request_id]},
        }}))
        ws.send(json.dumps({"jsonrpc": "2.0", "method": "collection_update", "params": {
            "collection": "core.get_jobs", "msg": "changed", "id": job_id,
            "fields": {"id": job_id, "state": "SUCCESS", "result": result, "message_ids": []},
        }}))

    def _login(self, params: list[Any], authed: bool) -> tuple[Any, bool]:
        p = params[0] if params else {}
        if p.get("mechanism") == "PASSWORD_PLAIN":
            self.logins += 1
            ok = p.get("username") == self.username and p.get("password") == self.password
            return ({"response_type": "SUCCESS"} if ok else {"response_type": "AUTH_ERR"}, ok or authed)
        if p.get("mechanism") == "API_KEY_PLAIN" and p.get("username") == self.username and p.get("api_key") == self.api_key:
            self.logins += 1
            return {"response_type": "SUCCESS"}, True
        if p.get("mechanism") == "SCRAM":
            return self._scram_login(p, authed)
        return {"response_type": "AUTH_ERR"}, authed

    def _scram_login(self, payload: dict[str, Any], authed: bool) -> tuple[Any, bool]:
        """Test server for the pinned client's real SCRAM-SHA-512 websocket exchange."""
        rfc = payload.get("rfc_str", "")
        fields = {part.split("=", 1)[0]: part.split("=", 1)[1] for part in rfc.split(",") if "=" in part}
        if payload.get("scram_type") == "CLIENT_FIRST_MESSAGE":
            self.logins += 1
            client_bare = rfc[3:] if rfc.startswith(("n,,", "y,,")) else ""
            user = fields.get("n", "")
            if ":" not in user or not fields.get("r"):
                return {"response_type": "AUTH_ERR"}, authed
            username, key_id = user.rsplit(":", 1)
            try:
                actual_id, material = self.api_key.split("-", 1)
            except ValueError:
                return {"response_type": "AUTH_ERR"}, authed
            if username != self.username or key_id != actual_id:
                return {"response_type": "AUTH_ERR"}, authed
            try:
                nonce = base64.b64encode(base64.b64decode(fields["r"], validate=True) + os.urandom(32)).decode()
            except (ValueError, TypeError):
                return {"response_type": "AUTH_ERR"}, authed
            salt = os.urandom(16)
            iterations = 50000
            first = f"r={nonce},s={base64.b64encode(salt).decode()},i={iterations}"
            self._scram[nonce] = {"client_bare": client_bare, "server_first": first, "material": material,
                                  "salt": salt, "iterations": iterations}
            return ({"response_type": "SCRAM_RESPONSE", "scram_type": "SERVER_FIRST_RESPONSE", "rfc_str": first}, authed)
        if payload.get("scram_type") != "CLIENT_FINAL_MESSAGE":
            return {"response_type": "AUTH_ERR"}, authed
        nonce = fields.get("r", "")
        state = self._scram.pop(nonce, None)
        if state is None or not fields.get("p"):
            return {"response_type": "AUTH_ERR"}, authed
        try:
            proof = base64.b64decode(fields["p"], validate=True)
        except (ValueError, TypeError):
            return {"response_type": "AUTH_ERR"}, authed
        without_proof = ",".join(part for part in rfc.split(",") if not part.startswith("p="))
        auth_message = f'{state["client_bare"]},{state["server_first"]},{without_proof}'.encode()
        salted = hashlib.pbkdf2_hmac("sha512", state["material"].encode(), state["salt"], state["iterations"])
        client_key = hmac.new(salted, b"Client Key", hashlib.sha512).digest()
        stored_key = hashlib.sha512(client_key).digest()
        signature = hmac.new(stored_key, auth_message, hashlib.sha512).digest()
        recovered = bytes(a ^ b for a, b in zip(proof, signature))
        if len(recovered) != len(client_key) or not hmac.compare_digest(hashlib.sha512(recovered).digest(), stored_key):
            return {"response_type": "AUTH_ERR"}, authed
        server_key = hmac.new(salted, b"Server Key", hashlib.sha512).digest()
        server_signature = hmac.new(server_key, auth_message, hashlib.sha512).digest()
        result = {"response_type": "SCRAM_RESPONSE", "scram_type": "SERVER_FINAL_RESPONSE",
                  "rfc_str": f"v={base64.b64encode(server_signature).decode()}"}
        return result, True

    def _dispatch(self, method: str, params: list[Any]) -> Any:
        handlers: dict[str, Callable[[list[Any]], Any]] = {
            "system.version": lambda p: self.version,
            "system.info": lambda p: {"version": self.version, "hostname": "fake"},
            "core.get_methods": lambda p: {m: {} for m in self.methods},
            "interface.query": lambda p: self.interfaces,
            "route.system_routes": lambda p: self.routes,
            "network.general.summary": lambda p: self.network_summary,
            "app.query": lambda p: self._query(self.apps, p),
            "app.start": lambda p: self._app_state(p, "RUNNING"),
            "app.stop": lambda p: self._app_state(p, "STOPPED"),
            "app.delete": self._app_delete,
            "audit.query": self._audit_query,
            "core.subscribe": lambda p: self._subscribe(p),
            "core.unsubscribe": lambda p: True,
            "auth.me": lambda p: {"pw_name": self.username, "pw_uid": 1000, "pw_gid": 1},
            "disk.query": lambda p: self.disks,
            "disk.details": self._details,
            "pool.query": lambda p: self.pools,
            "boot.get_state": lambda p: self.boot_state,
            "pool.dataset.query": self._dataset_query,
            "pool.dataset.details": lambda p: self._dataset_tree(),
            "pool.dataset.get_instance": lambda p: self._get(self.datasets, p[0], "dataset"),
            "pool.dataset.create": self._dataset_create,
            "pool.dataset.update": self._dataset_update,
            "pool.dataset.delete": self._dataset_delete,
            "pool.snapshot.query": self._snapshot_query,
            "pool.snapshot.get_instance": lambda p: self._get(self.snapshots, p[0], "snapshot"),
            "pool.snapshot.create": self._snapshot_create,
            "pool.snapshot.clone": self._snapshot_clone,
            "pool.snapshot.hold": lambda p: self._snapshot_hold(p, True),
            "pool.snapshot.release": lambda p: self._snapshot_hold(p, False),
            "pool.snapshot.rollback": lambda p: self._get(self.snapshots, p[0], "snapshot"),
            "pool.snapshot.delete": self._snapshot_delete,
            "pool.snapshottask.query": lambda p: self._query(self.snapshot_tasks, p),
            "pool.snapshottask.get_instance": lambda p: self._get(self.snapshot_tasks, p[0], "periodic snapshot task"),
            "pool.snapshottask.create": self._snapshot_task_create,
            "pool.snapshottask.update": self._snapshot_task_update,
            "pool.snapshottask.delete": self._snapshot_task_delete,
            "pool.snapshottask.run": self._snapshot_task_run,
            "replication.query": lambda p: self._query(self.replication_tasks, p),
            "replication.get_instance": lambda p: self._get(self.replication_tasks, p[0], "replication task"),
            "replication.update": self._replication_update,
            "replication.run": self._replication_run,
            "cloudsync.query": lambda p: self._query(self.cloudsync_tasks, p),
            "cloudsync.get_instance": lambda p: self._get(self.cloudsync_tasks, p[0], "cloud-sync task"),
            "cloudsync.update": self._cloudsync_update,
            "cloudsync.sync": self._cloudsync_run,
            "container.query": lambda p: self._query(self.containers, p),
            "container.get_instance": lambda p: self._get(self.containers, p[0], "container"),
            "container.create": self._container_create,
            "container.update": self._container_update,
            "container.start": lambda p: self._container_state(p, "RUNNING"),
            "container.stop": lambda p: self._container_state(p, "STOPPED"),
            "container.delete": self._container_delete,
            "vm.query": lambda p: self._query(self.vms, p),
            "vm.get_instance": lambda p: self._get(self.vms, p[0], "VM"),
            "vm.status": lambda p: self._get(self.vms, p[0], "VM").get("status"),
            "vm.create": self._vm_create,
            "vm.update": self._vm_update,
            "vm.start": lambda p: self._vm_state(p, "RUNNING"),
            "vm.stop": lambda p: self._vm_state(p, "STOPPED"),
            "vm.poweroff": lambda p: self._vm_state(p, "STOPPED"),
            "vm.restart": lambda p: self._vm_state(p, "RUNNING"),
            "vm.suspend": lambda p: self._vm_state(p, "SUSPENDED"),
            "vm.resume": lambda p: self._vm_state(p, "RUNNING"),
            "vm.delete": self._vm_delete,
            "vm.device.query": lambda p: self._query(self.vm_devices, p),
            "vm.device.create": self._vm_device_create,
            "vm.device.update": self._vm_device_update,
            "vm.device.delete": self._vm_device_delete,
            "vm.get_console": self._vm_get_console,
            "vm.get_display_devices": self._vm_get_display_devices,
            "vm.get_display_web_uri": self._vm_get_display_web_uri,
            "user.query": lambda p: self._query(self.users, p, username_key="username"),
            "user.get_instance": lambda p: self._get(self.users, p[0], "user"),
            "user.create": self._user_create,
            "user.update": self._user_update,
            "user.delete": self._user_delete,
            "user.set_password": self._user_set_password,
            "group.query": lambda p: self._query(self.groups, p),
            "user.shell_choices": lambda p: {"/usr/bin/zsh": "/usr/bin/zsh", "/usr/sbin/nologin": "/usr/sbin/nologin"},
            "filesystem.stat": self._filesystem_stat,
            "sharing.smb.query": lambda p: self._query(self.smb_shares, p),
            "sharing.smb.get_instance": lambda p: self._get(self.smb_shares, p[0], "SMB share"),
            "sharing.smb.getacl": self._smb_getacl,
            "sharing.smb.create": self._smb_create,
            "sharing.smb.update": self._smb_update,
            "sharing.smb.delete": self._smb_delete,
            "sharing.nfs.query": lambda p: self._query(self.nfs_shares, p),
            "sharing.nfs.get_instance": lambda p: self._get(self.nfs_shares, p[0], "NFS share"),
            "sharing.nfs.create": self._nfs_create,
            "sharing.nfs.update": self._nfs_update,
            "sharing.nfs.delete": self._nfs_delete,
        }
        if method not in handlers:
            raise _RpcError(-32601, f"Method not found: {method}")
        return handlers[method](params)

    def _subscribe(self, params: list[Any]) -> str:
        result = f"fake-subscription-{self._subscription_id}"
        self._subscription_id += 1
        return result

    def _send_container_metrics(self, ws: ServerConnection) -> None:
        fields = {
            str(ident): {
                "cpu": {"cpu_user_percentage": 1.25, "cpu_system_percentage": 0.75},
                "mem_usage_limit": {"mem_usage_limit_used_mib": 28.4, "mem_usage_limit_available_mib": 1024.0},
            }
            for ident, row in self.containers.items()
            if row.get("status", {}).get("state") == "RUNNING"
        }
        if fields:
            ws.send(json.dumps({"jsonrpc": "2.0", "method": "collection_update", "params": {
                "collection": "container.metrics", "msg": "added", "fields": fields,
            }}))

    @staticmethod
    def _get(rows: dict[Any, dict[str, Any]], key: Any, label: str) -> dict[str, Any]:
        try:
            return rows[int(key)] if isinstance(key, str) and key.isdigit() else rows[key]
        except (KeyError, ValueError):
            raise _RpcError(CALL_ERROR, f"{label} {key!r} not found") from None

    @staticmethod
    def _matches(row: dict[str, Any], filters: Any, *, username_key: str | None = None) -> bool:
        if not isinstance(filters, list):
            return True
        for item in filters:
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            key, operator, expected = item[:3]
            if key == "id":
                actual = row.get("id")
            else:
                actual = row.get(username_key or key)
            if operator == "=" and actual != expected:
                return False
            if operator == "!=" and actual == expected:
                return False
        return True

    def _query(self, rows: dict[Any, dict[str, Any]], params: list[Any], *, username_key: str | None = None) -> list[dict[str, Any]]:
        filters = params[0] if params else []
        return [row for row in rows.values() if self._matches(row, filters, username_key=username_key)]

    def _app_state(self, params: list[Any], state: str) -> None:
        app_id = params[0] if params else ""
        row = self._get(self.apps, app_id, "app")
        row["state"] = state
        return None

    def _app_delete(self, params: list[Any]) -> bool:
        app_id = params[0] if params else ""
        row = self._get(self.apps, app_id, "app")
        self.app_delete_options = dict(params[1]) if len(params) > 1 else {}
        del self.apps[row["id"]]
        return True

    def _audit_query(self, params: list[Any]) -> list[dict[str, Any]]:
        data = params[0] if params and isinstance(params[0], dict) else {}
        services = data.get("services", ["MIDDLEWARE"])
        filters = data.get("query-filters", [])
        options = data.get("query-options", {})
        rows = [dict(row) for row in self.audit_entries if row.get("service") in services]
        for filter_ in filters:
            if not isinstance(filter_, (list, tuple)) or len(filter_) != 3:
                continue
            key, operator, expected = filter_
            if operator == "=":
                rows = [row for row in rows if row.get(key) == expected]
            elif operator == ">=":
                rows = [row for row in rows if row.get(key) is not None and row[key] >= expected]
            elif operator == "<":
                rows = [row for row in rows if row.get(key) is not None and row[key] < expected]
        order_by = options.get("order_by", [])
        if order_by:
            field = order_by[0]
            reverse = isinstance(field, str) and field.startswith("-")
            key = field.lstrip("-") if isinstance(field, str) else "message_timestamp"
            rows.sort(key=lambda row: row.get(key), reverse=reverse)
        offset, limit = options.get("offset", 0), options.get("limit", 100)
        return rows[offset:offset + limit]

    def _filesystem_stat(self, params: list[Any]) -> dict[str, Any]:
        path = params[0] if params else ""
        if path not in self.share_paths:
            raise _RpcError(CALL_ERROR, f"No such file or directory: {path}")
        return {"realpath": path, "type": "DIRECTORY", "size": 4096, "allocation_size": 4096, "mode": 16877,
                "mount_id": 1, "uid": 0, "gid": 0, "atime": 0.0, "mtime": 0.0, "ctime": 0.0, "btime": 0.0,
                "dev": 1, "inode": 2, "nlink": 2, "acl": True, "is_mountpoint": False, "is_ctldir": False,
                "attributes": [], "user": "root", "group": "wheel"}

    def _smb_getacl(self, params: list[Any]) -> dict[str, Any]:
        share_name = params[0].get("share_name") if params and isinstance(params[0], dict) else None
        if not any(row.get("name") == share_name for row in self.smb_shares.values()):
            raise _RpcError(CALL_ERROR, f"SMB share {share_name!r} not found")
        return {"share_name": share_name, "share_acl": []}

    def _smb_create(self, params: list[Any]) -> dict[str, Any]:
        data = dict(params[0])
        if any(row.get("name", "").casefold() == data.get("name", "").casefold() for row in self.smb_shares.values()):
            raise _RpcError(CALL_ERROR, "SMB share already exists")
        ident = self._next_share_id
        self._next_share_id += 1
        row = {"id": ident, "purpose": "DEFAULT_SHARE", "enabled": True, "comment": "", "readonly": False,
               "browsable": True, "path": "", "name": "", "options": {}, **data}
        self.smb_shares[ident] = row
        self.share_paths.add(row["path"])
        return dict(row)

    def _smb_update(self, params: list[Any]) -> dict[str, Any]:
        row = self._get(self.smb_shares, params[0], "SMB share")
        row.update(params[1])
        return dict(row)

    def _smb_delete(self, params: list[Any]) -> bool:
        self._get(self.smb_shares, params[0], "SMB share")
        del self.smb_shares[int(params[0])]
        return True

    def _nfs_create(self, params: list[Any]) -> dict[str, Any]:
        data = dict(params[0])
        if any(row.get("path") == data.get("path") for row in self.nfs_shares.values()):
            raise _RpcError(CALL_ERROR, "NFS share already exists")
        ident = self._next_share_id
        self._next_share_id += 1
        row = {"id": ident, "path": "", "networks": [], "hosts": [], "ro": False, "security": [], **data}
        self.nfs_shares[ident] = row
        self.share_paths.add(row["path"])
        return dict(row)

    def _nfs_update(self, params: list[Any]) -> dict[str, Any]:
        row = self._get(self.nfs_shares, params[0], "NFS share")
        row.update(params[1])
        return dict(row)

    def _nfs_delete(self, params: list[Any]) -> bool:
        self._get(self.nfs_shares, params[0], "NFS share")
        del self.nfs_shares[int(params[0])]
        return True

    def _snapshot_task_create(self, params: list[Any]) -> dict[str, Any]:
        ident = self._next_snapshot_task_id
        self._next_snapshot_task_id += 1
        row = {"id": ident, "enabled": True, "recursive": False, "lifetime_value": 2,
               "lifetime_unit": "WEEK", "naming_schema": "auto-%Y-%m-%d_%H-%M", "allow_empty": True,
               "exclude": [], "schedule": {}, "vmware_sync": False, "state": {"state": "WAITING"}, **dict(params[0])}
        self.snapshot_tasks[ident] = row
        return dict(row)

    def _snapshot_task_update(self, params: list[Any]) -> dict[str, Any]:
        row = self._get(self.snapshot_tasks, params[0], "periodic snapshot task")
        row.update(params[1])
        return dict(row)

    def _snapshot_task_delete(self, params: list[Any]) -> bool:
        self._get(self.snapshot_tasks, params[0], "periodic snapshot task")
        del self.snapshot_tasks[int(params[0])]
        return True

    def _snapshot_task_run(self, params: list[Any]) -> None:
        row = self._get(self.snapshot_tasks, params[0], "periodic snapshot task")
        row["last_run"] = "2026-10-28T00:00:00Z"
        return None

    def _replication_update(self, params: list[Any]) -> dict[str, Any]:
        row = self._get(self.replication_tasks, params[0], "replication task")
        row.update(params[1])
        return dict(row)

    def _replication_run(self, params: list[Any]) -> None:
        self._get(self.replication_tasks, params[0], "replication task")
        return None

    def _cloudsync_update(self, params: list[Any]) -> dict[str, Any]:
        row = self._get(self.cloudsync_tasks, params[0], "cloud-sync task")
        row.update(params[1])
        return dict(row)

    def _cloudsync_run(self, params: list[Any]) -> None:
        self._get(self.cloudsync_tasks, params[0], "cloud-sync task")
        return None

    @staticmethod
    def _make_dataset(name: str, *, type_: str = "FILESYSTEM", **values: Any) -> dict[str, Any]:
        pool, _, tail = name.partition("/")
        return {"id": name, "name": tail or pool, "pool": pool, "type": type_, "children": [],
                "mountpoint": f"/mnt/{name}" if type_ == "FILESYSTEM" else None, "mounted": False,
                "used": {"value": "0 B"}, "available": {"value": "100 GiB"},
                "quota": {"value": "INHERIT"}, "refquota": {"value": "INHERIT"},
                "compression": {"value": "LZ4"}, "readonly": {"value": "OFF"}, "comments": {"value": ""},
                "vms": [], "containers": [], **values}

    def _dataset_query(self, params: list[Any]) -> list[dict[str, Any]]:
        filters = params[0] if params else []
        return [row for row in self.datasets.values() if self._matches(row, filters)]

    def _dataset_tree(self) -> list[dict[str, Any]]:
        roots = []
        for name, row in sorted(self.datasets.items()):
            row["children"] = []
        for name, row in sorted(self.datasets.items()):
            parent = name.rpartition("/")[0]
            if parent in self.datasets:
                self.datasets[parent].setdefault("children", []).append(row)
            else:
                roots.append(row)
        return roots

    def _dataset_create(self, params: list[Any]) -> dict[str, Any]:
        data = dict(params[0])
        name = data.get("name")
        if not isinstance(name, str) or name in self.datasets:
            raise _RpcError(CALL_ERROR, "dataset name is missing or already exists")
        row = self._make_dataset(name, type_=data.get("type", "FILESYSTEM"))
        row.update({key: {"value": value} for key, value in data.items() if key not in {"name", "type", "children"}})
        self.datasets[name] = row
        return row

    def _dataset_update(self, params: list[Any]) -> dict[str, Any]:
        row = self._get(self.datasets, params[0], "dataset")
        for key, value in dict(params[1]).items():
            row[key] = {"value": value}
        return row

    def _dataset_delete(self, params: list[Any]) -> bool:
        name = params[0]
        options = params[1] if len(params) > 1 else {}
        descendants = [candidate for candidate in self.datasets if candidate.startswith(f"{name}/")]
        if descendants and not options.get("recursive"):
            raise _RpcError(CALL_ERROR, "dataset has children")
        for candidate in [*descendants, name]:
            self.datasets.pop(candidate, None)
        return True

    def _snapshot_query(self, params: list[Any]) -> list[dict[str, Any]]:
        filters = params[0] if params else []
        return [row for row in self.snapshots.values() if self._matches(row, filters)]

    def _snapshot_create(self, params: list[Any]) -> dict[str, Any]:
        data = dict(params[0])
        name = f"{data['dataset']}@{data['name']}"
        if name in self.snapshots:
            raise _RpcError(CALL_ERROR, "snapshot already exists")
        row = {"id": name, "name": data["name"], "dataset": data["dataset"], "held": False,
               "recursive": data.get("recursive", False)}
        self.snapshots[name] = row
        return row

    def _snapshot_clone(self, params: list[Any]) -> dict[str, Any]:
        data = dict(params[0])
        source = self._get(self.snapshots, data["snapshot"], "snapshot")
        row = self._make_dataset(data["dataset_dst"], clone_of=source["id"])
        self.datasets[row["id"]] = row
        return row

    def _snapshot_hold(self, params: list[Any], held: bool) -> bool:
        row = self._get(self.snapshots, params[0], "snapshot")
        row["held"] = held
        return True

    def _snapshot_delete(self, params: list[Any]) -> bool:
        self.snapshots.pop(params[0], None)
        return True

    def _container_create(self, params: list[Any]) -> dict[str, Any]:
        data = dict(params[0])
        row = {"id": self._next_container_id, "name": data["name"], "status": {"state": "STOPPED"},
               "autostart": data.get("autostart", True), "cpuset": data.get("cpuset"),
               "pool": data.get("pool", "tank"), "dataset": f"tank/containers/{data['name']}", "devices": [],
               "memory": {"usage": 0}}
        self.containers[row["id"]] = row
        self._next_container_id += 1
        return row

    def _container_update(self, params: list[Any]) -> dict[str, Any]:
        row = self._get(self.containers, params[0], "container")
        row.update(dict(params[1]))
        return row

    def _container_state(self, params: list[Any], state: str) -> dict[str, Any]:
        row = self._get(self.containers, params[0], "container")
        row["status"] = {"state": state}
        return row

    def _container_delete(self, params: list[Any]) -> bool:
        self.containers.pop(int(params[0]), None)
        return True

    def _vm_create(self, params: list[Any]) -> dict[str, Any]:
        data = dict(params[0])
        row = {"id": self._next_vm_id, "name": data["name"], "status": {"state": "STOPPED", "pid": None},
               "devices": [], "display_available": False, "memory": data["memory"], "vcpus": data.get("vcpus", 1),
               "autostart": data.get("autostart", True), **data}
        self.vms[row["id"]] = row
        self._next_vm_id += 1
        return row

    def _vm_update(self, params: list[Any]) -> dict[str, Any]:
        row = self._get(self.vms, params[0], "VM")
        row.update(dict(params[1]))
        return row

    def _vm_state(self, params: list[Any], state: str) -> dict[str, Any]:
        row = self._get(self.vms, params[0], "VM")
        row["status"] = {"state": state, "pid": None}
        return row

    def _vm_delete(self, params: list[Any]) -> bool:
        self.vms.pop(int(params[0]), None)
        for device_id in [key for key, value in self.vm_devices.items() if value["vm"] == int(params[0])]:
            self.vm_devices.pop(device_id, None)
        return True

    def _vm_device_create(self, params: list[Any]) -> dict[str, Any]:
        data = dict(params[0])
        row = {"id": self._next_device_id, "vm": data["vm"], "order": data.get("order", self._next_device_id),
               "attributes": dict(data["attributes"])}
        self.vm_devices[row["id"]] = row
        self._next_device_id += 1
        vm = self._get(self.vms, row["vm"], "VM")
        vm["devices"] = [*vm.get("devices", []), row]
        vm["display_available"] = any(device["attributes"].get("dtype") == "DISPLAY" for device in vm["devices"])
        return row

    def _vm_device_update(self, params: list[Any]) -> dict[str, Any]:
        row = self._get(self.vm_devices, params[0], "VM device")
        row.update(dict(params[1]))
        return row

    def _vm_device_delete(self, params: list[Any]) -> bool:
        self.vm_devices.pop(int(params[0]), None)
        return True

    def _vm_get_console(self, params: list[Any]) -> str:
        vm_id = int(params[0])
        vm = self._get(self.vms, vm_id, "VM")
        return f"{vm_id}_{vm['name']}"

    def _vm_get_display_devices(self, params: list[Any]) -> list[dict[str, Any]]:
        vm_id = int(params[0])
        return [
            {**device, "attributes": {**device["attributes"],
                                      "password_configured": bool(device["attributes"].get("password"))}}
            for device in self.vm_devices.values()
            if device["vm"] == vm_id and device["attributes"].get("dtype") == "DISPLAY"
        ]

    def _vm_get_display_web_uri(self, params: list[Any]) -> dict[str, Any]:
        vm_id, host = int(params[0]), params[1] if len(params) > 1 else ""
        devices = self._vm_get_display_devices([vm_id])
        for device in devices:
            attributes = device["attributes"]
            if attributes.get("web"):
                web_host = host or "127.0.0.1"
                protocol = params[2].get("protocol", "HTTP").lower() if len(params) > 2 else "http"
                path = f"vm/display/{device['id']}/"
                query = urlencode({"path": path, "autoconnect": 1})
                return {"error": None, "uri": f"{protocol}://{web_host}/{path}spice_auto.html?{query}"}
        error = "Web display is not configured" if devices else "Display device is not configured for this VM"
        return {"error": error, "uri": None}

    def _user_create(self, params: list[Any]) -> dict[str, Any]:
        data = dict(params[0])
        username = data["username"]
        if any(row["username"] == username for row in self.users.values()):
            raise _RpcError(CALL_ERROR, "user already exists")
        if data.get("group_create"):
            group = {"id": max(self.groups, default=0) + 1, "gid": max(self.groups, default=0) + 1,
                     "name": username, "builtin": False}
            self.groups[group["id"]] = group
        else:
            group = self._get(self.groups, data["group"], "group")
        row = {"id": self._next_user_id, "uid": self._next_user_id, "username": username,
               "full_name": data["full_name"], "builtin": False, "local": True,
               "groups": data.get("groups", []), "group": group, "shell": data.get("shell", "/usr/bin/zsh"),
               "home": data.get("home", f"/home/{username}"), "sshpubkey": data.get("sshpubkey"),
               "password_disabled": data.get("password_disabled", False)}
        self.users[row["id"]] = row
        if data.get("password"):
            self.user_passwords[username] = data["password"]
        self._next_user_id += 1
        return {**row, "password": data.get("password")}

    def _user_update(self, params: list[Any]) -> dict[str, Any]:
        row = self._get(self.users, params[0], "user")
        row.update(dict(params[1]))
        return row

    def _user_delete(self, params: list[Any]) -> bool:
        row = self._get(self.users, params[0], "user")
        if row["builtin"] or row["username"] == self.username:
            raise _RpcError(CALL_ERROR, "cannot delete built-in or current login user")
        del self.users[row["id"]]
        return True

    def _user_set_password(self, params: list[Any]) -> None:
        data = dict(params[0])
        self.user_passwords[data["username"]] = data["new_password"]
        return None

    def _details(self, params: list[Any]) -> Any:
        rows = [dict(d, partitions=self.parts[d["name"]]) for d in self.disks if d["name"] in self.parts]
        if self.details_as_flat_list:
            return rows
        return {"used": [r for r in rows if r.get("pool")], "unused": [r for r in rows if not r.get("pool")]}


class _RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message
