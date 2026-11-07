"""Machine facts (``machine/1``) and the CPU speed basis (rigor-evidence CP-1).

cgprofiled never measures CPU speed. It reads the record that the shared host tool
(``scripts/debian-install-v2/tools/cpu_benchmark.py``, schema
``mdt-host-cpu-benchmark/1``) persisted in host state, and accepts it only while
the CPU model and the online CPU count still equal the live values. Otherwise
it falls back to the packaged published-score table (``cgprofile-scores/1``,
single-core only) keyed by CPU model and Geekbench major, and says which source
it used. With neither source the speed is ``null`` with a reason; there is no
default score.

Privacy: only the fields named in :func:`_measured_speed` are ever exported. The
result URL, the conditions and ``produced_by`` of the host record are dropped,
and the claim file (``cpu-benchmark.claim.json``) is never opened.

Every reader takes its root so tests point it at a fake tree.
"""

from __future__ import annotations

import importlib.resources
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from . import util

MACHINE_SCHEMA = "machine/1"
BENCHMARK_SCHEMA = "mdt-host-cpu-benchmark/1"
SCORES_SCHEMA = "cgprofile-scores/1"
SCORES_RESOURCE = "cgprofile-scores.json"

#: Patched by the test suite so no test reads the live host state.
DEFAULT_BENCHMARK_PATH = "/var/lib/mdt/host-facts/cpu-benchmark.json"
DEFAULT_SYS_ROOT = "/sys"

#: Score of the Geekbench baseline machine per major. Scores of different
#: majors are never comparable; a major without an entry here is no basis.
BASELINE_SCORE: Dict[int, int] = {6: 2500, 7: 2500}

REASON_NO_MEASUREMENT = "NO_HOST_MEASUREMENT"
REASON_STALE = "HOST_MEASUREMENT_STALE"
REASON_INCOMPLETE = "HOST_MEASUREMENT_INCOMPLETE"
REASON_INVALID = "HOST_MEASUREMENT_INVALID"
NOT_IN_TABLE = "_AND_MODEL_NOT_IN_TABLE"

SCORE_MAX = 1_000_000
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_ISO_DAY_RE = re.compile(r"(\d{4}-\d{2}-\d{2})T")

# (status, payload): ("ok", record) | ("absent"|"invalid", None)
Loaded = Tuple[str, Optional[Dict[str, Any]]]


def is_score(value: Any) -> bool:
    return type(value) is int and 0 < value <= SCORE_MAX


def normalise_model(text: Any) -> Optional[str]:
    """Whitespace-collapsed CPU model string; ``None`` for anything unusable."""
    if not isinstance(text, str):
        return None
    collapsed = " ".join(text.split())
    return collapsed or None


# ── host facts ──────────────────────────────────────────────────────────────

def read_cpuinfo(proc_root: str) -> Tuple[Optional[str], Optional[int], Optional[bool]]:
    """``(cpu_model, cpus_online, hypervisor)`` from ``<proc>/cpuinfo``.

    The model is the first ``model name`` line, whitespace-collapsed, exactly
    as the host benchmark tool records it. ``hypervisor`` is the CPU flag.
    """
    lines = util.read_lines(os.path.join(proc_root, "cpuinfo"))
    if lines is None:
        return (None, None, None)
    model: Optional[str] = None
    processors = 0
    hypervisor = False
    for line in lines:
        key, _, value = line.partition(":")
        key = key.strip()
        if key == "processor":
            processors += 1
        elif key == "model name" and model is None:
            model = normalise_model(value)
        elif key == "flags" and "hypervisor" in value.split():
            hypervisor = True
    return (model, processors or None, hypervisor if processors else None)


def _parse_cpu_list(text: str) -> Optional[int]:
    count = 0
    for part in text.strip().split(","):
        low, dash, high = part.partition("-")
        try:
            start = int(low)
            end = int(high) if dash else start
        except ValueError:
            return None
        if end < start:
            return None
        count += end - start + 1
    return count


def affinity_cpus(proc_root: str) -> Optional[int]:
    """CPUs the daemon may run on (``Cpus_allowed_list`` of ``<proc>/self/status``)."""
    lines = util.read_lines(os.path.join(proc_root, "self", "status")) or []
    for line in lines:
        if line.startswith("Cpus_allowed_list:"):
            return _parse_cpu_list(line.split(":", 1)[1])
    return None


def quota_cpus(cgroup: str, cgroup_root: str) -> Optional[float]:
    """Tightest ``cpu.max`` quota/period over *cgroup* and its ancestors."""
    best: Optional[float] = None
    for path in util.ancestors(cgroup):
        quota, period = util.parse_cpu_max(
            util.read_text(os.path.join(cgroup_root, path.lstrip("/"), "cpu.max"))
        )
        if quota is None or not period:
            continue
        value = quota / period
        best = value if best is None else min(best, value)
    return best


def cpus_available(
    proc_root: str, cgroup_root: str, cgroup: Optional[str]
) -> Optional[float]:
    """Affinity intersected with the cgroup ``cpu.max`` quota; ``None`` when neither is known."""
    affinity = affinity_cpus(proc_root)
    quota = quota_cpus(cgroup, cgroup_root) if cgroup else None
    known = [v for v in (affinity, quota) if v is not None]
    return round(min(known), 3) if known else None


def smt_active(sys_root: str) -> Optional[bool]:
    """SMT as the kernel reports it (informational; unreliable in VMs)."""
    value = util.read_int(os.path.join(sys_root, "devices/system/cpu/smt/active"))
    return None if value is None else bool(value)


# ── the host benchmark record ───────────────────────────────────────────────

def load_host_benchmark(path: str = "") -> Loaded:
    """Read the persisted host record. Only *path* is opened (never the claim file)."""
    path = path or DEFAULT_BENCHMARK_PATH
    text = util.read_text(path)
    if text is None:
        return ("absent", None)
    try:
        record = json.loads(text)
    except ValueError:
        return ("invalid", None)
    if (
        not isinstance(record, dict)
        or record.get("schema") != BENCHMARK_SCHEMA
        or record.get("tool") != "geekbench"
        or record.get("tool_major") not in BASELINE_SCORE
    ):
        return ("invalid", None)
    return ("ok", record)


def _measured_speed(record: Dict[str, Any]) -> Dict[str, Any]:
    major = record["tool_major"]
    measured = record.get("measured_at")
    day = _ISO_DAY_RE.match(measured) if isinstance(measured, str) else None
    multi = record.get("multi_core")
    version = record.get("tool_version")
    cpus = record.get("cpus_online")
    return {
        "basis": f"geekbench{major}-measured",
        "geekbench_major": major,
        "geekbench_version": version if isinstance(version, str) else None,
        "baseline_score": BASELINE_SCORE[major],
        "single_core": record["single_core"],
        "multi_core": multi if is_score(multi) else None,
        "cpus_at_measurement": cpus if type(cpus) is int else None,
        "measured_on": day.group(1) if day else None,
    }


# ── the fallback table ──────────────────────────────────────────────────────

def load_scores_table(text: Optional[str] = None) -> List[Dict[str, Any]]:
    """Valid entries of the packaged ``cgprofile-scores/1`` table (invalid ones are skipped)."""
    if text is None:
        text = (
            importlib.resources.files("cgprofile")
            .joinpath("data", SCORES_RESOURCE)
            .read_text(encoding="utf-8")
        )
    doc = json.loads(text)
    if doc.get("schema") != SCORES_SCHEMA:
        raise ValueError(f"scores table schema is not {SCORES_SCHEMA}")
    return [entry for entry in doc.get("entries", []) if valid_entry(entry)]


def valid_entry(entry: Any) -> bool:
    return (
        isinstance(entry, dict)
        and normalise_model(entry.get("cpu_model")) is not None
        and entry.get("geekbench_major") in BASELINE_SCORE
        and is_score(entry.get("single_core"))
        and isinstance(entry.get("source_url"), str)
        and entry["source_url"].startswith("https://")
        and isinstance(entry.get("retrieved"), str)
        and _DATE_RE.fullmatch(entry["retrieved"]) is not None
    )


def table_speed(
    model: Optional[str], prefer_major: Optional[int], entries: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Single-core table lookup keyed by model and major.

    The preferred major (that of a stale host record) wins when the table has
    it; otherwise the newest major the table has for the model.
    """
    if model is None:
        return None
    hits = [e for e in entries if normalise_model(e["cpu_model"]) == model]
    if not hits:
        return None
    hits.sort(key=lambda e: (e["geekbench_major"] == prefer_major, e["geekbench_major"]))
    hit = hits[-1]
    major = hit["geekbench_major"]
    return {
        "basis": f"geekbench{major}-table",
        "geekbench_major": major,
        "geekbench_version": None,
        "baseline_score": BASELINE_SCORE[major],
        "single_core": hit["single_core"],
        "multi_core": None,
        "cpus_at_measurement": None,
        "measured_on": None,
        "source_url": hit["source_url"],
        "retrieved": hit["retrieved"],
    }


def resolve_speed(
    loaded: Loaded,
    model: Optional[str],
    cpus_online: Optional[int],
    entries: List[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """``(speed, reason)``: the host record when valid and matching, else the table, else ``(None, reason)``."""
    status, record = loaded
    prefer: Optional[int] = None
    if status == "ok" and record is not None:
        prefer = record["tool_major"]
        if not is_score(record.get("single_core")):
            reason = REASON_INCOMPLETE
        elif (
            model is None
            or cpus_online is None
            or normalise_model(record.get("cpu_model")) != model
            or record.get("cpus_online") != cpus_online
        ):
            reason = REASON_STALE
        else:
            return (_measured_speed(record), None)
    elif status == "invalid":
        reason = REASON_INVALID
    else:
        reason = REASON_NO_MEASUREMENT
    speed = table_speed(model, prefer, entries)
    if speed is None:
        return (None, reason + NOT_IN_TABLE)
    return (speed, reason)


def build_machine(
    *,
    loaded: Loaded,
    entries: List[Dict[str, Any]],
    proc_root: str,
    cgroup_root: str,
    sys_root: str,
    cgroup: Optional[str] = None,
) -> Dict[str, Any]:
    """The ``machine/1`` object for the host, or for a lane when *cgroup* is given."""
    model, online, hypervisor = read_cpuinfo(proc_root)
    speed, reason = resolve_speed(loaded, model, online, entries)
    return {
        "schema": MACHINE_SCHEMA,
        "cpu_model": model,
        "cpus_online": online,
        "cpus_available": cpus_available(proc_root, cgroup_root, cgroup),
        "hypervisor": hypervisor,
        "smt": smt_active(sys_root),
        "speed": speed,
        "speed_reason": reason,
    }
