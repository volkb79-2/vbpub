"""Connection profiles. Credentials are wrapped so their repr and every display stay redacted."""
from __future__ import annotations

import base64
import json
import os
import re
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import quote, urlsplit

from .errors import ProfileError

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
TLS_MODES = ("verify", "insecure")
AUTH_MODES = ("api_key", "password")
CONFIG_SCHEMA_VERSION = 1
ALLOWED_KEYS = {"url", "username", "api_key", "api_key_file", "password", "tls", "auth", "ssh_user", "ssh_key_files", "ssh_known_hosts"}
LOOPBACK = ("127.0.0.1", "::1", "localhost")


class Secret:
    """Sensitive text. Only transport code should call :meth:`reveal`; repr and str are always redacted."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(<redacted>)"

    __str__ = __repr__

    def _variants(self) -> list[str]:
        """The secret and common encodings that may appear in a lower-level exception."""
        raw = self._value
        found = {raw, raw.split("-", 1)[-1], json.dumps(raw)[1:-1], quote(raw, safe=""), raw.encode().hex(), raw.encode().hex().upper()}
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
    name: str
    url: str
    username: str
    api_key_file: str | None = None
    tls: str = "verify"
    auth: str = "api_key"
    api_key: Secret | None = None
    password: Secret | None = None
    ssh_user: str | None = None
    ssh_key_files: tuple[str, ...] = ()
    ssh_known_hosts: str | None = None

    @property
    def host(self) -> str:
        return urlsplit(self.url).hostname or ""

    @property
    def insecure(self) -> bool:
        return self.tls == "insecure"

    def describe(self) -> dict[str, object]:
        """A safe, scriptable profile view; credential values are masked by construction."""
        return {
            "name": self.name,
            "url": self.url,
            "username": self.username,
            "api_key_file": self.api_key_file,
            "api_key": "***" if self.api_key is not None or self.api_key_file is not None else None,
            "password": "***" if self.password is not None else None,
            "tls": self.tls,
            "auth": self.auth,
            "ssh_user": self.ssh_user,
            "ssh_key_files": list(self.ssh_key_files),
            "ssh_known_hosts": self.ssh_known_hosts,
        }


def default_config_path(env: Mapping[str, str] | None = None, *, cwd: Path | None = None) -> Path:
    """Resolve config in order: TNT_CONFIG, ./tnt.toml, then XDG_CONFIG_HOME/tnt/tnt.toml."""
    env = os.environ if env is None else env
    if env.get("TNT_CONFIG"):
        return Path(env["TNT_CONFIG"])
    local = (Path.cwd() if cwd is None else Path(cwd)) / "tnt.toml"
    if local.exists():
        return local
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "tnt" / "tnt.toml"


def config_file_problem(path: str | os.PathLike[str]) -> str | None:
    """Require the credential-bearing profile itself to be a regular 0600 file owned by this user."""
    try:
        st = os.stat(path)
    except OSError as exc:
        return f"cannot stat config file {path}: {exc.strerror}"
    if not stat.S_ISREG(st.st_mode):
        return f"{path} is not a regular config file"
    if st.st_uid != os.geteuid():
        return f"{path} is owned by uid {st.st_uid}, not by you (uid {os.geteuid()})"
    mode = stat.S_IMODE(st.st_mode)
    if mode != 0o600:
        return f"{path} must have mode 0600 (found {mode:04o}); run chmod 600 {path}"
    return None


def _secret_value(raw: Mapping[str, object], key: str, name: str) -> Secret | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ProfileError(f"profile {name}: {key} must be a non-empty string")
    return Secret(value)


def _validate(name: str, raw: Mapping[str, object]) -> Profile:
    if not NAME_RE.fullmatch(name):
        raise ProfileError(f"profile name {name!r} is not [A-Za-z0-9._-], at most 64 characters")
    unknown = set(raw) - ALLOWED_KEYS
    if unknown:
        raise ProfileError(f"profile {name}: unknown key(s) {sorted(unknown)}")
    for required in ("url", "username"):
        if not isinstance(raw.get(required), str) or not raw[required]:
            raise ProfileError(f"profile {name}: {required} is required")
    url = str(raw["url"])
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises ValueError on a malformed port
    except ValueError:
        raise ProfileError(f"profile {name}: url is not a valid URL") from None
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ProfileError(f"profile {name}: url must not carry credentials, a query or a fragment")
    if parts.scheme != "wss" or not parts.hostname:
        raise ProfileError(f"profile {name}: url must be wss://HOST/api/current (ws:// is refused: credentials require TLS)")
    tls = raw.get("tls", "verify")
    auth = raw.get("auth", "api_key")
    if tls not in TLS_MODES:
        raise ProfileError(f"profile {name}: tls must be one of {TLS_MODES}")
    if auth not in AUTH_MODES:
        raise ProfileError(f"profile {name}: auth must be one of {AUTH_MODES}")
    key_file = raw.get("api_key_file")
    if key_file is not None and (not isinstance(key_file, str) or not os.path.isabs(key_file)):
        raise ProfileError(f"profile {name}: api_key_file must be an absolute path")
    api_key = _secret_value(raw, "api_key", name)
    password = _secret_value(raw, "password", name)
    if auth == "api_key":
        if password is not None:
            raise ProfileError(f"profile {name}: auth = 'api_key' does not accept password")
        if api_key is not None and key_file is not None:
            raise ProfileError(f"profile {name}: set either api_key or api_key_file, not both")
        if api_key is not None and api_key.reveal().startswith("/"):
            raise ProfileError(f"profile {name}: api_key must be key material, not a file path")
    else:
        if api_key is not None or key_file is not None:
            raise ProfileError(f"profile {name}: auth = 'password' does not accept api_key or api_key_file")
        if password is None:
            raise ProfileError(f"profile {name}: password is required when auth = 'password'")

    ssh_user = raw.get("ssh_user")
    if ssh_user is not None and (not isinstance(ssh_user, str) or not ssh_user):
        raise ProfileError(f"profile {name}: ssh_user must be a non-empty string")
    ssh_keys = raw.get("ssh_key_files", [])
    if not isinstance(ssh_keys, list) or any(not isinstance(v, str) or not v for v in ssh_keys):
        raise ProfileError(f"profile {name}: ssh_key_files must be an array of paths")
    known_hosts = raw.get("ssh_known_hosts")
    if known_hosts is not None and (not isinstance(known_hosts, str) or not known_hosts):
        raise ProfileError(f"profile {name}: ssh_known_hosts must be a non-empty path")
    return Profile(name, url, str(raw["username"]), key_file, str(tls), str(auth), api_key, password,
                   ssh_user, tuple(ssh_keys), known_hosts)


def load_profiles(path: Path) -> tuple[dict[str, Profile], str | None]:
    """Parse a mode-0600 profile file and return (profiles by name, default profile name)."""
    problem = config_file_problem(path)
    if problem:
        if not Path(path).exists():
            raise ProfileError(f"no profile file at {path} (create it; see README)") from None
        raise ProfileError(f"unsafe profile file: {problem}")
    try:
        data = tomllib.loads(Path(path).read_text("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ProfileError(f"cannot read {path}: {type(exc).__name__}") from None
    unknown = set(data) - {"schema_version", "default", "profiles"}
    if unknown:
        raise ProfileError(f"{path}: unknown top-level key(s) {sorted(unknown)}")
    schema_version = data.get("schema_version")
    if type(schema_version) is not int or schema_version != CONFIG_SCHEMA_VERSION:
        raise ProfileError(f"{path}: schema_version must be {CONFIG_SCHEMA_VERSION}")
    table = data.get("profiles")
    if not isinstance(table, dict) or not table:
        raise ProfileError(f"{path}: no [profiles.NAME] tables")
    profiles = {name: _validate(name, raw if isinstance(raw, dict) else {}) for name, raw in table.items()}
    default = data.get("default")
    if default is not None and default not in profiles:
        raise ProfileError(f"{path}: default {default!r} is not a defined profile")
    return profiles, default


def pick(profiles: Mapping[str, Profile], default: str | None, name: str | None) -> Profile:
    chosen = name or default or (next(iter(profiles)) if len(profiles) == 1 else None)
    if chosen is None:
        raise ProfileError(f"several profiles ({', '.join(sorted(profiles))}) and no default: pass --profile NAME")
    if chosen not in profiles:
        raise ProfileError(f"unknown profile {chosen!r} (have: {', '.join(sorted(profiles))})")
    return profiles[chosen]


def key_file_problem(path: str) -> str | None:
    """None when the API key file is usable, else the reason."""
    try:
        st = os.stat(path)
    except OSError as exc:
        return f"cannot stat {path}: {exc.strerror}"
    if not stat.S_ISREG(st.st_mode):
        return f"{path} is not a regular file"
    if st.st_uid != os.geteuid():
        return f"{path} is owned by uid {st.st_uid}, not by you (uid {os.geteuid()})"
    if st.st_mode & 0o077:
        return f"{path} is accessible to group/other (mode {stat.S_IMODE(st.st_mode):04o}); chmod 600 it"
    return None


def read_secret(profile: Profile, env: Mapping[str, str] | None = None) -> Secret:
    """Resolve an API key from inline config, a private file, or the existing environment fallback."""
    if profile.auth != "api_key":
        raise ProfileError(f"profile {profile.name}: API-key credentials requested for auth = {profile.auth!r}")
    if profile.api_key is not None:
        return profile.api_key
    env = os.environ if env is None else env
    path = profile.api_key_file or env.get("TNT_API_KEY_FILE")
    if path:
        problem = key_file_problem(path)
        if problem:
            raise ProfileError(problem)
        try:
            value = Path(path).read_text("utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise ProfileError(f"cannot read the key file {path}: {type(exc).__name__}") from None
    elif env.get("TNT_API_KEY"):
        value = env["TNT_API_KEY"].strip()
    else:
        raise ProfileError(f"profile {profile.name}: no API key (set api_key, api_key_file, TNT_API_KEY_FILE or TNT_API_KEY)")
    if not value:
        raise ProfileError(f"profile {profile.name}: the API key is empty")
    if value.startswith("/"):
        raise ProfileError(f"profile {profile.name}: the API key starts with '/', which the client would read as a path")
    return Secret(value)


def read_password(profile: Profile) -> Secret:
    if profile.auth != "password" or profile.password is None:
        raise ProfileError(f"profile {profile.name}: password credentials are not configured")
    return profile.password


def read_credential(profile: Profile, env: Mapping[str, str] | None = None) -> Secret:
    return read_password(profile) if profile.auth == "password" else read_secret(profile, env)
