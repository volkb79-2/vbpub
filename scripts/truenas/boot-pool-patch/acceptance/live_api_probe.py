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
import re
import sys

from path_b_ui_setup import reserved_partition_four_name
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


def synthetic_row_refused(ok: bool, text: str) -> bool:
    return not ok and "pool 0 does not exist" in text.lower()


def partition_table_state(target: Target, disk: str) -> str:
    raw = target.run([
        "lsblk", "-J", "--tree", "-b", "-p", "-o",
        "PATH,PKNAME,PARTN,PARTLABEL,PARTTYPE,PARTUUID,SIZE,TYPE,FSTYPE,MOUNTPOINT", disk,
    ], timeout=45)
    tree = json.loads(raw)
    partitions = []

    def visit(node):
        if node.get("type") == "part":
            partitions.append({
                key: node.get(key)
                for key in ("path", "pkname", "partn", "partlabel", "parttype", "partuuid", "size")
            })
        for child in node.get("children") or []:
            visit(child)

    for node in tree.get("blockdevices", []):
        visit(node)
    partitions.sort(key=lambda row: (int(row["partn"] or 0), row["path"] or ""))
    gpt = target.run(["sgdisk", "-p", disk], timeout=45)
    return json.dumps(partitions, sort_keys=True) + "\n" + gpt


def destroy_source_evidence(target: Target) -> str:
    command = (
        "cd /usr/lib/python3/dist-packages/middlewared && "
        "grep -nE 'pool.get_disks|disk.wipe|labelclear' plugins/pool_/export.py || true; "
        "grep -nE '_get_whole_disk|partition' plugins/zpool/get_zpool_disks_impl.py; "
        "grep -nE 'disk_path|size - \\(CHUNK|wipe first 32MB|wipe last 32MB' plugins/disk_/wipe.py"
    )
    return target.shell(command, timeout=60)


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
    job_methods = {"pool.export", "pool.update", "pool.expand", "pool.replace", "pool.attach", "pool.remove"}
    for method, params in mutations:
        ok, text = attempt(target, method, *params, job=method in job_methods)
        check(f"refused-{method}", synthetic_row_refused(ok, text), text)
    check("boot-pool-unchanged", boot_pool_state(target) == before, "zpool guid/health/status identical")


def reserved_partition(target: Target) -> tuple[str, str]:
    disk = os.environ.get("TRUENAS_DISK_DEVICE", "")
    boot_partition = os.environ.get("TRUENAS_BOOT_POOL_PARTITION", "")
    if (re.fullmatch(r"/dev/[A-Za-z0-9_.+-]+", disk) is None
            or re.fullmatch(r"/dev/[A-Za-z0-9_.+-]+", boot_partition) is None):
        raise RemoteError("TRUENAS_DISK_DEVICE and TRUENAS_BOOT_POOL_PARTITION must name /dev paths")
    try:
        expected_name = reserved_partition_four_name(disk)
    except ValueError as exc:
        raise RemoteError(str(exc)) from exc
    expected = f"/dev/{expected_name}"
    boot_name = os.path.basename(disk)
    boot_suffix = "p3" if boot_name.startswith(("nvme", "pmem")) else "3"
    if boot_partition != f"/dev/{boot_name}{boot_suffix}":
        raise RemoteError(f"configured boot-pool partition is not partition 3 of {disk!r}")
    partition = target.run(["readlink", "-f", f"/dev/disk/by-partlabel/{MARKER}"], timeout=30)
    if partition != expected or partition == boot_partition:
        raise RemoteError(f"the reserved partition is {partition!r}, expected {expected!r}")
    info = json.loads(target.run([
        "lsblk", "-J", "-b", "-p", "-o", "PATH,PARTLABEL,PARTTYPE,FSTYPE,MOUNTPOINT", partition,
    ], timeout=30))["blockdevices"][0]
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
    table_before = partition_table_state(target, disk)
    try:
        evidence = destroy_source_evidence(target)
        check(
            "destroy-source-parent-disk-wipe",
            "pool.get_disks" in evidence and "disk.wipe" in evidence
            and "_get_whole_disk" in evidence and "size - (CHUNK" in evidence,
            evidence,
        )
        check("boot-pool-online-before", "ONLINE" in before, before)
        ok, text = attempt(target, "disk.details", {"type": "UNUSED", "join_partitions": False})
        names = [row["name"] for row in json.loads(text)] if ok else []
        check("details-lists-reserved-partition", names == [name], text[:300])

        ok, text = attempt(target, "pool.create", {"name": "probemix", "topology": {"data": [
            {"type": "MIRROR", "disks": [name, disk.rsplit("/", 1)[1]]}]}}, job=True)
        check(
            "create-mixed-refused",
            not ok and "reserved boot-disk partition can only be the single disk of a one-disk data vdev" in text,
            text,
        )
        ok, text = attempt(target, "pool.create", {"name": "probemirror", "topology": {"data": [
            {"type": "MIRROR", "disks": [name]}]}}, job=True)
        check(
            "create-mirror-refused",
            not ok and "reserved boot-disk partition can only be used as a single-disk stripe data vdev" in text.lower(),
            text,
        )

        unsupported_options = (
            ("encryption", {"encryption": True}),
            ("encryption_options", {"encryption_options": {"generate_key": True}}),
            ("deduplication", {"deduplication": "ON"}),
            ("dedup_table_quota", {"dedup_table_quota": "CUSTOM"}),
            ("dedup_table_quota_value", {"dedup_table_quota_value": 1048576}),
            ("checksum", {"checksum": "SHA256"}),
            ("allow_duplicate_serials", {"allow_duplicate_serials": True}),
            ("all_sed", {"all_sed": True}),
            ("force_topology", {"force_topology": True}),
        )
        for index, (option, value) in enumerate(unsupported_options):
            payload = {
                "name": f"probeopt{index}",
                "topology": {"data": [{"type": "STRIPE", "disks": [name]}]},
                **value,
            }
            ok, text = attempt(target, "pool.create", payload, job=True)
            expected_refusal = f"{option} is not supported for reserved boot-disk partitions"
            check(f"create-option-refused-{option}", not ok and expected_refusal in text, text)

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

        pool = target.query("pool.query", [["name", "=", POOL]], {"get": True}, timeout=90)
        pool_id = pool["id"]
        vdev_guid = str(pool["topology"]["data"][0]["guid"])

        ok, text = attempt(target, "pool.export", pool_id, {"destroy": True}, job=True)
        check(
            "shim-destroy-refused",
            not ok and "refusing destructive export of pool" in text.lower()
            and "destroy=false" in text.lower(),
            text,
        )
        pool_status = target.shell(f"zpool status -P -L {POOL}", timeout=45)
        check(
            "shim-destroy-partitions-unchanged",
            boot_pool_state(target) == before and "ONLINE" in before
            and partition_table_state(target, disk) == table_before
            and partition in pool_status.split() and "ONLINE" in pool_status,
            f"pool={pool_status[:240]}; boot-pool ONLINE GUID/status and GPT/lsblk layout unchanged",
        )

        ok, text = attempt(
            target, "pool.replace", pool_id,
            {"label": vdev_guid, "disk": "{serial}missing-review-probe"}, job=True,
        )
        check("shim-replace-does-not-touch-other-partitions",
              boot_pool_state(target) == before and partition_table_state(target, disk) == table_before,
              f"ok={ok}; {text}")

        ok, text = attempt(
            target, "pool.offline", pool_id, {"label": vdev_guid}, job=False,
        )
        check("shim-offline-does-not-touch-other-partitions",
              boot_pool_state(target) == before and partition_table_state(target, disk) == table_before,
              f"ok={ok}; {text}")
        online_ok, online_text = attempt(
            target, "pool.online", pool_id, {"label": vdev_guid}, job=False,
        )
        check("shim-offline-restored", online_ok and "ONLINE" in target.shell(
            f"zpool status -P -L {POOL}", timeout=45), online_text)

        ok, text = attempt(
            target, "pool.detach", pool_id, {"label": vdev_guid, "wipe": False}, job=False,
        )
        check("shim-detach-does-not-touch-other-partitions",
              boot_pool_state(target) == before and partition_table_state(target, disk) == table_before,
              f"ok={ok}; {text}")

        ok, text = attempt(
            target, "pool.remove", pool_id, {"label": vdev_guid}, job=True,
        )
        check("shim-remove-does-not-touch-other-partitions",
              boot_pool_state(target) == before and partition_table_state(target, disk) == table_before,
              f"ok={ok}; {text}")

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
    check(
        "boot-pool-unchanged",
        boot_pool_state(target) == before and partition_table_state(target, disk) == table_before,
        "zpool guid/health/status and GPT/lsblk partition layout identical",
    )


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
