#!/usr/bin/env python3
"""Read-only, allowlisted evidence export for the v1001/r1002 reinstall."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
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

SENSITIVE_KEY_RE = re.compile(
    r"(?:TOKEN|PASSWORD|SECRET|PRIVATE[_ -]?KEY|CREDENTIAL|ACCESS[_ -]?KEY)",
    re.IGNORECASE,
)
SENSITIVE_VALUE_RE = re.compile(
    r"(?i)(\b[A-Z0-9_]*(?:TOKEN|PASSWORD|SECRET|PRIVATE[_ -]?KEY|CREDENTIAL|ACCESS[_ -]?KEY)[A-Z0-9_]*\s*[=:]\s*)([^\r\n]*)"
)
PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
    re.DOTALL,
)
ALLOWED_ARCHIVE_ROOTS = {
    "partition", "etc", "installer", "iobench", "iogovmon", "docker", "runner", "logs", "meta",
}
SENSITIVE_PATH_RE = re.compile(r"(?:token|password|secret|private.?key|credential|\.ssh)", re.IGNORECASE)


REMOTE_COLLECTOR = r'''#!/usr/bin/env python3
import io
import json
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path

EXPECTED = __HOST_EXPECTATION__

def checked(argv):
    return subprocess.run(argv, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

def identity():
    facts = (
        checked(["hostname", "-f"]).decode().strip(),
        checked(["hostnamectl", "--static"]).decode().strip(),
        Path("/etc/vbpub/server-id").read_text(encoding="utf-8").strip(),
        checked(["lsblk", "-dn", "-o", "NAME", "/dev/vda"]).decode().strip(),
    )
    wanted = (EXPECTED["fqdn"], EXPECTED["short"], str(EXPECTED["server_id"]), "vda")
    if facts != wanted:
        raise SystemExit("remote host identity mismatch; evidence export refused")
    return facts

def redact(value, key=""):
    if key and re.search(r"TOKEN|PASSWORD|SECRET|PRIVATE[_ -]?KEY|CREDENTIAL|ACCESS[_ -]?KEY", key, re.I):
        return "***REDACTED***"
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = re.sub(
            r"(?i)(\b[A-Z0-9_]*(?:TOKEN|PASSWORD|SECRET|PRIVATE[_ -]?KEY|CREDENTIAL|ACCESS[_ -]?KEY)[A-Z0-9_]*\s*[=:]\s*)([^\r\n]*)",
            r"\1***REDACTED***", value,
        )
        return re.sub(
            r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
            "***REDACTED PRIVATE KEY***", value, flags=re.S,
        )
    return value

def safe_bytes(path, data):
    if any(part.startswith(".") and part in {".ssh", ".aws", ".gnupg"} for part in Path(path).parts):
        return None
    if not data:
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if path.endswith((".json", ".jsonc")):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            pass
        else:
            return (json.dumps(redact(parsed), indent=2, sort_keys=True) + "\n").encode()
    if path.endswith(".jsonl"):
        rows = []
        for line in text.splitlines():
            try:
                rows.append(json.dumps(redact(json.loads(line)), sort_keys=True))
            except json.JSONDecodeError:
                rows.append(redact(line))
        return ("\n".join(rows) + ("\n" if rows else "")).encode()
    return redact(text).encode()

def add_bytes(archive, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o600
    archive.addfile(info, io.BytesIO(data))

def add_path(archive, source, destination, notes):
    path = Path(source)
    if path.is_symlink():
        notes.append("skipped symlink: " + source)
        return
    if path.is_file():
        candidates = [(path, destination)]
    elif path.is_dir():
        candidates = []
        for item in sorted(path.rglob("*")):
            if item.is_symlink() or not item.is_file():
                continue
            relative = item.relative_to(path)
            if any(re.search(r"TOKEN|PASSWORD|SECRET|PRIVATE[_ -]?KEY|CREDENTIAL|ACCESS[_ -]?KEY", part, re.I) for part in relative.parts):
                notes.append("skipped sensitive filename: " + str(item))
                continue
            candidates.append((item, str(Path(destination) / relative)))
    else:
        notes.append("absent: " + source)
        return
    for item, archive_name in candidates:
        if item.is_symlink() or not item.is_file():
            notes.append("skipped non-regular file: " + str(item))
            continue
        content = safe_bytes(archive_name, item.read_bytes())
        if content is None:
            notes.append("skipped non-text file: " + str(item))
            continue
        add_bytes(archive, archive_name, content)
        notes.append("exported: " + source + ("/" + str(item.relative_to(path)) if path.is_dir() else ""))

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
    ("/etc/vbpub/server-id", "etc/vbpub/server-id"),
    ("/etc/mdt/host-setup.env", "etc/mdt/host-setup.env"),
    ("/home/runner/runner-scripts/runner.env", "runner/runner.env"),
    ("/var/lib/vbpub/bootstrap", "installer/state"),
    ("/var/log/debian-install", "installer/logs"),
    ("/root/custom_script.output", "logs/custom_script.output"),
    ("/root/custom_script.output2", "logs/custom_script.output2"),
    ("/var/lib/iobench", "iobench/state"),
    ("/var/lib/iogovmon", "iogovmon/state"),
):
    add_path(archive, source, destination, notes)
add_bytes(archive, "meta/collection.txt", ("\n".join(notes) + "\n").encode())
archive.close()
'''


def remote_collector(host: str) -> str:
    expectation = HOSTS[host]
    return REMOTE_COLLECTOR.replace("__HOST_EXPECTATION__", repr(expectation))


def ssh_command(host: str) -> str:
    target = HOSTS[host]
    remote = target["remote"]
    return (
        "set -euo pipefail; "
        f"source {str(ENV_HELPER)!r}; "
        f"{target['api_check']} || exit $?; "
        f"{target['ssh_function']} {remote} 'python3 -'"
    )


def _redact_json(value, key: str = ""):
    if key and SENSITIVE_KEY_RE.search(key):
        return "***REDACTED***"
    if isinstance(value, dict):
        return {item_key: _redact_json(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        value = PRIVATE_KEY_RE.sub("***REDACTED PRIVATE KEY***", value)
        return SENSITIVE_VALUE_RE.sub(r"\1***REDACTED***", value)
    return value


def redact_export_bytes(name: str, content: bytes) -> bytes:
    """Redact sensitive fields and refuse binary content before it is saved."""
    if SENSITIVE_PATH_RE.search(name):
        raise ValueError(f"sensitive export path refused: {name}")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"non-text content refused: {name}") from exc
    if name.endswith((".json", ".jsonc")):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            pass
        else:
            return (json.dumps(_redact_json(parsed), indent=2, sort_keys=True) + "\n").encode()
    if name.endswith(".jsonl"):
        rows = []
        for line in text.splitlines():
            try:
                rows.append(json.dumps(_redact_json(json.loads(line)), sort_keys=True))
            except json.JSONDecodeError:
                rows.append(PRIVATE_KEY_RE.sub("***REDACTED PRIVATE KEY***", SENSITIVE_VALUE_RE.sub(r"\1***REDACTED***", line)))
        return ("\n".join(rows) + ("\n" if rows else "")).encode()
    text = PRIVATE_KEY_RE.sub("***REDACTED PRIVATE KEY***", text)
    text = SENSITIVE_VALUE_RE.sub(r"\1***REDACTED***", text)
    return text.encode()


def _safe_member_name(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe export archive path: {name}")
    if path.parts[0] not in ALLOWED_ARCHIVE_ROOTS:
        raise ValueError(f"export path is outside the explicit allowlist: {name}")
    return path.as_posix()


def _extract_redacted(archive_bytes: bytes, destination: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                raise ValueError(f"non-regular export archive member refused: {member.name}")
            name = _safe_member_name(member.name)
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"could not read export archive member: {name}")
            content = redact_export_bytes(name, stream.read())
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
) -> Path:
    if host not in HOSTS:
        raise ValueError("host must be r1002 or v1001")
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
            input=remote_collector(host).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"read-only evidence export failed ({completed.returncode}): {detail[-2000:]}")
        _extract_redacted(completed.stdout, destination)
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
    args = parser.parse_args(argv)
    try:
        destination = export_snapshot(args.host, args.date)
    except (FileExistsError, RuntimeError, ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f"Evidence exported to {destination}")
    print(f"Manifest: {destination / 'SHA256SUMS'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
