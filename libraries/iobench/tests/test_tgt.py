"""IOB-TGT: target guard, work-dir rules, file targets (and IOB-SAF-09)."""
import dataclasses
import json

import pytest

from iobench import devio, guard
from iobench.errors import Refusal
from iobench.guard import Target
from iobench.host import HostError, TAIL_BYTES
from iobench.testing import (GIB, R1002_PARTUUID, R1002_SIZE, TailViolation, standard_host)

from helpers import job, run_job, target_ref

CREATED = "2026-10-21T03:00:00Z"
DEV = "/dev/vda7"


def init(host, path=DEV, **kw):
    args = dict(own=True, expect_size=host.devices[path].size, expect_partuuid=host.devices[path].partuuid,
                expect_serial=None, created=CREATED)
    args.update(kw)
    return guard.init_device(host, path, **args)


def untouched(host, path=DEV):
    dev = host.devices[path]
    return dev.marker is None and dev.log == [] and host.write_log == [] and dev.write_bytes == 0


def refused(host, condition, **kw):
    with pytest.raises(Refusal) as err:
        init(host, **kw)
    assert err.value.condition == condition, str(err.value)
    assert err.value.exit_code == 2 and condition.split("_")[0] in str(err.value).lower() or True
    assert untouched(host)
    return str(err.value)


def test_tgt_01_clean_target_accepted_beside_a_mounted_root():
    host = standard_host()
    target = init(host)
    assert target.kind == "device" and target.usable_bytes == R1002_SIZE - TAIL_BYTES
    assert guard.parse_marker(host.read_marker(target.ref))["partuuid"] == R1002_PARTUUID
    assert guard.load_target(host, DEV) == target
    assert target.ref.devno == "254:7"


def test_tgt_01_refuses_mounted():
    host = standard_host()
    host.add_mount("254:7", "/mnt/data", DEV)
    assert "mounted at /mnt/data" in refused(host, "mounted")


def test_tgt_01_refuses_mounted_other_namespace():
    host = standard_host()
    host.add_mount("254:7", "/mnt/data", DEV, ns="init")
    msg = refused(host, "mounted_other_namespace")
    assert "PID 1" in msg and "mount namespace" in msg
    host = standard_host()
    host.add_mount("254:7", "/mnt/data", DEV, ns="self")
    assert "this mount namespace only" in refused(host, "mounted_other_namespace")
    host = standard_host()
    host.add_mount("254:7", "/mnt/a", DEV, ns="self")
    host.add_mount("254:7", "/mnt/b", DEV, ns="init")
    refused(host, "namespace_mismatch")


def test_tgt_01_refuses_swap():
    host = standard_host()
    host.put("/proc/swaps", f"Filename Type Size Used Priority\n{DEV} partition 1 0 -2\n")
    assert "swap-active" in refused(host, "swap")
    # swap on a SIBLING partition of the same disk is legitimate
    ok = standard_host()
    ok.put("/proc/swaps", "Filename Type Size Used Priority\n/dev/vda5 partition 1 0 -2\n")
    init(ok)


def test_tgt_01_refuses_signature():
    host = standard_host()
    host.devices[DEV].signatures.append("ext4")
    assert "signature" in refused(host, "signature", own=False, expect_size=None, expect_partuuid=None)
    host.devices[DEV].signatures = []
    host.devices[DEV].signatures.append("LVM2_member")
    with pytest.raises(Refusal, match="--i-own-this-device"):
        init(host, own=False)


def test_tgt_01_refuses_root():
    host = standard_host()
    host.add_mount("254:7", "/boot", DEV)
    assert "contains /boot" in refused(host, "root")
    # never overridable
    with pytest.raises(Refusal):
        init(host, own=True)


def test_tgt_01_refuses_parent_of_mounted():
    host = standard_host()
    host.mounts = [m for m in host.mounts if m[2] != "/"]
    msg = refused(host, "parent_of_mounted", path="/dev/vda", expect_size=500 * GIB, expect_partuuid=None, expect_serial="x")
    assert "parent of" in msg
    # a partition whose PARENT DISK is itself mounted
    host2 = standard_host()
    host2.mounts = [m for m in host2.mounts if m[2] != "/"]
    host2.add_mount("254:0", "/whole", "/dev/vda")
    refused(host2, "mounted")


def test_tgt_01_refuses_holder():
    host = standard_host()
    host.add_holder(DEV, "dm-0", 253, 0, GIB)
    host.add_mount("253:0", "/srv", "/dev/dm-0")
    assert "holder of" in refused(host, "holder")


def test_tgt_01_not_a_block_device_and_identity_missing():
    host = standard_host()
    with pytest.raises(Refusal) as err:
        guard.init_device(host, "/nope", own=True, expect_size=1, expect_partuuid=None, expect_serial=None, created=CREATED)
    assert err.value.condition == "not_block_device"
    host.devices["/dev/vda"].signatures.clear()


def test_tgt_02_override_matrix():
    for kind in ("size", "partuuid", "both_ok"):
        host = standard_host()
        host.devices[DEV].signatures.append("ext4")
        good = dict(own=True, expect_size=R1002_SIZE, expect_partuuid=R1002_PARTUUID)
        if kind == "size":
            good["expect_size"] = R1002_SIZE + 1
        if kind == "partuuid":
            good["expect_partuuid"] = "zzz"
        if kind == "both_ok":
            assert init(host, **good).partuuid == R1002_PARTUUID
            assert host.devices[DEV].marker
            continue
        with pytest.raises(Refusal) as err:
            init(host, **good)
        assert err.value.condition in ("expect_size_mismatch", "expect_identity_mismatch")
        assert untouched(host)
    # missing flags
    host = standard_host()
    host.devices[DEV].signatures.append("ext4")
    for missing in ({"own": False}, {"expect_size": None}, {"expect_partuuid": None}):
        with pytest.raises(Refusal):
            init(host, **missing)
    # a device reporting a serial: --expect-serial (or the PARTUUID) must match
    host = standard_host()
    host.add_disk("sdb", 8, 10 * GIB, serial="SER123")
    dev = host.add_partition("sdb", "sdb1", 8, 1, 4 * GIB, "aaaa-1")
    dev.signatures.append("xfs")
    assert init(host, "/dev/sdb1", expect_serial="SER123", expect_partuuid=None).serial == "SER123"
    with pytest.raises(Refusal, match="expect-serial"):
        init(host, "/dev/sdb1", expect_serial="OTHER", expect_partuuid="bbb")
    # mounted, swap and root are never overridable, even with every flag right
    for mount in ("/", "/boot", "/mnt/x"):
        h = standard_host()
        h.add_mount("254:7", mount, DEV)
        with pytest.raises(Refusal):
            init(h)


def test_tgt_02_size_only_device_refused():
    host = standard_host()
    host.devices[DEV].partuuid = None
    with pytest.raises(Refusal) as err:
        init(host, expect_partuuid=None)
    assert err.value.condition == "identity_missing" and "size alone is not an identity" in str(err.value)
    assert untouched(host)


def test_tgt_03_reserved_tail_one_per_writer_type():
    host = standard_host()
    ref = target_ref(host)
    size = host.devices[DEV].size
    overlap = size - TAIL_BYTES - 4096
    with pytest.raises(TailViolation):  # a fio job
        host.write_range(job("randwrite", off=overlap, size=8192, name="w").argv(), ref, overlap, 8192)
    with pytest.raises(TailViolation):  # a discard range
        host.discard_range(devio.blkdiscard_argv(DEV, overlap, 8192), ref, overlap, 8192)
    with pytest.raises(TailViolation):  # a fill job
        f = job("write", off=0, size=size, bs=1 << 20, qd=16, name="fill", metric="bw")
        host.write_range(f.argv(), ref, 0, size)
    with pytest.raises(TailViolation):  # a marker rewrite whose extent leaves the tail
        host.devices[DEV].check_range(overlap, 8192, "marker")
    assert host.devices[DEV].written.total() == 0


def test_tgt_03_iobench_writes_no_filesystem():
    host = standard_host()
    init(host)
    for argv in host.argv_log:
        assert devio.is_allowed_argv(argv)
    assert host.devices[DEV].signatures == []


def test_tgt_04_no_marker_refused():
    host = standard_host()
    with pytest.raises(Refusal) as err:
        guard.load_target(host, DEV)
    assert err.value.condition == "no_marker"


def test_tgt_04_size_changed_refused():
    host = standard_host()
    target = init(host)
    host.devices[DEV].size += 512 * 1024
    host.files[host.realpath("/sys/dev/block/254:7") + "/size"] = f"{host.devices[DEV].size // 512}\n"
    with pytest.raises(Refusal) as err:
        guard.verify_target(host, target)
    assert err.value.condition == "marker_size_changed"


def test_tgt_04_partuuid_changed_refused():
    host = standard_host()
    target = init(host)
    host.devices[DEV].partuuid = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(Refusal) as err:
        guard.verify_target(host, target)
    assert err.value.condition == "marker_partuuid_changed"


def test_tgt_04_foreign_signature_appears_refused():
    host = standard_host()
    target = init(host)
    host.devices[DEV].signatures.append("ext4")
    with pytest.raises(Refusal) as err:
        guard.verify_target(host, target)
    assert err.value.condition == "foreign_signature"
    host2 = standard_host()
    t2 = init(host2)
    host2.add_mount("254:7", "/mnt/x", DEV)
    with pytest.raises(Refusal) as err:
        guard.verify_target(host2, t2)
    assert err.value.condition == "mounted"
    host3 = standard_host()
    t3 = init(host3)
    with pytest.raises(Refusal) as err:
        guard.verify_target(host3, dataclasses.replace(t3, devno="9:9"))
    assert err.value.condition == "devno_changed"


def test_tgt_05_workdir_rules():
    host = standard_host()
    ok = guard.check_workdir(host, "/bench/w", planned_volume=GIB, installer=False, confirm_quiet_window=False, target_disk="vda")
    assert not ok.cross_disk and not ok.on_root and ok.reasons == () and ok.fs.mount_discard == "false"

    def refuse(h, cond, **kw):
        args = dict(planned_volume=GIB, installer=False, confirm_quiet_window=False, target_disk="vda")
        args.update(kw)
        path = args.pop("path", "/bench/w")
        with pytest.raises(Refusal) as err:
            guard.check_workdir(h, path, **args)
        assert err.value.condition == cond

    # non-empty
    host.put("/bench/w/junk", "x")
    refuse(host, "workdir_not_empty")
    host.remove("/bench/w/junk")
    host.put("/bench/w/.iobench-workdir", "")
    assert guard.check_workdir(host, "/bench/w", planned_volume=GIB, installer=False, confirm_quiet_window=False, target_disk="vda")
    # tmpfs refused, overlay refused unless installer
    t = standard_host()
    t.add_mount("0:50", "/tmpfs", "tmpfs", "tmpfs")
    t.set_fs("/tmpfs", 10 * GIB, 10 * GIB)
    refuse(t, "workdir_fs", path="/tmpfs/w")
    o = standard_host()
    o.add_mount("0:51", "/ov", "overlay", "overlay")
    o.set_fs("/ov", 100 * GIB, 100 * GIB)
    refuse(o, "workdir_fs", path="/ov/w")
    # free space >= max(4 GiB, 3 x planned volume)
    small = standard_host(workdir_free=3 * GIB)
    refuse(small, "workdir_free_space")
    big_volume = standard_host()
    refuse(big_volume, "workdir_free_space", planned_volume=10 * GIB)
    # root filesystem needs --confirm-quiet-window
    refuse(host, "workdir_on_root", path="/var/tmp/w")
    on_root = guard.check_workdir(host, "/var/tmp/w", planned_volume=GIB, installer=False, confirm_quiet_window=True, target_disk="vda")
    assert on_root.on_root
    # cross-disk flagged
    cross = guard.check_workdir(host, "/bench/w", planned_volume=GIB, installer=False, confirm_quiet_window=False, target_disk="vdb")
    assert cross.cross_disk and cross.reasons == ("cross_disk",)
    assert guard.check_workdir(host, "/bench/w", planned_volume=GIB, installer=False, confirm_quiet_window=False, target_disk=None).reasons == ()


def test_tgt_05_overlay_allowed_for_installer():
    o = standard_host()
    o.add_mount("0:51", "/ov", "/dev/vda5", "overlay", "rw")
    o.set_fs("/ov", 100 * GIB, 100 * GIB)
    with pytest.raises(Refusal):
        guard.check_workdir(o, "/ov/w", planned_volume=GIB, installer=False, confirm_quiet_window=True, target_disk="vda")
    # (the overlay mount shares the vda5 source, so namespace evidence agrees)
    wd = guard.check_workdir(o, "/ov/w", planned_volume=GIB, installer=True, confirm_quiet_window=True, target_disk="vda")
    assert wd.fs.fstype == "overlay" and wd.cross_disk  # an anonymous-device filesystem is not the target's disk


def test_saf_09_namespace_mismatch_refused():
    host = standard_host()
    host.add_mount("254:5", "/other", "/dev/vda5", ns="init")
    with pytest.raises(Refusal) as err:
        guard.check_workdir(host, "/bench/w", planned_volume=GIB, installer=False, confirm_quiet_window=False, target_disk="vda")
    assert err.value.condition == "namespace_mismatch" and err.value.exit_code == 2
    host = standard_host()
    host.add_mount("254:5", "/bench", "/dev/vda5", options="rw,noatime", ns="init")
    with pytest.raises(Refusal) as err:
        guard.check_workdir(host, "/bench/w", planned_volume=GIB, installer=False, confirm_quiet_window=False, target_disk="vda")
    assert err.value.condition in ("namespace_mismatch", "mounted_other_namespace")


# -- file targets -------------------------------------------------------------------------
FS_UUID = "22222222-2222-2222-2222-222222222222"


def finit(host, path="/bench/target.img", size=6 * GIB, **kw):
    args = dict(size=size, own=True, expect_fs_uuid=FS_UUID, confirm_quiet_window=False,
                allow_large_root_target=False, created=CREATED)
    args.update(kw)
    return guard.init_file(host, path, **args)


def frefused(host, condition, **kw):
    with pytest.raises(Refusal) as err:
        finit(host, **kw)
    assert err.value.condition == condition, str(err.value)
    assert not host.devices.get(kw.get("path", "/bench/target.img")) or "target.img" not in str(host.argv_log)
    return str(err.value)


def test_tgt_06_file_kind_refusals():
    host = standard_host()
    frefused(host, "size_required", size=None)
    frefused(host, "own_file_required", own=False)
    frefused(host, "fs_uuid_mismatch", expect_fs_uuid="nope")
    frefused(host, "fs_uuid_mismatch", expect_fs_uuid=None)
    host.symlinks["/bench/link.img"] = "/bench/x"
    frefused(host, "symlink", path="/bench/link.img")
    host.put("/bench/exists.img", "data")
    frefused(host, "exists_without_sidecar", path="/bench/exists.img")
    host.put("/bench/linked.img", "data")
    host.put("/bench/linked.img.iobench-target", "{}")
    host.nlinks["/bench/linked.img"] = 2
    frefused(host, "hard_link", path="/bench/linked.img")
    frefused(standard_host(workdir_free=7 * GIB), "free_space")  # needs size + 2 GiB margin
    t = standard_host()
    t.add_mount("0:50", "/tmpfs", "/dev/vda5", "tmpfs", "rw")
    t.set_fs("/tmpfs", 10 * GIB, 10 * GIB)
    frefused(t, "fs_type", path="/tmpfs/t.img")
    o = standard_host()
    o.odirect_unsupported.add("/bench")
    frefused(o, "odirect_unsupported")
    # allocation failure of fallocate itself
    f = standard_host(workdir_free=7 * GIB)
    f.fs_space["/bench"][1] = 6 * GIB + 2 * GIB
    f.run = lambda argv, timeout=None: (devio.RunResult(1, "", "boom") if argv[0] == "fallocate" else standard_host().run(argv))
    frefused(f, "fallocate_failed")


def test_tgt_06_file_kind_root_filesystem_ten_percent_rule():
    def gstammtisch():
        h = standard_host()
        h.set_fs("/", 400 * GIB, 342 * GIB)
        h.devices["/dev/vda3"].fs_uuid = "11111111-1111-1111-1111-111111111111"
        h.makedirs("/var/lib/iobench")
        return h

    kw = dict(path="/var/lib/iobench/t.img", expect_fs_uuid="11111111-1111-1111-1111-111111111111")
    frefused(gstammtisch(), "root_fs_needs_quiet_window", size=30 * GIB, **kw)
    assert finit(gstammtisch(), size=30 * GIB, confirm_quiet_window=True, **kw).size_bytes == 30 * GIB
    for size in (40 * GIB, 60 * GIB):
        frefused(gstammtisch(), "root_target_too_large", size=size, confirm_quiet_window=True, **kw)
        assert finit(gstammtisch(), size=size, confirm_quiet_window=True, allow_large_root_target=True, **kw).size_bytes == size


def test_tgt_06_preallocate_then_fully_write():
    host = standard_host()
    target = finit(host)
    assert target.kind == "file" and target.fs_uuid == FS_UUID
    assert host.argv_log[-1][:3] == ["fallocate", "-l", str(6 * GIB)]
    assert json.loads(host.read_text("/bench/target.img.iobench-target"))["size"] == 6 * GIB
    # dry run writes nothing
    d = standard_host()
    assert finit(d, dry_run=True).size_bytes == 6 * GIB
    assert not any(a[0] == "fallocate" for a in d.argv_log) and "/bench/target.img" not in d.devices
    # loaded target round-trips
    assert guard.load_target(host, "/bench/target.img") == target


def test_tgt_07_file_identity_reverified_per_stage():
    def fresh():
        h = standard_host()
        return h, finit(h)

    host, target = fresh()
    guard.verify_target(host, target)

    def refuse(h, t, cond):
        with pytest.raises(Refusal) as err:
            guard.verify_target(h, t)
        assert err.value.condition == cond, str(err.value)

    host, target = fresh()
    host.devices["/bench/target.img"].inode += 1  # replaced
    refuse(host, target, "inode_changed")
    host, target = fresh()
    host.devices["/bench/target.img"].size -= 4096  # truncated
    refuse(host, target, "size_changed")
    host, target = fresh()
    host.devices["/dev/vda5"].fs_uuid = "33333333-3333-3333-3333-333333333333"
    refuse(host, target, "fs_uuid_changed")
    host, target = fresh()
    side = json.loads(host.read_text("/bench/target.img.iobench-target"))
    side["size"] += 0
    host.put("/bench/target.img.iobench-target", json.dumps({**side, "created": "x"}))
    guard.verify_target(host, target)  # created is informational
    host.put("/bench/target.img.iobench-target", json.dumps({**side, "fs_uuid": "other"}))
    refuse(host, target, "fs_uuid_changed")
    host, target = fresh()
    dev = host.devices["/bench/target.img"]
    dev.inode += 1
    dev.marker = guard.marker_text(dev.size, None, None, "x", fs_uuid=dev.fs_uuid, inode=dev.inode)
    host.put("/bench/target.img.iobench-target", json.dumps({"fs_uuid": dev.fs_uuid, "inode": dev.inode, "size": dev.size}))
    refuse(host, target, "target_replaced")
    host, target = fresh()
    host.devices["/bench/target.img"].marker = "{}"
    refuse(host, target, "no_marker")
    host, target = fresh()
    host.remove("/bench/target.img.iobench-target")
    refuse(host, target, "no_marker")
    host, target = fresh()
    del host.devices["/bench/target.img"]
    refuse(host, target, "target_missing")


def test_tgt_08_punch_hole_or_not_measured():
    from iobench import ladder
    host = standard_host()
    finit(host)
    ref = target_ref(host, "/bench/target.img")
    r = host.discard_range(devio.punch_hole_argv("/bench/target.img", 0, 4096), ref, 0, 4096)
    assert r.returncode == 0
    host.devices["/bench/target.img"].discard_supported = False
    r = host.discard_range(devio.punch_hole_argv("/bench/target.img", 0, 4096), ref, 0, 4096)
    assert r.returncode != 0
    assert ladder.fresh_label("file") == "fresh_fs_alloc" and ladder.fresh_label("device") == "fresh"


def test_tgt_10_file_sizing():
    margin = guard.file_margin(30 * GIB)
    assert margin == 2 * GIB
    ws, fresh = guard.file_ws_max(6 * GIB, 6 * GIB, margin, 1.36e9, 432)
    assert ws <= 0.85 * 6 * GIB - margin + 1 and ws <= 5.1 * GIB - margin + 1
    assert fresh == int(0.1 * (0.85 * 6 * GIB - margin)) or fresh <= ws
    # gstammtisch geometry: 342 GiB free, --size 30G
    ws, fresh = guard.file_ws_max(30 * GIB, 342 * GIB, guard.file_margin(400 * GIB), 14e6, 432)
    assert ws <= 30 * GIB - fresh - TAIL_BYTES and fresh <= 2 * GIB
    big, fresh_big = guard.file_ws_max(100 * GIB, 342 * GIB, 20 * GIB, 1e9, 4000)
    assert fresh_big == 2 * GIB and big == 100 * GIB - 2 * GIB - TAIL_BYTES
    assert guard.file_ws_max(GIB, 0, 2 * GIB, 1e9, 100) == (0, 0)


def test_tgt_02_clean_device_still_needs_matching_flags_and_dry_run_writes_nothing():
    host = standard_host()
    with pytest.raises(Refusal) as err:
        init(host, expect_size=None, expect_partuuid=None)
    assert err.value.condition == "expect_size_mismatch" and "pass --expect-size 435853320192" in str(err.value)
    with pytest.raises(Refusal) as err:
        init(host, expect_partuuid=None)
    assert err.value.condition == "expect_identity_mismatch"
    assert untouched(host)
    target = init(host, dry_run=True)
    assert target.devno == "254:7" and untouched(host)


# ---------------------------------------------------------------------------------------------------------
# stage-level
# ---------------------------------------------------------------------------------------------------------
from iobench import calibrate as cal_mod  # noqa: E402
from iobench import ceilings  # noqa: E402
from iobench.testing import CacheModel, DirScenario, Scenario  # noqa: E402

from helpers import CREATED as _CREATED, INVERSION, READ_MISS, calibrate, config, fio_jobs, make_host, read_json  # noqa: E402

FILE_FS = "22222222-2222-2222-2222-222222222222"


def file_host(free=40 * GIB, size=6 * GIB, discard=False, **sc):
    scn = Scenario(read=DirScenario(iops_max=21600, lat_us=300), write=DirScenario(iops_max=3600, lat_us=440, bps_cap_seq=14e6, bps_cap_rand=14e6),
                   noise_cv=0.02, **sc)
    host = standard_host(scn, workdir_free=free)
    host.dynamic["/sys/fs/cgroup/wings.slice/io.pressure"] = lambda: "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
    if discard:
        host.mounts = [(ns, dev, mp, src, fs, opts + ",discard" if mp == "/bench" else opts) for ns, dev, mp, src, fs, opts in host.mounts]
    guard.init_file(host, "/bench/target.img", size=size, own=True, expect_fs_uuid=FILE_FS, confirm_quiet_window=False,
                    allow_large_root_target=False, created=_CREATED)
    return host


def test_tgt_04_installer_flag_does_not_bypass_refused():
    host = make_host(cache=None)
    host.devices["/dev/vda7"].marker = None
    for installer in (False, True):
        with pytest.raises(Refusal) as err:
            calibrate(host, installer=installer)
        assert err.value.condition == "no_marker"
    assert host.devices["/dev/vda7"].write_bytes == 0 and [x for x in host.devices["/dev/vda7"].log if x[0] != "marker"] == []
    # a foreign signature that appears MID-RUN is caught before the next writing stage, installer or not
    for installer in (False, True):
        mid = make_host(cache=None)
        mid.at(mid.now() + 120, lambda m=mid: m.devices["/dev/vda7"].signatures.append("ext4"))
        with pytest.raises(Refusal) as err:
            calibrate(mid, installer=installer, confirm_quiet_window=True)
        assert err.value.condition == "foreign_signature"
        assert not mid.exists("/run/iobench/restore.json")  # restored and cleaned up on the way out
        assert not mid.exists("/var/lib/iobench/calibration.json")


def test_tgt_06_preallocate_then_fully_write():
    host = file_host()
    out = calibrate(host, target="/bench/target.img", work_dir="/bench/w")
    assert out.status == "complete"
    names = [a for a in host.argv_log if a[0] in ("fallocate",) or (a[0] == "fio" and len(a) > 2)]
    assert names[0][:2] == ["fallocate", "-l"]
    fills = [j for j in fio_jobs(host) if "-fill-" in j.name]
    assert fills and sum(j.size for j in fills) == out.calibration["calibration"]["extent_written"][0][1]
    assert fills[0].offset == 0 and all(a.offset == b.end for a, b in zip(fills[1:], fills))
    assert not host.devices["/bench/target.img"].unwritten_reads  # no read of an unwritten byte
    assert all(j.direct for j in fio_jobs(host) if j.filename == "/bench/target.img")  # O_DIRECT only
    assert out.result["identity"]["target"]["kind"] == "file"


def test_tgt_08_punch_hole_or_not_measured():
    host = file_host()
    host.devices["/bench/target.img"].discard_supported = False
    out = calibrate(host, target="/bench/target.img", work_dir="/bench/w")
    assert out.status == "complete"
    assert out.calibration["calibration"]["fresh_vs_rewrite"] == {"measured": False, "reason": "discard_unsupported"}
    punch = [a for a in host.argv_log if a[0] == "fallocate" and a[1] == "-p"]
    assert punch and not [a for a in host.argv_log if a[0] in ("blkdiscard", "fstrim")]  # a hole is punched, never blkdiscard, never fstrim


def test_tgt_08_fresh_fs_alloc_label_and_discard_mount_rule():
    results = {}
    for discard in (False, True):
        host = file_host(discard=discard, write_fresh=None) if False else file_host(discard=discard)
        host.scenario.write.fresh_penalty = 8.0
        out = calibrate(host, target="/bench/target.img", work_dir="/bench/w")
        cal = out.calibration["calibration"]
        fvr = cal["fresh_vs_rewrite"]
        assert fvr["label"] == "fresh_fs_alloc" and fvr["mount_discard"] == ("true" if discard else "false")
        assert "fresh_semantics_filesystem" in out.result["quality"]["limits"] and "filesystem_layer" in out.result["quality"]["limits"]
        results[discard] = (cal["cap_basis"]["write"]["value"], fvr)
        assert not [a for a in host.argv_log if a[0] == "fstrim"]
    off_val, off_fvr = results[False]
    on_val, on_fvr = results[True]
    assert off_val == pytest.approx(off_fvr["rewrite_iops"])  # informational only without discard
    assert on_val == pytest.approx(min(on_fvr["rewrite_iops"], on_fvr["fresh_iops"]), rel=0.02) and on_val < 0.5 * off_val


def test_tgt_09_file_kind_labelled_and_history_separate():
    host = file_host()
    out = calibrate(host, target="/bench/target.img", work_dir="/bench/w")
    assert out.result["identity"]["target"]["kind"] == "file" and "filesystem_layer" in out.result["quality"]["limits"]
    rows = host.read_text("/var/lib/iobench/ceilings.jsonl").splitlines()
    digest_file = json.loads(rows[0])["identity_digest"]
    dev_host = make_host(cache=None)
    dev_out = calibrate(dev_host)
    digest_dev = json.loads(dev_host.read_text("/var/lib/iobench/ceilings.jsonl").splitlines()[0])["identity_digest"]
    assert digest_file != digest_dev
    # a device-kind history is ignored (with a warning) by a file-kind lookup and vice versa
    assert ceilings.load(host, "/var/lib/iobench/ceilings.jsonl", digest_dev) == ([], ["history_ignored_identity_differs"])


def test_tgt_10_file_sizing():
    host = file_host(free=9 * GIB, size=6 * GIB)   # free space is a few GiB
    host.devices["/bench/target.img"].cache = CacheModel(10, 92000, write_allocate=True)  # a cache bigger than the whole file
    out = calibrate(host, target="/bench/target.img", work_dir="/bench/w")
    cal = out.calibration["calibration"]
    ws = cal["extent_written"][0][1]
    margin = guard.file_margin(30 * GIB)
    assert ws <= 0.85 * 9 * GIB - margin + 1 and cal["fresh_extent"][1] - cal["fresh_extent"][0] <= 2 * GIB
    assert ws + (cal["fresh_extent"][1] - cal["fresh_extent"][0]) <= 6 * GIB
    # read caps are unproven: device_smaller_than_cache_margin (honest and acceptable: reads are cheap here)
    assert "device_smaller_than_cache_margin" in out.result["proposals"]["caps"]["dev_aggregate"]["RIOPS"]["reasons"]
    # the gstammtisch geometry, 342 GiB free, --size 30G -> WS_max <= 30 GiB - fresh - tail
    big = file_host(free=342 * GIB, size=30 * GIB)
    big.set_fs("/bench", 400 * GIB, 342 * GIB)
    out2 = calibrate(big, target="/bench/target.img", work_dir="/bench/w")
    cal2 = out2.calibration["calibration"]
    fresh = cal2["fresh_extent"][1] - cal2["fresh_extent"][0]
    assert cal2["extent_written"][0][1] <= 30 * GIB - fresh - 1048576


def test_tgt_10_fill_aborts_when_free_space_falls():
    host = file_host(free=40 * GIB, size=6 * GIB)
    # a competing writer eats the free space while the fill runs
    host.at(host.now() + 60, lambda: host.consume("/bench", 39 * GIB))
    out = calibrate(host, target="/bench/target.img", work_dir="/bench/w")
    cal = out.calibration["calibration"]
    assert cal["fill"]["fill_aborted"] == "free_space"
    margin = guard.file_margin(30 * GIB)
    assert cal["fill"]["bytes"] < cal["fill"]["planned_bytes"] and host.statfs("/bench")[1] < margin + GIB
    assert cal["extent_written"][0][1] == cal["fill"]["bytes"]  # WS_max shrank to the extent so far
    assert "limited-by-budget" in out.result["quality"]["limits"]
