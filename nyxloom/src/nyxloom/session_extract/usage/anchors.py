"""Private, explicit operator readings of the account usage display."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "usage-anchors-v1"
KINDS = {"five_hour", "weekly"}
COVERAGE_ASSUMPTIONS = {"local-only-lower-bound", "all-account-usage-observed"}
SOURCES = {"settings-usage", "account-usage-page"}


class AnchorError(ValueError):
    """Raised for invalid, ambiguous or unreadable anchor state."""


def default_anchor_path() -> Path:
    state_root = os.environ.get("XDG_STATE_HOME")
    base = Path(state_root).expanduser() if state_root else Path.home() / ".local" / "state"
    return base / "nyxloom" / "usage-anchors-v1.json"


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise AnchorError(f"{field} must be a timezone-aware ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AnchorError(f"{field} must be a timezone-aware ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AnchorError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _has_symlink_component(path: Path) -> bool:
    absolute = path if path.is_absolute() else Path.cwd() / path
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        if part == "..":
            current = current.parent
            continue
        current = current / part
        if current.is_symlink():
            return True
    return False


def _validate_anchor(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AnchorError("anchor entries must be objects")
    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in KINDS:
        raise AnchorError("kind must be five_hour or weekly")
    observed = _timestamp(value.get("observed_at"), "observed_at")
    reset = _timestamp(value.get("reset_at"), "reset_at")
    if reset <= observed:
        raise AnchorError("reset_at must be after observed_at")
    percent = value.get("percent")
    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
        raise AnchorError("percent must be a number from 0 through 100")
    try:
        percent_value = float(percent)
    except OverflowError as exc:
        raise AnchorError("percent must be a number from 0 through 100") from exc
    if not math.isfinite(percent_value) or not 0 <= percent_value <= 100:
        raise AnchorError("percent must be a number from 0 through 100")
    source = value.get("source")
    if not isinstance(source, str) or source not in SOURCES:
        raise AnchorError("source must be settings-usage or account-usage-page")
    coverage = value.get("coverage_assumption")
    if not isinstance(coverage, str) or coverage not in COVERAGE_ASSUMPTIONS:
        raise AnchorError("coverage_assumption must state local-only-lower-bound or all-account-usage-observed")
    material = json.dumps(
        (kind, _iso(observed), percent_value, _iso(reset), source, coverage),
        separators=(",", ":"),
    ).encode("utf-8")
    expected_id = "anchor_" + hashlib.sha256(material).hexdigest()[:24]
    anchor_id = value.get("anchor_id", expected_id)
    if anchor_id != expected_id:
        raise AnchorError("anchor_id does not match its fields")
    return {
        "anchor_id": expected_id,
        "kind": kind,
        "observed_at": _iso(observed),
        "percent": percent_value,
        "reset_at": _iso(reset),
        "source": source,
        "coverage_assumption": coverage,
    }


def _read_anchor_store(state_path: Path, *, missing_ok: bool) -> dict[str, Any]:
    if _has_symlink_component(state_path):
        raise AnchorError("anchor state path must not contain symlink components")
    try:
        content = state_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        if missing_ok:
            return {"schema_version": SCHEMA_VERSION, "anchors": []}
        raise AnchorError("selected anchor state file does not exist") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise AnchorError(f"cannot read anchor state: {exc}") from exc
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AnchorError(f"cannot read anchor state: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise AnchorError("unsupported schema_version in anchor state")
    anchors = raw.get("anchors")
    if not isinstance(anchors, list):
        raise AnchorError("anchor state anchors must be a list")
    validated = [_validate_anchor(item) for item in anchors]
    if len({item["anchor_id"] for item in validated}) != len(validated):
        raise AnchorError("anchor state contains duplicate identities")
    if stat.S_IMODE(state_path.stat().st_mode) & 0o077:
        raise AnchorError("anchor state file must be private (mode 0600)")
    if stat.S_IMODE(state_path.parent.stat().st_mode) & 0o077:
        raise AnchorError("anchor state directory must be private (mode 0700)")
    return {"schema_version": SCHEMA_VERSION, "anchors": sorted(validated, key=lambda item: (item["observed_at"], item["kind"], item["anchor_id"]))}


def read_anchor_store(path: str | Path | None = None) -> dict[str, Any]:
    """Read anchors: absent defaults are empty, missing selected files are errors."""
    state_path = Path(path) if path is not None else default_anchor_path()
    return _read_anchor_store(state_path, missing_ok=path is None)


def list_anchors(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Return sanitized anchor metadata in stable time order."""
    return read_anchor_store(path)["anchors"]


def _ensure_private_parent(path: Path) -> None:
    try:
        if _has_symlink_component(path.parent):
            raise AnchorError("anchor state directory path must not contain symlink components")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if _has_symlink_component(path.parent):
            raise AnchorError("anchor state directory path must not contain symlink components")
        parent_mode = path.parent.stat().st_mode & 0o777
        if parent_mode & 0o077:
            raise AnchorError("anchor state directory must be private (mode 0700)")
    except OSError as exc:
        raise AnchorError("cannot secure the anchor state directory") from exc


def _write_private(path: Path, payload: bytes) -> None:
    _ensure_private_parent(path)
    fd, temporary = tempfile.mkstemp(prefix=".usage-anchors-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except OSError as exc:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise AnchorError("cannot write private anchor state") from exc


def add_anchor(
    path: str | Path | None = None,
    *,
    kind: str,
    observed_at: str,
    percent: float,
    reset_at: str,
    source: str,
    coverage_assumption: str,
) -> dict[str, Any]:
    """Add one UI reading; identical input is idempotent, conflicts refuse."""
    state_path = Path(path) if path is not None else default_anchor_path()
    if _has_symlink_component(state_path):
        raise AnchorError("anchor state path must not contain symlink components")
    new_anchor = _validate_anchor({
        "kind": kind,
        "observed_at": observed_at,
        "percent": percent,
        "reset_at": reset_at,
        "source": source,
        "coverage_assumption": coverage_assumption,
    })
    _ensure_private_parent(state_path)
    store = _read_anchor_store(state_path, missing_ok=True)
    for existing in store["anchors"]:
        if existing["kind"] == kind and existing["observed_at"] == new_anchor["observed_at"]:
            if existing == new_anchor:
                return existing
            raise AnchorError("conflicting observation already exists for this kind and time")
    store["anchors"].append(new_anchor)
    payload = (json.dumps(store, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    _write_private(state_path, payload)
    return new_anchor
