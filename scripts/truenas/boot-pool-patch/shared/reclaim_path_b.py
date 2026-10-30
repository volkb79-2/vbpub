#!/usr/bin/env python3
"""Safely reclaim one exported Path B pool by its PARTUUID and pool GUID."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Sequence

MARKER = "boot-disk-data"
ZFS_TYPE_GUID = "6a898cc3-1dd2-11b2-99a6-080020736631"
PARTUUID_RE = re.compile(r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
POOL_GUID_RE = re.compile(r"^\d+$")
POOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_LEAF_LINE = re.compile(
    r"(?m)^\s+((?:/dev/\S+)|(?:[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}))\s+"
    r"(?:ONLINE|DEGRADED|FAULTED|UNAVAIL|OFFLINE)\b"
)


class ReclaimError(RuntimeError):
    """A required identity or state check did not pass."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


Runner = Callable[[Sequence[str]], CommandResult]


def _system_runner(argv: Sequence[str]) -> CommandResult:
    try:
        result = subprocess.run(
            list(argv), text=True, capture_output=True, timeout=180, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReclaimError(f"command could not be completed: {argv[0]} ({type(exc).__name__})") from exc
    return CommandResult(result.returncode, result.stdout, result.stderr)


def _pool_import_records(output: str) -> list[dict]:
    starts = list(re.finditer(r"(?m)^\s*pool:\s*(\S+)\s*$", output))
    records = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(output)
        section = output[start.start():end]
        guid = re.search(r"(?m)^\s*id:\s*(\d+)\s*$", section)
        config = re.search(r"(?ms)^\s*config:\s*$(.*)", section)
        records.append({
            "name": start.group(1),
            "guid": guid.group(1) if guid else None,
            "leaves": _LEAF_LINE.findall(config.group(1) if config else ""),
        })
    return records


class PathBReclaimer:
    """Reclaim with an injectable command runner so state transitions are testable."""

    def __init__(self, runner: Runner = _system_runner):
        self.runner = runner

    def _result(self, *argv: str) -> CommandResult:
        result = self.runner(argv)
        if result.returncode not in (0, 1) and argv[0] == "findmnt":
            raise ReclaimError(f"findmnt could not determine mount state: {result.stderr.strip()}")
        return result

    def _run(self, *argv: str) -> str:
        result = self._result(*argv)
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip()
            raise ReclaimError(f"command failed ({result.returncode}): {argv[0]} {detail}")
        return result.stdout.strip()

    def _field(self, field: str, device: str) -> str:
        return self._run("lsblk", "-dnro", field, device)

    def _verify_partition(self, partuuid: str) -> tuple[str, str]:
        normalized_uuid = partuuid.lower()
        device = f"/dev/disk/by-partuuid/{normalized_uuid}"
        resolved = self._run("readlink", "-f", device)
        if not resolved.startswith("/dev/"):
            raise ReclaimError(f"PARTUUID {normalized_uuid} did not resolve to a device: {resolved!r}")
        if self._field("PARTUUID", device).lower() != normalized_uuid:
            raise ReclaimError(f"{device} no longer has PARTUUID {normalized_uuid}")
        if self._field("PARTLABEL", device) != MARKER:
            raise ReclaimError(f"{device} no longer carries reserved GPT label {MARKER!r}")
        if self._field("PARTTYPE", device).lower() != ZFS_TYPE_GUID:
            raise ReclaimError(f"{device} no longer has the reserved ZFS GPT type")
        part_number = self._field("PARTN", device)
        if not part_number.isdecimal() or int(part_number) < 4:
            raise ReclaimError(f"{device} is not reserved partition number 4 or later")
        fstype = self._field("FSTYPE", device)
        if fstype not in {"", "zfs_member"}:
            raise ReclaimError(f"refusing to reclaim {device} with filesystem {fstype!r}")
        mountpoint = self._field("MOUNTPOINT", device)
        if mountpoint:
            raise ReclaimError(f"refusing to reclaim mounted partition {device}: {mountpoint!r}")
        findmnt = self._result("findmnt", "-rn", "-S", device)
        if findmnt.returncode == 0:
            raise ReclaimError(
                f"refusing to reclaim mounted partition {device}: "
                f"{findmnt.stdout.strip() or findmnt.stderr.strip()}"
            )

        parent = self._field("PKNAME", device)
        if not parent:
            raise ReclaimError(f"could not identify parent disk for {device}")
        boot_status = self._run("zpool", "status", "-P", "-L", "boot-pool")
        boot_leaves = {token for token in boot_status.split() if token.startswith("/dev/")}
        boot_parents = {self._field("PKNAME", leaf) for leaf in boot_leaves}
        if parent not in boot_parents:
            raise ReclaimError(f"{device} is no longer on a disk containing a boot-pool leaf")
        return device, resolved

    def _pool_rows(self, pool: str) -> list[dict]:
        query = json.dumps([["name", "=", pool]], separators=(",", ":"))
        output = self._run("midclt", "call", "pool.query", query)
        try:
            rows = json.loads(output)
        except json.JSONDecodeError as exc:
            raise ReclaimError("pool.query returned invalid JSON") from exc
        if not isinstance(rows, list):
            raise ReclaimError(f"pool.query returned {type(rows).__name__}")
        return rows

    def _pool_names(self) -> set[str]:
        return set(self._run("zpool", "list", "-H", "-o", "name").splitlines())

    def _status_leaves(self, pool: str | None = None) -> set[str]:
        command = ("zpool", "status", "-P", "-L") + ((pool,) if pool else ())
        output = self._run(*command)
        return {token for token in output.split() if token.startswith("/dev/")}

    def _assert_no_imported_target(self, expected_device: str) -> None:
        expected = self._run("readlink", "-f", expected_device)
        actual = {self._run("readlink", "-f", leaf) for leaf in self._status_leaves()}
        if expected in actual:
            raise ReclaimError(f"an imported pool still uses {expected_device}; labels were left untouched")

    def _export_scan(self, device: str) -> list[dict]:
        return _pool_import_records(self._run("zpool", "import", "-d", device))

    def _assert_expected_export(self, pool: str, guid: str, device: str, resolved: str) -> None:
        records = self._export_scan(device)
        if len(records) != 1 or records[0].get("name") != pool \
                or str(records[0].get("guid")) != guid:
            raise ReclaimError(
                f"export scan does not identify only pool {pool!r} with GUID {guid}: {records!r}"
            )
        leaves = set()
        for leaf in records[0]["leaves"]:
            if PARTUUID_RE.fullmatch(leaf):
                leaf = f"/dev/disk/by-partuuid/{leaf}"
            elif not leaf.startswith("/dev/"):
                raise ReclaimError(f"zpool import returned an unsupported leaf token: {leaf!r}")
            leaves.add(self._run("readlink", "-f", leaf))
        if leaves != {resolved}:
            raise ReclaimError(
                f"exported pool GUID {guid} is not a single-leaf pool on its captured PARTUUID: {leaves!r}"
            )

    def _assert_offered(self, partuuid: str, device: str) -> None:
        output = self._run(
            "midclt", "call", "disk.details",
            '{"type":"UNUSED","join_partitions":false}',
        )
        try:
            rows = json.loads(output)
        except json.JSONDecodeError as exc:
            raise ReclaimError("disk.details returned invalid JSON after reclaim") from exc
        resolved = self._run("readlink", "-f", device)
        name = resolved.rsplit("/", 1)[-1]
        matches = [
            row for row in rows
            if row.get("name") == name
            and row.get("identifier") == "{partuuid}" + partuuid.lower()
        ]
        if len(matches) != 1:
            raise ReclaimError(f"disk.details did not offer captured PARTUUID {partuuid}: {matches!r}")

    def reclaim(self, pool: str, partuuid: str, expected_guid: str) -> str:
        if not POOL_NAME_RE.fullmatch(pool):
            raise ReclaimError("pool name must be supplied explicitly as one valid name")
        if not PARTUUID_RE.fullmatch(partuuid):
            raise ReclaimError("operator-supplied PARTUUID must be a UUID")
        if not POOL_GUID_RE.fullmatch(expected_guid):
            raise ReclaimError("operator-supplied expected pool GUID must be numeric")

        partuuid = partuuid.lower()
        device, resolved = self._verify_partition(partuuid)
        rows = self._pool_rows(pool)
        if rows:
            if len(rows) != 1 or rows[0].get("name") != pool \
                    or str(rows[0].get("guid")) != expected_guid \
                    or type(rows[0].get("id")) is not int:
                raise ReclaimError(
                    f"middleware row does not identify expected pool GUID {expected_guid}; "
                    "cleanup stopped for manual recovery"
                )
            if pool not in self._pool_names():
                raise ReclaimError("middleware row exists for an exported pool; manual recovery is required")
            actual_guid = self._run("zpool", "get", "-H", "-o", "value", "guid", pool)
            if actual_guid != expected_guid:
                raise ReclaimError(f"imported pool GUID changed: expected {expected_guid}, found {actual_guid}")
            leaves = {self._run("readlink", "-f", leaf) for leaf in self._status_leaves(pool)}
            if leaves != {resolved}:
                raise ReclaimError(f"imported pool GUID {expected_guid} is not solely on {device}: {leaves!r}")
            self._run(
                "midclt", "call", "-j", "pool.export", str(rows[0]["id"]),
                '{"destroy":false}',
            )
            if self._pool_rows(pool):
                raise ReclaimError("pool.export destroy=false left a middleware row; labels were not cleared")
        elif pool in self._pool_names():
            raise ReclaimError("pool is imported without a middleware row; manual recovery is required")

        self._assert_no_imported_target(device)
        records = self._export_scan(device)
        if not records:
            if self._field("FSTYPE", device):
                raise ReclaimError(
                    f"no exported pool was found but {device} still has a filesystem signature; "
                    "manual recovery is required"
                )
            self._assert_offered(partuuid, device)
            return "already_clear"

        self._assert_expected_export(pool, expected_guid, device, resolved)
        # Repeat identity, mount, parent, import, and exported-GUID checks at the write boundary.
        device, resolved = self._verify_partition(partuuid)
        self._assert_no_imported_target(device)
        self._assert_expected_export(pool, expected_guid, device, resolved)
        self._run("zpool", "labelclear", "-f", device)

        self._verify_partition(partuuid)
        self._assert_no_imported_target(device)
        if self._export_scan(device) or self._field("FSTYPE", device):
            raise ReclaimError("labelclear returned but the expected pool signature remains")
        self._assert_offered(partuuid, device)
        return "reclaimed"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", required=True, help="explicit pool name to reclaim")
    parser.add_argument("--partuuid", required=True, help="captured PARTUUID for the reserved partition")
    parser.add_argument("--expected-pool-guid", required=True, help="captured numeric GUID of that pool")
    args = parser.parse_args(argv)
    try:
        result = PathBReclaimer().reclaim(args.pool, args.partuuid, args.expected_pool_guid)
    except ReclaimError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: {result}; PARTUUID {args.partuuid.lower()} is available in disk.details")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
