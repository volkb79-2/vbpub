"""Drive a vm-lab guest over pinned-key SSH, its serial log, and QMP."""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import errno
import ipaddress
import json
import math
import os
import re
import shlex
import shutil
import socket
import stat
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, NamedTuple, TextIO
from vmscreen.errors import NoExactText
from vmscreen.keys import parse_keys, send_sequence, validate_keys
from vmscreen.qmp import QmpClient
from vmscreen.serialtext import render as render_serial
from vmscreen.sources import make_mark, read_serial
from .core import (
    GENERIC_PROFILE,
    RunnerError,
    SERIAL_CONSOLE_KEY,
    VmProfile,
    docker_inspect,
    docker_run,
    inspect_current_container,
    unix_socket_client_path,
    validate_name,
    validate_port,
)

SSH_KEY_FILENAME = "id_ed25519"
HOST_KEYS_FILENAME = "known_hosts"
SSH_DIRECT_CONNECT_TIMEOUT_SECONDS = 5
SSH_MASTER_CONNECT_TIMEOUT_SECONDS = 30
SSH_MASTER_PERSIST_SECONDS = 900
SSH_LOCAL_TIMEOUT_GRACE_SECONDS = 10
SERIAL_WRITE_TIMEOUT_SECONDS = 30
READY_POLL_SECONDS = 2.0
SERIAL_POLL_SECONDS = 0.5
SERIAL_TAIL_BYTES = 4096
DEFAULT_GUEST_PORT = 22
TRIGGER_TIMEOUT_SECONDS = 30
DEFAULT_KEY_DELAY_SECONDS = 0.7
BOOT_ID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
SSH_DIAGNOSTIC_RE = re.compile(
    r"ssh:|mux_client|Control socket|Connection(?:\s|.*\s)?closed|Broken pipe|kex_", re.IGNORECASE
)
KEY_TYPE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
KEY_DATA_RE = re.compile(r"[A-Za-z0-9+/]+(?:=+)?")
PRIVATE_KEY_MODE = 0o600
PRIVATE_DIR_MODE = 0o700


def _positive_seconds(value: float, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise GuestError(f"{label} must be a positive finite number") from exc
    if isinstance(value, bool) or not math.isfinite(number) or number <= 0:
        raise GuestError(f"{label} must be a positive finite number")
    return number


def _validate_ssh_path(path: Path, label: str) -> None:
    text = os.fspath(path)
    if "%" in text or any(character.isspace() for character in text):
        raise GuestError(f"{label} must not contain '%' or whitespace: {text!r}")


class GuestError(RunnerError):
    """Base error for a guest session; every instance carries a serial tail."""

    def __init__(self, message: str, *, serial_tail: str = "") -> None:
        self.message = message
        self.serial_tail = serial_tail
        full = message
        if serial_tail:
            full += f"\nserial tail:\n{serial_tail}"
        super().__init__(full)


class GuestTimeout(GuestError):
    """A caller's deadline passed."""


class GuestTransportError(GuestError):
    """SSH cannot connect or authenticate, or the link was lost mid-command."""


class GuestIdentityChanged(GuestError):
    """The guest host key differs from the pinned known-hosts entry."""


class BootFailure(GuestError):
    """A fail-on serial pattern appeared while waiting."""

    def __init__(self, message: str, *, match: re.Match[str], serial_tail: str = "") -> None:
        self.match = match
        super().__init__(message, serial_tail=serial_tail)


class RebootNotProven(GuestError):
    """The boot id was unreadable before, or unchanged after, a reboot."""


class SerialInputUnavailable(GuestError):
    """serial_write was used on a guest started without --serial-console."""


@dataclasses.dataclass(frozen=True)
class SshEndpoint:
    host: str
    port: int
    alias: str
    key: Path
    known_hosts: Path
    user: str = "root"

    def __post_init__(self) -> None:
        _validate_ssh_path(self.key, "SSH private key path")
        _validate_ssh_path(self.known_hosts, "SSH known-hosts path")
        try:
            address = ipaddress.ip_address(self.host)
        except ValueError as exc:
            raise GuestError(f"SSH host must be a Docker network address, got {self.host!r}") from exc
        if address.is_unspecified:
            raise GuestError(f"SSH host must not be an unspecified address: {self.host!r}")
        try:
            validate_name(self.alias)
            validate_port(self.port, "SSH port")
        except RunnerError as exc:
            raise GuestError(str(exc)) from exc
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", self.user):
            raise GuestError(f"invalid SSH user {self.user!r}")


def _guest_error(message: str, *, serial_tail: str = "") -> GuestError:
    return GuestError(message, serial_tail=serial_tail)


def _runner_manifest(state_dir: Path) -> dict[str, object]:
    path = state_dir / "runner.json"
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise _guest_error(f"cannot read runner identity file {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise _guest_error(f"runner identity file {path} is not schema 1")
    return value


def _manifest_string(value: dict[str, object], key: str, path: Path) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise _guest_error(f"runner identity file {path} has no non-empty {key!r}")
    return item


def _forward_to_guest_port(manifest: dict[str, object], profile: VmProfile, guest_port: int) -> tuple[int, int]:
    validate_port(guest_port, "guest SSH port")
    if profile.sizing_from_cli:
        matches: list[tuple[str, int]] = []
        for key, value in manifest.items():
            if not key.endswith("_guest_port"):
                continue
            name = key.removesuffix("_guest_port")
            if not name or type(value) is not int:
                continue
            if value == guest_port:
                matches.append((name, value))
        if not matches:
            raise _guest_error(f"runner.json records no forward to guest port {guest_port}")
    else:
        matches = [
            (forward.name, forward.guest_port) for forward in profile.forwards if forward.guest_port == guest_port
        ]
        if not matches:
            raise _guest_error(f"profile {profile.program!r} has no forward to guest port {guest_port}")
    if len(matches) != 1:
        names = ", ".join(name for name, _port in matches)
        raise _guest_error(f"more than one forward targets guest port {guest_port}: {names}")
    name, recorded_guest_port = matches[0]
    host_port = manifest.get(f"{name}_port")
    if type(host_port) is not int:
        raise _guest_error(f"runner.json has no integer {name}_port")
    try:
        validate_port(host_port, f"host port of forward {name}")
    except RunnerError as exc:
        raise _guest_error(str(exc)) from exc
    return host_port, recorded_guest_port


def _ensure_private_dir(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        try:
            path.mkdir(mode=PRIVATE_DIR_MODE)
            os.chmod(path, PRIVATE_DIR_MODE)
        except OSError as exc:
            raise _guest_error(f"cannot create private directory {path}: {exc}") from exc
        try:
            info = path.lstat()
        except OSError as exc:
            raise _guest_error(f"cannot inspect private directory {path}: {exc}") from exc
    except OSError as exc:
        raise _guest_error(f"cannot inspect private directory {path}: {exc}") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise _guest_error(f"private credential path is not a directory: {path}")
    if stat.S_IMODE(info.st_mode) != PRIVATE_DIR_MODE:
        raise _guest_error(
            f"private credential directory {path} must have mode 0700, has {stat.S_IMODE(info.st_mode):04o}"
        )
    if info.st_uid != os.geteuid():
        raise _guest_error(
            f"private credential directory {path} must be owned by uid {os.geteuid()}, has uid {info.st_uid}"
        )


def _write_private_file(path: Path, content: bytes) -> None:
    _ensure_private_dir(path.parent)
    fd: int | None = None
    temporary: Path | None = None
    try:
        fd, temporary_name = tempfile.mkstemp(prefix=".vm-lab-private-", dir=path.parent)
        temporary = Path(temporary_name)
        os.fchmod(fd, PRIVATE_KEY_MODE)
        output = os.fdopen(fd, "wb")
        fd = None
        with output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        os.chmod(path, PRIVATE_KEY_MODE)
    except OSError as exc:
        raise _guest_error(f"cannot write private file {path}: {exc}") from exc
    finally:
        if fd is not None:
            os.close(fd)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _normalize_host_keys(source: Path, alias: str) -> bytes:
    try:
        lines = source.read_text().splitlines()
    except OSError as exc:
        raise _guest_error(f"cannot read the base host keys at {source}: {exc}") from exc
    normalized: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if fields[0] == "DR-HOSTKEY":
            fields = fields[1:]
        elif len(fields) >= 3:
            # Accept a conventional known_hosts line and re-key it under the alias.
            fields = fields[1:]
        if len(fields) != 2:
            raise _guest_error(f"malformed base host-key line in {source}: {line!r}")
        key_type, key_data = fields
        if KEY_TYPE_RE.fullmatch(key_type) is None or KEY_DATA_RE.fullmatch(key_data) is None:
            raise _guest_error(f"malformed base host-key line in {source}: {line!r}")
        try:
            base64.b64decode(key_data, validate=True)
        except ValueError as exc:
            raise _guest_error(f"base host-key data in {source} is not base64") from exc
        normalized.append(f"{alias} {key_type} {key_data}")
    if not normalized:
        raise _guest_error(f"the base host-key file {source} contains no usable host keys")
    return ("\n".join(normalized) + "\n").encode()


def ssh_endpoint(
    state_dir: Path,
    credentials: Path,
    *,
    profile: VmProfile = GENERIC_PROFILE,
    private_dir: Path,
    guest_port: int = DEFAULT_GUEST_PORT,
) -> SshEndpoint:
    try:
        resolved_state = Path(state_dir).resolve(strict=True)
    except OSError as exc:
        raise _guest_error(f"guest state directory is unavailable: {exc}") from exc
    if not resolved_state.is_dir():
        raise _guest_error(f"guest state directory is not a directory: {resolved_state}")
    resolved_private = Path(private_dir).resolve()
    _validate_ssh_path(resolved_private, "private credential directory")
    if resolved_private == resolved_state or resolved_state in resolved_private.parents:
        raise _guest_error(f"private credential directory {resolved_private} must be outside the guest state directory")

    state_path = state_dir / "runner.json"
    manifest = _runner_manifest(state_dir)
    name = _manifest_string(manifest, "name", state_path)
    network = _manifest_string(manifest, "network", state_path)
    port, _guest_port = _forward_to_guest_port(manifest, profile, guest_port)
    source_key = credentials / SSH_KEY_FILENAME
    source_host_keys = credentials / HOST_KEYS_FILENAME
    try:
        key_bytes = source_key.read_bytes()
    except OSError as exc:
        raise _guest_error(f"cannot read the SSH private key {source_key}: {exc}") from exc
    if not key_bytes:
        raise _guest_error(f"the SSH private key {source_key} is empty")
    known_hosts_bytes = _normalize_host_keys(source_host_keys, name)
    try:
        inspected = docker_inspect(name)
        networks = inspected.get("NetworkSettings", {}).get("Networks") or {}
        address = (networks.get(network) or {}).get("IPAddress")
    except (AttributeError, RunnerError) as exc:
        raise _guest_error(f"cannot inspect guest {name!r} on network {network!r}: {exc}") from exc
    if not isinstance(address, str) or not address:
        raise _guest_error(f"guest {name!r} has no network address on the exact network {network!r}")
    try:
        ipaddress.ip_address(address)
    except ValueError as exc:
        raise _guest_error(f"guest {name!r} has invalid network address {address!r}") from exc
    private_dir = resolved_private
    _ensure_private_dir(private_dir)
    private_key = private_dir / SSH_KEY_FILENAME
    private_known_hosts = private_dir / HOST_KEYS_FILENAME
    _write_private_file(private_key, key_bytes)
    _write_private_file(private_known_hosts, known_hosts_bytes)
    return SshEndpoint(
        host=address, port=port, alias=name, key=private_key, known_hosts=private_known_hosts, user="root"
    )


def _validate_network_name(network: str) -> None:
    if (
        not network
        or network.startswith("-")
        or any(character.isspace() or ord(character) < 32 for character in network)
    ):
        raise _guest_error(f"invalid Docker network name {network!r}")


@contextlib.contextmanager
def attached_to_network(network: str, *, plain_host: bool) -> Iterator[None]:
    if plain_host:
        yield
        return
    _validate_network_name(network)
    try:
        container_id, inspected = inspect_current_container()
    except (OSError, RunnerError) as exc:
        raise _guest_error(f"cannot identify the calling container: {exc}") from exc
    networks = inspected.get("NetworkSettings", {}).get("Networks") or {}
    already_connected = network in networks
    if not already_connected:
        try:
            docker_run(["docker", "network", "connect", network, container_id])
        except RunnerError as exc:
            raise _guest_error(f"cannot attach this container to network {network!r}: {exc}") from exc
    body_succeeded = False
    try:
        yield
        body_succeeded = True
    finally:
        if not already_connected:
            try:
                docker_run(["docker", "network", "disconnect", network, container_id])
            except RunnerError as exc:
                if body_succeeded:
                    raise _guest_error(f"cannot detach this container from network {network!r}: {exc}") from exc


class Result(NamedTuple):
    rc: int
    out: str
    err: str


class Guest:
    def __init__(
        self,
        state_dir: Path,
        ssh: SshEndpoint,
        *,
        profile: VmProfile = GENERIC_PROFILE,
        log: TextIO | None = None,
        ssh_program: str = "ssh",
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        try:
            self.state_dir = Path(state_dir).resolve(strict=True)
        except OSError as exc:
            raise _guest_error(f"guest state directory is unavailable: {exc}") from exc
        if not self.state_dir.is_dir():
            raise _guest_error(f"guest state directory is not a directory: {self.state_dir}")
        self.ssh = ssh
        self.profile = profile
        self.log = log
        self.ssh_program = ssh_program
        self.clock = clock
        self.sleep = sleep
        self.serial_log = self.state_dir / "serial.log"
        self._closed = False
        self._validate_material()
        try:
            self._control_dir: Path | None = Path(tempfile.mkdtemp(prefix="vm-lab-guest-", dir="/tmp"))
            os.chmod(self._control_dir, PRIVATE_DIR_MODE)
        except OSError as exc:
            raise _guest_error(f"cannot create a private SSH control directory: {exc}") from exc
        self._control_path = self._control_dir / "ssh"
        if len(os.fsencode(self._control_path)) > 100:
            self.close()
            raise _guest_error(f"SSH control path is too long for a Unix socket: {self._control_path}")

    def _validate_material(self) -> None:
        try:
            key_mode = self.ssh.key.stat().st_mode
        except OSError as exc:
            raise _guest_error(f"cannot inspect the SSH private key {self.ssh.key}: {exc}") from exc
        if not stat.S_ISREG(key_mode):
            raise _guest_error(f"SSH private key is not a regular file: {self.ssh.key}")
        if stat.S_IMODE(key_mode) != PRIVATE_KEY_MODE:
            raise _guest_error(f"SSH private key {self.ssh.key} must have mode 0600, has {stat.S_IMODE(key_mode):04o}")
        try:
            known_lines = self.ssh.known_hosts.read_text().splitlines()
        except OSError as exc:
            raise _guest_error(f"cannot read pinned host keys {self.ssh.known_hosts}: {exc}") from exc
        if not any(line.strip() and not line.lstrip().startswith("#") for line in known_lines):
            raise _guest_error(f"pinned host-key file {self.ssh.known_hosts} is empty; refusing first contact")

    def _tail(self) -> str:
        try:
            with self.serial_log.open("rb") as source:
                source.seek(0, os.SEEK_END)
                size = source.tell()
                source.seek(max(0, size - SERIAL_TAIL_BYTES))
                raw = source.read(SERIAL_TAIL_BYTES)
        except OSError:
            return ""
        return render_serial(raw)[-SERIAL_TAIL_BYTES:]

    def _guest_error(self, error_type: type[GuestError], message: str, **kwargs: object) -> GuestError:
        return error_type(message, serial_tail=self._tail(), **kwargs)

    def _require_open(self) -> None:
        if self._closed:
            raise self._guest_error(GuestError, "guest session is closed")

    def _ssh_base(self) -> list[str]:
        argv = [self.ssh_program, "-F", "/dev/null", "-T", "-p", str(self.ssh.port), "-l", self.ssh.user]
        for option in (
            "BatchMode=yes",
            "IdentitiesOnly=yes",
            f"IdentityFile={self.ssh.key}",
            "IdentityAgent=none",
            "ForwardAgent=no",
            f"UserKnownHostsFile={self.ssh.known_hosts}",
            "GlobalKnownHostsFile=/dev/null",
            "StrictHostKeyChecking=yes",
            "UpdateHostKeys=no",
            "CheckHostIP=no",
            f"HostKeyAlias={self.ssh.alias}",
            "ServerAliveInterval=5",
            "ServerAliveCountMax=24",
        ):
            argv += ["-o", option]
        return argv

    def _direct_argv(self, remote_command: str) -> list[str]:
        return [
            *self._ssh_base(),
            "-o",
            "ControlMaster=no",
            "-o",
            "ControlPath=none",
            "-o",
            f"ConnectTimeout={SSH_DIRECT_CONNECT_TIMEOUT_SECONDS}",
            self.ssh.host,
            remote_command,
        ]

    def _master_argv(self) -> list[str]:
        return [
            *self._ssh_base(),
            "-M",
            "-N",
            "-f",
            "-o",
            "ControlMaster=yes",
            "-o",
            f"ControlPath={self._control_path}",
            "-o",
            f"ControlPersist={SSH_MASTER_PERSIST_SECONDS}",
            "-o",
            f"ConnectTimeout={SSH_MASTER_CONNECT_TIMEOUT_SECONDS}",
            self.ssh.host,
        ]

    def _mux_argv(self, remote_command: str) -> list[str]:
        return [
            *self._ssh_base(),
            "-o",
            "ControlMaster=no",
            "-o",
            f"ControlPath={self._control_path}",
            "-o",
            "ProxyCommand=/bin/false",
            self.ssh.host,
            remote_command,
        ]

    def _control_argv(self, operation: str) -> list[str]:
        return [
            *self._ssh_base(),
            "-O",
            operation,
            "-o",
            "ControlMaster=no",
            "-o",
            f"ControlPath={self._control_path}",
            self.ssh.host,
        ]

    def _invoke(
        self, argv: list[str], *, timeout: float, input_bytes: bytes | None = None
    ) -> tuple[subprocess.CompletedProcess[bytes], float]:
        started = self.clock()
        try:
            if input_bytes is None:
                completed = subprocess.run(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=timeout,
                    check=False,
                )
            else:
                completed = subprocess.run(
                    argv,
                    input=input_bytes,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=timeout,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise self._guest_error(GuestTimeout, f"local SSH deadline {timeout:g}s passed") from exc
        except OSError as exc:
            raise self._guest_error(GuestTransportError, f"cannot run SSH client {self.ssh_program!r}: {exc}") from exc
        return completed, self.clock() - started

    def _record(self, command: str, rc: int | None, out: str, err: str, elapsed: float) -> None:
        if self.log is None:
            return
        record = {
            "time": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "rc": rc,
            "stdout": out,
            "stderr": err,
            "elapsed_s": elapsed,
        }
        try:
            self.log.write(json.dumps(record, sort_keys=True) + "\n")
            self.log.flush()
        except OSError as exc:
            raise self._guest_error(GuestError, f"cannot append command transcript: {exc}") from exc

    @staticmethod
    def _host_key_changed(stderr: str) -> bool:
        return "Host key verification failed" in stderr

    @staticmethod
    def _ssh_failure(stderr: str) -> bool:
        return SSH_DIAGNOSTIC_RE.search(stderr) is not None

    def _control_check(self) -> bool:
        try:
            completed, _elapsed = self._invoke(self._control_argv("check"), timeout=SSH_DIRECT_CONNECT_TIMEOUT_SECONDS)
        except GuestError:
            return False
        return completed.returncode == 0

    def _followup_true(self) -> bool:
        try:
            completed, _elapsed = self._invoke(self._mux_argv("true"), timeout=SSH_DIRECT_CONNECT_TIMEOUT_SECONDS)
        except GuestError:
            return False
        stderr = completed.stderr.decode("utf-8", "replace")
        return completed.returncode == 0 and not self._host_key_changed(stderr)

    def _drop_master(self, *, ignore_errors: bool = True) -> None:
        if self._control_dir is None:
            return
        if self._control_path.exists():
            try:
                self._invoke(self._control_argv("exit"), timeout=SSH_DIRECT_CONNECT_TIMEOUT_SECONDS)
            except GuestError:
                if not ignore_errors:
                    raise
            finally:
                self._control_path.unlink(missing_ok=True)

    def run(self, cmd: str, timeout: float = 300, *, input: bytes | None = None) -> Result:
        self._require_open()
        if not isinstance(cmd, str):
            raise self._guest_error(GuestError, "command must be a string")
        if "\0" in cmd:
            raise self._guest_error(GuestError, "command contains a NUL byte")
        seconds = _positive_seconds(timeout, "command timeout")
        if input is not None and not isinstance(input, bytes):
            raise self._guest_error(GuestError, "command input must be bytes")
        remote = shlex.join(["timeout", "-k", "5", f"{seconds:g}", "bash", "-c", cmd])
        started = self.clock()
        try:
            completed, elapsed = self._invoke(
                self._mux_argv(remote), timeout=seconds + SSH_LOCAL_TIMEOUT_GRACE_SECONDS, input_bytes=input
            )
        except GuestTimeout as exc:
            self._record(cmd, None, "", "", self.clock() - started)
            raise exc
        out = completed.stdout.decode("utf-8", "replace")
        err = completed.stderr.decode("utf-8", "replace")
        rc = completed.returncode
        self._record(cmd, rc, out, err, elapsed)
        if self._host_key_changed(err):
            raise self._guest_error(
                GuestIdentityChanged, f"guest host key changed from the pinned key under alias {self.ssh.alias!r}"
            )
        if rc in (124, 137) and elapsed >= seconds:
            raise self._guest_error(GuestTimeout, f"remote command deadline {seconds:g}s passed")
        if rc == 255:
            if self._ssh_failure(err) or not (self._control_check() and self._followup_true()):
                detail = err.strip() or "the SSH link was lost"
                raise self._guest_error(GuestTransportError, detail)
        return Result(rc, out, err)

    def _boot_id_from_output(self, out: str) -> str | None:
        candidate = out.strip()
        return candidate if BOOT_ID_RE.fullmatch(candidate) else None

    def _probe_boot_id(self, timeout: float) -> str | None:
        completed, _elapsed = self._invoke(self._direct_argv("cat /proc/sys/kernel/random/boot_id"), timeout=timeout)
        stderr = completed.stderr.decode("utf-8", "replace")
        if self._host_key_changed(stderr):
            raise self._guest_error(
                GuestIdentityChanged, f"guest host key changed from the pinned key under alias {self.ssh.alias!r}"
            )
        if completed.returncode == 0:
            return self._boot_id_from_output(completed.stdout.decode("utf-8", "replace"))
        return None

    def _open_master(self, deadline: float) -> None:
        while self.clock() < deadline:
            if self._control_check():
                return
            remaining = deadline - self.clock()
            try:
                completed, _elapsed = self._invoke(
                    self._master_argv(), timeout=min(SSH_MASTER_CONNECT_TIMEOUT_SECONDS + 1, max(0.1, remaining))
                )
            except (GuestTimeout, GuestTransportError):
                completed = None
            if completed is not None:
                stderr = completed.stderr.decode("utf-8", "replace")
                if self._host_key_changed(stderr):
                    raise self._guest_error(
                        GuestIdentityChanged,
                        f"guest host key changed from the pinned key under alias {self.ssh.alias!r}",
                    )
            remaining = deadline - self.clock()
            if remaining > 0:
                self.sleep(min(0.5, remaining))
        raise self._guest_error(GuestTimeout, "SSH master was not established before the deadline")

    @staticmethod
    def _search_serial(pattern: re.Pattern[str], text: str) -> tuple[re.Match[str], int] | None:
        """Search each rendered line; no pattern can cross a line boundary.

        An unfinished final line is searched as written, so ``$`` still matches
        serial prompts that have no newline yet.
        """
        if not isinstance(pattern, re.Pattern):
            raise TypeError("serial patterns must be compiled regular expressions")
        offset = 0
        for raw_line in text.splitlines(keepends=True):
            line = raw_line.rstrip("\r\n")
            match = pattern.search(line)
            if match is not None:
                return match, offset + match.start()
            offset += len(raw_line)
        return None

    def _serial_text(self, since: str) -> str:
        try:
            return read_serial(str(self.serial_log), mark=since).text
        except (NoExactText, ValueError) as exc:
            raise self._guest_error(GuestError, str(exc)) from exc

    def _serial_watch(
        self, since: str, until: re.Pattern[str], fail_on: re.Pattern[str] | None
    ) -> re.Match[str] | None:
        text = self._serial_text(since)
        success = self._search_serial(until, text)
        failure = self._search_serial(fail_on, text) if fail_on is not None else None
        # The earliest event in the stream wins; success wins an exact tie.
        if failure is not None and (success is None or failure[1] < success[1]):
            match = failure[0]
            raise self._guest_error(BootFailure, f"serial fail pattern matched: {match.group(0)!r}", match=match)
        if success is not None:
            return success[0]
        return None

    def _check_failure(self, fail_on: re.Pattern[str] | None, since: str) -> None:
        if fail_on is None:
            return
        failure = self._search_serial(fail_on, self._serial_text(since))
        if failure is not None:
            match = failure[0]
            raise self._guest_error(BootFailure, f"serial fail pattern matched: {match.group(0)!r}", match=match)

    def wait_ready(
        self,
        timeout: float,
        *,
        not_boot_id: str | None = None,
        fail_on: re.Pattern[str] | None = None,
        since: str | None = None,
    ) -> str:
        self._require_open()
        seconds = _positive_seconds(timeout, "readiness timeout")
        if fail_on is not None and since is None:
            since = self.serial_mark()
        deadline = self.clock() + seconds
        while True:
            if since is not None:
                self._check_failure(fail_on, since)
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise self._guest_error(GuestTimeout, f"guest did not become ready within {seconds:g}s")
            try:
                boot_id = self._probe_boot_id(min(remaining, 6.0))
            except (GuestTimeout, GuestTransportError):
                boot_id = None
            if boot_id is not None and boot_id != not_boot_id:
                if since is not None:
                    self._check_failure(fail_on, since)
                self._drop_master()
                self._open_master(deadline)
                return boot_id
            remaining = deadline - self.clock()
            if remaining > 0:
                self.sleep(min(READY_POLL_SECONDS, remaining))

    def boot_id(self) -> str:
        self._require_open()
        result = self.run("cat /proc/sys/kernel/random/boot_id", timeout=30)
        boot_id = self._boot_id_from_output(result.out)
        if result.rc != 0 or boot_id is None:
            raise self._guest_error(
                RebootNotProven, f"cannot read the guest boot id (rc={result.rc}, output={result.out!r})"
            )
        return boot_id

    def reboot(self, timeout: float = 1200, *, fail_on: re.Pattern[str] | None = None) -> str:
        try:
            old = self.boot_id()
        except RebootNotProven:
            raise
        except GuestError as exc:
            raise self._guest_error(RebootNotProven, f"cannot read the boot id before reboot: {exc.message}") from exc
        mark = self.serial_mark()
        self.trigger("sleep 1; exec systemctl reboot")
        try:
            new = self.wait_ready(timeout, not_boot_id=old, fail_on=fail_on, since=mark)
        except GuestTimeout as exc:
            raise self._guest_error(
                RebootNotProven, f"reboot was not proven within {timeout:g}s; boot_id before={old}"
            ) from exc
        if new == old:
            raise self._guest_error(RebootNotProven, f"reboot not proven: boot_id before={old!r} after={new!r}")
        return new

    def trigger(self, payload: str) -> None:
        self._require_open()
        if "\0" in payload:
            raise self._guest_error(GuestError, "trigger payload contains a NUL byte")
        detached = f"setsid -f bash -c {shlex.quote(payload)} </dev/null >/dev/null 2>&1"
        try:
            result = self.run(detached, timeout=TRIGGER_TIMEOUT_SECONDS)
        finally:
            self._drop_master()
        if result.rc != 0:
            raise self._guest_error(GuestError, f"trigger payload did not start (rc={result.rc})")

    def reset(self) -> None:
        self._require_open()
        self._drop_master()
        path = self._qmp_path()
        with QmpClient(str(path)) as client:
            client.execute("system_reset")

    def _qmp_path(self) -> Path:
        try:
            return unix_socket_client_path(self.profile, self.state_dir, "qmp.sock", "qmp")
        except RunnerError as exc:
            raise self._guest_error(GuestError, f"cannot resolve the QMP socket: {exc}") from exc

    @staticmethod
    def _key_sequence(keys: str | list[list[str]]) -> list[list[str]]:
        if isinstance(keys, str):
            chords = parse_keys(keys)
        else:
            chords = [[str(key) for key in chord] for chord in keys]
        validate_keys(chords)
        return chords

    def _send_chords(self, chords: list[list[str]], *, delay: float, hold_ms: int) -> None:
        with QmpClient(str(self._qmp_path())) as client:
            send_sequence(client, chords, delay=delay, hold_ms=hold_ms, sleep=self.sleep)

    def send_keys(
        self,
        keys: str | list[list[str]],
        *,
        delay: float = DEFAULT_KEY_DELAY_SECONDS,
        hold_ms: int = 100,
    ) -> None:
        self._require_open()
        chords = self._key_sequence(keys)
        self._send_chords(chords, delay=delay, hold_ms=hold_ms)

    def _require_serial_console(self) -> None:
        manifest = _runner_manifest(self.state_dir)
        if manifest.get(SERIAL_CONSOLE_KEY) is not True:
            raise self._guest_error(
                SerialInputUnavailable, "serial_write requires a guest started with --serial-console"
            )

    def serial_write(self, data: bytes) -> None:
        self._require_open()
        if not isinstance(data, bytes):
            raise self._guest_error(GuestError, "serial_write data must be bytes")
        self._require_serial_console()
        deadline = self.clock() + SERIAL_WRITE_TIMEOUT_SECONDS
        try:
            path = unix_socket_client_path(self.profile, self.state_dir, "serial.sock", "serial")
        except RunnerError as exc:
            raise self._guest_error(GuestError, f"cannot resolve the serial socket: {exc}") from exc
        while True:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.connect(str(path))
            except OSError as exc:
                client.close()
                if exc.errno not in (errno.ENOENT, errno.ECONNREFUSED):
                    raise self._guest_error(GuestError, f"cannot connect to the serial socket {path}: {exc}") from exc
                remaining = deadline - self.clock()
                if remaining <= 0:
                    raise self._guest_error(
                        GuestTimeout, f"serial socket {path} did not appear before {SERIAL_WRITE_TIMEOUT_SECONDS:g}s"
                    ) from exc
                self.sleep(min(0.25, remaining))
                continue
            try:
                client.sendall(data)
            except OSError as exc:
                raise self._guest_error(GuestError, f"cannot write to serial socket {path}: {exc}") from exc
            finally:
                client.close()
            return

    def serial_mark(self) -> str:
        self._require_open()
        try:
            return make_mark(str(self.serial_log))
        except (OSError, ValueError) as exc:
            raise self._guest_error(GuestError, f"cannot take a serial mark of {self.serial_log}: {exc}") from exc

    def wait_serial(
        self, pattern: re.Pattern[str], timeout: float, *, since: str, fail_on: re.Pattern[str] | None = None
    ) -> re.Match[str]:
        self._require_open()
        seconds = _positive_seconds(timeout, "serial wait timeout")
        deadline = self.clock() + seconds
        while True:
            match = self._serial_watch(since, pattern, fail_on)
            if match is not None:
                return match
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise self._guest_error(GuestTimeout, f"serial pattern did not match within {seconds:g}s")
            self.sleep(min(SERIAL_POLL_SECONDS, remaining))

    def press_until(
        self,
        keys: str | list[list[str]],
        until: re.Pattern[str],
        *,
        since: str,
        every: float,
        timeout: float,
        fail_on: re.Pattern[str] | None = None,
    ) -> re.Match[str]:
        self._require_open()
        interval = _positive_seconds(every, "key interval")
        seconds = _positive_seconds(timeout, "key wait timeout")
        chords = self._key_sequence(keys)
        deadline = self.clock() + seconds
        next_press = self.clock()
        while True:
            now = self.clock()
            if now >= next_press:
                self._send_chords(chords, delay=DEFAULT_KEY_DELAY_SECONDS, hold_ms=100)
                next_press = self.clock() + interval
            match = self._serial_watch(since, until, fail_on)
            if match is not None:
                return match
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise self._guest_error(
                    GuestTimeout, f"serial pattern did not match within {seconds:g}s after key presses"
                )
            self.sleep(min(SERIAL_POLL_SECONDS, interval, remaining))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._drop_master()
        finally:
            control_dir = self._control_dir
            self._control_dir = None
            if control_dir is not None:
                shutil.rmtree(control_dir, ignore_errors=True)
