"""Schemas, atomic IO, identity, ``check`` and ``show`` (IOB-RES-01..04, IOB-CHK-01..04, IOB-STA-05)."""
from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass, field
from typing import Any, Mapping

from . import ceilings, guard, proposals
from .errors import Refusal
from .host import Host

SCHEMA = 1
SCHEMA_MINOR = 0
DEFAULT_DIR = "/var/lib/iobench"
NOT_MEASURED = "not_measured"
REQUIRED_TOP = ("schema", "schema_minor", "tool", "run", "identity", "targets", "evidence", "proposals", "quality", "restore")
REQUIRED_CAL_TOP = ("schema", "schema_minor", "tool", "run", "identity", "quality", "calibration")
STRICT_DISK = ("devno", "partuuid", "size_bytes")
OPTIONAL_STRICT = ("model", "serial", "wwn")
AGE_WARN_DAYS = 180
EXIT = {"current": 0, "stale": 3, "absent": 4, "corrupt": 5, "aborted_only": 6, "current_unproven": 7}
LOAD_REASONS = ("corrupt", "schema_unsupported", "unreadable", "calibration_missing")
SHOW_WHAT = ("proposals", "evidence", "calibration", "ceilings", "paths")


class LoadError(Exception):
    def __init__(self, reason: str, path: str) -> None:
        self.reason, self.path = reason, path
        super().__init__(f"{reason}: {path}")


def paths(state_dir: str) -> dict[str, str]:
    return {"dir": state_dir, "result": posixpath.join(state_dir, "result.json"),
            "calibration": posixpath.join(state_dir, "calibration.json"),
            "ceilings": posixpath.join(state_dir, "ceilings.jsonl"),
            "result_failed": posixpath.join(state_dir, "result.last-failed.json"),
            "calibration_failed": posixpath.join(state_dir, "calibration.last-failed.json")}


def state_dir_of(host: Host, state_dir: str | None) -> str:
    """``--state-dir``, else ``IOBENCH_DIR``, else ``/var/lib/iobench`` (IOB-CHK-04)."""
    return state_dir or host.getenv("IOBENCH_DIR") or DEFAULT_DIR


def dumps(doc: Mapping[str, Any]) -> str:
    return json.dumps(doc, indent=1, sort_keys=True) + "\n"


def write_json(host: Host, path: str, doc: Mapping[str, Any]) -> None:
    """Atomic: temp + rename + fsync of file and directory (``Host.write_atomic``), mode 0644."""
    host.makedirs(posixpath.dirname(path))
    host.write_atomic(path, dumps(doc), 0o644)


def failed_path(path: str) -> str:
    stem, ext = posixpath.splitext(path)
    return f"{stem}.last-failed{ext}"


def write_failed(host: Host, path: str, doc: Mapping[str, Any]) -> None:
    """An aborted or failed run never replaces the good record (IOB-SAF-07): it writes ``*.last-failed.json``."""
    write_json(host, failed_path(path), doc)


def opt(doc: Mapping[str, Any], key: str) -> Any:
    """A missing newer optional key is ``not_measured``, never a default (IOB-RES-02)."""
    return doc.get(key, NOT_MEASURED)


def load(host: Host, path: str, required: tuple[str, ...]) -> dict[str, Any]:
    if not host.exists(path):
        raise LoadError("absent", path)
    try:
        text = host.read_text(path)
    except OSError as exc:
        raise LoadError("unreadable", path) from exc
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise LoadError("corrupt", path) from exc
    if not isinstance(doc, dict) or any(k not in doc for k in ("schema", "schema_minor")):
        raise LoadError("corrupt", path)
    if doc["schema"] != SCHEMA:
        raise LoadError("schema_unsupported", path)
    if any(k not in doc for k in required):
        raise LoadError("corrupt", path)
    return doc


# -- identity ---------------------------------------------------------------------------------------
def kernel_mm(host: Host) -> str:
    return ".".join(host.read_text("/proc/sys/kernel/osrelease").strip().split(".")[:2])


def mem_total(host: Host) -> int:
    line = next(ln for ln in host.read_text("/proc/meminfo").splitlines() if ln.startswith("MemTotal:"))
    return int(line.split()[1]) * 1024


def root_source(host: Host) -> str:
    return next(e.source for e in guard.parse_mountinfo(host.read_text("/proc/self/mountinfo")) if e.mountpoint == "/")


def s0_identity(host: Host, target: guard.Target, workdir_path: str | None) -> dict[str, Any]:
    """The S0 identity record (IOB-RES-03); also what ``check`` re-reads live."""
    if target.kind == "device":
        disk = {"devno": target.devno, "model": target.model, "serial": target.serial, "wwn": target.wwn,
                "partuuid": target.partuuid, "size_bytes": target.size_bytes,
                "scheduler": guard.disk_attr(host, target.disk_name, "queue/scheduler"),
                "rotational_flag": guard.disk_attr(host, target.disk_name, "queue/rotational")}
        tgt = {"kind": "device", "path": target.path}
    else:
        fs = guard.fs_info(host, posixpath.dirname(target.path))
        disk = {"devno": fs.devno, "model": None, "serial": None, "wwn": None, "partuuid": None, "size_bytes": target.size_bytes,
                "scheduler": None, "rotational_flag": None}
        tgt = {"kind": "file", "path": target.path, "fs_uuid": target.fs_uuid, "inode": target.inode}
    wd = None
    if workdir_path is not None:
        fs = guard.fs_info(host, workdir_path)
        wd = {"path": workdir_path, "type": fs.fstype, "uuid": fs.uuid}
    return {"target": tgt, "disk": disk, "identity_strength": "strong" if (disk["serial"] or disk["wwn"]) else "weak",
            "root_source": root_source(host), "workdir_fs": wd, "kernel": kernel_mm(host), "ram_bytes": mem_total(host),
            "virt": "unknown"}


def live_identity(host: Host, stored: Mapping[str, Any]) -> dict[str, Any] | None:
    """Identity of the stored target as it is now; ``None`` if the target cannot be found at all."""
    path = stored["target"]["path"]
    try:
        target = guard.load_target(host, path)
    except (Refusal, OSError, KeyError, ValueError):
        # a vanished device / replaced file is a changed identity (stale), not an unreadable result
        return None
    wd = stored.get("workdir_fs")
    return s0_identity(host, target, wd["path"] if wd else None)


def identity_differs(stored: Mapping[str, Any], live: Mapping[str, Any] | None) -> bool:
    """IOB-RES-03: strict fields only (devno, partuuid, size and each non-null model/serial/wwn; the file target tuple)."""
    if live is None:
        return True
    st, lv = stored["target"], live["target"]
    if any(st.get(k) != lv.get(k) for k in ("kind", "fs_uuid", "inode")):
        return True
    sd, ld = stored["disk"], live["disk"]
    if any(sd.get(k) != ld.get(k) for k in STRICT_DISK):
        return True
    return any(sd.get(k) is not None and sd[k] != ld.get(k) for k in OPTIONAL_STRICT)


def advisory_warnings(stored: Mapping[str, Any], live: Mapping[str, Any], started: float | None, now: float) -> list[str]:
    out = []
    if stored["disk"].get("scheduler") != live["disk"].get("scheduler"):
        out.append("scheduler_changed")
    if stored.get("ram_bytes") != live.get("ram_bytes"):
        out.append("ram_changed")
    if stored.get("kernel") != live.get("kernel"):
        out.append("kernel_changed")
    if stored.get("root_source") != live.get("root_source"):
        out.append("root_source_changed")
    if (stored.get("workdir_fs") or {}).get("uuid") != (live.get("workdir_fs") or {}).get("uuid"):
        out.append("workdir_fs_changed")
    if started is not None and now - started > AGE_WARN_DAYS * 86400:
        out.append("older_than_180_days")
    return out


# -- check -------------------------------------------------------------------------------------------
@dataclass
class CheckResult:
    state: str
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)
    identity_strength: str | None = None
    sections: dict[str, Any] = field(default_factory=dict)
    result: str | None = None
    calibration_id: str | None = None
    history: str = "single-run"

    @property
    def exit_code(self) -> int:
        return EXIT[self.state]

    def as_dict(self) -> dict[str, Any]:
        return {"state": self.state, "reasons": self.reasons, "warnings": self.warnings, "limits": self.limits,
                "identity_strength": self.identity_strength, "sections": self.sections, "result": self.result,
                "calibration_id": self.calibration_id, "history": self.history}


def check(host: Host, state_dir: str, *, calibration: bool = False, sections: tuple[str, ...] | None = None, now: float,
          require_strong_identity: bool = False) -> CheckResult:
    """States and exit codes exactly per the tables of IOB-STA-05."""
    p = paths(state_dir)
    path = p["calibration" if calibration else "result"]
    failed = p["calibration_failed" if calibration else "result_failed"]
    required = REQUIRED_CAL_TOP if calibration else REQUIRED_TOP
    asked = sections or proposals.SECTIONS
    try:
        doc = load(host, path, required)
    except LoadError as exc:
        if exc.reason == "absent":
            return CheckResult("aborted_only" if host.exists(failed) else "absent", result=path)
        return CheckResult("corrupt", [exc.reason], result=path)
    calibration_id = doc["calibration"]["id"] if calibration else doc["run"].get("calibration_id")
    cal_doc = doc
    if not calibration and calibration_id is not None:
        try:
            cal_doc = load(host, p["calibration"], REQUIRED_CAL_TOP)
            matching = cal_doc["calibration"]["id"] == calibration_id
        except LoadError:
            matching = False
        if not matching:
            return CheckResult("corrupt", ["calibration_missing"], result=path, calibration_id=calibration_id)
    live = live_identity(host, doc["identity"])
    res = CheckResult("current", result=path, calibration_id=calibration_id,
                      identity_strength=doc["identity"]["identity_strength"], limits=list(doc["quality"]["limits"]))
    if identity_differs(doc["identity"], live):
        res.state = "stale"
        return res
    res.warnings = advisory_warnings(doc["identity"], live, ceilings.parse_iso(doc["run"]["started"]), now)
    rows, _ = ceilings.load(host, p["ceilings"], ceilings.identity_digest(doc["identity"]))
    res.history = "recorded" if rows else "single-run"
    if "calibration" in cal_doc and ceilings.recalibrate_recommended(rows, cal_doc["calibration"].get("plateau_bps", {}), now):
        res.warnings.append("recalibrate_recommended")
    if host.exists(failed):
        try:
            newer = load(host, failed, ())["run"]["started"] > doc["run"]["started"]  # ISO-8601 Z strings sort in time order
        except LoadError:
            newer = True  # an unreadable failure record cannot be shown to be old
        if newer:
            res.warnings.append("last_run_aborted")
    group = doc["calibration"]["proposals"] if calibration else doc["proposals"]
    weak = require_strong_identity and doc["identity"]["identity_strength"] == "weak"
    unproven = False
    for name in asked:
        summary = proposals.section_summary(group.get(name, {}), identity_weak=weak)
        res.sections[name] = summary
        unproven = unproven or bool(summary["unproven"] or summary["absent"])
    res.state = "current_unproven" if unproven else "current"
    return res


# -- show ----------------------------------------------------------------------------------------------
def show(host: Host, state_dir: str, what: str, *, section: str | None = None, now: float) -> dict[str, Any]:
    """Print an object without recomputing or modifying it (IOB-CHK-02).  Raises ``LoadError`` / ``Refusal``."""
    if what not in SHOW_WHAT:
        raise Refusal("show_what", f"unknown show target {what!r}", f"use one of {', '.join(SHOW_WHAT)}")
    if section is not None and section not in proposals.SECTIONS:
        raise Refusal("show_section", f"unknown section {section!r}", f"use one of {', '.join(proposals.SECTIONS)}")
    p = paths(state_dir)
    if what == "paths":
        return p
    if what == "ceilings":
        text = host.read_text(p["ceilings"]) if host.exists(p["ceilings"]) else None
        if text is None:
            raise LoadError("absent", p["ceilings"])
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        return {"header": rows[0], "observations": rows[1:], "summary": ceilings.summary(rows[1:], now)}
    doc = load(host, p["calibration" if what == "calibration" else "result"], REQUIRED_CAL_TOP if what == "calibration" else REQUIRED_TOP)
    live = live_identity(host, doc["identity"])
    stale = identity_differs(doc["identity"], live)
    if what == "calibration":
        body = doc["calibration"]
    elif what == "evidence":
        body = doc["evidence"]
    else:
        body = doc["proposals"]
        if section is not None:
            body = body[section]
    return {"object": body, "stale": stale}
