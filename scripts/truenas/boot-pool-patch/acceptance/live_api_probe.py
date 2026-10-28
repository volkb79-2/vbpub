#!/usr/bin/env python3
"""Assert the middleware API limits and the safety of the pool patch.

Profile ``path-b`` (partition-only state left by ``live-path-b-partition``)
asserts the partition-pool shim: the reserved partition is offered by
``disk.details``, mixed/mirror requests are refused, ``boot.attach`` on the
partition fails without changing the boot pool, ``pool.create`` on the
partition creates a pool whose only vdev is that partition, and a hand-made pool
on the partition is adopted by ``pool.import_find``/``pool.import_pool``.

Profile ``path-a`` (patched guest with the mounted ``boot-pool/user-data``
root) asserts the synthetic read-only ``boot-pool`` row of ``pool.query`` and
that every id- and name-based mutation on it is refused with the boot pool
unchanged.

Every check prints ``[check] <key> PASS|FAIL <detail>``; any FAIL fails the lane.
The only mutations are throwaway pools on the reserved partition, removed and
label-cleared in a ``finally`` block, with the cleanup asserted.
"""

from __future__ import annotations

import json
import os
import sys

from remote import RemoteError, Target

POOL = "apiprobepool"
MARKER = "boot-disk-data"
ZFS_TYPE_GUID = "6a898cc3-1dd2-11b2-99a6-080020736631"
FAILURES: list[str] = []


def check(key: str, ok: bool, detail: object = "") -> None:
    print(f"[check] {key} {'PASS' if ok else 'FAIL'} {str(detail)[:400]}", flush=True)
    if not ok:
        FAILURES.append(key)


def attempt(target: Target, method: str, *params, job: bool = False, timeout: int = 180) -> tuple[bool, str]:
    """Return ``(succeeded, exact output or error text)``."""
    argv = ["midclt", "call"] + (["-j"] if job else []) + [method]
    argv += [p if isinstance(p, str) else json.dumps(p, separators=(",", ":")) for p in params]
    try:
        return True, " ".join(target.run(argv, timeout=timeout).split())
    except RemoteError as exc:
        return False, " ".join(str(exc).split())


def boot_pool_state(target: Target) -> str:
    return "\n".join([
        target.shell("zpool get -H -o value guid,health boot-pool", timeout=45),
        target.shell("zpool status -P -L boot-pool", timeout=45),
    ])


def refused(ok: bool, text: str) -> bool:
    lowered = text.lower()
    return (not ok) and any(word in lowered for word in ("does not exist", "not found", "einval", "enoent", "invalid"))


def run_path_a(target: Target) -> None:
    before = boot_pool_state(target)
    ok, rows = attempt(target, "pool.query", [], {"select": ["id", "name", "path"]})
    synthetic = [r for r in json.loads(rows) if r["name"] == "boot-pool"] if ok else []
    check("synthetic-row-present", len(synthetic) == 1 and synthetic[0]["id"] == 0, rows)
    mutations = [
        ("pool.export", (0, {})), ("pool.update", (0, {"autotrim": "ON"})), ("pool.expand", (0,)),
        ("pool.upgrade", (0,)), ("pool.replace", (0, {"label": "x", "disk": "vdz"})),
        ("pool.attach", (0, {"target_vdev": "x", "new_disk": "vdz"})),
        ("pool.detach", (0, {"label": "x"})), ("pool.offline", (0, {"label": "x"})),
        ("pool.online", (0, {"label": "x"})), ("pool.remove", (0, {"label": "x"})),
    ]
    # pool.scrub.run takes a pool NAME and, in stock v27, is accepted for boot-pool as well
    # (a scrub is non-destructive and boot.scrub offers the same); it is deliberately not
    # part of the refusal set, see ACTION-MATRIX.md.
    for method, params in mutations:
        ok, text = attempt(target, method, *params, job=method != "pool.upgrade")
        check(f"refused-{method}", refused(ok, text), text)
    check("boot-pool-unchanged", boot_pool_state(target) == before, "zpool guid/health/status identical")


def reserved_partition(target: Target) -> tuple[str, str]:
    disk = os.environ.get("TRUENAS_DISK_DEVICE", "")
    boot_partition = os.environ.get("TRUENAS_BOOT_POOL_PARTITION", "")
    if not disk.startswith("/dev/") or not boot_partition.startswith("/dev/"):
        raise RemoteError("TRUENAS_DISK_DEVICE and TRUENAS_BOOT_POOL_PARTITION must name /dev paths")
    partition = target.shell(f"readlink -f /dev/disk/by-partlabel/{MARKER}", timeout=30)
    expected = disk + "4" if not disk[-1].isdigit() else disk + "p4"
    if partition != expected or partition == boot_partition:
        raise RemoteError(f"the reserved partition is {partition!r}, expected {expected!r}")
    info = json.loads(target.shell(
        f"lsblk -J -b -p -o PATH,PARTLABEL,PARTTYPE,FSTYPE,MOUNTPOINT {partition}", timeout=30))["blockdevices"][0]
    if info["partlabel"] != MARKER or str(info["parttype"]).lower() != ZFS_TYPE_GUID \
            or info["fstype"] or info["mountpoint"]:
        raise RemoteError(f"partition {partition} is not an empty reserved partition: {info!r}")
    if target.shell(f"zdb -l {partition} 2>/dev/null | grep -c 'version:' || true", timeout=30).strip() not in ("", "0"):
        raise RemoteError(f"partition {partition} carries ZFS labels")
    if partition in target.shell("zpool status -P -L", timeout=45).split():
        raise RemoteError(f"partition {partition} belongs to an imported pool")
    if MARKER in target.shell("zpool list -H -o name", timeout=30).split():
        raise RemoteError(f"pool {MARKER} already exists")
    return disk, partition


def cleanup(target: Target, partition: str) -> None:
    target.shell(
        f"id=$(midclt call pool.query '[[\"name\",\"=\",\"{POOL}\"]]' | python3 -c "
        f"'import json,sys; r=json.load(sys.stdin); print(r[0][\"id\"] if r else \"\")'); "
        f"[ -z \"$id\" ] || midclt call -j pool.export \"$id\" '{{\"destroy\":false}}' >/dev/null 2>&1; "
        f"zpool list -H -o name | grep -Fxq {POOL} && zpool export {POOL}; "
        f"zpool labelclear -f {partition}; true", timeout=240, check=False)


def run_path_b(target: Target) -> None:
    disk, partition = reserved_partition(target)
    name = partition.rsplit("/", 1)[1]
    before = boot_pool_state(target)
    try:
        ok, text = attempt(target, "disk.details", {"type": "UNUSED", "join_partitions": False})
        names = [row["name"] for row in json.loads(text)] if ok else []
        check("details-lists-reserved-partition", names == [name], text[:300])

        ok, text = attempt(target, "pool.create", {"name": "probemix", "topology": {"data": [
            {"type": "MIRROR", "disks": [name, disk.rsplit("/", 1)[1]]}]}}, job=True)
        check("create-mixed-refused", refused(ok, text) or (not ok and "reserved" in text.lower()), text)
        ok, text = attempt(target, "pool.create", {"name": "probemirror", "topology": {"data": [
            {"type": "MIRROR", "disks": [name]}]}}, job=True)
        check("create-mirror-refused", not ok, text)
        ok, text = attempt(target, "boot.attach", name, {}, job=True)
        check("boot-attach-refused-unchanged", (not ok) and boot_pool_state(target) == before, text)

        ok, text = attempt(target, "pool.create", {"name": POOL, "topology": {"data": [
            {"type": "STRIPE", "disks": [name]}]}}, job=True)
        leaves = target.shell(f"zpool status -P -L {POOL} 2>&1", timeout=45)
        check("shim-create-on-partition", ok and partition in leaves.split() and "ONLINE" in leaves,
              f"create ok={ok}: {leaves[:200]}")
        ok, rows = attempt(target, "pool.query", [["name", "=", POOL]], {"select": ["name", "status"]})
        check("shim-pool-registered", ok and POOL in rows and "ONLINE" in rows, rows)
        ok, text = attempt(target, "disk.details", {"type": "UNUSED", "join_partitions": False})
        check("details-no-longer-offers-used-partition", ok and name not in text, text[:200])
        cleanup(target, partition)

        target.shell(f"zpool create -o cachefile=none -O mountpoint=none {POOL} {partition} && zpool export {POOL}",
                     timeout=120)
        ok, found = attempt(target, "pool.import_find", job=True)
        guid = ""
        try:
            guid = next(str(r["guid"]) for r in json.loads(found) if r.get("name") == POOL)
        except (json.JSONDecodeError, StopIteration, TypeError, KeyError):
            pass
        ok2, imported = attempt(target, "pool.import_pool", {"guid": guid, "name": POOL}, job=True) if guid else (False, "no guid")
        ok3, rows = attempt(target, "pool.query", [["name", "=", POOL]], {"select": ["name", "status"]})
        check("import-hand-made-pool", ok and ok2 and ok3 and "ONLINE" in rows, f"{found[:150]} / {imported} / {rows}")
    finally:
        cleanup(target, partition)
    gone = POOL not in target.shell("zpool list -H -o name", timeout=30).split()
    labels = target.shell(f"zdb -l {partition} 2>/dev/null | grep -c 'version:' || true", timeout=30).strip()
    check("cleanup-complete", gone and labels in ("", "0"), f"pool gone={gone} labels={labels!r}")
    check("boot-pool-unchanged", boot_pool_state(target) == before, "zpool guid/health/status identical")


def main() -> int:
    if os.environ.get("TRUENAS_TEST_ALLOW_MUTATIONS") != "1":
        raise RemoteError("set TRUENAS_TEST_ALLOW_MUTATIONS=1 for this disposable guest")
    profile = os.environ.get("TRUENAS_STORAGE_PROFILE", "")
    if profile not in {"path-a", "path-b"}:
        raise RemoteError("TRUENAS_STORAGE_PROFILE must be path-a or path-b")
    target = Target.from_environment()
    expected = os.environ.get("TRUENAS_EXPECTED_VERSION", "")
    version = str(target.call("system.info").get("version", ""))
    if not expected or version != expected:
        raise RemoteError(f"expected TrueNAS {expected!r}, found {version!r}")
    (run_path_a if profile == "path-a" else run_path_b)(target)
    if FAILURES:
        raise RemoteError(f"failed checks: {', '.join(FAILURES)}")
    print(f'[result] "PASS {profile} API limits and patch safety asserted"', flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RemoteError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
