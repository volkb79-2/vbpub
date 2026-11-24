"""Stateful fake machine boundaries for vm_lab.guest tests."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import socket
import stat
import subprocess
import textwrap
import threading
import time
from pathlib import Path
from typing import Callable, Iterator

BOOT_IDS = (
    "11111111-1111-1111-1111-111111111111",
    "22222222-2222-2222-2222-222222222222",
    "33333333-3333-3333-3333-333333333333",
    "44444444-4444-4444-4444-444444444444",
)
PANIC_LINE = "[   12.345678] Kernel panic - not syncing: Attempted to kill init! exitcode=0x00000100\n"
HOST_KEY = "ZmFrZWhvc3RrZXk="


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeMachine:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.state_path = root / "machine.json"
        self.serial_log = root / "serial.log"
        self.lock_path = root / "machine.lock"
        self.serial_log.write_text("initial boot\n")
        initial = {
            "boot_id": BOOT_IDS[0],
            "boot_index": 0,
            "up": True,
            "boots": 1,
            "pending_reboot": False,
            "keep_boot_id": False,
            "old_boot_answers": 0,
            "panic_on_boot": False,
            "exec_broken": False,
            "host_key": HOST_KEY,
            "master": False,
            "link_loss": False,
            "late_check": 0,
            "silent_loss": False,
            "commands": 0,
            "last_command": "",
            "send_keys": 0,
            "cursor": 0,
            "entry": "new",
            "entries": ["new", "older"],
            "boot_ids": list(BOOT_IDS),
            "panic_line": PANIC_LINE,
            "master_boot_id": None,
        }
        self.state_path.write_text(json.dumps(initial) + "\n")

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def read(self) -> dict:
        with self._locked():
            return json.loads(self.state_path.read_text())

    def update(self, **changes) -> dict:
        with self._locked():
            state = json.loads(self.state_path.read_text())
            state.update(changes)
            self.state_path.write_text(json.dumps(state) + "\n")
            return state

    def mutate(self, function: Callable[[dict], None]) -> dict:
        with self._locked():
            state = json.loads(self.state_path.read_text())
            function(state)
            self.state_path.write_text(json.dumps(state) + "\n")
            return state

    def append_serial(self, text: str) -> None:
        with self.serial_log.open("a", encoding="utf-8") as output:
            output.write(text)

    def reset(self, *, keep_boot_id: bool = False) -> str:
        def change(state: dict) -> None:
            state["boot_index"] = min(state["boot_index"] + 1, len(BOOT_IDS) - 1)
            if not keep_boot_id and not state.get("keep_boot_id"):
                state["boot_id"] = BOOT_IDS[state["boot_index"]]
            state["boots"] += 1
            state["pending_reboot"] = False
            state["old_boot_answers"] = 0
            state["master"] = False
            state["master_boot_id"] = None
            if state.get("panic_on_boot"):
                state["up"] = False
                self.append_serial(state["panic_line"])
            else:
                state["up"] = True
                self.append_serial(f"boot {state['boot_id']} entry={state.get('entry', 'new')}\n")
            return state["boot_id"]

        return self.mutate(change)["boot_id"]

    def send_key(self, key: str) -> None:
        def change(state: dict) -> None:
            state["send_keys"] += 1
            entries = state["entries"]
            if key == "down":
                state["cursor"] = min(int(state["cursor"]) + 1, len(entries) - 1)
            elif key == "up":
                state["cursor"] = max(int(state["cursor"]) - 1, 0)
            elif key == "ret":
                state["entry"] = entries[int(state["cursor"])]
                state["up"] = True
                self.append_serial(f"booted entry {state['entry']}\n")

        self.mutate(change)


class FakeQmp:
    """One-connection-at-a-time QMP server over a Unix socket."""

    def __init__(self, path: Path, machine: FakeMachine) -> None:
        self.path = path
        self.machine = machine
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._listener: socket.socket | None = None

    def __enter__(self) -> "FakeQmp":
        self.path.unlink(missing_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.path))
        listener.listen()
        listener.settimeout(0.2)
        self._listener = listener
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._listener is not None:
            self._listener.close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.path.unlink(missing_ok=True)

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                assert self._listener is not None
                connection, _address = self._listener.accept()
            except (TimeoutError, OSError):
                continue
            with connection:
                self._handle(connection)

    def _handle(self, connection: socket.socket) -> None:
        connection.sendall((json.dumps({"QMP": {"version": {}, "capabilities": []}}) + "\n").encode())
        while True:
            line = connection.recv(65536)
            if not line:
                return
            for raw in line.decode().splitlines():
                request = json.loads(raw)
                command = request.get("execute")
                if command == "qmp_capabilities":
                    response: dict = {"return": {}}
                elif command == "system_reset":
                    self.machine.reset()
                    response = {"return": {}}
                elif command == "send-key":
                    for item in request.get("arguments", {}).get("keys", []):
                        self.machine.send_key(item["data"])
                    response = {"return": {}}
                else:
                    response = {"error": {"class": "CommandNotFound", "desc": f"fake QMP has no {command!r}"}}
                connection.sendall((json.dumps(response) + "\n").encode())


class FakeSerial:
    """Records bytes written to the guest's serial input socket."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.written = bytearray()
        self.received = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._listener: socket.socket | None = None

    def __enter__(self) -> "FakeSerial":
        self.path.unlink(missing_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.path))
        listener.listen()
        listener.settimeout(0.2)
        self._listener = listener
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._listener is not None:
            self._listener.close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self.path.unlink(missing_ok=True)

    def start_later(self) -> socket.socket:
        """Bind but do not listen yet, to exercise ECONNREFUSED."""
        self.path.unlink(missing_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.path))
        return listener

    def start_bound(self, listener: socket.socket) -> None:
        listener.listen()
        listener.settimeout(0.2)
        self._listener = listener
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                assert self._listener is not None
                connection, _address = self._listener.accept()
            except (TimeoutError, OSError):
                continue
            with connection:
                while True:
                    data = connection.recv(65536)
                    if not data:
                        break
                    self.written.extend(data)
                    self.received.set()


FAKE_SSH_SCRIPT = r"""#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import json
import os
import stat
import subprocess
import sys
from pathlib import Path


state_path = Path(os.environ["FAKE_SSH_STATE"])
lock_path = state_path.with_suffix(".lock")
serial_path = state_path.with_name("serial.log")


def read_state():
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
        try:
            return json.loads(state_path.read_text())
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def update_state(function):
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            state = json.loads(state_path.read_text())
            function(state)
            state_path.write_text(json.dumps(state) + "\n")
            return state
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def fail(message):
    sys.stderr.write(message + "\n")
    raise SystemExit(255)


def append_serial(text):
    with serial_path.open("a", encoding="utf-8") as output:
        output.write(text)


def parse_args(arguments):
    options = {}
    operation = None
    destination = None
    remote = None
    index = 0
    while index < len(arguments):
        item = arguments[index]
        if item in ("-o", "-p", "-l"):
            value = arguments[index + 1]
            if item == "-o":
                key, _, setting = value.partition("=")
                options[key] = setting
            elif item == "-p":
                options["Port"] = value
            else:
                options["User"] = value
            index += 2
            continue
        if item == "-O":
            operation = arguments[index + 1]
            index += 2
            continue
        if item == "-F":
            index += 2
            continue
        if item in ("-T", "-M", "-N", "-f"):
            if item == "-M":
                options["Master"] = "yes"
            index += 1
            continue
        if item.startswith("-"):
            index += 1
            continue
        destination = item
        remote = arguments[index + 1] if index + 1 < len(arguments) else None
        break
    return options, operation, destination, remote


options, operation, destination, remote = parse_args(sys.argv[1:])
if destination is None:
    fail("ssh: no destination")

key_path = Path(options["IdentityFile"])
if stat.S_IMODE(key_path.stat().st_mode) != 0o600:
    fail("Bad permissions: fake SSH requires a private key mode 0600")

alias = options["HostKeyAlias"]
known_lines = Path(options["UserKnownHostsFile"]).read_text().splitlines()
known_key = None
for line in known_lines:
    fields = line.split()
    if len(fields) == 3 and fields[0] == alias:
        known_key = fields
        break

state = read_state()
strict = options.get("StrictHostKeyChecking", "yes")
if known_key is None or known_key[2] != state["host_key"]:
    if strict == "yes":
        fail("Host key verification failed.")

if operation == "check":

    def check_late(current):
        if not current["master"]:
            raise PermissionError("no master")
        if current["late_check"] > 0:
            current["late_check"] -= 1

    try:
        update_state(check_late)
    except PermissionError:
        fail("Control socket connect: no such file or directory")
    raise SystemExit(0)

if operation == "exit":
    update_state(lambda current: current.update(master=False, master_boot_id=None))
    Path(options["ControlPath"]).unlink(missing_ok=True)
    raise SystemExit(0)

if options.get("Master") == "yes":
    if not state["up"] or state["exec_broken"]:
        fail("ssh: connection refused")

    def open_master(current):
        current["master"] = True
        current["master_boot_id"] = current["boot_id"]

    update_state(open_master)
    Path(options["ControlPath"]).touch()
    raise SystemExit(0)

uses_master = options.get("ControlPath") != "none"
if uses_master and not state["master"]:
    fail("mux_client_request_session: Control socket connect failed")

if remote and "cat /proc/sys/kernel/random/boot_id" in remote:
    if state["exec_broken"] or not state["up"]:
        fail("ssh: exec request failed on channel 0")
    if state["old_boot_answers"] > 0:

        def answer_old_boot(current):
            current["old_boot_answers"] -= 1

        update_state(answer_old_boot)
        print(state["boot_id"])
        raise SystemExit(0)
    if state["pending_reboot"]:

        def finish_boot(current):
            current["pending_reboot"] = False
            current["boots"] += 1
            current["boot_index"] = min(current["boot_index"] + 1, len(current["boot_ids"]) - 1)
            if not current["keep_boot_id"]:
                current["boot_id"] = current["boot_ids"][current["boot_index"]]
            # The old master and its control path briefly survive a guest-side reboot.
            if current["panic_on_boot"]:
                current["up"] = False
                append_serial(current["panic_line"])
            else:
                current["up"] = True
                append_serial("boot %s\n" % current["boot_id"])

        state = update_state(finish_boot)
    print(state["boot_id"])
    raise SystemExit(0)

state = read_state()
if uses_master and not state["master"]:
    fail("mux_client_request_session: Control socket connect failed")
if not state["up"] or state["exec_broken"]:
    fail("ssh: Connection to fake guest closed by remote host")
if state["link_loss"]:
    fail("ssh: Connection to fake guest closed by remote host")


def note_command(current):
    current["commands"] += 1
    current["last_command"] = remote or ""


update_state(note_command)
if remote and "systemctl reboot" in remote:
    update_state(lambda current: current.update(pending_reboot=True))
    append_serial("reboot requested\n")
    raise SystemExit(0)
if remote and "__DAMAGE_PANIC__" in remote:
    def damage(current):
        current["pending_reboot"] = True
        current["panic_on_boot"] = True
        current["exec_broken"] = True

    update_state(damage)
    append_serial(read_state()["panic_line"])
    raise SystemExit(0)

stdin_data = None
if not sys.stdin.isatty():
    stdin_data = sys.stdin.buffer.read()

completed = subprocess.run(
    ["/bin/bash", "-c", remote or ""],
    input=stdin_data,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    check=False,
)

sys.stdout.buffer.write(completed.stdout)
sys.stderr.buffer.write(completed.stderr)
rc = completed.returncode
if state["silent_loss"]:
    update_state(lambda current: current.update(link_loss=True))
raise SystemExit(rc)
"""


def write_fake_ssh(tmp_path: Path) -> Path:
    path = tmp_path / "fake-ssh"
    path.write_text(textwrap.dedent(FAKE_SSH_SCRIPT))
    path.chmod(0o700)
    return path
