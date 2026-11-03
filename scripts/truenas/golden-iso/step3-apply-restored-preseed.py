#!/usr/bin/env python3
"""Rotate golden credentials and apply root-key SSH on a restored host.

The golden disk carries a shared admin password hash and API key. This step,
run over the pinned root SSH channel, replaces both with a fresh random
password and API key per host and stores them in a gitignored 0600 sidecar.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tomllib
import time
from datetime import datetime, timedelta, timezone
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import credentials  # noqa: E402


class PreseedError(RuntimeError):
    """The restored-host preseed could not be applied and verified."""


HOST_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\Z")


def _default_host_secrets(host: str) -> Path:
    # <repo>/scripts/truenas/golden-iso/artifacts/ is covered by the **/artifacts/ ignore rule.
    return HERE / "artifacts" / "hosts" / f"{host}.secrets.toml"


def _write_host_secrets(
    path: Path, host: str, password: str, api: dict[str, Any] | None
) -> None:
    lines = [
        "schema_version = 1",
        f"host = {credentials.toml_string(host)}",
        f'state = "{"rotated" if api else "pending"}"',
        "",
        "[truenas_admin]",
        'username = "truenas_admin"',
        f"password = {credentials.toml_string(password)}",
        "",
    ]
    if api:
        lines += [
            "[truenas_api]",
            'username = "truenas_admin"',
            f"api_key = {credentials.toml_string(api['api_key'])}",
            f"key_name = {credentials.toml_string(api['key_name'])}",
            f"expires_at = {credentials.toml_string(api['expires_at'])}",
            f"id = {int(api['id'])}",
            "",
        ]
    credentials.atomic_write_private(path, "\n".join(lines).encode("utf-8"))


def _check_host_secrets_target(path: Path) -> None:
    """A finished per-host sidecar is never silently replaced; a pending one may be."""
    if not path.exists():
        return
    try:
        state = tomllib.loads(path.read_text(encoding="utf-8")).get("state")
    except (OSError, tomllib.TOMLDecodeError):
        state = None
    if state != "pending":
        raise PreseedError(f"refusing to replace an existing per-host secret file: {path}")


def _read_root_public_key(path: Path) -> str:
    if not path.is_file():
        raise PreseedError(f"root SSH public key not found: {path}")
    rows = [row.strip() for row in path.read_text(encoding="utf-8").splitlines() if row.strip()]
    if len(rows) != 1 or not rows[0].startswith("ssh-ed25519 "):
        raise PreseedError("root SSH public key file must contain exactly one Ed25519 key")
    return rows[0]


def _user_id(rows: Any, username: str) -> int:
    if not isinstance(rows, list):
        raise PreseedError(f"TrueNAS user.query returned an invalid result for {username}")
    matches = [row.get("id") for row in rows if isinstance(row, dict) and row.get("username") == username]
    if len(matches) != 1 or type(matches[0]) is not int:
        raise PreseedError(f"expected one TrueNAS account named {username}")
    return matches[0]


class RemoteTrueNAS:
    def __init__(self, host: str, port: int, identity: Path, known_hosts: Path) -> None:
        if not HOST_RE.fullmatch(host):
            raise PreseedError("host must be a DNS name or IPv4 address")
        self.host = host
        self.port = port
        self.identity = identity.resolve(strict=True)
        self.known_hosts = known_hosts.resolve(strict=True)
        for label, path in (("SSH identity", self.identity), ("known_hosts", self.known_hosts)):
            if not path.is_file():
                raise PreseedError(f"{label} is not a regular file: {path}")
        if stat.S_IMODE(self.identity.stat().st_mode) & 0o077:
            raise PreseedError("root SSH identity must be mode 0600 or stricter")
        known_hosts_name = host if port == 22 else f"[{host}]:{port}"
        try:
            pinned = subprocess.run(
                ["ssh-keygen", "-F", known_hosts_name, "-f", str(self.known_hosts)],
                text=True,
                capture_output=True,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise PreseedError(f"could not inspect known_hosts: {type(exc).__name__}") from None
        if pinned.returncode != 0:
            raise PreseedError(f"known_hosts has no host-key pin for {host}")

    def command(
        self,
        remote_argv: list[str],
        *,
        input_data: str | None = None,
        timeout: int = 120,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        argv = [
            "ssh", "-T", "-i", str(self.identity), "-p", str(self.port),
            "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={self.known_hosts}",
            "-o", "ConnectTimeout=8", f"root@{self.host}", shlex.join(remote_argv),
        ]
        try:
            result = subprocess.run(
                argv, input=input_data, text=True, capture_output=True,
                check=False, timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise PreseedError(f"SSH command failed to start: {type(exc).__name__}") from None
        if check and result.returncode != 0:
            raise PreseedError(f"remote command {remote_argv[0]} failed (exit {result.returncode})")
        return result

    def call(self, method: str, *params: Any, sensitive: bool = False) -> Any:
        if sensitive:
            if not params:
                raise PreseedError(f"sensitive middleware call {method} has no payload")
            encoded = [json.dumps(item, separators=(",", ":")) for item in params[:-1]]
            result = self.command(
                ["midclt", "call", method, *encoded, "-"],
                input_data=json.dumps(params[-1], separators=(",", ":")),
                timeout=300,
            )
        else:
            encoded = [json.dumps(item, separators=(",", ":")) for item in params]
            result = self.command(["midclt", "call", method, *encoded], timeout=300)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            raise PreseedError(f"TrueNAS returned invalid JSON for {method}") from None


def _wait_job(target: RemoteTrueNAS, job_id: int) -> None:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        jobs = target.call("core.get_jobs", [["id", "=", job_id]])
        if isinstance(jobs, list) and jobs:
            state = str(jobs[0].get("state", "")).upper()
            if state == "SUCCESS":
                return
            if state in {"FAILED", "ABORTED"}:
                raise PreseedError(f"SSH service job {job_id} ended in {state}")
        time.sleep(2)
    raise PreseedError(f"SSH service job {job_id} did not finish within 180 seconds")


def apply_preseed(
    target: RemoteTrueNAS,
    *,
    expected_version: str,
    admin_password: str,
    root_public_key: str,
    host: str = "restored-host",
    api_key_expiry_days: int = 30,
) -> dict[str, Any]:
    """Rotate every golden credential on the restored host; return the new API key record."""
    try:
        credentials.validate_admin_password(admin_password)
    except credentials.CredentialError as exc:
        raise PreseedError(str(exc)) from None
    info = target.call("system.info")
    if not isinstance(info, dict) or info.get("version") != expected_version:
        observed = info.get("version") if isinstance(info, dict) else "malformed response"
        raise PreseedError(f"expected TrueNAS {expected_version}, got {observed!r}")
    pristine = target.command(["test", "!", "-e", "/root/v27-boot-pool-patch-install.json"], check=False)
    if pristine.returncode != 0:
        raise PreseedError("refusing to preseed a host with an existing v27 patch install record")

    admin_id = _user_id(target.call("user.query", [["username", "=", "truenas_admin"]]), "truenas_admin")
    target.call("user.update", admin_id, {"password": admin_password}, sensitive=True)
    api_record = _rotate_api_key(target, host, api_key_expiry_days)

    root_id = _user_id(target.call("user.query", [["username", "=", "root"]]), "root")
    target.call(
        "user.update", root_id,
        {"sshpubkey": root_public_key, "ssh_password_enabled": False},
    )
    target.call("ssh.update", {"passwordauth": False})
    services = target.call("service.query", [["service", "=", "ssh"]])
    matches: list[Any] = []
    if isinstance(services, list):
        matches = [
            row.get("id") for row in services
            if isinstance(row, dict) and row.get("service") == "ssh"
        ]
    if len(matches) != 1 or type(matches[0]) is not int:
        raise PreseedError("expected one TrueNAS SSH service record")
    target.call("service.update", matches[0], {"enable": True})
    job = target.call("service.control", "START", "ssh")
    if isinstance(job, int):
        _wait_job(target, job)

    root_rows = target.call("user.query", [["id", "=", root_id]])
    if not isinstance(root_rows, list) or len(root_rows) != 1:
        raise PreseedError("could not verify the configured root account")
    root = root_rows[0]
    root_public_key_value = root.get("sshpubkey")
    if (not isinstance(root_public_key_value, str)
            or root_public_key_value.strip() != root_public_key
            or root.get("ssh_password_enabled") is not False):
        raise PreseedError("root SSH key or password-login settings did not persist")
    ssh_config = target.call("ssh.config")
    if not isinstance(ssh_config, dict) or ssh_config.get("passwordauth") is not False:
        raise PreseedError("global SSH password authentication remains enabled")
    if target.command(["id", "-u"], timeout=30).stdout.strip() != "0":
        raise PreseedError("root SSH did not return UID 0 after applying the preseed")
    return api_record


def _rotate_api_key(target: RemoteTrueNAS, host: str, expiry_days: int) -> dict[str, Any]:
    """Delete every API key the golden disk carries and mint one fresh key for this host."""
    keys = target.call("api_key.query")
    if not isinstance(keys, list) or any(not isinstance(row, dict) for row in keys):
        raise PreseedError("api_key.query returned an unexpected response shape")
    for row in keys:
        if type(row.get("id")) is not int:
            raise PreseedError("api_key.query returned a key without an integer id")
        target.call("api_key.delete", row["id"])
    expires_at = datetime.now(timezone.utc) + timedelta(days=expiry_days)
    key_name = f"restored-{host}"[:60]
    created = target.call(
        "api_key.create",
        {
            "name": key_name,
            "username": "truenas_admin",
            "expires_at": {"$date": int(expires_at.timestamp() * 1000)},
        },
        sensitive=True,
    )
    key = created.get("key") or created.get("api_key") if isinstance(created, dict) else created
    key_id = created.get("id") if isinstance(created, dict) else None
    if not isinstance(key, str) or len(key.strip()) < 16 or type(key_id) is not int:
        raise PreseedError("api_key.create did not return a usable one-time key and id")
    remaining = target.call("api_key.query")
    if (not isinstance(remaining, list)
            or [row.get("id") for row in remaining if isinstance(row, dict)] != [key_id]):
        raise PreseedError("golden API keys remain on the restored host after rotation")
    return {
        "api_key": key.strip(),
        "key_name": key_name,
        "expires_at": expires_at.isoformat(timespec="seconds"),
        "id": key_id,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="TrueNAS host name covered by the pinned known_hosts file")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--ssh-identity", required=True, type=Path)
    parser.add_argument("--known-hosts", required=True, type=Path)
    parser.add_argument("--host-secrets", type=Path, default=None,
                        help="output: per-host mode-0600 sidecar for the NEW random admin password and "
                        "API key (default: golden-iso/artifacts/hosts/<host>.secrets.toml, gitignored)")
    parser.add_argument("--api-key-expiry-days", type=int, default=30,
                        help="expiry of the per-host API key created at restore")
    parser.add_argument("--root-public-key", required=True, type=Path)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--yes", action="store_true", help="confirm this credential mutation on the named host")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not args.yes:
            raise PreseedError("pass --yes only for the explicitly authorized restored host")
        if not 1 <= args.port <= 65535:
            raise PreseedError("SSH port must be between 1 and 65535")
        if not 1 <= args.api_key_expiry_days <= 3650:
            raise PreseedError("API key expiry must be between 1 and 3650 days")
        secrets_path = (args.host_secrets or _default_host_secrets(args.host)).resolve()
        _check_host_secrets_target(secrets_path)
        password = credentials.generate_password()
        public_key = _read_root_public_key(args.root_public_key)
        target = RemoteTrueNAS(args.host, args.port, args.ssh_identity, args.known_hosts)
        # Persist the password BEFORE any mutation so a crash never strands the host.
        _write_host_secrets(secrets_path, args.host, password, None)
        api_record = apply_preseed(
            target,
            expected_version=args.expected_version,
            admin_password=password,
            root_public_key=public_key,
            host=args.host,
            api_key_expiry_days=args.api_key_expiry_days,
        )
        _write_host_secrets(secrets_path, args.host, password, api_record)
        print(f"PASS restored-host preseed applied on {args.host}: {args.expected_version}; root SSH UID 0")
        print(f"Per-host credentials (new random password and API key): {secrets_path}")
        return 0
    except (OSError, PreseedError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
