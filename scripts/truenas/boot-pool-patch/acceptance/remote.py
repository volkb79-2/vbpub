"""Small, fail-closed SSH and middleware helpers for disposable TrueNAS tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any


class RemoteError(RuntimeError):
    pass


@dataclass(frozen=True)
class Target:
    host: str
    port: int
    key: Path
    known_hosts: Path
    user: str = "root"

    @classmethod
    def from_environment(cls) -> "Target":
        required = {
            "TRUENAS_TEST_HOST": os.environ.get("TRUENAS_TEST_HOST"),
            "TRUENAS_TEST_KEY": os.environ.get("TRUENAS_TEST_KEY"),
            "TRUENAS_TEST_KNOWN_HOSTS": os.environ.get("TRUENAS_TEST_KNOWN_HOSTS"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RemoteError(f"required environment variables are missing: {', '.join(missing)}")

        key = Path(required["TRUENAS_TEST_KEY"] or "")
        known_hosts = Path(required["TRUENAS_TEST_KNOWN_HOSTS"] or "")
        for label, path in (("SSH identity", key), ("known_hosts", known_hosts)):
            if not path.is_file():
                raise RemoteError(f"{label} file does not exist: {path}")

        try:
            port = int(os.environ.get("TRUENAS_TEST_PORT", ""))
        except ValueError as exc:
            raise RemoteError("TRUENAS_TEST_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise RemoteError("TRUENAS_TEST_PORT must be between 1 and 65535")

        return cls(
            host=required["TRUENAS_TEST_HOST"] or "",
            port=port,
            key=key,
            known_hosts=known_hosts,
            user=os.environ.get("TRUENAS_TEST_USER", "root"),
        )

    def run(self, remote_argv: list[str], *, timeout: int = 45, check: bool = True) -> str:
        ssh_argv = [
            "ssh",
            "-i", str(self.key),
            "-p", str(self.port),
            "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={self.known_hosts}",
            "-o", "ConnectTimeout=10",
            f"{self.user}@{self.host}",
            shlex.join(remote_argv),
        ]
        result = None
        for attempt in range(3):
            try:
                result = subprocess.run(
                    ssh_argv, text=True, capture_output=True, timeout=timeout, check=False
                )
            except subprocess.TimeoutExpired as exc:
                raise RemoteError(f"remote command timed out after {timeout}s: {remote_argv[0]}") from exc
            message = (result.stderr or "").lower()
            ssh_never_started = result.returncode == 255 and any(
                phrase in message
                for phrase in (
                    "connection timed out during banner exchange",
                    "connection refused",
                    "no route to host",
                    "network is unreachable",
                    "temporary failure in name resolution",
                )
            )
            ssh_never_started = ssh_never_started or (
                result.returncode == 255
                and "kex_exchange_identification" in message
                and "connection reset by peer" in message
            )
            if not ssh_never_started or attempt == 2:
                break
            delay = 2 ** attempt
            print(
                f"[retry] SSH connection for {remote_argv[0]} failed before command startup; "
                f"retrying in {delay}s ({attempt + 1}/2)",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)
        assert result is not None
        if check and result.returncode:
            detail = (result.stderr or result.stdout).strip()[-2500:]
            raise RemoteError(
                f"remote command failed ({result.returncode}): {remote_argv[0]}"
                + (f"\n{detail}" if detail else "")
            )
        if not check and result.returncode:
            return result.stderr.strip() or result.stdout.strip()
        return result.stdout.strip()

    def shell(self, script: str, *, timeout: int = 45, check: bool = True) -> str:
        return self.run(["bash", "-lc", script], timeout=timeout, check=check)

    def call(self, method: str, *params: Any, timeout: int = 90) -> Any:
        argv = ["midclt", "call", method]
        for value in params:
            if isinstance(value, str):
                argv.append(value)
            else:
                argv.append(json.dumps(value, separators=(",", ":")))
        output = ""
        for attempt in range(3):
            try:
                output = self.run(argv, timeout=timeout)
                break
            except RemoteError as exc:
                # middlewared raises this while constructing its WebSocket
                # client, before sending the method call; retry only that
                # exact pre-call handshake failure. SSH startup retries live
                # in run(), where we can distinguish them from lost replies.
                transport_startup_failure = "failed connection handshake" in str(exc).lower()
                if transport_startup_failure and attempt < 2:
                    delay = 2 ** attempt
                    print(
                        f"[retry] midclt {method} websocket handshake failed; retrying in {delay}s "
                        f"({attempt + 1}/2)",
                        file=sys.stderr,
                        flush=True,
                    )
                    time.sleep(delay)
                    continue
                raise RemoteError(f"midclt call {method} failed: {exc}") from exc
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            python_scalars = {"True": True, "False": False, "None": None}
            if output in python_scalars:
                return python_scalars[output]
            raise RemoteError(f"midclt {method} returned invalid JSON: {output[:500]!r}") from exc

    def query(self, method: str, *params: Any, timeout: int = 90) -> Any:
        """Retry only read-only query calls when middleware reports a timeout."""
        if not method.endswith(".query"):
            raise RemoteError(f"refusing retryable query helper for non-query method {method!r}")

        for attempt in range(3):
            try:
                return self.call(method, *params, timeout=timeout)
            except RemoteError as exc:
                message = str(exc)
                retryable = (
                    "Call timeout" in message
                    or "remote command timed out after" in message
                )
                if not retryable or attempt == 2:
                    raise
                delay = 2 ** attempt
                print(
                    f"[retry] read-only {method} timed out; retrying in {delay}s "
                    f"({attempt + 1}/2)",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(delay)

    def wait_job(self, job_id: int, *, timeout: int = 600, label: str = "middleware job") -> Any:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            jobs = self.call("core.get_jobs", [["id", "=", job_id]], timeout=45)
            if not jobs:
                time.sleep(2)
                continue
            job = jobs[0]
            state = job.get("state")
            if state == "SUCCESS":
                return job.get("result")
            if state in {"FAILED", "ABORTED"}:
                raise RemoteError(
                    f"{label} {job_id} ended {state}: {job.get('error') or job.get('exception') or job}"
                )
            time.sleep(2)
        raise RemoteError(f"{label} {job_id} did not finish within {timeout}s")


def flatten_datasets(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    flattened: dict[str, dict[str, Any]] = {}

    def visit(row: dict[str, Any]) -> None:
        flattened[row["id"]] = row
        for child in row.get("children") or []:
            visit(child)

    for row in rows:
        visit(row)
    return flattened


def wait_for_vm(target: Target, vm_id: int, state: str, *, timeout: int = 180) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_query_error: str | None = None
    while time.monotonic() < deadline:
        try:
            rows = target.query("vm.query", [["id", "=", vm_id]], timeout=45)
            last_query_error = None
        except RemoteError as exc:
            message = str(exc)
            if "Call timeout" not in message and "remote command timed out after" not in message:
                raise
            last_query_error = message
            print(
                f"[retry] vm.query could not determine VM {vm_id} state; retrying before deadline",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(3)
            continue
        if rows and rows[0].get("status", {}).get("state") == state:
            return rows[0]
        time.sleep(3)
    detail = f"; last query failure: {last_query_error}" if last_query_error else ""
    raise RemoteError(f"VM {vm_id} did not reach {state} within {timeout}s{detail}")


def wait_for_container(target: Target, container_id: int, state: str, *, timeout: int = 240) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_query_error: str | None = None
    while time.monotonic() < deadline:
        try:
            rows = target.query("container.query", [["id", "=", container_id]], timeout=45)
            last_query_error = None
        except RemoteError as exc:
            message = str(exc)
            if "Call timeout" not in message and "remote command timed out after" not in message:
                raise
            last_query_error = message
            print(
                f"[retry] container.query could not determine container {container_id} state; "
                "retrying before deadline",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(3)
            continue
        if rows:
            status = rows[0].get("status") or {}
            if status.get("state") == state:
                return rows[0]
        time.sleep(3)
    detail = f"; last query failure: {last_query_error}" if last_query_error else ""
    raise RemoteError(f"container {container_id} did not reach {state} within {timeout}s{detail}")
