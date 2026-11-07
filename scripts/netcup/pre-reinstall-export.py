#!/usr/bin/env python3
"""Read-only, allowlisted evidence export for the v1001/r1002 reinstall."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path, PurePosixPath
from typing import Callable


RELEASE_ROOT = Path("/home/vscode/vbpub-release-program-20261027")
ENV_HELPER = RELEASE_ROOT / "dstdns-r2" / "env.sh"
HOSTS = {
    "r1002": {
        "fqdn": "r1002.vxxu.de", "short": "r1002", "server_id": 799611,
        "api_check": "r1002_api_check", "ssh_function": "r1002_ssh",
        "remote": 'root@152.53.187.201',
    },
    "v1001": {
        "fqdn": "v1001.vxxu.de", "short": "v1001", "server_id": 804027,
        "api_check": "v1001_api_check", "ssh_function": "v1001_ssh",
        "remote": 'root@"$V1001_IP"',
    },
}

MAX_FILE_BYTES = 1024 * 1024

# The redaction logic lives in ONE file that is both imported here (controller
# side pass) and embedded verbatim into the remote collector (host side pass).
REDACTION_PATH = Path(__file__).resolve().with_name("export_redaction.py")


def _load_redaction():
    spec = importlib.util.spec_from_file_location("netcup_export_redaction", REDACTION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


export_redaction = _load_redaction()
REDACTION_SOURCE = REDACTION_PATH.read_text(encoding="utf-8")
SENSITIVE_PATH_RE = export_redaction.SENSITIVE_PATH_RE
ALLOWED_ARCHIVE_ROOTS = {
    "partition", "etc", "home", "installer", "iobench", "iogovmon", "docker", "runner", "logs", "meta",
}


REMOTE_COLLECTOR = r"""#!/usr/bin/env python3
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

EXPECTED = __HOST_EXPECTATION__
MAX_FILE_BYTES = __MAX_FILE_BYTES__
ROOT = Path("/")

__REDACTION_SOURCE__

def host_path(value):
    path = Path(value)
    return ROOT / path.relative_to("/") if path.is_absolute() else ROOT / path

def checked(argv):
    return subprocess.run(argv, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

def identity():
    facts = (
        checked(["hostname", "-f"]).decode().strip(),
        checked(["hostnamectl", "--static"]).decode().strip(),
        host_path("/etc/vbpub/server-id").read_text(encoding="utf-8").strip(),
        checked(["lsblk", "-dn", "-o", "NAME", "/dev/vda"]).decode().strip(),
    )
    wanted = (EXPECTED["fqdn"], EXPECTED["short"], str(EXPECTED["server_id"]), "vda")
    if facts != wanted:
        raise SystemExit("remote host identity mismatch; evidence export refused")
    return facts

def add_bytes(archive, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o600
    archive.addfile(info, io.BytesIO(data))

def read_regular(path, source, notes):
    # O_NONBLOCK: opening a FIFO for reading blocks forever without it, and the
    # S_ISREG check below only runs after the open succeeded.
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        notes.append("skipped unreadable file: " + source + " (" + str(exc) + ")")
        return None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            notes.append("skipped non-regular file: " + source)
            return None
        if metadata.st_size > MAX_FILE_BYTES:
            notes.append(f"skipped oversized file: {source} size={metadata.st_size} cap={MAX_FILE_BYTES}")
            return None
        data = os.read(descriptor, MAX_FILE_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(data) > MAX_FILE_BYTES:
        notes.append(f"skipped oversized file: {source} size>{MAX_FILE_BYTES} cap={MAX_FILE_BYTES}")
        return None
    return data

def add_path(archive, source, destination, notes):
    path = host_path(source)
    if path.is_symlink():
        notes.append("skipped symlink: " + source)
        return
    if path.is_file():
        candidates = [(path, destination)]
    elif path.is_dir():
        candidates = []
        for current, directories, filenames in os.walk(path, topdown=True, followlinks=False):
            current_path = Path(current)
            retained = []
            for directory in sorted(directories):
                child = current_path / directory
                if child.is_symlink():
                    notes.append("skipped symlink directory: " + str(child))
                elif source == "/var/lib/iogovmon" and child == path / "scratch":
                    notes.append("skipped iogovmon scratch directory: " + str(child))
                else:
                    retained.append(directory)
            directories[:] = retained
            for filename in sorted(filenames):
                item = current_path / filename
                relative = item.relative_to(path)
                if any(key_is_sensitive(part) or SENSITIVE_PATH_RE.search(part) for part in relative.parts):
                    notes.append("skipped sensitive filename: " + str(item))
                    continue
                candidates.append((item, str(Path(destination) / relative)))
    else:
        notes.append("absent: " + source)
        return
    for item, archive_name in candidates:
        source_name = source + ("/" + str(item.relative_to(path)) if path.is_dir() else "")
        data = read_regular(item, source_name, notes)
        if data is None:
            continue
        try:
            content = redact_bytes(archive_name, data)
        except ValueError as exc:
            notes.append("skipped binary, sensitive or unredactable file: " + source_name + " (" + str(exc)[:120] + ")")
            continue
        add_bytes(archive, archive_name, content)
        notes.append("exported: " + source_name)

def cockpit_paths(archive, notes):
    runner_env = host_path("/home/runner/runner-scripts/runner.env")
    raw = read_regular(runner_env, "/home/runner/runner-scripts/runner.env", notes)
    if raw is None:
        return
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        notes.append("skipped non-text runner.env while resolving cockpit paths")
        return
    wanted = {"COCKPIT_CONFIG_DIR", "COCKPIT_HOME_DIR"}
    found = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in wanted:
            found[key] = value.strip().strip("\"'")
    for key in sorted(wanted):
        value = found.get(key)
        if not value or not value.startswith("/home/runner/") or ".." in Path(value).parts:
            notes.append("skipped invalid " + key + " from runner.env")
            continue
        path = Path(value)
        if path.parent != Path("/home/runner") or not path.name.startswith("mdt-cockpit-runner-"):
            notes.append("skipped out-of-scope " + key + " from runner.env")
            continue
        add_path(archive, value, "home/runner/" + path.name, notes)

facts = identity()
notes = ["identity verified: " + " / ".join(facts)]
archive = tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz")
for name, argv in (
    ("partition/sfdisk-d.txt", ["sfdisk", "-d", "/dev/vda"]),
    ("partition/lsblk-b-J.json", ["lsblk", "-b", "-J", "/dev/vda"]),
    ("partition/blkid.txt", ["blkid"]),
    ("docker/volume-names.txt", ["docker", "volume", "ls", "--format", "{{.Name}}"]),
):
    add_bytes(archive, name, checked(argv))
    notes.append("exported command: " + " ".join(argv))

for source, destination in (
    ("/etc/fstab", "etc/fstab"),
    ("/etc/hosts", "etc/hosts"),
    ("/etc/cloud/cloud.cfg.d", "etc/cloud.cfg.d"),
    ("/etc/containerd/config.toml", "etc/containerd/config.toml"),
    ("/etc/docker/daemon.json", "etc/docker/daemon.json"),
    ("/etc/mdt", "etc/mdt"),
    ("/etc/mdt-cow-root.sh", "etc/mdt-cow-root.sh"),
    ("/etc/udev/rules.d", "etc/rules.d"),
    ("/etc/sudoers.d", "etc/sudoers.d"),
    ("/etc/sysctl.d", "etc/sysctl.d"),
    ("/etc/systemd/system", "etc/systemd-system"),
    ("/etc/vbpub/server-id", "etc/vbpub/server-id"),
    ("/etc/vbpub/bootstrap.env", "etc/vbpub/bootstrap.env"),
    ("/home/runner/runner-scripts/runner.env", "runner/runner.env"),
    ("/var/lib/vbpub/bootstrap", "installer/state"),
    ("/var/log/debian-install", "installer/logs"),
    ("/root/custom_script.output", "logs/custom_script.output"),
    ("/root/custom_script.output2", "logs/custom_script.output2"),
    ("/var/lib/iobench", "iobench/state"),
    ("/var/lib/iogovmon", "iogovmon/state"),
):
    add_path(archive, source, destination, notes)
cockpit_paths(archive, notes)
add_bytes(archive, "meta/collection.txt", ("\n".join(notes) + "\n").encode())
archive.close()
"""


def remote_collector(host: str, *, max_file_bytes: int = MAX_FILE_BYTES) -> str:
    if isinstance(max_file_bytes, bool) or not isinstance(max_file_bytes, int) or max_file_bytes <= 0:
        raise ValueError("max_file_bytes must be a positive integer")
    if host not in HOSTS:
        raise ValueError("host must be r1002 or v1001")
    expectation = HOSTS[host]
    return (
        REMOTE_COLLECTOR
        .replace("__HOST_EXPECTATION__", repr(expectation))
        .replace("__MAX_FILE_BYTES__", str(max_file_bytes))
        .replace("__REDACTION_SOURCE__", REDACTION_SOURCE)
    )

def ssh_command(host: str) -> str:
    target = HOSTS[host]
    remote = target["remote"]
    return (
        "set -euo pipefail; "
        f"source {str(ENV_HELPER)!r}; "
        f"{target['api_check']} || exit $?; "
        f"{target['ssh_function']} {remote} 'python3 -'"
    )


def redact_export_bytes(name: str, content: bytes) -> bytes:
    """Redact sensitive fields and refuse binary or incompletely redacted content."""
    return export_redaction.redact_bytes(name, content)


def _safe_member_name(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe export archive path: {name}")
    if path.parts[0] not in ALLOWED_ARCHIVE_ROOTS:
        raise ValueError(f"export path is outside the explicit allowlist: {name}")
    return path.as_posix()


def _extract_redacted(
    archive_bytes: bytes,
    destination: Path,
    *,
    max_file_bytes: int = MAX_FILE_BYTES,
) -> None:
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                raise ValueError(f"non-regular export archive member refused: {member.name}")
            name = _safe_member_name(member.name)
            if member.size > max_file_bytes:
                raise ValueError(
                    f"export archive member exceeds the {max_file_bytes}-byte file cap: {name}"
                )
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"could not read export archive member: {name}")
            raw = stream.read(max_file_bytes + 1)
            if len(raw) > max_file_bytes:
                raise ValueError(f"export archive member exceeds the {max_file_bytes}-byte file cap: {name}")
            content = redact_export_bytes(name, raw)
            target = destination.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with target.open("xb") as output:
                os.chmod(target, 0o600)
                output.write(content)


def _write_manifest(destination: Path) -> None:
    rows = []
    for path in sorted(item for item in destination.rglob("*") if item.is_file()):
        if path.name == "SHA256SUMS":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(f"{digest}  {path.relative_to(destination).as_posix()}")
    manifest = destination / "SHA256SUMS"
    manifest.write_text("\n".join(rows) + "\n", encoding="utf-8")
    os.chmod(manifest, 0o600)


def export_snapshot(
    host: str,
    date: str,
    *,
    root: Path = RELEASE_ROOT,
    runner: Callable = subprocess.run,
    max_file_bytes: int = MAX_FILE_BYTES,
) -> Path:
    if host not in HOSTS:
        raise ValueError("host must be r1002 or v1001")
    if isinstance(max_file_bytes, bool) or not isinstance(max_file_bytes, int) or max_file_bytes <= 0:
        raise ValueError("max_file_bytes must be a positive integer")
    try:
        parsed_date = dt.datetime.strptime(date, "%Y%m%d")
    except ValueError as exc:
        raise ValueError("date must be a valid YYYYMMDD date") from exc
    if parsed_date.strftime("%Y%m%d") != date:
        raise ValueError("date must be a valid YYYYMMDD date")

    destination = Path(root) / host / f"pre-reinstall-{date}"
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite existing export: {destination}")
    if not destination.parent.exists():
        destination.parent.mkdir(parents=True, mode=0o700)
        os.chmod(destination.parent, 0o700)
    destination.mkdir(mode=0o700)

    try:
        command = ssh_command(host)
        completed = runner(
            ["bash", "-lc", command],
            input=remote_collector(host, max_file_bytes=max_file_bytes).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"read-only evidence export failed ({completed.returncode}): {detail[-2000:]}")
        _extract_redacted(completed.stdout, destination, max_file_bytes=max_file_bytes)
        (destination / "meta").mkdir(mode=0o700, exist_ok=True)
        (destination / "meta" / "export.txt").write_text(
            f"host={host}\ndate={date}\nmethod=read-only SSH snapshot\n",
            encoding="utf-8",
        )
        os.chmod(destination / "meta" / "export.txt", 0o600)
        _write_manifest(destination)
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", choices=tuple(HOSTS))
    parser.add_argument("--date", default=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d"))
    parser.add_argument(
        "--max-file-bytes", type=int, default=MAX_FILE_BYTES,
        help=f"maximum bytes read/exported per regular file (default {MAX_FILE_BYTES})",
    )
    args = parser.parse_args(argv)
    try:
        destination = export_snapshot(args.host, args.date, max_file_bytes=args.max_file_bytes)
    except (FileExistsError, RuntimeError, ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f"Evidence exported to {destination}")
    print(f"Manifest: {destination / 'SHA256SUMS'}")
    notes = destination / "meta" / "collection.txt"
    if notes.is_file():
        lines = notes.read_text(encoding="utf-8").splitlines()
        for heading, prefix in (
            ("Oversized files skipped", "skipped oversized file:"),
            ("Files skipped as binary, sensitive or unredactable", "skipped binary, sensitive or unredactable file:"),
        ):
            skipped = [line for line in lines if line.startswith(prefix)]
            if skipped:
                print(f"{heading} (listed in meta/collection.txt):")
                for line in skipped:
                    print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
