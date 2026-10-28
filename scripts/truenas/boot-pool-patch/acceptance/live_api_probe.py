#!/usr/bin/env python3
"""Record the exact middleware API limits for Path B storage actions.

Runs on a disposable patched guest whose reserved partition exists but holds no
pool (``live-path-b-partition`` state). Every probe prints one
``[api-probe] <method> -> <result>`` line; the UI action matrix cites these
methods. The only mutation is a throwaway hand-made pool on the reserved
partition, imported through middleware and then destroyed and label-cleared.
"""

from __future__ import annotations

import json
import os
import sys

from remote import RemoteError, Target

PARTITION_LABEL = "boot-disk-data"
POOL = "apiprobepool"


def probe(label: str, outcome: object) -> None:
    print(f"[api-probe] {label} -> {str(outcome)[:600]}", flush=True)


def attempt(target: Target, method: str, *params, job: bool = False, timeout: int = 180) -> str:
    """Return the exact result text or the exact middleware error text."""
    argv = ["midclt", "call"] + (["-j"] if job else []) + [method]
    argv += [p if isinstance(p, str) else json.dumps(p, separators=(",", ":")) for p in params]
    try:
        return " ".join(target.run(argv, timeout=timeout).split())
    except RemoteError as exc:
        return "ERROR " + " ".join(str(exc).split())


def main() -> int:
    if os.environ.get("TRUENAS_TEST_ALLOW_MUTATIONS") != "1":
        raise RemoteError("set TRUENAS_TEST_ALLOW_MUTATIONS=1 for this disposable guest")
    target = Target.from_environment()
    if target.shell("zpool list -H -o name | grep -Fx boot-disk-data || true", timeout=30):
        raise RemoteError("boot-disk-data already exists; run on the partition-only state")
    partition = target.shell(f"readlink -f /dev/disk/by-partlabel/{PARTITION_LABEL}", timeout=30)
    if not partition.startswith("/dev/"):
        raise RemoteError("the reserved Path B partition is missing; run live-path-b-partition first")
    name = partition.rsplit("/", 1)[1]

    probe("disk.query names", attempt(target, "disk.query", [], {"select": ["name"]}))
    probe("disk.details UNUSED join_partitions", attempt(
        target, "disk.details", {"type": "UNUSED", "join_partitions": True}))
    topology = {"topology": {"data": [{"type": "STRIPE", "disks": [name]}]}}
    probe(f"pool.create disks=[{name}]", attempt(
        target, "pool.create", {"name": "probefail", **topology}, job=True))
    probe(f"boot.attach {name}", attempt(target, "boot.attach", name, {}, job=True))
    # Id 0 is the synthetic read-only boot-pool row of ui-pools.patch: mutations refuse it.
    probe("pool.export id=0", attempt(target, "pool.export", 0, {}, job=True))
    probe("pool.update id=0", attempt(target, "pool.update", 0, {"autotrim": "ON"}, job=True))
    probe("pool.query after refused create", attempt(target, "pool.query", [], {"select": ["name"]}))

    # Hand-made pool on the partition, then adoption through middleware.
    target.shell(
        f"zpool create -f -m none -o ashift=12 {POOL} /dev/disk/by-partlabel/{PARTITION_LABEL} "
        f"&& zpool export {POOL}", timeout=120)
    try:
        found = attempt(target, "pool.import_find", job=True)
        probe("pool.import_find", found)
        guid = ""
        try:
            for row in json.loads(found):
                if row.get("name") == POOL:
                    guid = str(row["guid"])
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
        if guid:
            probe("pool.import_pool", attempt(
                target, "pool.import_pool", {"guid": guid, "name": POOL}, job=True))
            probe("pool.query after import", attempt(
                target, "pool.query", [["name", "=", POOL]], {"select": ["name", "path", "status"]}))
        else:
            probe("pool.import_pool", "skipped: pool.import_find did not list the hand-made pool")
    finally:
        # Remove the throwaway pool whether or not it was adopted.
        target.shell(
            f"(midclt call -j pool.export $(midclt call pool.query '[[\"name\",\"=\",\"{POOL}\"]]' "
            f"| python3 -c 'import json,sys; r=json.load(sys.stdin); print(r[0][\"id\"] if r else \"\")') "
            f"'{{\"destroy\":false}}' >/dev/null 2>&1 || true); "
            f"zpool export {POOL} >/dev/null 2>&1 || true; "
            f"zpool labelclear -f /dev/disk/by-partlabel/{PARTITION_LABEL} >/dev/null 2>&1 || true",
            timeout=180, check=False)
    left = target.shell("zpool list -H -o name", timeout=30)
    if POOL in left.split():
        raise RemoteError(f"throwaway pool {POOL} was not removed")
    probe("cleanup", "throwaway pool removed; reserved partition left empty")
    print('[result] "PASS recorded Path B API limits; reserved partition left unused"', flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RemoteError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
