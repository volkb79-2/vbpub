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
import stat
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
SENSITIVE_FRAGMENT = r"(?:TOKEN|PASSWORD|PASSWD|PASS|SECRET|PRIVATE|CREDENTIAL|AUTHORIZATION|(?:API|ACCESS)[_-]?KEY|PAT|PSK|JWT)"
SENSITIVE_KEY_RE = re.compile(rf"(?i)(?<![A-Z0-9]){SENSITIVE_FRAGMENT}(?![A-Z0-9])")
SENSITIVE_PAIR_RE = re.compile(
    r'''(?ix)
    (?P<prefix>(?P<key_quote>["']?)(?P<key>[A-Z0-9_.-]+)(?P=key_quote)\s*[:=]\s*)
    (?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)
    '''
)
SENSITIVE_ASSIGNMENT_RE = re.compile(
    rf"(?im)^(?P<prefix>\s*(?:export\s+)?(?:[A-Z0-9_.-]*{SENSITIVE_FRAGMENT}[A-Z0-9_.-]*)\s*=\s*)(?P<value>[^\r\n]*)"
)
AUTHORIZATION_RE = re.compile(
    r'''(?im)(?P<prefix>(?:["']?authorization["']?)\s*[:=]\s*)(?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^,}\r\n]+)'''
)
CLI_SECRET_RE = re.compile(
    rf'''(?i)(?P<prefix>--(?:{SENSITIVE_FRAGMENT})(?:=|\s+))(?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;]+)'''
)
URL_USERINFO_RE = re.compile(r"(?i)(https?://)[^/@\s]+@")
PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
    re.DOTALL,
)
ALLOWED_ARCHIVE_ROOTS = {
    "partition", "etc", "home", "installer", "iobench", "iogovmon", "docker", "runner", "logs", "meta",
}
SENSITIVE_PATH_RE = re.compile(
    r"(?i)(?:token|password|passwd|secret|private.?key|credential|api.?key|(?<![a-z0-9])pass(?![a-z0-9])|\.ssh)"
)


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
SENSITIVE_FRAGMENT = r"(?:TOKEN|PASSWORD|PASSWD|PASS|SECRET|PRIVATE|CREDENTIAL|AUTHORIZATION|(?:API|ACCESS)[_-]?KEY|PAT|PSK|JWT)"
SENSITIVE_KEY_RE = re.compile(rf"(?i)(?<![A-Z0-9]){SENSITIVE_FRAGMENT}(?![A-Z0-9])")
SENSITIVE_PAIR_RE = re.compile(
    r'''(?ix)
    (?P<prefix>(?P<key_quote>["']?)(?P<key>[A-Z0-9_.-]+)(?P=key_quote)\s*[:=]\s*)
    (?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)
    '''
)
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r'''(?im)^(?P<prefix>\s*(?:export\s+)?[A-Z0-9_.-]*(?:TOKEN|PASSWORD|PASSWD|PASS|SECRET|PRIVATE|CREDENTIAL|AUTHORIZATION|(?:API|ACCESS)[_-]?KEY|PAT|PSK|JWT)[A-Z0-9_.-]*\s*=\s*)(?P<value>[^\r\n]*)'''
)
AUTHORIZATION_RE = re.compile(
    r'''(?im)(?P<prefix>(?:["']?authorization["']?)\s*[:=]\s*)(?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^,}\r\n]+)'''
)
CLI_SECRET_RE = re.compile(
    r'''(?i)(?P<prefix>--(?:TOKEN|PASSWORD|PASSWD|PASS|SECRET|PRIVATE|CREDENTIAL|AUTHORIZATION|(?:API|ACCESS)[_-]?KEY|PAT|PSK|JWT)(?:=|\s+))(?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;]+)'''
)
URL_USERINFO_RE = re.compile(r"(?i)(https?://)[^/@\s]+@")
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----", re.S)
SENSITIVE_PATH_RE = re.compile(r"(?i)(?:token|password|passwd|secret|private.?key|credential|api.?key|(?<![a-z0-9])pass(?![a-z0-9])|\.ssh)")

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

def replace_value(match):
    value = match.group("value")
    if value[:1] in {"'", '"'} and value[-1:] == value[:1]:
        replacement = value[0] + "***REDACTED***" + value[0]
    else:
        replacement = "***REDACTED***"
    return match.group("prefix") + replacement

def strip_jsonc_comments(text):
    output = []
    index = 0
    in_string = escaped = False
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
        elif char == '"':
            in_string = True
            output.append(char)
            index += 1
        elif char == "/" and next_char == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
        elif char == "/" and next_char == "*":
            index += 2
            while index + 1 < len(text) and text[index:index + 2] != "*/":
                if text[index] in "\r\n":
                    output.append(text[index])
                index += 1
            index = min(len(text), index + 2)
        else:
            output.append(char)
            index += 1
    return "".join(output)

def redact_text(text):
    text = PRIVATE_KEY_RE.sub("***REDACTED PRIVATE KEY***", text)
    text = URL_USERINFO_RE.sub(r"\1", text)
    text = CLI_SECRET_RE.sub(replace_value, text)
    text = AUTHORIZATION_RE.sub(replace_value, text)
    text = SENSITIVE_ASSIGNMENT_RE.sub(replace_value, text)
    def redact_pair(match):
        return replace_value(match) if SENSITIVE_KEY_RE.search(match.group("key")) else match.group(0)
    return SENSITIVE_PAIR_RE.sub(redact_pair, text)

def redact(value, key=""):
    if key and SENSITIVE_KEY_RE.search(key):
        return "***REDACTED***"
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value

def has_unredacted(text):
    for pattern in (AUTHORIZATION_RE, SENSITIVE_ASSIGNMENT_RE, SENSITIVE_PAIR_RE, CLI_SECRET_RE):
        for match in pattern.finditer(text):
            key = match.groupdict().get("key", "")
            if key and not SENSITIVE_KEY_RE.search(key):
                continue
            if match.group("value").strip().strip("'\"") != "***REDACTED***":
                return True
    return bool(PRIVATE_KEY_RE.search(text) or URL_USERINFO_RE.search(text))

def safe_bytes(path, data):
    if SENSITIVE_PATH_RE.search(path) or b"\x00" in data:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        parsed = json.loads(strip_jsonc_comments(text))
    except json.JSONDecodeError:
        parsed = None
    else:
        cleaned = (json.dumps(redact(parsed), indent=2, sort_keys=True) + "\n").encode()
        if has_unredacted(cleaned.decode("utf-8")):
            raise ValueError("unredacted secret-like value remains: " + path)
        return cleaned
    if path.endswith(".jsonl"):
        rows = []
        for line in text.splitlines():
            try:
                rows.append(json.dumps(redact(json.loads(strip_jsonc_comments(line))), sort_keys=True))
            except json.JSONDecodeError:
                rows.append(redact_text(line))
        text = "\n".join(rows) + ("\n" if rows else "")
    else:
        text = redact_text(text)
    if has_unredacted(text):
        raise ValueError("unredacted secret-like value remains: " + path)
    return text.encode()

def add_bytes(archive, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o600
    archive.addfile(info, io.BytesIO(data))

def read_regular(path, source, notes):
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
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
                if any(SENSITIVE_KEY_RE.search(part) or SENSITIVE_PATH_RE.search(part) for part in relative.parts):
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
        content = safe_bytes(archive_name, data)
        if content is None:
            notes.append("skipped binary or sensitive file: " + source_name)
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


def _replace_sensitive_value(match: re.Match[str]) -> str:
    value = match.group("value")
    if value[:1] in {"'", '"'} and value[-1:] == value[:1]:
        replacement = value[0] + "***REDACTED***" + value[0]
    else:
        replacement = "***REDACTED***"
    return match.group("prefix") + replacement


def _strip_jsonc_comments(text: str) -> str:
    """Remove JSONC comments without changing comment-like text inside strings."""
    output = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if char == "/" and next_char == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        if char == "/" and next_char == "*":
            index += 2
            while index + 1 < len(text) and text[index:index + 2] != "*/":
                if text[index] in "\r\n":
                    output.append(text[index])
                index += 1
            index = min(len(text), index + 2)
            continue
        output.append(char)
        index += 1
    return "".join(output)


def _redact_text(text: str) -> str:
    text = PRIVATE_KEY_RE.sub("***REDACTED PRIVATE KEY***", text)
    text = URL_USERINFO_RE.sub(r"\1", text)
    text = CLI_SECRET_RE.sub(_replace_sensitive_value, text)
    text = AUTHORIZATION_RE.sub(_replace_sensitive_value, text)
    text = SENSITIVE_ASSIGNMENT_RE.sub(_replace_sensitive_value, text)

    def redact_pair(match: re.Match[str]) -> str:
        if not SENSITIVE_KEY_RE.search(match.group("key")):
            return match.group(0)
        return _replace_sensitive_value(match)

    text = SENSITIVE_PAIR_RE.sub(redact_pair, text)
    return text


def _redact_json(value, key: str = ""):
    if key and SENSITIVE_KEY_RE.search(key):
        return "***REDACTED***"
    if isinstance(value, dict):
        return {item_key: _redact_json(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _has_unredacted_secret(text: str) -> bool:
    for pattern in (AUTHORIZATION_RE, SENSITIVE_ASSIGNMENT_RE, SENSITIVE_PAIR_RE, CLI_SECRET_RE):
        for match in pattern.finditer(text):
            key = match.groupdict().get("key", "")
            if key and not SENSITIVE_KEY_RE.search(key):
                continue
            value = match.group("value").strip().strip("'\"")
            if value != "***REDACTED***":
                return True
    return bool(PRIVATE_KEY_RE.search(text) or URL_USERINFO_RE.search(text))


def redact_export_bytes(name: str, content: bytes) -> bytes:
    """Redact sensitive fields and refuse binary or incompletely redacted content."""
    if SENSITIVE_PATH_RE.search(name):
        raise ValueError(f"sensitive export path refused: {name}")
    if b"\x00" in content:
        raise ValueError(f"binary NUL content refused: {name}")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"non-text content refused: {name}") from exc

    candidate = _strip_jsonc_comments(text)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        parsed = None
    else:
        cleaned = (json.dumps(_redact_json(parsed), indent=2, sort_keys=True) + "\n").encode()
        if _has_unredacted_secret(cleaned.decode("utf-8")):
            raise ValueError(f"unredacted secret-like value remains in {name}")
        return cleaned

    if name.endswith(".jsonl"):
        rows = []
        for line in text.splitlines():
            try:
                rows.append(json.dumps(_redact_json(json.loads(_strip_jsonc_comments(line))), sort_keys=True))
            except json.JSONDecodeError:
                rows.append(_redact_text(line))
        text = "\n".join(rows) + ("\n" if rows else "")
    else:
        text = _redact_text(text)
    if _has_unredacted_secret(text):
        raise ValueError(f"unredacted secret-like value remains in {name}")
    return text.encode()


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
        oversized = [
            line for line in notes.read_text(encoding="utf-8").splitlines()
            if line.startswith("skipped oversized file:")
        ]
        if oversized:
            print("Oversized files skipped (listed in meta/collection.txt):")
            for line in oversized:
                print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
