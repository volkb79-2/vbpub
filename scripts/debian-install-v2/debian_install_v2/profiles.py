"""JSONC profile parsing and validation for NoCloud bootstrap profiles.

This deliberately supports JSON objects with JavaScript-style comments only;
trailing commas and other JSON5 extensions are not accepted. Keeping the
grammar small lets the stdlib-only remote bootstrap validate profiles without
installing another Python package.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import SCHEMA_VERSION, ConfigError, load_config

PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
PROFILE_SECRET_KEYS = frozenset(
    {
        "mattermost_webhook_url",
        "telegram_bot_token",
        "telegram_chat_id",
        "controller_ssh_pubkey",
        "ssh_authorized_keys",
    }
)
BUILTIN_PROFILE_DIR = Path(__file__).resolve().parents[1] / "profiles"


def strip_jsonc_comments(text: str, *, source: str = "profile") -> str:
    """Replace // and /* */ comments outside JSON strings with whitespace."""
    result: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        if in_string:
            result.append(char)
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
            result.append(char)
            index += 1
            continue

        if char == "/" and index + 1 < len(text) and text[index + 1] == "/":
            result.extend((" ", " "))
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                result.append(" ")
                index += 1
            continue

        if char == "/" and index + 1 < len(text) and text[index + 1] == "*":
            result.extend((" ", " "))
            index += 2
            closed = False
            while index < len(text):
                if text[index] == "*" and index + 1 < len(text) and text[index + 1] == "/":
                    result.extend((" ", " "))
                    index += 2
                    closed = True
                    break
                result.append(text[index] if text[index] in "\r\n" else " ")
                index += 1
            if not closed:
                raise ConfigError(f"{source}: unterminated JSONC block comment")
            continue

        result.append(char)
        index += 1
    return "".join(result)


def parse_profile_jsonc(text: str, *, source: str = "profile") -> dict[str, Any]:
    """Parse a non-secret profile object and validate it with the config loader."""
    try:
        value = json.loads(strip_jsonc_comments(text, source=source))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{source}: invalid JSONC object at line {exc.lineno}, column {exc.colno}"
        ) from None
    if not isinstance(value, dict):
        raise ConfigError(f"{source}: profile root must be a JSON object")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ConfigError(
            f"{source}: schema_version must be {SCHEMA_VERSION}"
        )
    secrets = sorted(PROFILE_SECRET_KEYS.intersection(value))
    if secrets:
        raise ConfigError(
            f"{source}: profiles must not contain per-host secret field(s): "
            + ", ".join(secrets)
        )
    load_config(raw_json=json.dumps(value))
    return value


def validate_profile_id(profile_id: str) -> str:
    if not isinstance(profile_id, str) or not PROFILE_ID_RE.fullmatch(profile_id):
        raise ConfigError(
            "profile id must use lowercase letters, digits, and hyphens, "
            "starting with a letter or digit"
        )
    return profile_id


def load_builtin_profile(
    profile_id: str, *, profile_dir: Path = BUILTIN_PROFILE_DIR
) -> dict[str, Any]:
    profile_id = validate_profile_id(profile_id)
    path = profile_dir / f"{profile_id}.jsonc"
    if path.is_symlink() or not path.is_file():
        raise ConfigError(f"unknown released profile id: {profile_id}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read profile {profile_id}: {exc}") from None
    return parse_profile_jsonc(text, source=f"profile {profile_id}")


def list_builtin_profiles(*, profile_dir: Path = BUILTIN_PROFILE_DIR) -> tuple[str, ...]:
    if not profile_dir.is_dir():
        raise ConfigError(f"released profile directory is missing: {profile_dir}")
    profile_ids = []
    for path in sorted(profile_dir.glob("*.jsonc")):
        profile_id = validate_profile_id(path.stem)
        load_builtin_profile(profile_id, profile_dir=profile_dir)
        profile_ids.append(profile_id)
    if not profile_ids:
        raise ConfigError(f"no released profiles found under {profile_dir}")
    return tuple(profile_ids)
