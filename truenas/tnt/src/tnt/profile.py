"""Host configuration and private credential loading.

``tnt.toml`` contains connection facts only. Credentials are looked up by the
same host name in its paired ``tnt.secrets.toml`` file. Sensitive files are
opened with ``O_NOFOLLOW`` and validated with ``fstat`` on the descriptor that
is actually read, so a path-based permission check cannot race the read.
"""
from __future__ import annotations

import base64
import errno
import json
import os
import re
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import quote, urlsplit

from .errors import ProfileError

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
TLS_MODES = ("verify", "insecure")
AUTH_MODES = ("api_key", "password")
CONFIG_SCHEMA_VERSION = 1
SECRETS_SCHEMA_VERSION = 1
HOST_KEYS = {"url", "tls", "auth", "username", "ssh_user", "ssh_key_files", "ssh_known_hosts", "description", "tags"}
CONFIG_SECRET_KEYS = {"api_key", "api_key_file", "password", "credential", "credentials", "secret", "token"}
SECRET_KEYS = {"api_key", "api_key_file", "password"}
LOOPBACK = ("127.0.0.1", "::1", "localhost")


class Secret:
    """Sensitive text. Only transport code should call :meth:`reveal`; repr and str are redacted."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(<redacted>)"

    __str__ = __repr__

    def _variants(self) -> list[str]:
        """The secret and common encodings that may leak into a lower-level exception."""
        raw = self._value
        found = {raw, raw.split("-", 1)[-1], json.dumps(raw)[1:-1], quote(raw, safe=""), raw.encode().hex(),
                 raw.encode().hex().upper()}
        for encoder in (base64.b64encode, base64.urlsafe_b64encode):
            encoded = encoder(raw.encode()).decode()
            found.update({encoded, encoded.rstrip("=")})
        return sorted((value for value in found if value and (value == raw or len(value) >= 4)), key=len, reverse=True)

    def scrub(self, text: str) -> str:
        for variant in self._variants():
            text = text.replace(variant, "<redacted>")
        return text


@dataclass(frozen=True)
class Profile:
    """Non-secret facts for one TrueNAS host; no credential value or key path is stored here."""

    name: str
    url: str
    username: str
    tls: str = "verify"
    auth: str = "api_key"
    ssh_user: str | None = None
    ssh_key_files: tuple[str, ...] = field(default=(), repr=False)
    ssh_known_hosts: str | None = field(default=None, repr=False)
    description: str = ""
    tags: tuple[str, ...] = ()

    @property
    def host(self) -> str:
        return urlsplit(self.url).hostname or ""

    @property
    def insecure(self) -> bool:
        return self.tls == "insecure"

    def describe(self) -> dict[str, object]:
        """Safe host display. Local SSH paths are never emitted by list/show/JSON output."""
        return {
            "name": self.name,
            "url": self.url,
            "username": self.username,
            "tls": self.tls,
            "auth": self.auth,
            "ssh_user": self.ssh_user,
            "ssh_key_files_configured": bool(self.ssh_key_files),
            "ssh_known_hosts_configured": self.ssh_known_hosts is not None,
            "description": self.description,
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class SecretEntry:
    """Credential material for one host. ``Secret`` keeps values out of reprs."""

    api_key: Secret | None = None
    api_key_file: str | None = None
    password: Secret | None = None


@dataclass(frozen=True)
class ConfigPaths:
    config: Path
    secrets: Path


def _path(value: str | os.PathLike[str]) -> Path:
    return Path(value).expanduser()


def default_config_path(env: Mapping[str, str] | None = None, *, cwd: Path | None = None) -> Path:
    """Resolve config: explicit environment path, existing working-directory file, then XDG config."""
    env = os.environ if env is None else env
    if env.get("TNT_CONFIG"):
        return _path(env["TNT_CONFIG"])
    local = (Path.cwd() if cwd is None else Path(cwd)) / "tnt.toml"
    if os.path.lexists(local):
        return local
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return _path(base) / "tnt" / "tnt.toml"


def default_secrets_path(env: Mapping[str, str] | None = None, *, cwd: Path | None = None) -> Path:
    """Resolve the conventional secrets location when used directly."""
    env = os.environ if env is None else env
    if env.get("TNT_SECRETS"):
        return _path(env["TNT_SECRETS"])
    local = (Path.cwd() if cwd is None else Path(cwd)) / "tnt.secrets.toml"
    if os.path.lexists(local):
        return local
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return _path(base) / "tnt" / "tnt.secrets.toml"


def resolve_paths(config: str | os.PathLike[str] | None = None,
                  secrets: str | os.PathLike[str] | None = None, *,
                  env: Mapping[str, str] | None = None, cwd: Path | None = None) -> ConfigPaths:
    """Resolve config and secrets independently with the same search precedence.

    Each explicit CLI path overrides its environment variable. Without either,
    the existing current-directory file wins, followed by the XDG config path.
    Pairing is by configured host name, not by requiring the files to be siblings.
    """
    env = os.environ if env is None else env
    config_path = _path(config) if config is not None else default_config_path(env, cwd=cwd)
    secrets_path = _path(secrets) if secrets is not None else default_secrets_path(env, cwd=cwd)
    return ConfigPaths(config_path, secrets_path)


def _open_private(path: str | os.PathLike[str], label: str) -> int:
    """Open a private regular file without following a symlink and validate the opened descriptor."""
    path = _path(path)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ProfileError(f"cannot safely open {label}: this platform does not provide no-follow file opens")
    flags = os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ProfileError(f"{label} must not be a symlink: {path}") from None
        if exc.errno == errno.ENOENT:
            raise ProfileError(f"no {label} at {path}") from None
        raise ProfileError(f"cannot open {label} {path}: {exc.strerror or type(exc).__name__}") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ProfileError(f"{label} {path} is not a regular file")
        if st.st_uid != os.geteuid():
            raise ProfileError(f"{label} {path} is owned by uid {st.st_uid}, not by you (uid {os.geteuid()})")
        mode = stat.S_IMODE(st.st_mode)
        if mode != 0o600:
            raise ProfileError(f"{label} {path} must have mode 0600 (found {mode:04o}); run chmod 600 {path}")
        return fd
    except Exception:
        os.close(fd)
        raise


def _read_private_bytes(path: str | os.PathLike[str], label: str) -> bytes:
    fd = _open_private(path, label)
    try:
        chunks: list[bytes] = []
        while True:
            block = os.read(fd, 65536)
            if not block:
                break
            chunks.append(block)
        return b"".join(chunks)
    except OSError as exc:
        raise ProfileError(f"cannot read {label} {path}: {type(exc).__name__}") from None
    finally:
        os.close(fd)


def _decode_toml(path: Path, label: str) -> dict[str, object]:
    try:
        text = _read_private_bytes(path, label).decode("utf-8")
        data = tomllib.loads(text)
    except ProfileError:
        raise
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        # Never echo parser text: it may include source lines with credential material.
        raise ProfileError(f"cannot read {label} {path}: {type(exc).__name__}") from None
    if not isinstance(data, dict):
        raise ProfileError(f"{label} {path} must contain a TOML table")
    return data


def _contains_secret_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any((isinstance(key, str) and key.casefold() in CONFIG_SECRET_KEYS) or _contains_secret_key(child)
                   for key, child in value.items())
    if isinstance(value, list):
        return any(_contains_secret_key(child) for child in value)
    return False


def _validate_host(name: str, raw: Mapping[str, object]) -> Profile:
    if not NAME_RE.fullmatch(name):
        raise ProfileError(f"host name {name!r} must use [A-Za-z0-9._-] and be at most 64 characters")
    if _contains_secret_key(raw):
        raise ProfileError(f"host {name}: credentials belong in tnt.secrets.toml, not tnt.toml")
    unknown = set(raw) - HOST_KEYS
    if unknown:
        raise ProfileError(f"host {name}: unknown key(s) {sorted(unknown)}")
    url_value, username = raw.get("url"), raw.get("username")
    if not isinstance(url_value, str) or not url_value:
        raise ProfileError(f"host {name}: url is required")
    if not isinstance(username, str) or not username:
        raise ProfileError(f"host {name}: username is required by the pinned TrueNAS client")
    try:
        parts = urlsplit(url_value)
        parts.port  # raises ValueError for malformed ports
    except ValueError:
        raise ProfileError(f"host {name}: url is not a valid URL") from None
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ProfileError(f"host {name}: url must not carry credentials, a query or a fragment")
    if parts.scheme != "wss" or not parts.hostname:
        raise ProfileError(f"host {name}: url must be wss://HOST/api/current (ws:// is refused: credentials require TLS)")
    tls, auth = raw.get("tls", "verify"), raw.get("auth", "api_key")
    if not isinstance(tls, str) or tls not in TLS_MODES:
        raise ProfileError(f"host {name}: tls must be one of {TLS_MODES}")
    if not isinstance(auth, str) or auth not in AUTH_MODES:
        raise ProfileError(f"host {name}: auth must be one of {AUTH_MODES}")
    ssh_user = raw.get("ssh_user")
    if ssh_user is not None and (not isinstance(ssh_user, str) or not ssh_user):
        raise ProfileError(f"host {name}: ssh_user must be a non-empty string")
    ssh_keys = raw.get("ssh_key_files", [])
    if not isinstance(ssh_keys, list) or any(not isinstance(value, str) or not value for value in ssh_keys):
        raise ProfileError(f"host {name}: ssh_key_files must be an array of paths")
    known_hosts = raw.get("ssh_known_hosts")
    if known_hosts is not None and (not isinstance(known_hosts, str) or not known_hosts):
        raise ProfileError(f"host {name}: ssh_known_hosts must be a non-empty path")
    description = raw.get("description", "")
    if not isinstance(description, str):
        raise ProfileError(f"host {name}: description must be a string")
    tags = raw.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(value, str) or not value for value in tags):
        raise ProfileError(f"host {name}: tags must be an array of non-empty strings")
    return Profile(name=name, url=url_value, username=username, tls=tls, auth=auth, ssh_user=ssh_user,
                   ssh_key_files=tuple(ssh_keys), ssh_known_hosts=known_hosts, description=description,
                   tags=tuple(tags))


def load_profiles(path: str | os.PathLike[str]) -> tuple[dict[str, Profile], str | None]:
    """Load non-secret host records and the optional default host from a mode-0600 file."""
    path = _path(path)
    data = _decode_toml(path, "config file")
    if _contains_secret_key(data):
        raise ProfileError(f"{path}: credentials belong in tnt.secrets.toml, not tnt.toml")
    unknown = set(data) - {"schema_version", "default_host", "hosts"}
    if unknown:
        raise ProfileError(f"{path}: unknown top-level key(s) {sorted(unknown)}")
    schema = data.get("schema_version")
    if type(schema) is not int or schema != CONFIG_SCHEMA_VERSION:
        raise ProfileError(f"{path}: schema_version must be {CONFIG_SCHEMA_VERSION}")
    table = data.get("hosts", {})
    if not isinstance(table, dict):
        raise ProfileError(f"{path}: [hosts.NAME] must be a table")
    profiles = {name: _validate_host(name, raw if isinstance(raw, dict) else {}) for name, raw in table.items()}
    default = data.get("default_host")
    if default is not None and (not isinstance(default, str) or default not in profiles):
        raise ProfileError(f"{path}: default_host {default!r} is not a defined host")
    return profiles, default


def load_secrets(path: str | os.PathLike[str]) -> dict[str, SecretEntry]:
    """Load credentials keyed by host name from an exact-0600, owned, non-symlink file."""
    path = _path(path)
    data = _decode_toml(path, "secrets file")
    unknown = set(data) - {"schema_version", "hosts"}
    if unknown:
        raise ProfileError(f"{path}: unknown secrets top-level key(s) {sorted(unknown)}")
    schema = data.get("schema_version")
    if type(schema) is not int or schema != SECRETS_SCHEMA_VERSION:
        raise ProfileError(f"{path}: schema_version must be {SECRETS_SCHEMA_VERSION}")
    table = data.get("hosts", {})
    if not isinstance(table, dict):
        raise ProfileError(f"{path}: [hosts.NAME] tables are required")
    result: dict[str, SecretEntry] = {}
    for name, raw in table.items():
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise ProfileError("secrets host names must use [A-Za-z0-9._-] and be at most 64 characters")
        if not isinstance(raw, dict):
            raise ProfileError(f"secrets for host {name}: expected a TOML table")
        unknown_keys = set(raw) - SECRET_KEYS
        if unknown_keys:
            raise ProfileError(f"secrets for host {name}: unknown key(s) {sorted(unknown_keys)}")
        values = {key: raw.get(key) for key in SECRET_KEYS if key in raw}
        if len(values) != 1:
            raise ProfileError(f"secrets for host {name}: specify exactly one credential source")
        api_key = raw.get("api_key")
        api_key_file = raw.get("api_key_file")
        password = raw.get("password")
        for key, value in (("api_key", api_key), ("password", password)):
            if key in raw and (not isinstance(value, str) or not value):
                raise ProfileError(f"secrets for host {name}: {key} must be a non-empty string")
        if api_key is not None and api_key.startswith("/"):
            raise ProfileError(f"secrets for host {name}: api_key must be key material, not a file path")
        if api_key_file is not None and (not isinstance(api_key_file, str) or not os.path.isabs(api_key_file)):
            raise ProfileError(f"secrets for host {name}: api_key_file must be an absolute path")
        result[name] = SecretEntry(Secret(api_key) if isinstance(api_key, str) else None,
                                   api_key_file if isinstance(api_key_file, str) else None,
                                   Secret(password) if isinstance(password, str) else None)
    return result


def pick(profiles: Mapping[str, Profile], default: str | None, name: str | None) -> Profile:
    chosen = name or default or (next(iter(profiles)) if len(profiles) == 1 else None)
    if chosen is None:
        names = ", ".join(sorted(profiles))
        raise ProfileError(f"several hosts ({names}) and no default: pass --host NAME")
    if chosen not in profiles:
        raise ProfileError(f"unknown host {chosen!r} (have: {', '.join(sorted(profiles))})")
    return profiles[chosen]


def _read_key_file(path: str) -> Secret:
    try:
        value = _read_private_bytes(path, "API key file").decode("utf-8").strip()
    except UnicodeDecodeError:
        raise ProfileError(f"cannot read API key file {path}: UnicodeDecodeError") from None
    if not value:
        raise ProfileError("the API key file is empty")
    if value.startswith("/"):
        raise ProfileError("the API key file starts with '/', which is not API key material")
    return Secret(value)


def key_file_problem(path: str | os.PathLike[str]) -> str | None:
    """Return None only for a private regular 0600 key file owned by this user."""
    try:
        fd = _open_private(path, "API key file")
    except ProfileError as exc:
        return str(exc)
    os.close(fd)
    return None


def read_secret(profile: Profile, entries: Mapping[str, SecretEntry]) -> Secret:
    """Read the API key credential for this host from the already paired secrets table."""
    if profile.auth != "api_key":
        raise ProfileError(f"host {profile.name}: API-key credentials requested for auth = {profile.auth!r}")
    entry = entries.get(profile.name)
    if entry is None:
        raise ProfileError(f"no credentials for host {profile.name!r} in the paired tnt.secrets.toml")
    if entry.password is not None:
        raise ProfileError(f"host {profile.name}: secrets credential does not match auth = {profile.auth!r}")
    if entry.api_key is not None:
        if entry.api_key.reveal().startswith("/"):
            raise ProfileError("the API key starts with '/', which is not API key material")
        return entry.api_key
    if entry.api_key_file is not None:
        return _read_key_file(entry.api_key_file)
    raise ProfileError(f"no API key configured for host {profile.name!r}")


def read_password(profile: Profile, entries: Mapping[str, SecretEntry]) -> Secret:
    if profile.auth != "password":
        raise ProfileError(f"host {profile.name}: password credentials requested for auth = {profile.auth!r}")
    entry = entries.get(profile.name)
    if entry is None:
        raise ProfileError(f"no credentials for host {profile.name!r} in the paired tnt.secrets.toml")
    if entry.api_key is not None or entry.api_key_file is not None:
        raise ProfileError(f"host {profile.name}: secrets credential does not match auth = {profile.auth!r}")
    if entry.password is None:
        raise ProfileError(f"no password configured for host {profile.name!r}")
    return entry.password


def read_credential(profile: Profile, entries: Mapping[str, SecretEntry]) -> Secret:
    return read_password(profile, entries) if profile.auth == "password" else read_secret(profile, entries)
