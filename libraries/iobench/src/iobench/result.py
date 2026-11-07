"""Schemas, atomic IO, identity, ``check`` and ``show`` (IOB-RES-01..04, IOB-CHK-01..04, IOB-STA-05)."""
from __future__ import annotations

import hashlib
import json
import posixpath
from dataclasses import dataclass, field
from typing import Any, Mapping

from . import capability, ceilings, guard, proposals
from .errors import Refusal
from .host import Host

SCHEMA = 1
SCHEMA_MINOR = 2  # 1: disk-scope identity / cache plausibility / veryquick; 2: read-cache evidence hints and capability history
DEFAULT_DIR = "/var/lib/iobench"
NOT_MEASURED = "not_measured"
REQUIRED_TOP = ("schema", "schema_minor", "tool", "run", "identity", "targets", "evidence", "proposals", "quality", "restore")
REQUIRED_CAL_TOP = ("schema", "schema_minor", "tool", "run", "identity", "quality", "calibration")
OPTIONAL_STRICT = ("model", "serial", "wwn")  # of the disk scope: each one that is non-null in the stored record must still match
AGE_WARN_DAYS = 180
EXIT = {"current": 0, "stale": 3, "absent": 4, "corrupt": 5, "aborted_only": 6, "current_unproven": 7}
LOAD_REASONS = ("corrupt", "schema_unsupported", "unreadable", "calibration_missing", "calibration_superseded", "damaged_proposals")
SHOW_WHAT = ("proposals", "evidence", "calibration", "ceilings", "paths")


class LoadError(Exception):
    def __init__(self, reason: str, path: str) -> None:
        self.reason, self.path = reason, path
        super().__init__(f"{reason}: {path}")


def paths(state_dir: str) -> dict[str, str]:
    return {"dir": state_dir, "result": posixpath.join(state_dir, "result.json"),
            "calibration": posixpath.join(state_dir, "calibration.json"),
            "ceilings": posixpath.join(state_dir, "ceilings.jsonl"),
            "capability": posixpath.join(state_dir, "capability.jsonl"),
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
    minor = doc["schema_minor"]
    if doc["schema"] != SCHEMA or not isinstance(minor, int) or isinstance(minor, bool) or minor < 0 or minor > SCHEMA_MINOR:
        raise LoadError("schema_unsupported", path)  # IOB-RES-02: any minor <= the reader's; a newer one may carry statuses it cannot judge
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


def machine_id_hash(host: Host) -> str | None:
    """A hash of ``/etc/machine-id`` (never the id itself): what binds a serial-less disk to its host.

    ``None`` when there is no usable id (missing, empty, or systemd's ``uninitialized`` placeholder of a first boot in an installer
    chroot): such a record is then not compared on the host, so the real id that appears at first boot does not make it stale."""
    try:
        text = host.read_text("/etc/machine-id").strip()
    except OSError:
        return None
    return hashlib.sha256(("iobench-machine-id:" + text).encode()).hexdigest() if text not in ("", "uninitialized") else None


def disk_scope(host: Host, disk: guard.DiskFacts) -> dict[str, Any]:
    """The disk-scope identity (IOB-CEI-06): the whole disk, never a partition.  ``name`` is only a hint for finding the disk again."""
    strong = bool(disk.serial or disk.wwn)
    scope = {"model": disk.model, "serial": disk.serial, "wwn": disk.wwn, "size_bytes": disk.size_bytes,
             "strength": "strong" if strong else "weak", "machine_id_hash": None if strong else machine_id_hash(host), "name": disk.name}
    scope["key"] = ceilings.scope_key(scope)
    return scope


def _workdir_fs(host: Host, path: str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    fs = guard.fs_info(host, path)
    return {"path": path, "type": fs.fstype, "uuid": fs.uuid}


def _identity(host: Host, tgt: dict[str, Any] | None, disk: dict[str, Any], scope: dict[str, Any] | None, wd: dict[str, Any] | None) -> dict[str, Any]:
    return {"target": tgt, "disk": disk, "disk_scope": scope, "identity_strength": scope["strength"] if scope else "weak",
            "root_source": root_source(host), "workdir_fs": wd, "kernel": kernel_mm(host), "ram_bytes": mem_total(host), "virt": "unknown"}


def s0_identity(host: Host, target: guard.Target, workdir_path: str | None) -> dict[str, Any]:
    """The S0 identity record (IOB-RES-03, IOB-CEI-06); also what ``check`` re-reads live.

    ``disk`` keeps what is true of the measured partition or file (its devno, PARTUUID and size: fields of the observation);
    ``disk_scope`` is the whole disk behind it and is the only thing that decides whether a result is still current."""
    if target.kind == "device":
        disk = {"devno": target.devno, "model": target.model, "serial": target.serial, "wwn": target.wwn,
                "partuuid": target.partuuid, "size_bytes": target.size_bytes,
                "scheduler": guard.disk_attr(host, target.disk_name, "queue/scheduler"),
                "rotational_flag": guard.disk_attr(host, target.disk_name, "queue/rotational")}
        tgt = {"kind": "device", "path": target.path}
        disk_name = target.disk_name
    else:
        fs = guard.fs_info(host, posixpath.dirname(target.path))
        disk = {"devno": fs.devno, "model": None, "serial": None, "wwn": None, "partuuid": None, "size_bytes": target.size_bytes,
                "scheduler": None, "rotational_flag": None}
        tgt = {"kind": "file", "path": target.path, "fs_uuid": target.fs_uuid, "inode": target.inode}
        disk_name = guard.backing_disk(host, target.path)
    scope = disk_scope(host, guard.disk_facts(host, disk_name)) if disk_name else None
    return _identity(host, tgt, disk, scope, _workdir_fs(host, workdir_path))


def live_identity(host: Host, stored: Mapping[str, Any]) -> dict[str, Any] | None:
    """Identity of the stored measurement as it is now; ``None`` if the disk cannot be found at all.

    The target is read first (it still carries the partition facts). A target that is gone (a throwaway partition, a
    formatted data partition, a deleted file) is not a changed identity when its disk can still be identified (IOB-CEI-06).
    Strong scopes use stored identifiers and size; weak scopes require the remembered device name, equal model and size, and a
    candidate that remains weak. A known stored machine-id hash must match a live non-null hash; a null stored hash binds no host.
    Only a disk that cannot be found, or that differs, is stale."""
    path = stored["target"]["path"]
    wd = stored.get("workdir_fs")
    wd_path = wd["path"] if wd else None
    try:
        target = guard.load_target(host, path)
    except (Refusal, OSError, KeyError, ValueError):
        target = None
    try:
        wd_live = _workdir_fs(host, wd_path)
    except (OSError, ValueError):
        wd_live = None  # the work dir is removed after a run: nothing to compare
    if target is not None:
        return {**s0_identity(host, target, None), "workdir_fs": wd_live}
    stored_scope = stored.get("disk_scope")
    if stored_scope is None:
        return None  # a record from before the disk scope: nothing to find the disk with
    stored_machine_id_hash = stored_scope.get("machine_id_hash")
    live_machine_id_hash = machine_id_hash(host)
    facts = guard.find_disk(host, model=stored_scope["model"], serial=stored_scope["serial"], wwn=stored_scope["wwn"],
                            size_bytes=stored_scope["size_bytes"], hint=stored_scope.get("name"),
                            machine_id_matches=(stored_machine_id_hash is None
                                                or stored_machine_id_hash == live_machine_id_hash))
    if facts is None:
        return None
    disk = {"devno": None, "model": facts.model, "serial": facts.serial, "wwn": facts.wwn, "partuuid": None, "size_bytes": None,
            "scheduler": facts.scheduler, "rotational_flag": facts.rotational}
    return _identity(host, None, disk, disk_scope(host, facts), wd_live)


def identity_differs(stored: Mapping[str, Any], live: Mapping[str, Any] | None) -> bool:
    """IOB-RES-03: the disk scope only: whole-disk size, each stored non-null model/serial/wwn, weak-to-strong changes, and host id.

    The partition (devno, PARTUUID, size) or file (filesystem uuid, inode) that was measured is no part of it: it may be gone
    (``target_changed`` warns), the disk it was on is what the result describes."""
    if live is None:
        return True
    ss, ls = stored.get("disk_scope"), live.get("disk_scope")
    if ss is None or ls is None:
        return True  # a record from before the disk scope cannot be shown to describe this disk
    if ss["size_bytes"] != ls["size_bytes"] or any(ss.get(k) is not None and ss[k] != ls.get(k) for k in OPTIONAL_STRICT):
        return True
    if ss["strength"] == "weak" and ls["strength"] == "strong":
        return True
    if ss["strength"] != "weak" or ls["strength"] != "weak":
        return False
    # A null stored hash binds no host, including after a chroot's first boot. A known stored hash requires
    # the live host to have the same known id; missing or uninitialized now means stale.
    stored_machine_id_hash = ss.get("machine_id_hash")
    live_machine_id_hash = ls.get("machine_id_hash")
    return stored_machine_id_hash is not None and stored_machine_id_hash != live_machine_id_hash


def target_changed(stored: Mapping[str, Any], live: Mapping[str, Any]) -> bool:
    """Whether the partition or file that was measured is no longer the same (or is gone): advisory, the disk still describes it."""
    st, lt = stored["target"], live["target"]
    if lt is None:
        return True
    if any(st.get(k) != lt.get(k) for k in ("kind", "fs_uuid", "inode")):
        return True
    return any(stored["disk"].get(k) != live["disk"].get(k) for k in ("devno", "partuuid", "size_bytes"))


WARNINGS = ("scheduler_changed", "ram_changed", "kernel_changed", "root_source_changed", "workdir_fs_changed", "target_changed",
            "older_than_180_days", "recalibrate_recommended", "last_run_aborted")


def advisory_warnings(stored: Mapping[str, Any], live: Mapping[str, Any], started: float | None, now: float) -> list[str]:
    out = []
    if target_changed(stored, live):
        out.append("target_changed")
    if stored["disk"].get("scheduler") != live["disk"].get("scheduler"):
        out.append("scheduler_changed")
    if stored.get("ram_bytes") != live.get("ram_bytes"):
        out.append("ram_changed")
    if stored.get("kernel") != live.get("kernel"):
        out.append("kernel_changed")
    if stored.get("root_source") != live.get("root_source"):
        out.append("root_source_changed")
    live_wd = live.get("workdir_fs")
    if live_wd is not None and (stored.get("workdir_fs") or {}).get("uuid") != live_wd.get("uuid"):
        out.append("workdir_fs_changed")  # a work dir that is gone (removed after the run) has nothing to compare
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
    capability: dict[str, Any] | None = None

    @property
    def exit_code(self) -> int:
        return EXIT[self.state]

    def as_dict(self) -> dict[str, Any]:
        return {"state": self.state, "reasons": self.reasons, "warnings": self.warnings, "limits": self.limits,
                "identity_strength": self.identity_strength, "sections": self.sections, "result": self.result,
                "calibration_id": self.calibration_id, "history": self.history, "capability": self.capability}


def check(host: Host, state_dir: str, *, calibration: bool = False, sections: tuple[str, ...] | None = None, now: float,
          require_strong_identity: bool = False, result_path: str | None = None) -> CheckResult:
    """States and exit codes exactly per the tables of IOB-STA-05 (``result_path`` is ``--result P``)."""
    p = paths(state_dir)
    path = result_path or p["calibration" if calibration else "result"]
    failed = p["calibration_failed" if calibration else "result_failed"]
    required = REQUIRED_CAL_TOP if calibration else REQUIRED_TOP
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
        except LoadError:
            return CheckResult("corrupt", ["calibration_missing"], result=path, calibration_id=calibration_id)
        if cal_doc["calibration"]["id"] != calibration_id:  # a valid calibration exists, but a newer run replaced the one this result used
            return CheckResult("corrupt", ["calibration_superseded"], result=path, calibration_id=calibration_id)
    live = live_identity(host, doc["identity"])
    res = CheckResult("current", result=path, calibration_id=calibration_id,
                      identity_strength=doc["identity"]["identity_strength"], limits=list(doc["quality"]["limits"]))
    try:
        digest = ceilings.identity_digest(doc["identity"])
    except Refusal:
        capability_rows, capability_warnings = [], []
    else:
        capability_rows, capability_warnings = capability.load(host, p["capability"], digest)
    res.capability = capability.latest_summary(capability_rows)
    res.warnings.extend(capability_warnings)
    if identity_differs(doc["identity"], live):
        res.state = "stale"
        return res
    res.warnings.extend(advisory_warnings(doc["identity"], live, ceilings.parse_iso(doc["run"]["started"]), now))
    rows, _ = ceilings.load(host, p["ceilings"], ceilings.identity_digest(doc["identity"]))
    res.history = ceilings.summary(rows, now)["history"]
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
    kind_sections = proposals.CALIBRATION_SECTIONS if calibration else proposals.SECTIONS
    if sections:
        for name in sections:
            if name not in proposals.SECTIONS:
                raise Refusal("unknown_section", f"unknown section {name!r}", f"use one of {', '.join(proposals.SECTIONS)}")
            if name not in kind_sections:
                raise Refusal("section_not_in_document", f"a calibration.json has no {name!r} section", "check result.json for writeback")
        asked = sections
    else:
        asked = kind_sections
    unproven = False
    try:
        for name in asked:
            summary = proposals.section_report(name, group.get(name), identity_weak=weak)
            res.sections[name] = summary
            unproven = unproven or bool(summary["unproven"] or summary["absent"])
    except (ValueError, KeyError, TypeError, AttributeError):
        return CheckResult("corrupt", ["damaged_proposals"], result=path, calibration_id=calibration_id)
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
