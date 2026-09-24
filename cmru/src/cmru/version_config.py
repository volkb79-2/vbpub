"""Strict parser helpers for the public ``[versions]`` configuration.

The regular CMRU config loader owns this grammar. Keeping the version policy
schema in a separate module lets the network resolver share the same vocabulary
without making release orchestration depend on registry clients.
"""
from __future__ import annotations

import copy
import re
from datetime import date, datetime
from urllib.parse import urlsplit

from cmru.version_registry import RegistryError, validate_constraint, version_satisfies


SOURCE_FIELDS = {
    "npm": {"name", "registry", "token_env", "username_env", "password_env"},
    "pypi": {"name", "registry", "token_env", "username_env", "password_env"},
    "go": {"module", "proxy", "token_env", "username_env", "password_env"},
    "oci": {"image", "tag", "token_env", "username_env", "password_env"},
}

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_PACKAGE_NAME = re.compile(r"^(?:@[a-z0-9._-]+/)?[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)
_GO_MODULE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~/-]*$")
_IMAGE = re.compile(r"^[a-z0-9.-]+(?::[0-9]+)?/[a-z0-9._/-]+$", re.IGNORECASE)
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _fail(message: str) -> None:
    raise ValueError(message)


def _keys(raw: dict, allowed: set[str], where: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        _fail(f"{where}: unknown keys {unknown}")


def _nonempty(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{where} must be a non-empty string")
    return value.strip()


def _https_url(value: object, where: str) -> str:
    url = _nonempty(value, where)
    try:
        parts = urlsplit(url)
    except ValueError:
        _fail(f"{where} must be a valid HTTPS URL")
    if parts.scheme != "https":
        _fail(f"{where} must be an HTTPS URL")
    if not parts.hostname:
        _fail(f"{where} must include a host")
    if parts.username is not None:
        _fail(f"{where} must be an HTTPS URL without embedded credentials, query, or fragment")
    if "?" in url.split("#", 1)[0]:
        _fail(f"{where} must not contain a query or fragment")
    if "#" in url:
        _fail(f"{where} must not contain a query or fragment")
    if parts.netloc.endswith(":"):
        _fail(f"{where} must contain a valid port")
    try:
        port = parts.port
    except ValueError:
        _fail(f"{where} must contain a valid port")
    if port is not None and not 1 <= port <= 65535:
        _fail(f"{where} must contain a valid port")
    return url.rstrip("/")


def _source(raw: object, source_type: str, where: str, *, allow_partial: bool) -> dict:
    if not isinstance(raw, dict):
        _fail(f"{where} must be a table")
    _keys(raw, SOURCE_FIELDS[source_type], where)
    result = copy.deepcopy(raw)
    if source_type in {"npm", "pypi"}:
        for key in ("name", "registry"):
            if key not in raw and allow_partial:
                continue
            value = _nonempty(raw.get(key), f"{where}.{key}")
            if key == "name" and not _PACKAGE_NAME.fullmatch(value):
                _fail(f"{where}.name is not a valid registry package name")
            result[key] = value if key == "name" else _https_url(value, f"{where}.{key}")
    elif source_type == "go":
        for key in ("module", "proxy"):
            if key not in raw and allow_partial:
                continue
            value = _nonempty(raw.get(key), f"{where}.{key}")
            if key == "module" and (not _GO_MODULE.fullmatch(value) or ".." in value.split("/")):
                _fail(f"{where}.module is not a valid Go module path")
            result[key] = value if key == "module" else _https_url(value, f"{where}.{key}")
    else:
        image = _nonempty(raw.get("image"), f"{where}.image") if "image" in raw or not allow_partial else None
        if image is not None:
            if not _IMAGE.fullmatch(image) or ".." in image.split("/"):
                _fail(f"{where}.image must be a fully-qualified registry/repository name")
            result["image"] = image
        tag = _nonempty(raw.get("tag"), f"{where}.tag") if "tag" in raw or not allow_partial else None
        if tag is not None:
            if tag.count("{version}") != 1:
                _fail(f"{where}.tag must contain exactly one {{version}} placeholder")
            placeholders = re.findall(r"\{([^{}]+)\}", tag)
            if any(token != "version" for token in placeholders):
                _fail(f"{where}.tag supports only the {{version}} placeholder")
            if "{{" in tag or "}}" in tag:
                _fail(f"{where}.tag contains malformed placeholders")
            result["tag"] = tag
    _validate_auth_fields(result, where, allow_partial=allow_partial)
    return result


def _validate_auth_fields(raw: dict, where: str, *, allow_partial: bool) -> None:
    names = ("token_env", "username_env", "password_env")
    for name in names:
        value = raw.get(name)
        if value is not None and not _ENV_NAME.fullmatch(_nonempty(value, f"{where}.{name}")):
            _fail(f"{where}.{name} must name an environment variable")
    username, password, token = (raw.get(name) for name in names)
    if not allow_partial and bool(username) != bool(password):
        _fail(f"{where}.username_env and .password_env must be set together")
    if token is not None and (username is not None or password is not None):
        _fail(f"{where}: use token_env or username_env/password_env, not both")


def _resolved(raw: object, where: str) -> dict:
    if not isinstance(raw, dict):
        _fail(f"{where} must be a table")
    _keys(raw, {"version", "resolved_at", "age_cutoff", "sources", "provenance"}, where)
    version = _nonempty(raw.get("version"), f"{where}.version")
    resolved_at = _nonempty(raw.get("resolved_at"), f"{where}.resolved_at")
    age_cutoff = _nonempty(raw.get("age_cutoff"), f"{where}.age_cutoff")
    try:
        parsed_at = datetime.fromisoformat(resolved_at.replace("Z", "+00:00"))
    except ValueError:
        _fail(f"{where}.resolved_at must be an ISO timestamp")
    if parsed_at.tzinfo is None:
        _fail(f"{where}.resolved_at must include a timezone")
    if not isinstance(raw.get("sources"), dict) or not raw["sources"]:
        _fail(f"{where}.sources must be a non-empty table")
    for source_type, source_result in raw["sources"].items():
        if source_type not in SOURCE_FIELDS or not isinstance(source_result, dict):
            _fail(f"{where}.sources.{source_type} must be a supported source table")
        _keys(source_result, {"version", "released_at", "age_source", "tag"}, f"{where}.sources.{source_type}")
        _nonempty(source_result.get("version"), f"{where}.sources.{source_type}.version")
        released_at = _nonempty(source_result.get("released_at"), f"{where}.sources.{source_type}.released_at")
        try:
            parsed_release = datetime.fromisoformat(released_at.replace("Z", "+00:00"))
        except ValueError:
            _fail(f"{where}.sources.{source_type}.released_at must be an ISO timestamp")
        if parsed_release.tzinfo is None:
            _fail(f"{where}.sources.{source_type}.released_at must include a timezone")
        _nonempty(source_result.get("age_source"), f"{where}.sources.{source_type}.age_source")
        if "tag" in source_result:
            _nonempty(source_result["tag"], f"{where}.sources.{source_type}.tag")
    provenance = raw.get("provenance")
    if provenance is not None and not isinstance(provenance, str):
        _fail(f"{where}.provenance must be a string")
    try:
        cutoff = datetime.fromisoformat(age_cutoff.replace("Z", "+00:00"))
    except ValueError:
        _fail(f"{where}.age_cutoff must be an ISO timestamp")
    if cutoff.tzinfo is None:
        _fail(f"{where}.age_cutoff must include a timezone")
    return copy.deepcopy(raw)


def parse_versions_section(raw: object, where: str, *, allow_partial: bool = False) -> dict:
    """Validate and copy a root or project ``[versions]`` document.

    Empty/missing sections are represented as an empty mapping. The resolver
    applies the documented 14-day policy only after root/project merging.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        _fail(f"{where} must be a table")
    _keys(raw, {"age_window_days", "targets", "outputs"}, where)
    result: dict = {}
    if "age_window_days" in raw:
        days = raw["age_window_days"]
        if not isinstance(days, int) or isinstance(days, bool) or days <= 0:
            _fail(f"{where}.age_window_days must be a positive integer")
        result["age_window_days"] = days
    targets = raw.get("targets", {})
    if not isinstance(targets, dict):
        _fail(f"{where}.targets must be a table")
    clean_targets: dict[str, dict] = {}
    for target_id, target_raw in targets.items():
        target_where = f"{where}.targets.{target_id}"
        if not isinstance(target_id, str) or not _IDENTIFIER.fullmatch(target_id) or ".." in target_id:
            _fail(f"{where}.targets keys must be simple target identifiers")
        if not isinstance(target_raw, dict):
            _fail(f"{target_where} must be a table")
        _keys(
            target_raw,
            {"mode", "constraint", "version", "reason", "expires", "resolved", *SOURCE_FIELDS},
            target_where,
        )
        mode = target_raw.get("mode")
        if mode is not None and mode not in {"aligned", "single"}:
            _fail(f"{target_where}.mode must be one of: aligned, single")
        constraint = target_raw.get("constraint")
        if constraint is not None:
            constraint = _nonempty(constraint, f"{target_where}.constraint")
            try:
                validate_constraint(constraint)
            except RegistryError as exc:
                _fail(f"{target_where}.constraint: {exc}")
        source_types = [name for name in SOURCE_FIELDS if name in target_raw]
        if not source_types and not allow_partial:
            _fail(f"{target_where} must declare at least one source table (.npm, .pypi, .go, or .oci)")
        if not allow_partial and (mode is None or constraint is None):
            _fail(f"{target_where} requires mode and constraint")
        if not allow_partial and mode == "aligned" and len(source_types) < 2:
            _fail(f"{target_where}.mode = 'aligned' requires at least two source tables")
        if not allow_partial and mode == "single" and len(source_types) != 1:
            _fail(f"{target_where}.mode = 'single' requires exactly one source table")
        version = target_raw.get("version")
        reason = target_raw.get("reason")
        if version is not None:
            version = _nonempty(version, f"{target_where}.version")
            if reason is None:
                _fail(f"{target_where}.reason is required with an exact version override")
            reason = _nonempty(reason, f"{target_where}.reason")
            if constraint is not None and not version_satisfies(version, constraint):
                _fail(f"{target_where}.version {version!r} does not satisfy constraint {constraint!r}")
        elif reason is not None and not allow_partial:
            _fail(f"{target_where}.reason requires an exact version override")
        elif reason is not None:
            reason = _nonempty(reason, f"{target_where}.reason")
        expires = target_raw.get("expires")
        if expires is not None:
            if version is None and not allow_partial:
                _fail(f"{target_where}.expires requires an exact version override")
            if not isinstance(expires, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expires):
                _fail(f"{target_where}.expires must be an ISO date")
            try:
                date.fromisoformat(expires)
            except ValueError:
                _fail(f"{target_where}.expires must contain a valid ISO date")
        clean = {
            **({"mode": mode} if mode is not None else {}),
            **({"constraint": constraint} if constraint is not None else {}),
            **({"version": version} if version is not None else {}),
            **({"reason": reason} if reason is not None else {}),
            **({"expires": expires} if expires is not None else {}),
        }
        for source_type in source_types:
            clean[source_type] = _source(
                target_raw[source_type], source_type, f"{target_where}.{source_type}",
                allow_partial=allow_partial,
            )
        if "resolved" in target_raw:
            clean["resolved"] = _resolved(target_raw["resolved"], f"{target_where}.resolved")
        clean_targets[target_id] = clean
    result["targets"] = clean_targets

    outputs = raw.get("outputs", {})
    if not isinstance(outputs, dict):
        _fail(f"{where}.outputs must be a table")
    clean_outputs: dict[str, dict] = {}
    for output_id, output_raw in outputs.items():
        output_where = f"{where}.outputs.{output_id}"
        if not isinstance(output_id, str) or not _IDENTIFIER.fullmatch(output_id) or ".." in output_id:
            _fail(f"{where}.outputs keys must be simple output identifiers")
        if not isinstance(output_raw, dict):
            _fail(f"{output_where} must be a table")
        _keys(output_raw, {"template", "path", "dated_path"}, output_where)
        template = (
            _nonempty(output_raw.get("template"), f"{output_where}.template")
            if "template" in output_raw or not allow_partial else None
        )
        path = (
            _nonempty(output_raw.get("path"), f"{output_where}.path")
            if "path" in output_raw or not allow_partial else None
        )
        dated_path = (
            _nonempty(output_raw.get("dated_path"), f"{output_where}.dated_path")
            if "dated_path" in output_raw or not allow_partial else None
        )
        if dated_path is not None and "{date}" not in dated_path:
            _fail(f"{output_where}.dated_path must contain a {{date}} placeholder")
        for label, candidate in (("template", template), ("path", path), ("dated_path", dated_path)):
            if candidate is not None and (candidate.startswith("/") or ".." in candidate.split("/")):
                _fail(f"{output_where}.{label} must stay within the project directory")
        clean_outputs[output_id] = {
            **({"template": template} if template is not None else {}),
            **({"path": path} if path is not None else {}),
            **({"dated_path": dated_path} if dated_path is not None else {}),
        }
    result["outputs"] = clean_outputs
    return result


def deep_merge_versions(base: dict, overlay: dict) -> dict:
    """Merge tables recursively and replace all non-table values."""
    merged = copy.deepcopy(base)
    for key, value in overlay.items():
        previous = merged.get(key)
        if isinstance(previous, dict) and isinstance(value, dict):
            merged[key] = deep_merge_versions(previous, value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged
