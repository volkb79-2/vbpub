"""Pure request builders for the Augeaswrap ``Pam_env`` overlay lens."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_NAME_RE = re.compile(r"[A-Za-z0-9_]+\Z")
_ENVIRONMENT_FIELDS = ("kind", "name", "value")
_CONF_FIELDS = (
    ("kind", "name"),
    ("kind", "name", "default"),
    ("kind", "name", "override"),
    ("kind", "name", "default", "override"),
)


def _name(value: object) -> str:
    if not isinstance(value, str) or _NAME_RE.fullmatch(value) is None:
        raise ValueError("pam_env name must contain only letters, digits, and underscore")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"pam_env {label} must be a string")
    if any(char in value for char in ("\x00", "\n", "\r")):
        raise ValueError(f"pam_env {label} must not contain NUL, newline, or carriage return")
    if any(char.isspace() and char not in {" ", "\t"} for char in value):
        raise ValueError(f"pam_env {label} contains unsupported whitespace")
    return value


def _pam_expression(value: object, label: str) -> str:
    """Validate one raw ``pam_env.conf`` value expression without rewriting it."""

    text = _text(value, label)
    index = 0
    while index < len(text):
        if text[index] != "\\":
            index += 1
            continue
        following = text[index + 1] if index + 1 < len(text) else None
        if following not in {"$", "@", "\\"}:
            raise ValueError(
                f"pam_env {label} contains an unsupported backslash escape; "
                "pam_env.conf expressions support only \\$, \\@, and \\\\"
            )
        index += 2
    return text


def _environment_literal(value: object) -> str:
    text = _text(value, "value")
    if "#" in text:
        raise ValueError("pam_env environment value must not contain #")
    if '"' in text and "'" in text:
        raise ValueError("pam_env environment value cannot contain both quote characters")
    if '"' in text:
        return "'" + text + "'"
    if "'" in text or not text or any(char in text for char in {" ", "\t", "="}):
        return '"' + text + '"'
    return text


def _conf_literal(value: object, label: str) -> str:
    text = _pam_expression(value, label)
    if '"' in text:
        raise ValueError(f"pam_env {label} cannot contain a double quote")
    if not text or "=" in text or any(char in text for char in {" ", "\t"}):
        return '"' + text + '"'
    return text


def environment_item(name: str, value: str) -> dict[str, str]:
    """Build one semantic environment record in overlay-lens field order."""

    return {
        "kind": "environment",
        "name": _name(name),
        "value": _text(value, "value"),
    }


def pam_env_item(
    name: str,
    *,
    default: str | None = None,
    override: str | None = None,
) -> dict[str, str]:
    """Build one semantic ``pam_env.conf`` record in overlay-lens field order."""

    normalized = {"kind": "conf", "name": _name(name)}
    if default is not None:
        normalized["default"] = _pam_expression(default, "default")
    if override is not None:
        normalized["override"] = _pam_expression(override, "override")
    return normalized


def _environment_record(item: object) -> dict[str, str]:
    if not isinstance(item, Mapping):
        raise ValueError("pam_env environment items must be mappings from environment_item")
    if tuple(item) != _ENVIRONMENT_FIELDS:
        raise ValueError("pam_env environment item fields must be exactly (kind, name, value)")
    if item["kind"] != "environment":
        raise ValueError("pam_env environment item kind must be 'environment'")
    return {
        "kind": "environment",
        "name": _name(item["name"]),
        "value": _environment_literal(item["value"]),
    }


def _conf_record(item: object) -> dict[str, str]:
    if not isinstance(item, Mapping):
        raise ValueError("pam_env conf items must be mappings from pam_env_item")
    if tuple(item) not in _CONF_FIELDS:
        raise ValueError(
            "pam_env conf item fields must be (kind, name[, default][, override]) "
            "in lens order"
        )
    if item["kind"] != "conf":
        raise ValueError("pam_env conf item kind must be 'conf'")
    normalized = {"kind": "conf", "name": _name(item["name"])}
    if "default" in item:
        normalized["default"] = _conf_literal(item["default"], "default")
    if "override" in item:
        normalized["override"] = _conf_literal(item["override"], "override")
    return normalized


def _records_op(
    items: Sequence[Mapping[str, Any]],
    *,
    kind: str,
    normalize,
) -> dict[str, Any]:
    if not isinstance(items, (tuple, list)):
        raise ValueError(f"pam_env {kind} items must be a sequence")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        record = normalize(item)
        name = record["name"]
        if name in seen:
            raise ValueError(f"duplicate pam_env {kind} name {name!r}")
        seen.add(name)
        normalized.append(record)
    return {
        "records": f"*[kind='{kind}']",
        "key": "name",
        "exclusive": False,
        "items": normalized,
    }


def environment_op(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return one overlay ``records`` op for simple environment records."""

    return _records_op(items, kind="environment", normalize=_environment_record)


def pam_env_op(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return one overlay ``records`` op for ``pam_env.conf`` records."""

    return _records_op(items, kind="conf", normalize=_conf_record)


__all__ = ["environment_item", "environment_op", "pam_env_item", "pam_env_op"]
