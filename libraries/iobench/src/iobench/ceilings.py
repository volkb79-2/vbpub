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
WORKLOADS = ("seq1m", "rand4k")
DIRS = ("read", "write")
MIN_DURATION_S = 60
SHIFT_NOTE = "reference_shift"
RECAL_SHIFT = 0.25
WINDOW_H = 12


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def identity_digest(identity: Mapping[str, Any]) -> str:
    """Digest of the strict identity fields AND the target kind (file observations never mix with device ones)."""
    disk = identity["disk"]
    fields = {k: disk.get(k) for k in ("devno", "partuuid", "size_bytes", "model", "serial", "wwn")}
    fields["kind"] = identity["target"]["kind"]
    return "sha256:" + hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


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


def load(host: Host, path: str, digest: str) -> tuple[list[dict[str, Any]], list[str]]:
    """``(observations, warnings)``; a history of another identity is ignored with a warning."""
    if not host.exists(path):
        return [], []
    text = host.read_text(path)
    if header_of(text) != digest:
        return [], ["history_ignored_identity_differs"]
    return [json.loads(line) for line in text.splitlines()[1:] if line.strip()], []


def summary(observations: Sequence[Mapping[str, Any]], now: float, window_days: int = 30) -> dict[str, Any]:
    """worst / median / best / n per (dir, workload) inside the window; ``single-run`` without records."""
    cutoff = now - window_days * 86400
    groups: dict[tuple[str, str], list[float]] = {}
    for o in observations:
        if o.get("note") != SHIFT_NOTE and parse_iso(o["ts"]) >= cutoff:
            groups.setdefault((o["dir"], o["workload"]), []).append(float(o["plateau_bps"]))
    out: dict[str, Any] = {"history": "recorded" if groups else "single-run", "window_days": window_days}
    for (d, w), vals in sorted(groups.items()):
        out.setdefault(d, {})[w] = {"worst": min(vals), "median": statistics.median(vals), "best": max(vals), "n": len(vals)}
    return out


def recalibrate_recommended(observations: Sequence[Mapping[str, Any]], calibration_plateaus: Mapping[str, float], now: float) -> bool:
    """The 12 h median differs from the calibration's by > 25 % in two consecutive 12 h windows (warning only)."""
    for key, cal in calibration_plateaus.items():
        d, w = key.split("/")
        flags = []
        for k in (0, 1):
            hi, lo = now - k * WINDOW_H * 3600, now - (k + 1) * WINDOW_H * 3600
            vals = [float(o["plateau_bps"]) for o in observations
                    if o["dir"] == d and o["workload"] == w and o.get("note") != SHIFT_NOTE and lo < parse_iso(o["ts"]) <= hi]
            flags.append(bool(vals) and abs(statistics.median(vals) / cal - 1) > RECAL_SHIFT)
        if all(flags):
            return True
    return False


def run_observations(plateaus: Sequence[Mapping[str, Any]], ts: str) -> list[dict[str, Any]]:
    """One ``benchmark`` observation per sustained-plateau window (>= 60 s, steady/flat) of a complete run."""
    return [{"ts": ts, "dir": p["dir"], "workload": p["workload"], "plateau_bps": p["plateau_bps"], "duration_s": p["observation_s"],
             "source": "benchmark"}
            for p in plateaus if p["state"] in ("steady", "flat") and p["observation_s"] >= MIN_DURATION_S]


def reference_shift(ts: str, direction: str, plateau_bps: float) -> dict[str, Any]:
    return {"ts": ts, "dir": direction, "workload": "rand4k", "plateau_bps": plateau_bps, "duration_s": 15,
            "source": "benchmark", "note": SHIFT_NOTE}
