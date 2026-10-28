"""Test support: a minimal fake TrueNAS JSON-RPC 2.0 websocket server.

It speaks just enough of the middleware protocol for ``truenas_api_client``: ``core.set_options``,
``auth.mechanism_choices``, ``auth.login_ex`` with ``API_KEY_PLAIN`` (SCRAM is not implemented: the live lane
covers it), ``system.version``, ``system.info``, ``core.get_methods``, ``disk.query`` and ``disk.details``.
It listens on loopback only and records every request, so tests can assert which methods were called.
This module imports ``websockets``: it is for tests only and is never imported by the shipped CLI or TUI.
"""
from __future__ import annotations

import json
import threading
from typing import Any, Callable

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
                 disks: list[dict[str, Any]] | None = None, parts: dict[str, list[dict[str, Any]]] | None = None,
                 methods: list[str] | None = None, fail: dict[str, str] | None = None, details_as_flat_list: bool = False):
        std_disks, std_parts = standard_disks()
        self.version, self.username, self.api_key = version, username, api_key
        self.disks = std_disks if disks is None else disks
        self.parts = std_parts if parts is None else parts
        self.methods = sorted(m for ms in V27.manifest.values() for m in ms) if methods is None else methods
        self.fail = dict(fail or {})
        self.pools: Any = [{"id": 1, "name": "tank", "topology": {"data": [{"type": "DISK", "disk": "sdb", "path": "/dev/sdb1"}]}}]
        self.boot_state: Any = {"name": "boot-pool", "topology": {"data": [{"type": "DISK", "disk": "sda3", "path": "/dev/sda3"}]}}
        self.details_as_flat_list = details_as_flat_list
        self.requests: list[dict[str, Any]] = []
        self.logins = 0
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
        return Profile(name=name, url=self.url, username=self.username, tls="verify", auth="plain", **kw)

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
                    result = ["API_KEY_PLAIN"] if method == "auth.mechanism_choices" else None
                elif not authed:
                    raise _RpcError(CALL_ERROR, "Not authenticated")
                else:
                    result = self._dispatch(method, params)
                reply: dict[str, Any] = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
            except _RpcError as exc:
                error: dict[str, Any] = {"code": exc.code, "message": exc.message}
                if exc.code == CALL_ERROR:  # the client reads these fields from a call error
                    error["data"] = {"reason": exc.message, "error": 22, "trace": None, "extra": None}
                reply = {"jsonrpc": "2.0", "id": msg["id"], "error": error}
            ws.send(json.dumps(reply))

    def _login(self, params: list[Any], authed: bool) -> tuple[Any, bool]:
        self.logins += 1
        p = params[0] if params else {}
        if p.get("mechanism") == "API_KEY_PLAIN" and p.get("username") == self.username and p.get("api_key") == self.api_key:
            return {"response_type": "SUCCESS"}, True
        return {"response_type": "AUTH_ERR"}, authed

    def _dispatch(self, method: str, params: list[Any]) -> Any:
        handlers: dict[str, Callable[[list[Any]], Any]] = {
            "system.version": lambda p: self.version,
            "system.info": lambda p: {"version": self.version, "hostname": "fake"},
            "core.get_methods": lambda p: {m: {} for m in self.methods},
            "disk.query": lambda p: self.disks,
            "disk.details": self._details,
            "pool.query": lambda p: self.pools,
            "boot.get_state": lambda p: self.boot_state,
        }
        if method not in handlers:
            raise _RpcError(-32601, f"Method not found: {method}")
        return handlers[method](params)

    def _details(self, params: list[Any]) -> Any:
        rows = [dict(d, partitions=self.parts[d["name"]]) for d in self.disks if d["name"] in self.parts]
        if self.details_as_flat_list:
            return rows
        return {"used": [r for r in rows if r.get("pool")], "unused": [r for r in rows if not r.get("pool")]}


class _RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message
