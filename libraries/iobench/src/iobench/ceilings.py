"""Ceiling history: append-only plateau observations (IOB-CEI-01..05)."""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .errors import Refusal
from .host import Host

SCHEMA = 1
SOURCES = ("benchmark", "governor", "probe")
SOURCE_KINDS = {"benchmark": ("raw", "file"), "governor": ("raw", "file"), "probe": ("probe",)}
WORKLOADS = ("seq1m", "rand4k")
DIRS = ("read", "write")
# What an observation was measured on (IOB-CEI-06): a raw partition or device (R), a file on a filesystem (F), or a read-only raw probe (P).
KINDS = ("raw", "file", "probe")
TARGET_KIND = {"device": "raw", "file": "file"}  # identity.target.kind -> observation kind
# The evidence-class table: which kinds may confirm the STABILITY of a ceiling, per direction.  F counts for writes only (it is the
# real filesystem path, but never cache-free: it can say a write plateau is stable, never that a value is cache-proof); P counts for
# reads only (it never writes).  Every kind is recorded and reported whatever it counts for.
STABILITY_KINDS = {"write": ("raw", "file"), "read": ("raw", "probe")}
SCOPE_VERSION = "disk-scope-1"
MIN_DURATION_S = 60
SHIFT_NOTE = "reference_shift"
RECAL_SHIFT = 0.25
WINDOW_H = 12


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def scope_key(scope: Mapping[str, Any]) -> str:
    """The disk-scope key (IOB-CEI-06): ``sha256`` of ``disk-scope-1|model|serial|wwn|size_bytes`` (an absent field is the empty string).

    A disk with a serial or a wwn is identified by the disk alone.  A disk with neither (a virtio disk without a serial) is weak: the
    hash of ``/etc/machine-id`` is appended, so the same model and size on another host is another disk.  Partition fields, the
    target kind and the device name are never part of it."""
    parts = [SCOPE_VERSION, scope.get("model") or "", scope.get("serial") or "", scope.get("wwn") or "", str(scope["size_bytes"])]
    if not (scope.get("serial") or scope.get("wwn")):
        parts.append(scope.get("machine_id_hash") or "")
    return "sha256:" + hashlib.sha256("|".join(parts).encode()).hexdigest()


def identity_digest(identity: Mapping[str, Any]) -> str:
    """The history key: the disk scope of the identity, so a raw calibration, a file observation and a probe read of one disk share it."""
    scope = identity.get("disk_scope")
    if scope is None:
        raise Refusal("identity_legacy", "this record predates the disk-scope identity (schema minor 1) and names no disk",
                      "recalibrate: `iobench calibrate` writes the disk scope")
    return scope_key(scope)


def counts_for_stability(kind: str | None, direction: str) -> bool:
    """Whether an observation of ``kind`` may confirm the stability of a ``direction`` ceiling (the evidence-class table)."""
    return kind in STABILITY_KINDS[direction]


def header_line(digest: str) -> str:
    return json.dumps({"header": True, "schema": SCHEMA, "identity_digest": digest})


def validate(obs: Mapping[str, Any]) -> None:
    """IOB-CEI-02 refusals."""
    bps = obs["plateau_bps"]
    if not isinstance(bps, (int, float)) or isinstance(bps, bool) or not math.isfinite(bps) or bps <= 0:
        raise Refusal("ceiling_plateau", f"plateau_bps must be a positive finite number, got {bps!r}", "pass the measured plateau in bytes/s")
    dur = obs["duration_s"]
    floor = 15 if obs.get("note") == SHIFT_NOTE else MIN_DURATION_S
    if not isinstance(dur, (int, float)) or not math.isfinite(dur) or dur < floor:
        raise Refusal("ceiling_duration", f"duration_s {dur!r} is below {floor} s", "observe the plateau for at least 60 s")
    if obs["source"] not in SOURCES:
        raise Refusal("ceiling_source", f"unknown source {obs['source']!r}", f"use one of {', '.join(SOURCES)}")
    if obs["workload"] not in WORKLOADS:
        raise Refusal("ceiling_workload", f"unknown workload {obs['workload']!r}", f"use one of {', '.join(WORKLOADS)}")
    if obs["dir"] not in DIRS:
        raise Refusal("ceiling_dir", f"unknown dir {obs['dir']!r}", "use read or write")
    if obs.get("kind") not in KINDS:
        raise Refusal("ceiling_kind", f"unknown or missing kind {obs.get('kind')!r}", f"say what the observation was measured on: {', '.join(KINDS)}")
    if obs["kind"] not in SOURCE_KINDS[obs["source"]]:
        allowed = " or ".join(SOURCE_KINDS[obs["source"]])
        raise Refusal("ceiling_kind", f"source {obs['source']!r} cannot use kind {obs['kind']!r}", f"use kind {allowed} for source {obs['source']}")
    if obs["kind"] == "probe" and obs["dir"] == "write":
        raise Refusal("ceiling_kind", "a probe only reads; it cannot observe a write ceiling", "use kind raw or file for a write observation")


def append(host: Host, path: str, obs: Mapping[str, Any], digest: str) -> None:
    """Append one observation; creates the header first.  Atomic: ``Host.append_line`` is one write plus fsync."""
    validate(obs)
    if host.exists(path):
        existing = header_of(host.read_text(path))
        if existing != digest:
            raise Refusal("history_identity_mismatch", f"{path} belongs to another disk identity",
                          "move the old file aside; its observations are not this disk's")
    else:
        host.write_atomic(path, header_line(digest) + "\n")
    host.append_line(path, json.dumps(dict(obs), sort_keys=True))


def header_of(text: str) -> str:
    return json.loads(text.splitlines()[0])["identity_digest"]


def rotate_foreign(host: Host, path: str, digest: str, stamp: str) -> str | None:
    """Move a history that belongs to another disk identity (or is unreadable) aside BEFORE a run writes its records.

    ``load`` already ignores such a file, but ``append`` refuses it: refusing after the result is written would leave a complete
    record and a non-zero exit.  Returns the new path, or ``None`` when nothing needed to move."""
    if not host.exists(path):
        return None
    text = host.read_text(path)
    try:
        if header_of(text) == digest:
            return None
    except (ValueError, KeyError, IndexError):
        pass  # an empty or garbled header cannot be appended to either
    aside = f"{path}.superseded-{stamp.replace(':', '')}"
    host.write_atomic(aside, text)
    host.remove(path)
    return aside


def load(host: Host, path: str, digest: str) -> tuple[list[dict[str, Any]], list[str]]:
    """``(observations, warnings)``; foreign or unreadable headers are ignored with a warning."""
    if not host.exists(path):
        return [], []
    text = host.read_text(path)
    try:
        history_digest = header_of(text)
    except (ValueError, KeyError, IndexError):
        return [], ["history_ignored_unreadable"]
    if history_digest != digest:
        return [], ["history_ignored_identity_differs"]
    return [json.loads(line) for line in text.splitlines()[1:] if line.strip()], []


def summary(observations: Sequence[Mapping[str, Any]], now: float, window_days: int = 30, kind: str | None = None) -> dict[str, Any]:
    """worst / median / best / n per (dir, workload) inside the window; ``single-run`` without records.

    Without ``kind`` only the observations that may confirm stability (``counts_for_stability``: file reads and probe writes are left
    out) are summarised.  With ``kind`` every observation of that one class is summarised, whatever it counts for, and the object
    names the class."""
    cutoff = now - window_days * 86400
    groups: dict[tuple[str, str], list[float]] = {}
    for o in observations:
        wanted = o.get("kind") == kind if kind is not None else counts_for_stability(o.get("kind"), o["dir"])
        if wanted and o.get("note") != SHIFT_NOTE and parse_iso(o["ts"]) >= cutoff:
            groups.setdefault((o["dir"], o["workload"]), []).append(float(o["plateau_bps"]))
    out: dict[str, Any] = {"history": "recorded" if groups else "single-run", "window_days": window_days}
    if kind is not None:
        out["kind"] = kind
    for (d, w), vals in sorted(groups.items()):
        out.setdefault(d, {})[w] = {"worst": min(vals), "median": statistics.median(vals), "best": max(vals), "n": len(vals)}
    return out


def value_summary(observations: Sequence[Mapping[str, Any]], now: float, window_days: int = 30) -> dict[str, Any]:
    """History permitted to derive model and cap values (4.1.1): raw calibration rows only.

    File observations and read-only probes can confirm stability in their allowed direction, but cannot supply the value itself.
    """
    return summary([o for o in observations if o.get("kind") == "raw"], now, window_days)


def summary_by_kind(observations: Sequence[Mapping[str, Any]], now: float, window_days: int = 30) -> dict[str, Any]:
    """Each evidence class reported separately (IOB-CEI-06): ``{kind: summary(kind=kind)}`` for every kind that has observations."""
    return {k: summary(observations, now, window_days, k) for k in KINDS if any(o.get("kind") == k for o in observations)}


def recalibrate_recommended(observations: Sequence[Mapping[str, Any]], calibration_plateaus: Mapping[str, float], now: float) -> bool:
    """The 12 h median differs from the calibration's by > 25 % in two consecutive 12 h windows (warning only).

    Only observations that may confirm stability of their direction count (a file read or a probe write is not evidence)."""
    for key, cal in calibration_plateaus.items():
        d, w = key.split("/")
        flags = []
        for k in (0, 1):
            hi, lo = now - k * WINDOW_H * 3600, now - (k + 1) * WINDOW_H * 3600
            vals = [float(o["plateau_bps"]) for o in observations
                    if o["dir"] == d and o["workload"] == w and o.get("note") != SHIFT_NOTE and counts_for_stability(o.get("kind"), d)
                    and lo < parse_iso(o["ts"]) <= hi]
            flags.append(bool(vals) and abs(statistics.median(vals) / cal - 1) > RECAL_SHIFT)
        if all(flags):
            return True
    return False


def run_observations(plateaus: Sequence[Mapping[str, Any]], ts: str, kind: str, partition: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """One ``benchmark`` observation per sustained-plateau window (>= 60 s, steady/flat) of a complete run.

    ``kind`` is what the run measured (``TARGET_KIND``); ``partition`` (``devno``, ``partuuid``) is recorded as fields of the
    observation, not of the history key."""
    extra = {} if partition is None else {"devno": partition.get("devno"), "partuuid": partition.get("partuuid")}
    return [{"ts": ts, "dir": p["dir"], "workload": p["workload"], "plateau_bps": p["plateau_bps"], "duration_s": p["observation_s"],
             "source": "benchmark", "kind": kind, **extra}
            for p in plateaus if p["state"] in ("steady", "flat") and p["observation_s"] >= MIN_DURATION_S]


def reference_shift(ts: str, direction: str, plateau_bps: float, kind: str) -> dict[str, Any]:
    return {"ts": ts, "dir": direction, "workload": "rand4k", "plateau_bps": plateau_bps, "duration_s": 15,
            "source": "benchmark", "kind": kind, "note": SHIFT_NOTE}
