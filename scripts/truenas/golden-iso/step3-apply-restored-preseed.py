#!/usr/bin/env python3
"""Apply protected TrueNAS credentials and root-key SSH after a disk restore."""

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
from typing import Any


class PreseedError(RuntimeError):
    """The restored-host preseed could not be applied and verified."""


HOST_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\Z")


def _read_admin_password(path: Path) -> str:
    if not path.is_file():
        raise PreseedError(f"administrator secret file not found: {path}")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise PreseedError("administrator secret file must be mode 0600 or stricter")
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PreseedError(f"cannot read administrator secret file: {type(exc).__name__}") from None
    admin = document.get("truenas_admin")
    if not isinstance(admin, dict) or admin.get("username") != "truenas_admin":
        raise PreseedError("administrator secret file must identify the truenas_admin account")
    password = admin.get("password")
    if not isinstance(password, str) or len(password) < 16 or "\n" in password or "\r" in password:
        raise PreseedError("administrator secret file has an invalid password value")
    return password


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
) -> None:
    info = target.call("system.info")
    if not isinstance(info, dict) or info.get("version") != expected_version:
        observed = info.get("version") if isinstance(info, dict) else "malformed response"
        raise PreseedError(f"expected TrueNAS {expected_version}, got {observed!r}")
    pristine = target.command(["test", "!", "-e", "/root/v27-boot-pool-patch-install.json"], check=False)
    if pristine.returncode != 0:
        raise PreseedError("refusing to preseed a host with an existing v27 patch install record")

    admin_id = _user_id(target.call("user.query", [["username", "=", "truenas_admin"]]), "truenas_admin")
    target.call("user.update", admin_id, {"password": admin_password}, sensitive=True)

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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="TrueNAS host name covered by the pinned known_hosts file")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--ssh-identity", required=True, type=Path)
    parser.add_argument("--known-hosts", required=True, type=Path)
    parser.add_argument("--admin-secrets", required=True, type=Path,
                        help="mode-0600 TOML with [truenas_admin].username and password")
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
        password = _read_admin_password(args.admin_secrets)
        public_key = _read_root_public_key(args.root_public_key)
        target = RemoteTrueNAS(args.host, args.port, args.ssh_identity, args.known_hosts)
        apply_preseed(
            target,
            expected_version=args.expected_version,
            admin_password=password,
            root_public_key=public_key,
        )
        print(f"PASS restored-host preseed applied on {args.host}: {args.expected_version}; root SSH UID 0")
        return 0
    except (OSError, PreseedError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
