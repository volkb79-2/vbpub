"""Append-only JSONL journal with a schema version, plus the small atomic state file.

One file per UTC month (`journal-YYYY-MM.jsonl`), never rewritten, never truncated by this tool. Every line is one JSON
object: {"v": 1, "ts": ISO-8601 UTC, "t": epoch seconds, "type": ..., ...}. Readers skip torn last lines and records
of an unknown major version instead of failing, and count what they skipped.
"""

from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
from typing import Callable, Iterable, Iterator

from . import SCHEMA_VERSION
from . import safeio

MAX_LINE_BYTES = 48 * 1024

# type -> required fields (besides v, ts, t, type, run). The schema is documented in docs/IO-GOVERNOR-V1-DESIGN.md.
REQUIRED: dict[str, tuple[str, ...]] = {
    "run_start": ("kind", "version", "mode", "settings_epoch"),
    "run_end": ("kind", "status", "duration_s", "io"),
    "skip": ("kind", "reasons"),
    "probe": ("lane", "device", "quiet", "n", "zero", "lat_us", "hist", "direct"),
    "bench": ("status", "region", "lanes", "settings_epoch"),
    "passive": ("devices", "pressure"),
    "recommendation": ("lane", "kind", "text", "evidence"),
    "note": ("text",),
    "harm": ("kind", "evidence"),
}


def utc_iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def validate_record(rec: dict) -> list[str]:
    problems = []
    if not isinstance(rec, dict):
        return ["record is not an object"]
    if rec.get("v") != SCHEMA_VERSION:
        problems.append(f"v must be {SCHEMA_VERSION}")
    for key in ("ts", "t", "type"):
        if key not in rec:
            problems.append(f"missing {key}")
    typ = rec.get("type")
    if typ not in REQUIRED:
        problems.append(f"unknown type {typ!r}")
    else:
        for key in REQUIRED[typ]:
            if key not in rec:
                problems.append(f"{typ}: missing {key}")
    return problems


class Journal:
    def __init__(self, directory: str, wall: Callable[[], float], run_id: str | None = None):
        self.directory = directory
        self.wall = wall
        self.run_id = run_id

    def path_for(self, t: float) -> str:
        month = dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m")
        return os.path.join(self.directory, f"journal-{month}.jsonl")

    def append(self, type: str, **fields) -> dict:
        t = self.wall()
        rec = {"v": SCHEMA_VERSION, "ts": utc_iso(t), "t": round(t, 3), "type": type}
        if self.run_id:
            rec["run"] = self.run_id
        rec.update(fields)
        problems = validate_record(rec)
        if problems:
            raise ValueError("invalid journal record: " + "; ".join(problems))
        line = json.dumps(rec, separators=(",", ":"), sort_keys=True) + "\n"
        data = line.encode()
        if len(data) > MAX_LINE_BYTES:
            raise ValueError(f"journal record of {len(data)} bytes exceeds {MAX_LINE_BYTES}")
        safeio.ensure_dir(self.directory)
        fd = safeio.open_file(self.path_for(t), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640, private=False)
        try:
            n = os.write(fd, data)  # one write(2) per record: O_APPEND keeps concurrent runs from interleaving lines
            if n != len(data):
                raise OSError(f"short journal write ({n} of {len(data)} bytes)")
        finally:
            os.close(fd)
        return rec


class ReadStats:
    def __init__(self) -> None:
        self.records = 0
        self.torn = 0
        self.unknown_version = 0
        self.invalid = 0


def journal_files(path: str) -> list[str]:
    if os.path.isdir(path):
        return sorted(glob.glob(os.path.join(path, "journal-*.jsonl")))
    return [path]


def read_records(path: str | Iterable[str], stats: ReadStats | None = None, since: float | None = None) -> Iterator[dict]:
    stats = stats or ReadStats()
    files = journal_files(path) if isinstance(path, str) else list(path)
    for name in files:
        try:
            fh = os.fdopen(safeio.open_file(name, os.O_RDONLY, private=False), "r", encoding="utf-8", errors="replace")
        except (FileNotFoundError, safeio.UnsafePath):
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    stats.torn += 1
                    continue
                if not isinstance(rec, dict) or not isinstance(rec.get("v"), int):
                    stats.invalid += 1
                    continue
                if rec["v"] != SCHEMA_VERSION:
                    stats.unknown_version += 1
                    continue
                if validate_record(rec):
                    stats.invalid += 1
                    continue
                if since is not None and rec["t"] < since:
                    continue
                stats.records += 1
                yield rec


# ---------------------------------------------------------------------------------------------------------
# state file (small JSON, atomic replace)
# ---------------------------------------------------------------------------------------------------------


def load_state(path: str) -> dict:
    return safeio.load_json(path)


def save_state(path: str, state: dict) -> None:
    safeio.save_json(path, state, lambda n: os.urandom(n).hex())


# ---------------------------------------------------------------------------------------------------------
# retention
# ---------------------------------------------------------------------------------------------------------


def prune(directory: str, now: float, max_age_days: float, max_bytes: int, dry_run: bool = False) -> list[str]:
    """Delete whole monthly files that are entirely older than max_age_days, then oldest-first while the total exceeds
    max_bytes. The current month is never deleted. Returns the names removed (or that would be)."""

    files = journal_files(directory) if os.path.isdir(directory) else []
    cur = dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y-%m")
    info = []
    for f in files:
        m = re.fullmatch(r"journal-(\d{4})-(\d{2})\.jsonl", os.path.basename(f))
        if not m or m.group(0)[8:15] == cur:
            continue
        y, mo = int(m.group(1)), int(m.group(2))
        end = dt.datetime(y + (mo == 12), 1 if mo == 12 else mo + 1, 1, tzinfo=dt.timezone.utc).timestamp()
        info.append((end, f, os.stat(f).st_size))
    total = sum(os.stat(f).st_size for f in files)
    removed = []
    for end, f, size in sorted(info):
        if now - end > max_age_days * 86400 or total > max_bytes:
            removed.append(os.path.basename(f))
            total -= size
            if not dry_run:
                os.unlink(f)
    return removed
