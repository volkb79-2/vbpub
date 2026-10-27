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

MAX_LINE_BYTES = 48 * 1024  # a record is refused at write time beyond this
# Read-side bounds (the journal is the service user's; a reader run by root must survive a damaged or hostile file):
MAX_LINE_READ = 1024 * 1024  # a line longer than this is damage: counted as `oversized`, its remainder discarded in bounded chunks
MAX_FILE_BYTES = 256 * 1024 * 1024  # per monthly file (about 20 MiB is normal); the rest of a larger file is not read (named in `truncated`)
MAX_RECORDS = 2_000_000  # per read (about 20 years of ticks); the reader stops there and says so (`limit_hit`)

NUM = (int, float)
# type -> required fields and their types (besides v, ts, t, type, run). The schema is documented in docs/IO-GOVERNOR-V1-DESIGN.md.
# A record with a wrong type is `invalid` for every reader: the reports never index an untyped value.
REQUIRED: dict[str, dict[str, type | tuple]] = {
    "run_start": {"kind": str, "version": str, "mode": str, "settings_epoch": str},
    "run_end": {"kind": str, "status": str, "duration_s": NUM, "io": dict},
    "skip": {"kind": str, "reasons": list},
    "probe": {"lane": str, "device": str, "quiet": bool, "n": int, "zero": int, "lat_us": list, "hist": list, "direct": bool},
    "bench": {"status": str, "region": dict, "lanes": dict, "settings_epoch": str},
    "passive": {"devices": dict, "pressure": (dict, type(None))},
    "recommendation": {"lane": str, "kind": str, "text": str, "evidence": dict},
    "note": {"text": str},
    "harm": {"kind": str, "evidence": dict},
}
COMMON: dict[str, type | tuple] = {"ts": str, "t": NUM, "type": str}


def utc_iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def validate_record(rec: dict) -> list[str]:
    problems = []
    if not isinstance(rec, dict):
        return ["record is not an object"]
    if rec.get("v") != SCHEMA_VERSION:
        problems.append(f"v must be {SCHEMA_VERSION}")
    for key, typ_ in COMMON.items():
        if key not in rec:
            problems.append(f"missing {key}")
        elif not _is(rec[key], typ_):
            problems.append(f"{key}: wrong type")
    typ = rec.get("type")
    if not isinstance(typ, str) or typ not in REQUIRED:
        problems.append(f"unknown type {typ!r}")
    else:
        for key, want in REQUIRED[typ].items():
            if key not in rec:
                problems.append(f"{typ}: missing {key}")
            elif not _is(rec[key], want):
                problems.append(f"{typ}: {key} has the wrong type")
    return problems


def _is(value, want) -> bool:
    """isinstance that does not let a bool pass as a number (bool is an int subclass in Python)."""

    if want is not bool and isinstance(value, bool):
        return False
    return isinstance(value, want)


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
        self.unreadable: list[str] = []  # files skipped whole: a symlink, not a regular file, or unreadable (permissions)
        self.oversized = 0  # lines longer than MAX_LINE_READ (discarded)
        self.truncated: list[str] = []  # files whose remainder beyond MAX_FILE_BYTES was not read
        self.limit_hit = False  # MAX_RECORDS reached: later records were not read

    def skipped(self) -> bool:
        return bool(self.torn or self.unknown_version or self.invalid or self.unreadable or self.oversized or self.truncated or self.limit_hit)

    def describe(self) -> str:
        parts = [f"{self.torn} torn", f"{self.unknown_version} other-schema", f"{self.invalid} invalid", f"{self.oversized} oversized line(s)",
                 f"{len(self.unreadable)} unreadable file(s)", f"{len(self.truncated)} truncated file(s)"]
        if self.limit_hit:
            parts.append(f"record limit {MAX_RECORDS} reached, later records NOT read")
        return ", ".join(parts)


def journal_files(path: str) -> list[str]:
    if os.path.isdir(path):
        return sorted(glob.glob(os.path.join(path, "journal-*.jsonl")))
    return [path]


def read_records(path: str | Iterable[str], stats: ReadStats | None = None, since: float | None = None) -> Iterator[dict]:
    """Read-only: the journal may be read by an operator or root (`any_owner`), never through a symlink. A file that cannot be
    read is counted in `stats.unreadable` (and named), never silently dropped: "0 records" must not look like an empty journal."""

    stats = stats or ReadStats()
    files = journal_files(path) if isinstance(path, str) else list(path)
    for name in files:
        try:
            fh = os.fdopen(safeio.open_file(name, os.O_RDONLY, private=False, any_owner=True), "r", encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
        except (safeio.UnsafePath, PermissionError) as exc:
            stats.unreadable.append(f"{os.path.basename(name)}: {exc}")
            continue
        with fh:
            consumed = 0
            while True:
                line = fh.readline(MAX_LINE_READ)
                if not line:
                    break
                consumed += len(line)
                if len(line) >= MAX_LINE_READ and not line.endswith("\n"):
                    stats.oversized += 1
                    while consumed <= MAX_FILE_BYTES:  # discard the remainder of the oversized line in bounded chunks
                        rest = fh.readline(MAX_LINE_READ)
                        consumed += len(rest)
                        if not rest or rest.endswith("\n"):
                            break
                    line = ""
                if consumed > MAX_FILE_BYTES:
                    stats.truncated.append(os.path.basename(name))
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (ValueError, RecursionError):  # torn, not JSON, or nested beyond what the parser accepts
                    stats.torn += 1
                    continue
                if not isinstance(rec, dict) or not isinstance(rec.get("v"), int) or isinstance(rec.get("v"), bool):
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
                if stats.records >= MAX_RECORDS:
                    stats.limit_hit = True
                    return


# ---------------------------------------------------------------------------------------------------------
# state file (small JSON, atomic replace)
# ---------------------------------------------------------------------------------------------------------


def load_state(path: str) -> dict:
    """Read-only view for `status` (root or the service user); the runner loads its state through `safeio.load_json`.
    Raises safeio.CorruptFile / safeio.UnsafePath instead of guessing."""

    return safeio.load_json(path, any_owner=True, strict=True)


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
