"""Profiles: which host, which API key file, which TLS mode. Secrets never live in the profile file."""
from __future__ import annotations

import base64
import os
import re
import stat
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import quote, urlsplit

from .errors import ProfileError

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
TLS_MODES = ("verify", "insecure")
AUTH_MODES = ("scram", "plain")
ALLOWED_KEYS = {"url", "username", "api_key_file", "tls", "auth"}
LOOPBACK = ("127.0.0.1", "::1", "localhost")


class Secret:
    """An API key. ``repr`` and ``str`` never show it; ``reveal`` is called only by the transport."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(<redacted>)"

    __str__ = __repr__

    def _variants(self) -> list[str]:
        """The key and the common ways it turns up in an error text: its secret part, base64, percent-, hex- and JSON-encoding."""
        raw = self._value
        found = {raw, raw.split("-", 1)[-1], json.dumps(raw)[1:-1], quote(raw, safe=""), raw.encode().hex(), raw.encode().hex().upper()}
        for encoder in (base64.b64encode, base64.urlsafe_b64encode):
            enc = encoder(raw.encode()).decode()
            found.update({enc, enc.rstrip("=")})
        return sorted((v for v in found if v and (v == raw or len(v) >= 4)), key=len, reverse=True)

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
    auth: str = "scram"

    @property
    def host(self) -> str:
        return urlsplit(self.url).hostname or ""

    @property
    def insecure(self) -> bool:
        return self.tls == "insecure"

    def describe(self) -> dict[str, object]:
        """The profile as shown to a person or a script: nothing secret is in it by construction."""
        return {"name": self.name, "url": self.url, "username": self.username, "api_key_file": self.api_key_file,
                "tls": self.tls, "auth": self.auth}


def default_config_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get("TNT_CONFIG"):
        return Path(env["TNT_CONFIG"])
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "tnt" / "profiles.toml"


def _validate(name: str, raw: Mapping[str, object]) -> Profile:
    if not NAME_RE.match(name):
        raise ProfileError(f"profile name {name!r} is not [A-Za-z0-9._-], at most 64 characters")
    unknown = set(raw) - ALLOWED_KEYS
    if unknown:
        hint = " (a key never belongs in the profile file: use api_key_file)" if unknown & {"api_key", "password", "token"} else ""
        raise ProfileError(f"profile {name}: unknown key(s) {sorted(unknown)}{hint}")
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
        raise ProfileError(f"profile {name}: url must not carry credentials, a query or a fragment (the key never goes in the URL)")
    if parts.scheme != "wss" or not parts.hostname:
        raise ProfileError(f"profile {name}: url must be wss://HOST/api/current (ws:// is refused: TrueNAS revokes API keys sent in clear)")
    tls = str(raw.get("tls", "verify"))
    auth = str(raw.get("auth", "scram"))
    if tls not in TLS_MODES:
        raise ProfileError(f"profile {name}: tls must be one of {TLS_MODES}")
    if auth not in AUTH_MODES:
        raise ProfileError(f"profile {name}: auth must be one of {AUTH_MODES}")
    key_file = raw.get("api_key_file")
    if key_file is not None and (not isinstance(key_file, str) or not os.path.isabs(key_file)):
        raise ProfileError(f"profile {name}: api_key_file must be an absolute path")
    return Profile(name, url, str(raw["username"]), key_file, tls, auth)


def load_profiles(path: Path) -> tuple[dict[str, Profile], str | None]:
    """Parse the profile file. Returns (profiles by name, default profile name)."""
    try:
        data = tomllib.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        raise ProfileError(f"no profile file at {path} (create it; see README)") from None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileError(f"cannot read {path}: {exc}") from None
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
    """None when the key file is usable, else the reason. A group- or world-accessible key file is refused."""
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
    """The key comes from the profile's key file, else TNT_API_KEY_FILE, else TNT_API_KEY. Never from argv."""
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
        raise ProfileError(f"profile {profile.name}: no API key (set api_key_file, TNT_API_KEY_FILE or TNT_API_KEY)")
    if not value:
        raise ProfileError(f"profile {profile.name}: the API key is empty")
    if value.startswith("/"):
        # truenas_api_client treats an absolute path as "read the key from this file": never let key material do that
        raise ProfileError(f"profile {profile.name}: the API key starts with '/', which the client would read as a path")
    return Secret(value)
