"""Serialized, atomic edits for the paired host and secrets TOML files."""
from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import tempfile
import tomllib
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping

from .errors import ProfileError
from .profile import (AUTH_MODES, CONFIG_SCHEMA_VERSION, SECRETS_SCHEMA_VERSION, TLS_MODES,
                      ConfigPaths, Profile, Secret, SecretEntry, _validate_host, load_profiles, load_secrets)

_TABLE_RE = re.compile(r'^\[hosts\.(?:([A-Za-z0-9_-]+)|"([A-Za-z0-9._-]+)")\]\s*(?:#.*)?$')
_FIELD_RE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=")
_TOP_LEVEL_RE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=")
_HOST_FIELDS = ("url", "tls", "auth", "username", "ssh_user", "ssh_key_files", "ssh_known_hosts",
                "description", "tags")
_CREDENTIAL_FIELDS = ("api_key", "api_key_file", "password")
_SECRET_FIELDS = (*_CREDENTIAL_FIELDS, "host_url")


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def _toml_value(value: object) -> str:
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (tuple, list)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise ProfileError(f"cannot write unsupported TOML value of type {type(value).__name__}")


def _table_header(name: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", name):
        return f"[hosts.{name}]"
    return f"[hosts.{_toml_string(name)}]"


def _line_table_name(line: str) -> str | None:
    match = _TABLE_RE.fullmatch(line.strip())
    return (match.group(1) or match.group(2)) if match else None


def _table_bounds(lines: list[str], name: str) -> tuple[int, int] | None:
    start = next((index for index, line in enumerate(lines) if _line_table_name(line) == name), None)
    if start is None:
        return None
    end = start + 1
    while end < len(lines):
        stripped = lines[end].lstrip()
        if stripped.startswith("["):
            break
        end += 1
    return start, end


def _inline_comment(line: str) -> str:
    quoted = False
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quoted:
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif char == "#" and not quoted:
            return " " + line[index:].rstrip("\r\n")
    return ""


def _patch_table(text: str, name: str, values: Mapping[str, object | None], *, create: bool = False,
                 rename_to: str | None = None, remove: bool = False) -> str:
    lines = text.splitlines(keepends=True)
    bounds = _table_bounds(lines, name)
    if bounds is None:
        if remove:
            return text
        if not create:
            raise ProfileError(f"host table {name!r} is missing")
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.append(_table_header(name) + "\n")
        for key, value in values.items():
            if value is not None:
                lines.append(f"{key} = {_toml_value(value)}\n")
        return "".join(lines)
    start, end = bounds
    if remove:
        del lines[start:end]
        return "".join(lines)
    if rename_to is not None:
        if _table_bounds(lines, rename_to) is not None:
            raise ProfileError(f"host {rename_to!r} already exists")
        lines[start] = _table_header(rename_to) + "\n"
    for key, value in values.items():
        indices = [index for index in range(start + 1, end) if _FIELD_RE.match(lines[index])
                   and _FIELD_RE.match(lines[index]).group(1) == key]
        if value is None:
            for index in reversed(indices):
                del lines[index]
                end -= 1
            continue
        replacement = f"{key} = {_toml_value(value)}"
        if indices:
            index = indices[0]
            comment = _inline_comment(lines[index])
            newline = "\n" if lines[index].endswith("\n") else ""
            lines[index] = replacement + comment + newline
            for duplicate in reversed(indices[1:]):
                del lines[duplicate]
                end -= 1
        else:
            insertion = end
            if insertion > start + 1 and lines[insertion - 1].strip():
                lines.insert(insertion, "\n")
                insertion += 1
                end += 1
            lines.insert(insertion, replacement + "\n")
            end += 1
    return "".join(lines)


def _set_default(text: str, name: str | None) -> str:
    lines = text.splitlines(keepends=True)
    first_table = next((index for index, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines))
    indices = [index for index in range(first_table) if _TOP_LEVEL_RE.match(lines[index])
               and _TOP_LEVEL_RE.match(lines[index]).group(1) == "default_host"]
    if name is None:
        for index in reversed(indices):
            del lines[index]
        return "".join(lines)
    value = f"default_host = {_toml_string(name)}"
    if indices:
        index = indices[0]
        comment = _inline_comment(lines[index])
        newline = "\n" if lines[index].endswith("\n") else ""
        lines[index] = value + comment + newline
        for duplicate in reversed(indices[1:]):
            del lines[duplicate]
    else:
        lines.insert(first_table, value + "\n")
    return "".join(lines)


def _profile_values(profile: Profile) -> dict[str, object | None]:
    if profile.tls not in TLS_MODES or profile.auth not in AUTH_MODES:
        raise ProfileError("host TLS or auth mode is not supported")
    values: dict[str, object | None] = {
        "url": profile.url,
        "tls": profile.tls,
        "auth": profile.auth,
        "username": profile.username,
        "ssh_user": profile.ssh_user,
        "ssh_key_files": list(profile.ssh_key_files) if profile.ssh_key_files else None,
        "ssh_known_hosts": profile.ssh_known_hosts,
        "description": profile.description or None,
        "tags": list(profile.tags) if profile.tags else None,
    }
    return values


def _validate_profile(profile: Profile) -> None:
    values = {key: value for key, value in _profile_values(profile).items() if value is not None}
    _validate_host(profile.name, values)


def _secret_values(entry: SecretEntry | None, host_url: str | None = None) -> dict[str, object | None]:
    if entry is None:
        return {key: None for key in _SECRET_FIELDS}
    return {
        "api_key": entry.api_key.reveal() if entry.api_key else None,
        "api_key_file": entry.api_key_file,
        "password": entry.password.reveal() if entry.password else None,
        "host_url": host_url or entry.host_url,
    }


def _read_store_file(path: Path, label: str) -> str:
    if not os.path.lexists(path):
        return ""
    from .profile import _read_private_bytes
    try:
        return _read_private_bytes(path, label).decode("utf-8")
    except UnicodeDecodeError:
        raise ProfileError(f"cannot decode {label} {path}: UnicodeDecodeError") from None


@contextmanager
def _serialized(paths: ConfigPaths, *, create_parents: bool) -> Iterator[None]:
    lock_paths = sorted({path.parent / f".{path.name}.lock" for path in (paths.config, paths.secrets)}, key=str)
    descriptors: list[int] = []
    try:
        for lock_path in lock_paths:
            if create_parents:
                lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            elif not lock_path.parent.is_dir():
                continue
            nofollow = getattr(os, "O_NOFOLLOW", None)
            if nofollow is None:
                raise ProfileError("cannot safely lock host files: no-follow file opens are unavailable")
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | nofollow
            try:
                fd = os.open(lock_path, flags, 0o600)
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid():
                    raise ProfileError(f"unsafe host-file lock {lock_path}")
                os.fchmod(fd, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX)
            except ProfileError:
                if "fd" in locals():
                    os.close(fd)
                    del fd
                raise
            except OSError as exc:
                if "fd" in locals():
                    os.close(fd)
                    del fd
                raise ProfileError(f"cannot lock host files: {exc.strerror or type(exc).__name__}") from None
            descriptors.append(fd)
            del fd
        yield
    finally:
        for fd in reversed(descriptors):
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def _stage(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    staged = Path(temporary)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=True) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return staged
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        staged.unlink(missing_ok=True)
        raise


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _validate_pair(config_text: str, secrets_text: str) -> None:
    try:
        config_data = tomllib.loads(config_text)
        secrets_data = tomllib.loads(secrets_text)
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"host edit did not produce valid TOML: {type(exc).__name__}") from None
    if type(config_data.get("schema_version")) is not int or config_data.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ProfileError(f"config schema_version must be {CONFIG_SCHEMA_VERSION}")
    if type(secrets_data.get("schema_version")) is not int or secrets_data.get("schema_version") != SECRETS_SCHEMA_VERSION:
        raise ProfileError(f"secrets schema_version must be {SECRETS_SCHEMA_VERSION}")
    if set(config_data) - {"schema_version", "default_host", "hosts"}:
        raise ProfileError("config edit contains an unknown top-level key")
    if set(secrets_data) - {"schema_version", "hosts"}:
        raise ProfileError("secrets edit contains an unknown top-level key")
    hosts = config_data.get("hosts", {})
    secret_hosts = secrets_data.get("hosts", {})
    if not isinstance(hosts, dict) or not isinstance(secret_hosts, dict):
        raise ProfileError("config and secrets hosts must be TOML tables")
    for name, raw in hosts.items():
        if not isinstance(raw, dict):
            raise ProfileError(f"host {name!r} is not a table")
        _validate_host(name, raw)
    default = config_data.get("default_host")
    if default is not None and default not in hosts:
        raise ProfileError(f"default host {default!r} is not defined")
    for name, raw in secret_hosts.items():
        if name not in hosts or not isinstance(raw, dict) or set(raw) - set(_SECRET_FIELDS):
            raise ProfileError(f"secrets for host {name!r} do not match a configured host")
        credentials = set(raw) & set(_CREDENTIAL_FIELDS)
        if len(credentials) != 1:
            raise ProfileError(f"secrets for host {name!r} must contain exactly one credential source")
        key = next(iter(credentials))
        value = raw[key]
        if not isinstance(value, str) or not value:
            raise ProfileError(f"secrets for host {name!r} must contain a non-empty credential")
        host_url = raw.get("host_url")
        if host_url is not None and (not isinstance(host_url, str) or not host_url):
            raise ProfileError(f"secrets for host {name!r}: host_url must be a non-empty endpoint URL")
        if key == "api_key_file" and not os.path.isabs(value):
            raise ProfileError(f"secrets for host {name!r}: api_key_file must be absolute")
        if key == "api_key" and value.startswith("/"):
            raise ProfileError(f"secrets for host {name!r}: api_key must be key material, not a file path")
        expected = "password" if hosts[name].get("auth", "api_key") == "password" else "api_key"
        if key not in (expected, "api_key_file" if expected == "api_key" else expected):
            raise ProfileError(f"secrets for host {name!r} do not match its auth mode")


def _restore(path: Path, previous: bytes | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
        _sync_directory(path.parent)
        return
    staged = _stage(path, previous)
    os.replace(staged, path)
    _sync_directory(path.parent)


def _commit(changes: Mapping[Path, str], old_contents: Mapping[Path, bytes | None]) -> None:
    staged: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for path, content in changes.items():
            staged[path] = _stage(path, content.encode("utf-8"))
        for path, temporary in staged.items():
            os.replace(temporary, path)
            replaced.append(path)
            _sync_directory(path.parent)
    except Exception as exc:
        rollback_failed = False
        for path in reversed(replaced):
            try:
                _restore(path, old_contents.get(path))
            except Exception:
                rollback_failed = True
        if rollback_failed:
            raise ProfileError("host-file transaction failed and rollback also failed; do not connect until the pair is repaired") from None
        raise ProfileError(f"host-file transaction failed and was rolled back: {type(exc).__name__}") from None
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


class HostStore:
    """Mutate host and secrets tables under stable locks, with atomic replacement and caught-failure rollback."""

    def __init__(self, paths: ConfigPaths):
        self.paths = paths

    def _read_pair(self) -> tuple[str, str]:
        config = _read_store_file(self.paths.config, "config file") or "schema_version = 1\n"
        secrets = _read_store_file(self.paths.secrets, "secrets file") or "schema_version = 1\n"
        return config, secrets

    def _old_contents(self, paths: tuple[Path, ...]) -> dict[Path, bytes | None]:
        from .profile import _read_private_bytes
        result: dict[Path, bytes | None] = {}
        for path in paths:
            if not os.path.lexists(path):
                result[path] = None
            else:
                result[path] = _read_private_bytes(path, "TOML file")
        return result

    def add(self, profile: Profile, credential: SecretEntry, *, make_default: bool = False) -> None:
        # Reuse the loader's strict validation before serializing caller-supplied values.
        _validate_profile(profile)
        secret_values = _secret_values(credential, profile.url)
        if sum(secret_values[key] is not None for key in _CREDENTIAL_FIELDS) != 1:
            raise ProfileError("a host must have exactly one secret credential source")
        with _serialized(self.paths, create_parents=True):
            config, secrets = self._read_pair()
            try:
                hosts = tomllib.loads(config).get("hosts", {})
                if profile.name in hosts:
                    raise ProfileError(f"host {profile.name!r} already exists")
            except tomllib.TOMLDecodeError as exc:
                raise ProfileError(f"cannot edit malformed config: {type(exc).__name__}") from None
            config = _patch_table(config, profile.name, _profile_values(profile), create=True)
            secrets = _patch_table(secrets, profile.name, secret_values, create=True)
            if make_default:
                config = _set_default(config, profile.name)
            _validate_pair(config, secrets)
            old = self._old_contents((self.paths.config, self.paths.secrets))
            _commit({self.paths.config: config, self.paths.secrets: secrets}, old)

    def edit(self, name: str, profile: Profile, credential: SecretEntry | None = None) -> None:
        if profile.name != name:
            raise ProfileError("use hosts rename to change a host name")
        _validate_profile(profile)
        with _serialized(self.paths, create_parents=False):
            config, secrets = self._read_pair()
            try:
                hosts = tomllib.loads(config).get("hosts", {})
            except tomllib.TOMLDecodeError as exc:
                raise ProfileError(f"cannot edit malformed config: {type(exc).__name__}") from None
            if name not in hosts:
                raise ProfileError(f"unknown host {name!r}")
            config = _patch_table(config, name, _profile_values(profile))
            if credential is not None:
                secrets = _patch_table(secrets, name, _secret_values(credential, profile.url))
            _validate_pair(config, secrets)
            changed = {self.paths.config: config}
            if credential is not None:
                changed[self.paths.secrets] = secrets
            old = self._old_contents(tuple(changed))
            _commit(changed, old)

    def rename(self, old_name: str, new_name: str, *, profile: Profile | None = None,
               credential: SecretEntry | None = None) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", new_name):
            raise ProfileError("host name must use [A-Za-z0-9._-] and be at most 64 characters")
        with _serialized(self.paths, create_parents=False):
            config, secrets = self._read_pair()
            try:
                data = tomllib.loads(config)
                hosts = data.get("hosts", {})
            except tomllib.TOMLDecodeError as exc:
                raise ProfileError(f"cannot edit malformed config: {type(exc).__name__}") from None
            if old_name not in hosts:
                raise ProfileError(f"unknown host {old_name!r}")
            if new_name in hosts:
                raise ProfileError(f"host {new_name!r} already exists")
            if profile is not None and profile.name != old_name:
                raise ProfileError("rename host facts must still identify the original host")
            config = _patch_table(config, old_name, {}, rename_to=new_name)
            if profile is not None:
                config = _patch_table(config, new_name, _profile_values(profile))
            if data.get("default_host") == old_name:
                config = _set_default(config, new_name)
            try:
                secret_data = tomllib.loads(secrets).get("hosts", {})
            except tomllib.TOMLDecodeError as exc:
                raise ProfileError(f"cannot edit malformed secrets file: {type(exc).__name__}") from None
            if old_name in secret_data:
                secrets = _patch_table(secrets, old_name, {}, rename_to=new_name)
            if credential is not None:
                raw_profile = hosts[old_name]
                binding_profile = profile or _validate_host(old_name, raw_profile)
                secrets = _patch_table(secrets, new_name, _secret_values(credential, binding_profile.url),
                                       create=old_name not in secret_data)
            _validate_pair(config, secrets)
            old = self._old_contents((self.paths.config, self.paths.secrets))
            _commit({self.paths.config: config, self.paths.secrets: secrets}, old)

    def remove(self, name: str) -> None:
        with _serialized(self.paths, create_parents=False):
            config, secrets = self._read_pair()
            try:
                data = tomllib.loads(config)
                hosts = data.get("hosts", {})
            except tomllib.TOMLDecodeError as exc:
                raise ProfileError(f"cannot edit malformed config: {type(exc).__name__}") from None
            if name not in hosts:
                raise ProfileError(f"unknown host {name!r}")
            config = _patch_table(config, name, {}, remove=True)
            if data.get("default_host") == name:
                config = _set_default(config, None)
            try:
                secret_data = tomllib.loads(secrets).get("hosts", {})
            except tomllib.TOMLDecodeError as exc:
                raise ProfileError(f"cannot edit malformed secrets file: {type(exc).__name__}") from None
            if name in secret_data:
                secrets = _patch_table(secrets, name, {}, remove=True)
            _validate_pair(config, secrets)
            old = self._old_contents((self.paths.config, self.paths.secrets))
            _commit({self.paths.config: config, self.paths.secrets: secrets}, old)

    def set_default(self, name: str | None) -> bool:
        with _serialized(self.paths, create_parents=False):
            config, secrets = self._read_pair()
            try:
                hosts = tomllib.loads(config).get("hosts", {})
            except tomllib.TOMLDecodeError as exc:
                raise ProfileError(f"cannot edit malformed config: {type(exc).__name__}") from None
            if name is not None and name not in hosts:
                raise ProfileError(f"unknown host {name!r}")
            config = _set_default(config, name)
            _validate_pair(config, secrets)
            old = self._old_contents((self.paths.config,))
            _commit({self.paths.config: config}, old)
            return name is not None


def load_host_store(paths: ConfigPaths, *, require_secrets: bool = False
                    ) -> tuple[dict[str, Profile], str | None, dict[str, SecretEntry]]:
    """Read the paired files under the same locks used by edits and reject alias/auth mismatches."""
    with _serialized(paths, create_parents=False):
        hosts, default = load_profiles(paths.config)
        if os.path.lexists(paths.secrets):
            secrets = load_secrets(paths.secrets)
        elif require_secrets:
            raise ProfileError(f"no secrets file at {paths.secrets}")
        else:
            secrets = {}
        for name, entry in secrets.items():
            host = hosts.get(name)
            if host is None:
                raise ProfileError(f"secrets file contains host {name!r}, which is not in the paired config file")
            matches = (entry.password is not None) if host.auth == "password" else (
                entry.api_key is not None or entry.api_key_file is not None)
            if not matches:
                raise ProfileError(f"secrets credential for host {name!r} does not match auth = {host.auth!r}")
        return hosts, default, secrets
