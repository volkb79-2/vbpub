#!/usr/bin/env python3
"""Narrow host-side ZFS lifecycle broker for remote mutation audits.

The tester receives only a Unix socket.  It never receives /dev/zfs, host root,
sudo, or a privileged container.  The broker accepts four fixed operations for
one prevalidated, explicitly opted-in dataset and one run-unique snapshot.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import tomllib
from datetime import UTC, datetime
from pathlib import Path


DATASET_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,254}")
SNAPSHOT_RE = re.compile(r"nyxloom-[A-Za-z0-9_.-]{1,120}")


class BrokerError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ZfsBroker:
    def __init__(
        self,
        manifest: Path,
        snapshot_name: str,
        events_path: Path,
        allowed_prefix: str,
        use_sudo: bool,
    ) -> None:
        raw = tomllib.loads(manifest.read_text(encoding="utf-8")).get("zfs", {})
        if raw.get("enabled") is not True:
            raise BrokerError("[zfs].enabled must be true")
        self.dataset = str(raw.get("dataset", ""))
        if (
            DATASET_RE.fullmatch(self.dataset) is None
            or DATASET_RE.fullmatch(allowed_prefix) is None
            or "@" in self.dataset
            or "@" in allowed_prefix
            or not self.dataset.startswith(allowed_prefix.rstrip("/") + "/")
        ):
            raise BrokerError("dataset must be a child of the host-authorized ZFS prefix")
        if SNAPSHOT_RE.fullmatch(snapshot_name) is None:
            raise BrokerError("unsafe audit snapshot name")
        self.snapshot = f"{self.dataset}@{snapshot_name}"
        self.property = "nyxloom:mutation-audit"
        self.property_value = "on"
        self.command_prefix = ("sudo", "-n") if use_sudo else ()
        self.token = os.environ.get("NYXLOOM_ZFS_BROKER_TOKEN", "")
        if not re.fullmatch(r"[0-9a-f]{64}", self.token):
            raise BrokerError("missing or invalid broker capability token")
        self.events_path = events_path
        self.created = False
        self._preflight()
        self._record("preflight", "PASS", mutant_id=None)

    def _run(self, *args: str) -> str:
        argv = [*self.command_prefix, "zfs", *args]
        proc = subprocess.run(argv, text=True, capture_output=True)
        if proc.returncode:
            raise BrokerError(f"{' '.join(argv)} failed ({proc.returncode}): {proc.stderr[-2000:].strip()}")
        return proc.stdout.strip()

    def _preflight(self) -> None:
        actual = self._run("list", "-H", "-o", "name", self.dataset)
        if actual != self.dataset:
            raise BrokerError("zfs list did not resolve the exact configured dataset")
        guard = self._run("get", "-H", "-o", "value", self.property, self.dataset)
        if guard != self.property_value:
            raise BrokerError(
                f"dataset lacks safety guard: zfs set {self.property}={self.property_value} {self.dataset}"
            )
        mountpoint = self._run("get", "-H", "-o", "value", "mountpoint", self.dataset)
        if not mountpoint.startswith("/") or mountpoint == "/" or not Path(mountpoint).is_dir():
            raise BrokerError("dataset must have an existing, non-root filesystem mountpoint")
        self.mountpoint = mountpoint
        exists = subprocess.run(
            [*self.command_prefix, "zfs", "list", "-H", "-t", "snapshot", "-o", "name", self.snapshot],
            text=True, capture_output=True,
        )
        if exists.returncode == 0:
            raise BrokerError(f"refusing to reuse existing snapshot {self.snapshot}")

    def _record(self, operation: str, outcome: str, *, mutant_id: str | None, error: str | None = None) -> None:
        payload = {
            "kind": "zfs", "operation": operation, "outcome": outcome,
            "dataset": self.dataset, "snapshot": self.snapshot,
            "mutant_id": mutant_id, "finished_at": _now(),
        }
        if error is not None:
            payload["error"] = error
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def execute(self, operation: str, mutant_id: str | None) -> dict[str, str]:
        try:
            if operation == "snapshot_create":
                if self.created:
                    raise BrokerError("snapshot already created")
                self._run("snapshot", self.snapshot)
                self.created = True
            elif operation == "restore":
                if not self.created:
                    raise BrokerError("snapshot has not been created")
                # Deliberately omit -r/-R: later snapshots or clones make this
                # fail closed instead of being destroyed as collateral damage.
                self._run("rollback", self.snapshot)
            elif operation == "snapshot_destroy":
                if not self.created:
                    raise BrokerError("snapshot has not been created")
                self._run("destroy", self.snapshot)
                self.created = False
            elif operation != "status":
                raise BrokerError(f"unsupported operation {operation!r}")
            self._record(operation, "PASS", mutant_id=mutant_id)
            return {"outcome": "PASS", "operation": operation}
        except Exception as exc:
            self._record(operation, "ERROR", mutant_id=mutant_id, error=str(exc))
            return {"outcome": "ERROR", "operation": operation, "error": str(exc)}


def _dispatch(broker: ZfsBroker, request: object) -> dict[str, str]:
    if not isinstance(request, dict):
        return {"outcome": "ERROR", "error": "request must be an object"}
    supplied = str(request.get("token", ""))
    if not secrets.compare_digest(supplied, broker.token):
        return {"outcome": "ERROR", "error": "unauthorized host lifecycle request"}
    return broker.execute(str(request.get("operation", "")), request.get("mutant_id"))


def _serve(broker: ZfsBroker, socket_path: Path, ready_path: Path) -> int:
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(socket_path))
        os.chmod(socket_path, 0o666)
        server.listen(4)
        server.settimeout(1.0)
        ready_path.write_text(
            json.dumps({"dataset": broker.dataset, "mountpoint": broker.mountpoint, "snapshot": broker.snapshot}) + "\n",
            encoding="utf-8",
        )
        while not stopping:
            try:
                connection, _ = server.accept()
            except TimeoutError:
                continue
            with connection:
                request_bytes = b""
                while not request_bytes.endswith(b"\n") and len(request_bytes) <= 65536:
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    request_bytes += chunk
                try:
                    request = json.loads(request_bytes)
                    reply = _dispatch(broker, request)
                except Exception as exc:
                    reply = {"outcome": "ERROR", "error": f"invalid request: {exc}"}
                connection.sendall((json.dumps(reply, sort_keys=True) + "\n").encode())
    if broker.created:
        broker._record("shutdown", "SNAPSHOT_RETAINED", mutant_id=None)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--socket", required=True, type=Path)
    parser.add_argument("--ready", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--snapshot-name", required=True)
    parser.add_argument("--allowed-prefix", required=True)
    parser.add_argument("--sudo", action="store_true", help="invoke zfs through sudo -n")
    args = parser.parse_args()
    for path in (args.socket, args.ready):
        if path.exists():
            raise SystemExit(f"refusing existing broker path: {path}")
    broker = ZfsBroker(
        args.manifest, args.snapshot_name, args.events, args.allowed_prefix, args.sudo,
    )
    return _serve(broker, args.socket, args.ready)


if __name__ == "__main__":
    raise SystemExit(main())
