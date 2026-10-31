#!/usr/bin/env python3
"""Prepare an installed TrueNAS VM and capture its whole boot disk.

The TrueNAS installer itself is interactive. Install the VM once using the
serial/VNC procedure in README.md, then this script configures the administrator
password and root SSH key, creates (or reuses) an API key, validates it through
the shared WebSocket client, and captures the powered-off QCOW2 as raw.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from typing import Any, Iterator

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from truenas_api_client import TrueNASAPIClient, TrueNASAPIError, TrueNASSettings


DEFAULT_VM_DISK = HERE / "artifacts" / "truenas-27-20GiB-source.qcow2"
DEFAULT_VM_VARS = HERE / "vm" / "truenas-27-20GiB-OVMF_VARS.fd"
DEFAULT_VM_KEY = HERE / "vm" / "root_ed25519"
DEFAULT_KNOWN_HOSTS = HERE / "vm" / "truenas-27-20GiB-known_hosts"
DEFAULT_IMAGE = HERE / "artifacts" / "truenas-27-20GiB-golden.raw"
DEFAULT_SECRETS = HERE / "artifacts" / "truenas-27-20GiB-golden.secrets.toml"
DEFAULT_QEMU_LOG = HERE / "vm" / "step1-source-console.log"
GIB_BYTES = 1024**3
MIN_GOLDEN_DISK_BYTES = 16 * GIB_BYTES
API_KEY_NAME = "truenas-27-20GiB-golden-image"
SSH_HOST = "127.0.0.1"
SSH_PORT = 2222
API_PORT = 8443


class StepError(RuntimeError):
    """An expected failure in source preparation or image capture."""


def log(message: str) -> None:
    print(message, flush=True)


def require_tools(names: list[str]) -> None:
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise StepError("missing required tools: " + ", ".join(missing))


def run(
    argv: list[str],
    *,
    timeout: int = 120,
    sensitive: bool = False,
    check: bool = True,
    input_data: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_data,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        suffix = " (sensitive command output suppressed)" if sensitive else ""
        raise StepError(f"could not run {argv[0]}: {type(exc).__name__}{suffix}") from None
    if check and result.returncode != 0:
        suffix = " (sensitive command output suppressed)" if sensitive else ""
        if not sensitive:
            details = (result.stderr or result.stdout).strip().splitlines()
            suffix += (": " + "\n".join(details[-8:])) if details else ""
        raise StepError(f"{argv[0]} exited {result.returncode}{suffix}")
    return result


def toml_string(value: str) -> str:
    # JSON string quoting is a valid subset of TOML basic-string quoting.
    return json.dumps(value, ensure_ascii=False)


def load_secret_document(path: Path, image: Path) -> dict[str, Any]:
    if not path.is_file():
        raise StepError(f"secret sidecar not found: {path}")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise StepError("secret sidecar must have mode 0600 or stricter")
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise StepError(f"cannot read secret sidecar: {type(exc).__name__}") from None
    if document.get("schema_version") != 1 or document.get("image_file") != image.name:
        raise StepError("secret sidecar does not identify the requested golden image")
    admin = document.get("truenas_admin", {})
    root_ssh = document.get("root_ssh", {})
    mattermost = document.get("mattermost", {})
    password = admin.get("password")
    private_key = root_ssh.get("private_key")
    public_key = root_ssh.get("public_key")
    webhook = mattermost.get("webhook_url")
    if (
        admin.get("username") != "truenas_admin"
        or not isinstance(password, str)
        or len(password) < 16
        or "\n" in password
        or "\r" in password
        or root_ssh.get("username") != "root"
        or not isinstance(private_key, str)
        or not private_key.strip()
        or not isinstance(public_key, str)
        or not public_key.startswith("ssh-ed25519 ")
        or not isinstance(webhook, str)
        or not webhook.startswith("https://")
    ):
        raise StepError("secret sidecar is missing expected TrueNAS credentials")
    if not document.get("truenas_api"):
        return document
    api = document["truenas_api"]
    if (
        not isinstance(api, dict)
        or api.get("username") != "truenas_admin"
        or api.get("key_name") != API_KEY_NAME
        or not isinstance(api.get("api_key"), str)
        or len(api["api_key"].strip()) < 16
    ):
        raise StepError("existing [truenas_api] sidecar data is incomplete or unexpected")
    return document


def serialize_secret_document(document: dict[str, Any]) -> str:
    admin = document["truenas_admin"]
    root_ssh = document["root_ssh"]
    mattermost = document["mattermost"]
    api = document["truenas_api"]
    lines = [
        "schema_version = 1",
        f"image_file = {toml_string(document['image_file'])}",
        "",
        "[truenas_admin]",
        'username = "truenas_admin"',
        f"password = {toml_string(admin['password'])}",
        "",
        "[root_ssh]",
        'username = "root"',
        f"private_key = {toml_string(root_ssh['private_key'])}",
        f"public_key = {toml_string(root_ssh['public_key'])}",
        "",
        "[mattermost]",
        f"webhook_url = {toml_string(mattermost['webhook_url'])}",
        "",
        "[truenas_api]",
        'username = "truenas_admin"',
        f"api_key = {toml_string(api['api_key'])}",
        f"key_name = {toml_string(api['key_name'])}",
        f"expires_at = {toml_string(api['expires_at'])}",
        f"id = {int(api['id'])}",
        "",
    ]
    return "\n".join(lines)


def atomic_write(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_result(stdout: str, method: str) -> Any:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        raise StepError(f"TrueNAS returned invalid JSON for {method}") from None


class SourceVM:
    def __init__(self, args: argparse.Namespace, private_key: Path, qemu_process: subprocess.Popen[bytes]):
        self.args = args
        self.private_key = private_key
        self.qemu_process = qemu_process

    def ssh(
        self,
        remote_argv: list[str],
        *,
        timeout: int = 30,
        sensitive: bool = False,
        check: bool = True,
        stdin_data: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        remote = shlex.join(remote_argv)
        argv = [
            "ssh",
            "-T",
            "-i",
            str(self.private_key),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={self.args.known_hosts}",
            "-o",
            "ConnectTimeout=8",
            "-p",
            str(SSH_PORT),
            f"root@{SSH_HOST}",
            remote,
        ]
        return run(argv, timeout=timeout, sensitive=sensitive, check=check, input_data=stdin_data)

    def middleware(self, method: str, *params: Any, sensitive: bool = False) -> Any:
        log(f"Calling TrueNAS middleware method {method}.")
        started = time.monotonic()
        if sensitive:
            # TrueNAS midclt accepts '-' for a JSON payload on stdin. Keep the
            # final (sensitive) parameter out of both the SSH command and
            # remote process argv.
            if not params:
                raise StepError(f"sensitive middleware call {method} has no stdin payload")
            encoded = [json.dumps(param, separators=(",", ":")) for param in params[:-1]]
            result = self.ssh(
                ["midclt", "call", method, *encoded, "-"],
                timeout=300,
                sensitive=True,
                stdin_data=json.dumps(params[-1], separators=(",", ":")),
            )
        else:
            encoded = [json.dumps(param, separators=(",", ":")) for param in params]
            result = self.ssh(["midclt", "call", method, *encoded], timeout=300)
        log(f"TrueNAS middleware method {method} returned in {time.monotonic() - started:.1f}s.")
        return json_result(result.stdout, method)

    def wait_for_root_ssh(self) -> None:
        deadline = time.monotonic() + self.args.startup_timeout
        log("Waiting for the installed TrueNAS VM's root SSH service.")
        while time.monotonic() < deadline:
            if self.qemu_process.poll() is not None:
                raise StepError(
                    f"QEMU exited before root SSH became available (exit {self.qemu_process.returncode}); "
                    f"inspect {self.args.qemu_log}"
                )
            try:
                result = self.ssh(["id", "-u"], check=False)
                if result.returncode == 0 and result.stdout.strip() == "0":
                    log("Root SSH is ready and returned UID 0.")
                    return
            except StepError:
                pass
            time.sleep(5)
        raise StepError(
            f"root SSH did not become ready within {self.args.startup_timeout}s; "
            f"inspect {self.args.qemu_log} and {self.args.known_hosts}"
        )

    def wait_for_middleware(self) -> None:
        deadline = time.monotonic() + self.args.startup_timeout
        log("Waiting for TrueNAS middleware system.ready before making configuration changes.")
        last_report = 0.0
        while time.monotonic() < deadline:
            if self.qemu_process.poll() is not None:
                raise StepError("source VM powered off before TrueNAS middleware became ready")
            try:
                result = self.ssh(
                    ["midclt", "call", "system.ready"],
                    timeout=30,
                    sensitive=True,
                    check=False,
                )
            except StepError:
                result = None
            if result is not None and result.returncode == 0:
                if result.stdout.strip().lower() == "true":
                    log("TrueNAS middleware reports system.ready.")
                    return
            now = time.monotonic()
            if now - last_report >= 30:
                log("TrueNAS middleware is not ready yet; retrying system.ready.")
                last_report = now
            time.sleep(5)
        raise StepError(
            f"TrueNAS middleware did not report system.ready within {self.args.startup_timeout}s"
        )

    def configure(self, document: dict[str, Any]) -> None:
        info = self.middleware("system.info")
        if not isinstance(info, dict):
            raise StepError("TrueNAS system.info returned an unexpected response")
        version = str(info.get("version", ""))
        match = re.match(r"^(\d+)\.", version)
        if match is None or int(match.group(1)) < 27:
            raise StepError(f"source VM must be TrueNAS 27 or newer; got {version or 'unknown'}")
        log(f"Source TrueNAS version: {version}")

        users = self.middleware("user.query", [["username", "=", "truenas_admin"]])
        admin_id = find_user_id(users, "truenas_admin")
        self.middleware(
            "user.update",
            admin_id,
            {"password": document["truenas_admin"]["password"]},
            sensitive=True,
        )
        log("Updated the truenas_admin password from the protected sidecar.")

        root_users = self.middleware("user.query", [["username", "=", "root"]])
        root_id = find_user_id(root_users, "root")
        self.middleware(
            "user.update",
            root_id,
            {
                "sshpubkey": document["root_ssh"]["public_key"],
                "ssh_password_enabled": False,
            },
        )
        self.middleware("ssh.update", {"passwordauth": False})
        services = self.middleware("service.query", [["service", "=", "ssh"]])
        service_id = find_service_id(services, "ssh")
        self.middleware("service.update", service_id, {"enable": True})
        job = self.middleware("service.control", "START", "ssh")
        if isinstance(job, int):
            self.wait_job(job)
        log("Configured root Ed25519 SSH access; SSH password authentication is disabled.")

    def wait_job(self, job_id: int) -> None:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            jobs = self.middleware("core.get_jobs", [["id", "=", job_id]])
            if not isinstance(jobs, list) or not jobs:
                time.sleep(2)
                continue
            state = str(jobs[0].get("state", "")).upper()
            if state == "SUCCESS":
                return
            if state in {"FAILED", "ABORTED"}:
                raise StepError(f"TrueNAS SSH service job {job_id} ended in {state}")
            time.sleep(2)
        raise StepError(f"TrueNAS SSH service job {job_id} did not finish within 120s")

    def list_api_keys(self) -> list[dict[str, Any]]:
        keys = self.middleware("api_key.query")
        if not isinstance(keys, list) or any(not isinstance(item, dict) for item in keys):
            raise StepError("api_key.query returned an unexpected response shape")
        return keys

    def create_api_key(self, expires_at: datetime) -> dict[str, Any]:
        existing = [entry for entry in self.list_api_keys() if entry.get("name") == API_KEY_NAME]
        if existing:
            if not self.args.revoke_orphan_api_key:
                raise StepError(
                    "a TrueNAS API key with the golden-image name already exists, but its secret is not in the sidecar; "
                    "inspect it and use --revoke-orphan-api-key only if it is safe to replace"
                )
            if len(existing) != 1 or type(existing[0].get("id")) is not int:
                raise StepError("cannot safely recover an orphaned golden-image API key: expected one key with an integer id")
            orphan_id = existing[0]["id"]
            self.middleware("api_key.delete", orphan_id, sensitive=True)
            remaining = [entry for entry in self.list_api_keys() if entry.get("name") == API_KEY_NAME]
            if remaining:
                raise StepError("orphaned golden-image API key remains after deletion; refusing to create a duplicate")
            log(f"Removed the explicitly approved orphaned golden-image API key (id {orphan_id}).")
        expiry_ms = int(expires_at.timestamp() * 1000)
        result = self.middleware(
            "api_key.create",
            {
                "name": API_KEY_NAME,
                "username": "truenas_admin",
                "expires_at": {"$date": expiry_ms},
            },
            sensitive=True,
        )
        return parse_created_api_key(result)

    def revoke_api_key(self, key_id: int) -> None:
        self.middleware("api_key.delete", key_id, sensitive=True)
        log(f"Revoked the newly created source API key (id {key_id}) after a failed step.")

    def shutdown(self) -> None:
        if self.qemu_process.poll() is not None:
            return
        log("Requesting a clean source VM shutdown before reading its disk.")
        # Run shutdown as a transient systemd job. Calling shutdown directly
        # can keep the SSH session open while systemd stops middleware and
        # networking, which makes the SSH client time out even though the VM
        # is shutting down correctly.
        try:
            self.ssh(["/usr/bin/systemd-run", "--no-block", "/sbin/shutdown", "-h", "now"])
        except StepError as exc:
            if "TimeoutExpired" not in str(exc):
                raise
            log("SSH timed out while the shutdown job was starting; waiting for QEMU power-off confirmation.")
        try:
            code = self.qemu_process.wait(timeout=180)
        except subprocess.TimeoutExpired:
            raise StepError("source VM did not power off cleanly within 180 seconds") from None
        if code != 0:
            raise StepError(f"QEMU exited with status {code} after source VM shutdown")
        log("Source VM powered off cleanly.")


def find_user_id(value: Any, username: str) -> int:
    if not isinstance(value, list):
        raise StepError(f"user.query did not return a list for {username}")
    matches = [item for item in value if isinstance(item, dict) and item.get("username") == username]
    if len(matches) != 1 or type(matches[0].get("id")) is not int:
        raise StepError(f"expected one TrueNAS user record with an integer id for {username}")
    return matches[0]["id"]


def find_service_id(value: Any, service: str) -> int:
    if not isinstance(value, list):
        raise StepError(f"service.query did not return a list for {service}")
    matches = [item for item in value if isinstance(item, dict) and item.get("service") == service]
    if len(matches) != 1 or type(matches[0].get("id")) is not int:
        raise StepError(f"expected one TrueNAS service record with an integer id for {service}")
    return matches[0]["id"]


def parse_created_api_key(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        # Some middleware versions return the one-time key string directly.
        key = value.strip()
        key_id = None
    elif isinstance(value, dict):
        key = value.get("key") or value.get("api_key")
        key_id = value.get("id")
    else:
        key, key_id = None, None
    if not isinstance(key, str) or len(key.strip()) < 16:
        raise StepError("api_key.create did not return a usable one-time key; no secret was logged")
    try:
        parsed_id = int(key_id)
    except (TypeError, ValueError):
        raise StepError("api_key.create did not return the key id needed for safe rollback") from None
    return {"api_key": key.strip(), "id": parsed_id}


def validate_api_key(api_key: str, uri: str) -> dict[str, Any]:
    try:
        with TrueNASAPIClient(
            TrueNASSettings(uri=uri, username="truenas_admin", verify_ssl=False),
            api_key,
        ) as client:
            info = client.call("system.info")
    except Exception as exc:
        # Never propagate the upstream exception text: some versions include
        # request details in it, and this path handles a newly issued secret.
        raise StepError(f"new API key failed shared-client authentication ({type(exc).__name__})") from None
    if not isinstance(info, dict):
        raise StepError("authenticated system.info returned an unexpected response")
    version = str(info.get("version", ""))
    if not re.match(r"^27\.", version):
        raise StepError(f"authenticated source API reported an unexpected TrueNAS version: {version or 'unknown'}")
    return info


def check_disk_not_in_use(path: Path) -> None:
    result = run(["fuser", "-s", str(path)], timeout=10, check=False)
    if result.returncode == 0:
        raise StepError(f"source QCOW2 is already open by another process: {path}")
    if result.returncode != 1:
        raise StepError(f"could not determine whether the source disk is in use: {path}")


def check_port_available(port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((SSH_HOST, port))
    except OSError:
        raise StepError(f"required local QEMU forwarding port {port} is already in use") from None
    finally:
        sock.close()


def find_ovmf_code() -> Path:
    options = [
        Path("/usr/share/OVMF/OVMF_CODE_4M.fd"),
        Path("/usr/share/edk2/ovmf/OVMF_CODE_4M.fd"),
    ]
    for candidate in options:
        if candidate.is_file():
            return candidate
    raise StepError("could not find OVMF_CODE_4M.fd; install the host OVMF package")


def source_disk_size_bytes(info: dict[str, Any]) -> int:
    size = info.get("virtual-size")
    if (
        isinstance(size, bool)
        or not isinstance(size, int)
        or size < MIN_GOLDEN_DISK_BYTES
        or size % GIB_BYTES
    ):
        raise StepError("source VM disk must be a whole-GiB QCOW2 disk of at least 16 GiB")
    return size


def start_source_vm(args: argparse.Namespace, disk: Path) -> tuple[subprocess.Popen[bytes], int]:
    if not disk.is_file():
        raise StepError(f"source VM disk not found: {disk}")
    if not args.vm_vars.is_file():
        raise StepError(f"source VM UEFI variable store not found: {args.vm_vars}")
    info = json_result(run(["qemu-img", "info", "--output=json", str(disk)]).stdout, "qemu-img info")
    if info.get("format") != "qcow2":
        raise StepError("source VM disk must be QCOW2")
    disk_size_bytes = source_disk_size_bytes(info)
    check_disk_not_in_use(disk)
    check_port_available(SSH_PORT)
    check_port_available(API_PORT)
    args.qemu_log.parent.mkdir(parents=True, exist_ok=True)
    log_fd = os.open(args.qemu_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.fchmod(log_fd, 0o600)
    os.close(log_fd)
    code = find_ovmf_code()
    argv = [
        "qemu-system-x86_64",
        "-name",
        "truenas-golden-source",
        "-machine",
        "q35,accel=tcg",
        "-cpu",
        "max",
        "-smp",
        "2",
        "-m",
        "8G",
        "-rtc",
        "base=utc,clock=host",
        "-drive",
        f"if=pflash,format=raw,readonly=on,file={code}",
        "-drive",
        f"if=pflash,format=raw,file={args.vm_vars}",
        "-drive",
        f"file={disk},if=virtio,format=qcow2",
        "-netdev",
        f"user,id=net0,hostfwd=tcp:{SSH_HOST}:{SSH_PORT}-:22,hostfwd=tcp:{SSH_HOST}:{API_PORT}-:443",
        "-device",
        "virtio-net-pci,netdev=net0",
        "-display",
        "none",
        "-monitor",
        "none",
        "-serial",
        f"file:{args.qemu_log}",
    ]
    log(
        f"Starting the installed {disk_size_bytes // GIB_BYTES} GiB source VM "
        "with loopback-only SSH/API forwards."
    )
    log(f"QEMU serial log: {args.qemu_log}")
    try:
        log_stream = args.qemu_log.open("ab")
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log_stream, stderr=log_stream, start_new_session=True)
    except OSError as exc:
        raise StepError(f"could not start QEMU: {type(exc).__name__}") from None
    # QEMU owns the duplicated descriptor after exec; close our copy.
    log_stream.close()
    return process, disk_size_bytes


def api_uri() -> str:
    return f"wss://{SSH_HOST}:{API_PORT}/api/current"


def api_block_is_valid(document: dict[str, Any]) -> bool:
    return isinstance(document.get("truenas_api"), dict)


def verify_raw_layout(image: Path, expected_size_bytes: int) -> None:
    size = image.stat().st_size
    if size != expected_size_bytes:
        raise StepError(f"captured raw disk is {size} bytes, expected {expected_size_bytes}")
    table = run(["sgdisk", "--print", str(image)]).stdout
    if "Disk identifier (GUID):" not in table:
        raise StepError("captured raw disk has no readable GPT identifier")
    expected_codes = ["EF02", "EF00", "BF01"]
    for number, expected in enumerate(expected_codes, start=1):
        info = run(["sgdisk", "--info", str(number), str(image)]).stdout
        match = re.search(r"Partition GUID code:\s*([0-9A-Fa-f-]{36})", info)
        if match is None:
            raise StepError(f"could not inspect partition {number} in captured raw image")
        codes = {
            "21686148-6449-6e6f-744e-656564454649": "EF02",
            "c12a7328-f81f-11d2-ba4b-00a0c93ec93b": "EF00",
            "6a898cc3-1dd2-11b2-99a6-080020736631": "BF01",
        }
        actual = codes.get(match.group(1).lower(), match.group(1).upper())
        if actual != expected:
            raise StepError(f"partition {number} type is {actual}, expected {expected}")
    verification = run(["sgdisk", "--verify", str(image)]).stdout
    if "No problems found" not in verification:
        raise StepError("captured raw image GPT failed sgdisk verification")
    esp = run(["sgdisk", "--info", "2", str(image)]).stdout
    start = re.search(r"First sector:\s*(\d+)", esp)
    if start is None:
        raise StepError("could not read EFI partition offset in captured image")
    efi = run(["mdir", "-i", f"{image}@@{int(start.group(1)) * 512}", "-s", "::/EFI/BOOT"]).stdout
    if "bootx64" not in efi.lower():
        raise StepError("captured EFI partition does not contain EFI/BOOT/BOOTX64.EFI")


def capture_image(
    source: Path,
    output: Path,
    *,
    expected_size_bytes: int,
    force: bool,
) -> str:
    if output.exists() and not force:
        raise StepError(f"refusing to replace existing raw image without --force: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".raw", dir=output.parent)
    os.close(fd)
    stage = Path(temp_name)
    try:
        os.chmod(stage, 0o600)
        log("Converting the powered-off QCOW2 to a sparse raw whole-disk image.")
        run(
            ["qemu-img", "convert", "-p", "-f", "qcow2", "-O", "raw", "-S", "4k", str(source), str(stage)],
            timeout=3600,
        )
        info = json_result(run(["qemu-img", "info", "--output=json", str(stage)]).stdout, "qemu-img info")
        if info.get("format") != "raw" or info.get("virtual-size") != expected_size_bytes:
            raise StepError(
                f"captured image is not a {expected_size_bytes}-byte raw whole-disk image"
            )
        log("Comparing the raw capture with the guest-visible QCOW2 sectors.")
        run(["qemu-img", "compare", "-f", "qcow2", "-F", "raw", str(source), str(stage)], timeout=3600)
        verify_raw_layout(stage, expected_size_bytes)
        digest = sha256_file(stage)
        os.chmod(stage, 0o600)
        os.replace(stage, output)
        directory_fd = os.open(output.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return digest
    finally:
        if stage.exists():
            stage.unlink()


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StepError("another step1 process already holds the source VM lock") from None
        yield
    finally:
        os.close(fd)


def run_step(args: argparse.Namespace) -> None:
    require_tools(["qemu-img", "qemu-system-x86_64", "ssh", "fuser", "sgdisk", "mdir", "ssh-keygen"])
    disk = args.vm_disk.resolve()
    image = args.image.resolve()
    secrets_path = args.secrets.resolve()
    key_path = args.ssh_private_key.resolve()
    known_hosts = args.known_hosts.resolve()
    args.vm_vars = args.vm_vars.resolve()
    args.qemu_log = args.qemu_log.resolve()
    if image.parent != secrets_path.parent:
        raise StepError("the secret sidecar must remain beside the golden raw image")
    if image == disk or image == secrets_path or secrets_path == disk:
        raise StepError("VM disk, raw image, and secret sidecar must be distinct files")
    if not disk.is_file():
        raise StepError(f"source VM disk not found: {disk}")
    if not key_path.is_file() or stat.S_IMODE(key_path.stat().st_mode) & 0o077:
        raise StepError("root SSH private key must exist and have mode 0600 or stricter")
    if not known_hosts.is_file():
        raise StepError(f"pinned SSH known_hosts file not found: {known_hosts}")
    document = load_secret_document(secrets_path, image)
    if image.exists() and not args.force:
        raise StepError(f"refusing to replace existing raw image without --force: {image}")

    current_key = document.get("truenas_api", {}).get("api_key") if api_block_is_valid(document) else None
    old_sidecar = secrets_path.read_bytes()
    created_id: int | None = None
    sidecar_changed = False
    qemu: subprocess.Popen[bytes] | None = None
    with exclusive_lock(disk.with_suffix(disk.suffix + ".step1.lock")):
        try:
            qemu, disk_size_bytes = start_source_vm(args, disk)
            source = SourceVM(args, key_path, qemu)
            source.wait_for_root_ssh()
            source.wait_for_middleware()
            source.configure(document)

            expires_at: datetime | None = None
            if current_key:
                log("Validating the existing source API key; no duplicate key will be created.")
                info = validate_api_key(current_key, api_uri())
                api_block = document["truenas_api"]
                log(f"Existing API key authenticated to TrueNAS {info.get('version')}.")
            else:
                expires_at = datetime.now(timezone.utc) + timedelta(days=args.api_key_expiry_days)
                log(f"Creating API key for truenas_admin with the explicit {args.api_key_expiry_days}-day expiry.")
                created = source.create_api_key(expires_at)
                created_id = created["id"]
                try:
                    info = validate_api_key(created["api_key"], api_uri())
                    api_block = {
                        "api_key": created["api_key"],
                        "key_name": API_KEY_NAME,
                        "expires_at": expires_at.isoformat(timespec="seconds"),
                        "id": created_id,
                    }
                    log(f"New API key authenticated through the shared client to TrueNAS {info.get('version')}.")
                except Exception:
                    source.revoke_api_key(created_id)
                    created_id = None
                    raise
                document["truenas_api"] = api_block
                try:
                    atomic_write(secrets_path, serialize_secret_document(document).encode("utf-8"))
                    sidecar_changed = True
                except Exception:
                    source.revoke_api_key(created_id)
                    created_id = None
                    raise
                # Re-open the protected file and verify the exact atomically written TOML.
                reloaded = load_secret_document(secrets_path, image)
                if reloaded.get("truenas_api", {}).get("api_key") != created["api_key"]:
                    source.revoke_api_key(created_id)
                    created_id = None
                    raise StepError("API key did not survive protected sidecar read-back")

            # A valid key already present in the sidecar is intentionally left as-is.
            source.shutdown()
            qemu = None
            # Once validation and persistence have succeeded, a capture failure
            # leaves a valid key/sidecar pair that a later run can reuse. Do not
            # revoke it after the VM has shut down and strand the source config.
            sidecar_changed = False
            created_id = None
            digest = capture_image(
                disk,
                image,
                expected_size_bytes=disk_size_bytes,
                force=args.force,
            )
            log(f"Golden raw image: {image} ({disk_size_bytes} bytes)")
            log(f"Golden raw SHA-256: {digest}")
            log(f"Protected secret sidecar: {secrets_path} (mode 0600)")
        except Exception:
            if created_id is not None and qemu is not None and qemu.poll() is None:
                try:
                    source = SourceVM(args, key_path, qemu)
                    source.revoke_api_key(created_id)
                except Exception:
                    log("WARNING: automatic API-key rollback failed; revoke the newly created key in TrueNAS.")
                created_id = None
            # Once the VM has powered off, a newly-created TrueNAS key cannot
            # be revoked over middleware. Keep its already-persisted secret in
            # the sidecar so a retry can authenticate and reuse the key.
            if sidecar_changed and not (created_id is not None and qemu is not None and qemu.poll() is not None):
                try:
                    atomic_write(secrets_path, old_sidecar)
                    log("Restored the previous secret sidecar after the failed capture.")
                except Exception:
                    log("WARNING: could not restore the previous secret sidecar; inspect it before retrying.")
            elif sidecar_changed:
                log("Source VM powered off after API-key persistence; keeping the matching protected sidecar for retry.")
            if qemu is not None and qemu.poll() is None:
                try:
                    SourceVM(args, key_path, qemu).shutdown()
                except Exception as shutdown_exc:
                    log(f"WARNING: source VM remains running; clean shutdown failed ({type(shutdown_exc).__name__}).")
            raise


def positive_days(value: str) -> int:
    try:
        days = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive integer") from None
    if days <= 0 or days > 3650:
        raise argparse.ArgumentTypeError("must be between 1 and 3650 days")
    return days


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare an installed TrueNAS source VM, provision its API key, and capture its boot disk."
    )
    parser.add_argument("--vm-disk", type=Path, default=DEFAULT_VM_DISK)
    parser.add_argument("--vm-vars", type=Path, default=DEFAULT_VM_VARS)
    parser.add_argument("--ssh-private-key", type=Path, default=DEFAULT_VM_KEY)
    parser.add_argument("--known-hosts", type=Path, default=DEFAULT_KNOWN_HOSTS)
    parser.add_argument("--qemu-log", type=Path, default=DEFAULT_QEMU_LOG)
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--secrets", type=Path, default=DEFAULT_SECRETS)
    parser.add_argument(
        "--api-key-expiry-days",
        type=positive_days,
        required=True,
        help="explicit expiration interval for a newly created truenas_admin API key",
    )
    parser.add_argument("--startup-timeout", type=int, default=900)
    parser.add_argument("--force", action="store_true", help="replace an existing raw golden image after verified capture")
    parser.add_argument(
        "--revoke-orphan-api-key",
        action="store_true",
        help="revoke the reserved golden-image key if its one-time secret is missing from the sidecar",
    )
    parser.set_defaults(func=run_step)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        args.func(args)
    except (StepError, OSError, TrueNASAPIError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
