"""run_gate_profile: pytest plugin writing one resource profile per process.

RG-146 / RESOURCE-PROFILES design 6.3. Run-gate sets `RUN_GATE_PROFILE_DIR`
for a lane with `workers` and mounts cgprofile's observer socket (path in
`ASSAY_RESOURCE_OBSERVER_SOCKET`). In the xdist controller and in every
worker this plugin opens a cgprofile `process-tree` window rooted at its own
PID at `pytest_configure`, closes it at `pytest_unconfigure`, and writes a
`resource-profile/1` file `<id>.json` (`controller` or `gw<N>`).

The plugin is inert unless `RUN_GATE_PROFILE_DIR` is set. Without a usable
observer (socket unset, daemon without contract 3, any wire error) the
profile is still written, with status `NO_MEASUREMENT` and the reason, so
that a missing number is never mistaken for a zero.

The window wire is the design's section 3.1 (contract 3); the daemon side is
cgprofile CP-39 and is not verified against a live daemon by this module's
tests, which use a fake observer socket.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

PROFILE_DIR_ENV = "RUN_GATE_PROFILE_DIR"
OBSERVER_SOCKET_ENV = "ASSAY_RESOURCE_OBSERVER_SOCKET"
WINDOW_INTERVAL_MS = 250
OBSERVER_TIMEOUT_SECONDS = 5.0


def observer_call(socket_path: str, verb: str, args: dict) -> dict:
    """One request/reply line on the observer socket (contract 3)."""
    request = json.dumps({"verb": verb, "args": args, "contract": 3}) + "\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(OBSERVER_TIMEOUT_SECONDS)
        connection.connect(socket_path)
        connection.sendall(request.encode())
        reply = b""
        while not reply.endswith(b"\n"):
            chunk = connection.recv(65536)
            if not chunk:
                break
            reply += chunk
    document = json.loads(reply.decode())
    if not isinstance(document, dict) or document.get("ok") is not True:
        raise RuntimeError(f"observer refused {verb}: {document!r}")
    return document


class ProcessProfile:
    """One process's window and the profile it writes."""

    def __init__(self, directory: Path, subject_id: str, kind: str,
                 socket_path: str | None) -> None:
        self.directory = directory
        self.subject_id = subject_id
        self.kind = kind
        self.socket_path = socket_path
        self.window: str | None = None
        self.reason: str | None = None
        self.started = time.monotonic()
        self.tests_run = 0
        self.tests_collected: int | None = None

    def open(self) -> None:
        if not self.socket_path:
            self.reason = f"{OBSERVER_SOCKET_ENV} is not set"
            return
        try:
            reply = observer_call(self.socket_path, "window-open", {
                "thread_id": threading.get_native_id(), "root_pid": os.getpid(),
                "mode": "process-tree", "interval_ms": WINDOW_INTERVAL_MS})
            self.window = reply["window"]
        except Exception as exc:  # noqa: BLE001 - never fail the lane
            self.reason = f"window-open failed: {type(exc).__name__}: {exc}"

    def close(self) -> dict:
        """Close the window and build the profile object."""
        wall = round(time.monotonic() - self.started, 3)
        closed: dict = {}
        if self.window is not None:
            try:
                closed = observer_call(self.socket_path, "window-close", {
                    "thread_id": threading.get_native_id(),
                    "window": self.window})
            except Exception as exc:  # noqa: BLE001 - never fail the lane
                self.reason = f"window-close failed: {type(exc).__name__}: {exc}"
        measured = bool(closed) and isinstance(closed.get("metrics"), dict)
        subject = {"tool": "run-gate", "kind": self.kind, "id": self.subject_id,
                   "tests": self.tests_run}
        if self.tests_collected is not None:
            subject["tests_collected"] = self.tests_collected
        return {
            "schema": "resource-profile/1", "subject": subject,
            "wall_seconds": wall,
            "status": "MEASURED" if measured else "NO_MEASUREMENT",
            "reason": None if measured else (
                self.reason or "observer returned no metrics"),
            "observer": ({"name": "cgprofile", "contract": 3,
                          "mode": "process-tree", "window": self.window}
                         if self.window else None),
            "metrics": closed.get("metrics") if measured else {},
            "missing": closed.get("missing", {}),
            "sources": closed.get("sources", {}),
            "context": closed.get("context", {}),
        }

    def write(self) -> Path:
        profile = self.close()
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{self.subject_id}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(profile, sort_keys=True) + "\n",
                             encoding="utf-8")
        temporary.replace(target)
        return target


_PROFILE: ProcessProfile | None = None


def subject_for(config) -> tuple[str, str]:
    """`(id, kind)`: `gw<N>` for an xdist worker, `controller` otherwise."""
    worker_input = getattr(config, "workerinput", None)
    if worker_input is not None:
        return str(worker_input["workerid"]), "xdist-worker"
    return "controller", "controller"


def pytest_configure(config) -> None:
    global _PROFILE
    directory = os.environ.get(PROFILE_DIR_ENV)
    if not directory:
        return
    subject_id, kind = subject_for(config)
    _PROFILE = ProcessProfile(Path(directory), subject_id, kind,
                              os.environ.get(OBSERVER_SOCKET_ENV))
    _PROFILE.open()


def pytest_collection_finish(session) -> None:
    if _PROFILE is not None and _PROFILE.kind == "controller":
        _PROFILE.tests_collected = len(session.items)


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node, ids) -> None:
    """The xdist controller does not collect itself; a worker's collected id
    list is the suite size.

    `optionalhook=True`: the hookspec comes from pytest-xdist. Without it, a
    pytest run where xdist is absent or disabled (`-p no:xdist`, as tnt's and
    other lanes do) dies in `check_pending` with an INTERNALERROR
    (PluginValidationError: unknown hook), because this plugin is registered
    through the `pytest11` entry point in every pytest in the venv."""
    if _PROFILE is not None and _PROFILE.kind == "controller":
        _PROFILE.tests_collected = len(ids)


def pytest_runtest_logreport(report) -> None:
    if _PROFILE is not None and report.when == "call":
        _PROFILE.tests_run += 1


def pytest_unconfigure(config) -> None:
    global _PROFILE
    profile, _PROFILE = _PROFILE, None
    if profile is None:
        return
    # A profiling failure must never change the lane verdict: a missing
    # number is NO_MEASUREMENT, not an error.
    try:
        profile.write()
    except Exception as exc:  # noqa: BLE001 - profiling is best-effort
        print(f"run-gate profile: NO_MEASUREMENT, could not write "
              f"{profile.subject_id}: {type(exc).__name__}: {exc}",
              file=sys.stderr)
