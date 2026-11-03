"""Shared credential rules for the golden-image steps (TN-03).

Passwords are generated, never typed or documented. The bootstrap password of
a freshly installed TrueNAS is public and is never accepted as a final value.
Secret files are written atomically with mode 0600 inside a 0700 directory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import stat

# No quote, backslash, space, or shell/TOML-significant characters.
PASSWORD_ALPHABET = (
    "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789-_.+=@%^"
)
GENERATED_PASSWORD_LENGTH = 32
MIN_PASSWORD_LENGTH = 24
# Values known from public install recipes; refused even when long enough.
BOOTSTRAP_PASSWORDS = frozenset({"truenas"})


class CredentialError(ValueError):
    """A credential value or secret-file location violates the policy."""


def generate_password(length: int = GENERATED_PASSWORD_LENGTH) -> str:
    if length < MIN_PASSWORD_LENGTH:
        raise CredentialError(f"generated passwords need at least {MIN_PASSWORD_LENGTH} characters")
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def validate_admin_password(value: object) -> str:
    if not isinstance(value, str):
        raise CredentialError("administrator password must be a string")
    if value.strip().lower() in BOOTSTRAP_PASSWORDS:
        raise CredentialError("the public bootstrap password is never accepted as a final value")
    if len(value) < MIN_PASSWORD_LENGTH:
        raise CredentialError(
            f"administrator password must contain at least {MIN_PASSWORD_LENGTH} characters"
        )
    if any(ch in value for ch in "\r\n\x00") or value != value.strip():
        raise CredentialError("administrator password must not contain newlines or edge whitespace")
    return value


def toml_string(value: str) -> str:
    # JSON string quoting is a valid subset of TOML basic-string quoting.
    return json.dumps(value, ensure_ascii=False)


def ensure_private_dir(path: Path) -> None:
    """Create the secret directory and force mode 0700 on it."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def atomic_write_private(path: Path, content: bytes) -> None:
    """Atomically write a mode-0600 file inside a 0700 directory."""
    ensure_private_dir(path.parent)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise CredentialError("secret file did not end up with mode 0600")
