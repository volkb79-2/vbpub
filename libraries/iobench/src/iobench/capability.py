"""Append-only, disk-scoped cache and burst observations (IOB-CAP-01)."""
from __future__ import annotations

import json
import math
from typing import Any, Mapping, Sequence

from . import ceilings
from .errors import Refusal
from .host import Host

SCHEMA = 1
PROFILES = ("veryquick", "quick", "standard", "thorough")
CONFIDENCE = ("low", "medium", "high")
NUMERIC_FIELDS = {
    "burst_bytes": 0.0,
    "knee_s": 0.0,
    "post_cliff_bps": 0.0,
    "pre_cliff_bps": 0.0,
    "cliff_bytes": 0.0,
    "cold_region_bps": 0.0,
}
ROW_FIELDS = ("schema", "ts", "disk_key", "kind", "profile", "source", *NUMERIC_FIELDS, "write_burst_seen", "confidence", "note")


def validate(row: Mapping[str, Any], digest: str | None = None) -> None:
    schema = row.get("schema")
    if not isinstance(schema, int) or isinstance(schema, bool) or schema != SCHEMA:
        raise Refusal("capability_schema", f"capability row schema must be {SCHEMA}", "write a schema 1 capability row")
    try:
        ceilings.parse_iso(row["ts"])
    except (KeyError, TypeError, ValueError):
        raise Refusal("capability_timestamp", f"invalid capability timestamp {row.get('ts')!r}", "use UTC YYYY-MM-DDTHH:MM:SSZ") from None
    disk_key = row.get("disk_key")
    if not isinstance(disk_key, str) or not disk_key.startswith("sha256:") or len(disk_key) != 71 or (digest is not None and disk_key != digest):
        raise Refusal("capability_identity", "capability row does not name the calibrated disk scope", "use identity.disk_scope.key")
    if row.get("kind") not in ceilings.KINDS:
        raise Refusal("capability_kind", f"unknown evidence kind {row.get('kind')!r}", f"use one of {', '.join(ceilings.KINDS)}")
    source = row.get("source")
    if source not in ceilings.SOURCES:
        raise Refusal("capability_source", f"unknown source {source!r}", f"use one of {', '.join(ceilings.SOURCES)}")
    if row["kind"] not in ceilings.SOURCE_KINDS[source]:
        raise Refusal("capability_kind", f"source {source!r} cannot use kind {row['kind']!r}", "use the evidence class allowed for this source")
    if row.get("profile") not in PROFILES:
        raise Refusal("capability_profile", f"unknown calibration profile {row.get('profile')!r}", f"use one of {', '.join(PROFILES)}")
    for name, floor in NUMERIC_FIELDS.items():
        value = row.get(name)
        if value is None:
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < floor:
            raise Refusal("capability_number", f"{name} must be a finite non-negative number or null", "write the measured value or null")
        if name in ("post_cliff_bps", "pre_cliff_bps", "cliff_bytes", "cold_region_bps") and value == 0:
            raise Refusal("capability_number", f"{name} must be positive when measured", "write a positive measured value or null")
    seen = row.get("write_burst_seen")
    if seen is not None and not isinstance(seen, bool):
        raise Refusal("capability_burst_seen", "write_burst_seen must be boolean or null", "write the observed boolean or null")
    if row.get("confidence") not in CONFIDENCE:
        raise Refusal("capability_confidence", f"unknown confidence {row.get('confidence')!r}", f"use one of {', '.join(CONFIDENCE)}")
    if row.get("note") is not None and not isinstance(row.get("note"), str):
        raise Refusal("capability_note", "note must be text or null", "write a short note or null")
    missing = [name for name in ROW_FIELDS if name not in row]
    if missing:
        raise Refusal("capability_fields", f"capability row is missing {', '.join(missing)}", "write every schema field")


def header_line(digest: str) -> str:
    return json.dumps({"header": True, "schema": SCHEMA, "identity_digest": digest}, sort_keys=True)


def _header(text: str) -> Mapping[str, Any]:
    first = text.splitlines()[0]
    header = json.loads(first)
    schema = header.get("schema") if isinstance(header, dict) else None
    if not isinstance(header, dict) or header.get("header") is not True or not isinstance(schema, int) or isinstance(schema, bool) or schema != SCHEMA:
        raise ValueError("invalid capability header")
    return header


def append(host: Host, path: str, row: Mapping[str, Any], digest: str) -> None:
    """Validate and append one row; the first line binds the history to the disk-scope key."""
    validate(row, digest)
    if host.exists(path):
        try:
            existing = _header(host.read_text(path))
        except (OSError, ValueError, IndexError, TypeError):
            raise Refusal("capability_history_corrupt", f"{path} has no readable capability header", "move the unreadable history aside") from None
        if existing.get("identity_digest") != digest:
            raise Refusal("capability_identity_mismatch", f"{path} belongs to another disk identity", "move the old file aside before appending")
    else:
        host.write_atomic(path, header_line(digest) + "\n")
    host.append_line(path, json.dumps(dict(row), sort_keys=True))


def rotate_foreign(host: Host, path: str, digest: str, stamp: str) -> str | None:
    """Move an unreadable or other-disk history aside before the completed run appends its first row."""
    if not host.exists(path):
        return None
    text = host.read_text(path)
    try:
        if _header(text).get("identity_digest") == digest:
            _, warnings = load(host, path, digest)
            if not warnings:
                return None
    except (ValueError, IndexError, TypeError):
        pass
    aside = f"{path}.superseded-{stamp.replace(':', '')}"
    host.write_atomic(aside, text)
    host.remove(path)
    return aside


def load(host: Host, path: str, digest: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Read descriptive rows only; malformed or foreign history never invalidates calibration or supplies a value."""
    if not host.exists(path):
        return [], []
    try:
        text = host.read_text(path)
        header = _header(text)
    except (OSError, ValueError, IndexError, TypeError):
        return [], ["capability_history_corrupt"]
    if header.get("identity_digest") != digest:
        return [], ["capability_history_identity_differs"]
    rows: list[dict[str, Any]] = []
    try:
        for line in text.splitlines()[1:]:
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("capability row is not an object")
            validate(row, digest)
            rows.append(row)
    except (ValueError, TypeError, KeyError, Refusal):
        return [], ["capability_history_corrupt"]
    return rows, []


def select_since(rows: Sequence[Mapping[str, Any]], since: str | None) -> list[dict[str, Any]]:
    if since is None:
        return [dict(row) for row in rows]
    try:
        cutoff = ceilings.parse_iso(since)
    except (TypeError, ValueError):
        raise Refusal("capability_since", f"invalid --since timestamp {since!r}", "use UTC YYYY-MM-DDTHH:MM:SSZ") from None
    return [dict(row) for row in rows if ceilings.parse_iso(row["ts"]) >= cutoff]


def latest_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {"count": len(rows), "latest": dict(rows[-1]) if rows else None}


def run_observation(cal: Mapping[str, Any], identity: Mapping[str, Any], profile: str, kind: str, ts: str) -> dict[str, Any]:
    """Extract observations already measured by calibration; veryquick stores only its burst-seen boolean."""
    numeric: dict[str, Any] = {name: None for name in NUMERIC_FIELDS}
    write_burst_seen = None
    proof = cal["cache"]["write"].get("time_proof")
    cold = cal["cache"]["read"].get("e_cold")
    cliff = cal["ladder"].get("cliff_bytes")
    if profile == "veryquick":
        write_burst_seen = cal["veryquick"]["write_burst_seen"]
        confidence = "low"
        note = "veryquick records write_burst_seen only; numeric burst and cliff fields are null"
    else:
        if proof is not None:
            numeric.update({"burst_bytes": proof.get("burst_bytes"), "knee_s": proof.get("knee_s"),
                            "post_cliff_bps": proof.get("post_cliff_bps"), "pre_cliff_bps": proof.get("pre_cliff_bps")})
        else:
            writes = [row for row in cal["ladder"].get("rungs", []) if row.get("dir") == "write" and row.get("state") in ("steady", "flat")]
            if writes:
                last = max(writes, key=lambda row: row["ws_bytes"])
                bs = last.get("bs")
                burst_units = last.get("burst_units")
                knee = last.get("knee_s")
                plateau = last["steady_iops"] * bs if isinstance(bs, int) and isinstance(last.get("steady_iops"), (int, float)) else None
                burst = burst_units * bs if isinstance(bs, int) and isinstance(burst_units, (int, float)) else None
                numeric.update({"burst_bytes": burst, "knee_s": knee, "post_cliff_bps": plateau,
                                "pre_cliff_bps": plateau + burst / knee if plateau is not None and burst is not None and knee is not None and knee > 0 else None})
        if cliff is not None:
            numeric["cliff_bytes"] = cliff
        cold_iops = cold.get("R_cold_iops") if cold else None
        if isinstance(cold_iops, (int, float)) and not isinstance(cold_iops, bool) and cold_iops > 0:
            numeric["cold_region_bps"] = cold_iops * 4096
        measured = any(value is not None for value in numeric.values())
        if kind == "raw" and proof is not None and proof.get("proven") is True and cold is not None and cold.get("class") == "miss-like":
            confidence = "high"
        else:
            confidence = "medium" if measured else "low"
        note = "indirect evidence only; the guest cannot identify the cache layer"
    row = {"schema": SCHEMA, "ts": ts, "disk_key": identity["disk_scope"]["key"], "kind": kind, "profile": profile,
           "source": "benchmark", **numeric, "write_burst_seen": write_burst_seen, "confidence": confidence, "note": note}
    validate(row, identity["disk_scope"]["key"])
    return row
