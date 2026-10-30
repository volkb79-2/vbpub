"""Hardened interactive SSH hand-off for a configured TrueNAS host.

This module deliberately has no remote-command interface. It starts OpenSSH with
an argv list, an empty config file, configured identity files, and one explicit
known-hosts file.
"""
from __future__ import annotations

import ipaddress
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Callable

from .errors import TntError
from .profile import Profile

_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_SSH_USER = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
_PIN_FILE_PATH = re.compile(r"^/[A-Za-z0-9_./-]+$")


class SshHandoffError(TntError):
    """An SSH hand-off refusal or local SSH startup failure."""


def _refuse(message: str) -> SshHandoffError:
    return SshHandoffError(message)


def _host_argument(profile: Profile) -> str:
    try:
        host = profile.host
    except (TypeError, ValueError):
        raise _refuse("the configured host URL does not contain a safe SSH hostname") from None
    if not isinstance(host, str) or not host or any(ord(char) < 33 or ord(char) == 127 for char in host):
        raise _refuse("the configured host URL does not contain a safe SSH hostname")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError:
            raise _refuse("the configured host URL does not contain a safe SSH hostname") from None
        checked = ascii_host[:-1] if ascii_host.endswith(".") else ascii_host
        labels = checked.split(".")
        if (not checked or len(ascii_host) > 254 or any(not _HOST_LABEL.fullmatch(label) for label in labels)):
            raise _refuse("the configured host URL does not contain a safe SSH hostname")
        host = ascii_host
    return host


def _configured_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise _refuse(f"configure a valid {label} path")
    try:
        path = Path(value).expanduser()
    except (OSError, RuntimeError):
        raise _refuse(f"configure a valid {label} path") from None
    if not path.is_absolute():
        raise _refuse(f"configure an absolute {label} path")
    return os.path.abspath(os.fspath(path))


def _check_file(path: str, *, label: str, private_key: bool) -> None:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise _refuse(f"the configured {label} cannot be safely opened on this platform")
    try:
        fd = os.open(path, os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0))
    except (OSError, ValueError):
        raise _refuse(f"the configured {label} is missing or unsafe") from None
    try:
        facts = os.fstat(fd)
    finally:
        os.close(fd)
    mode = stat.S_IMODE(facts.st_mode)
    if not stat.S_ISREG(facts.st_mode) or facts.st_uid != os.geteuid():
        raise _refuse(f"the configured {label} must be a regular file owned by the current user")
    if private_key:
        if mode & 0o077 or not mode & stat.S_IRUSR:
            raise _refuse(f"the configured {label} must be readable by its owner only (mode 0600 or stricter)")
    elif mode & (stat.S_IWGRP | stat.S_IWOTH) or not mode & stat.S_IRUSR or facts.st_size == 0:
        raise _refuse(f"the configured {label} must be a non-empty, owner-readable file that others cannot modify")


def _known_hosts_path(profile: Profile) -> str:
    path = _configured_path(profile.ssh_known_hosts, "known-hosts file")
    # OpenSSH parses -o values as config syntax. Keep this one value unambiguous:
    # the common absolute path alphabet is sufficient for a known_hosts file.
    if not _PIN_FILE_PATH.fullmatch(path) or ".." in Path(path).parts:
        raise _refuse("the configured known-hosts path contains unsupported SSH option characters")
    _check_file(path, label="known-hosts file", private_key=False)
    return path


def _identity_paths(profile: Profile) -> tuple[str, ...]:
    if not profile.ssh_key_files:
        raise _refuse("configure at least one SSH identity file for this host")
    paths = tuple(_configured_path(value, "SSH identity file") for value in profile.ssh_key_files)
    for path in paths:
        _check_file(path, label="SSH identity file", private_key=True)
    return paths


def build_ssh_argv(profile: Profile, *, executable: str = "ssh") -> list[str]:
    """Build a safe interactive-only OpenSSH argv from a configured host."""
    if not isinstance(profile.ssh_user, str) or not _SSH_USER.fullmatch(profile.ssh_user):
        raise _refuse("configure an SSH user containing only letters, numbers, underscore, dot, or hyphen")
    host = _host_argument(profile)
    known_hosts = _known_hosts_path(profile)
    identities = _identity_paths(profile)
    if not isinstance(executable, str) or not executable or "\x00" in executable:
        raise _refuse("the local OpenSSH executable is unavailable")

    argv = [
        executable,
        "-F", "/dev/null",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={known_hosts}",
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "VerifyHostKeyDNS=no",
        "-o", "UpdateHostKeys=no",
        "-o", "IdentitiesOnly=yes",
        "-o", "IdentityAgent=none",
        "-o", "IdentityFile=none",
        "-o", "PreferredAuthentications=publickey",
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", "HostbasedAuthentication=no",
        "-tt",
        "-l", profile.ssh_user,
    ]
    for identity in identities:
        argv.extend(("-i", identity))
    # OpenSSH sees a destination only. There is intentionally no remote command
    # argument or path from which one could be constructed.
    argv.extend(("--", host))
    return argv


def run_interactive_ssh(profile: Profile, *, executable: str = "ssh",
                        runner: Callable[..., object] | None = None) -> int:
    """Run a terminal-attached SSH process without capturing its session stream."""
    argv = build_ssh_argv(profile, executable=executable)
    execute = runner or subprocess.run
    try:
        completed = execute(argv, check=False, shell=False)
    except (OSError, ValueError):
        raise _refuse("could not start the local OpenSSH client") from None
    code = getattr(completed, "returncode", None)
    if not isinstance(code, int):
        raise _refuse("the local OpenSSH client returned no process status")
    return code if code >= 0 else 128 - code
