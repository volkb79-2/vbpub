"""IOB-CEI-06 / IOB-CEI-07 / IOB-RES-03: the disk-scope identity, the history key it gives, and the evidence-class table."""
import io
import json

import pytest

from cli_extended import CliIdentity

from iobench import calibrate as cal_mod, ceilings, cli, guard, proposals, result as res
from iobench.errors import Refusal
from iobench.testing import GIB

from helpers import INVERSION, calibrate, config, make_host, read_json
from test_tgt import file_host

STATE = "/var/lib/iobench"
HIST = f"{STATE}/ceilings.jsonl"
IDENT = CliIdentity("iobench", "0.1.0", "cache-proof disk benchmark", "iobench")
VDA = "/sys/devices/pci0/block/vda/"


def run(host, *argv):
    out, err = io.StringIO(), io.StringIO()
    code = cli.build_cli(IDENT, host_factory=lambda: host).run(argv=list(argv), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def check(host, **kw):
    return res.check(host, STATE, now=host.now(), **kw)


@pytest.fixture(scope="module")
def world():
    host = make_host(cache=INVERSION)
    return host, calibrate(host)


def clone(host):
    import copy
    return copy.deepcopy(host)


def obs(kind, d="write", w="rand4k", bps=1e7, src="governor", ts=None, **kw):
    return {"ts": ceilings.iso(ts if ts is not None else 1_792_550_400.0), "dir": d, "workload": w, "plateau_bps": bps, "duration_s": 600,
            "source": src, "kind": kind, **kw}


# -- the scope and its key ---------------------------------------------------------------------------------
def test_cei_06_the_identity_records_the_whole_disk_and_the_partition_apart(world):
    host, out = world
    ident = out.result["identity"]
    scope = ident["disk_scope"]
    assert (scope["model"], scope["serial"], scope["wwn"], scope["size_bytes"], scope["name"]) == (None, None, None, 500 * GIB, "vda")
    assert scope["strength"] == ident["identity_strength"] == "weak" and len(scope["machine_id_hash"]) == 64
    assert "0123456789abcdef" not in json.dumps(scope)  # the machine id itself is never stored
    assert scope["key"] == ceilings.scope_key(scope) == ceilings.identity_digest(ident)
    # the partition that was measured stays in `disk`: it is a field of the observation, not of the key
    assert (ident["disk"]["devno"], ident["disk"]["size_bytes"]) == ("254:7", 435853320192)
    assert res.SCHEMA_MINOR == 5


def test_cei_06_observations_name_the_kind_and_the_partition(world):
    host, _ = world
    rows = [json.loads(x) for x in host.read_text(HIST).splitlines()[1:]]
    assert rows and all((r["kind"], r["devno"], r["partuuid"]) == ("raw", "254:7", "3e1f7a52-5b6d-4c1e-9a0d-7b2d0c1e0007") for r in rows if "note" not in r)


def test_cei_06_a_strong_disk_is_keyed_by_model_serial_wwn_and_size_alone():
    host = make_host(cache=None)
    host.put("/sys/block/vda/serial", "SER-1\n")
    host.put(VDA + "device/model", "QEMU HARDDISK\n")
    scope = calibrate(host).result["identity"]["disk_scope"]
    assert (scope["strength"], scope["serial"], scope["model"], scope["machine_id_hash"]) == ("strong", "SER-1", None, None)
    other_host = make_host(cache=None)
    other_host.put("/sys/block/vda/serial", "SER-1\n")
    other_host.put(VDA + "device/model", "QEMU HARDDISK\n")
    other_host.put("/etc/machine-id", "ffffffffffffffffffffffffffffffff\n")
    # the same disk behind another host's machine id is the same key; another serial is another disk
    assert calibrate(other_host).result["identity"]["disk_scope"]["key"] == scope["key"]
    other_host.put("/sys/block/vda/serial", "SER-2\n")
    assert ceilings.scope_key({**scope, "serial": "SER-2"}) != scope["key"]


def test_cei_06_reads_serial_from_sys_block_and_does_not_fall_back_to_device_serial():
    host = make_host(cache=None)
    host.put("/sys/block/vda/serial", "SYS-BLOCK-SERIAL\n")
    assert guard.disk_facts(host, "vda").serial == "SYS-BLOCK-SERIAL"

    host.files.pop(host.realpath("/sys/block/vda/serial"))
    host.put(VDA + "device/serial", "OLD-DEVICE-SERIAL\n")
    assert guard.disk_facts(host, "vda").serial is None


def test_cei_06_placeholder_serials_are_weak_and_use_the_machine_id():
    for serial in ("QM00001", "QM00002", "QM99999", "VB0123abcd-5678ef90", "VDISK17",
                   "QEMU HARDDISK", "000000000000", "AAAAAAAA", "ABABABAB", "0", "", "UNKNOWN", "N/A"):
        host = make_host(cache=None)
        host.put("/sys/block/vda/serial", serial + "\n")
        scope = res.disk_scope(host, guard.disk_facts(host, "vda"))
        assert scope["serial"] is None and scope["strength"] == "weak", serial
        assert scope["machine_id_hash"] == res.machine_id_hash(host) and scope["key"] == ceilings.scope_key(scope)
    model_only = make_host(cache=None)
    model_only.put(VDA + "device/model", "QEMU HARDDISK\n")
    scope = res.disk_scope(model_only, guard.disk_facts(model_only, "vda"))
    assert scope["model"] is None and scope["strength"] == "weak"
    host = make_host(cache=None)
    host.put("/sys/block/vda/serial", "SERIAL-123\n")
    assert guard.disk_facts(host, "vda").serial == "SERIAL-123"


def test_cei_06_a_serialless_disk_is_bound_to_its_host_and_an_unusable_machine_id_is_not_a_binding():
    a, b = make_host(cache=None), make_host(cache=None)
    b.put("/etc/machine-id", "ffffffffffffffffffffffffffffffff\n")
    assert calibrate(a).result["identity"]["disk_scope"]["key"] != calibrate(b).result["identity"]["disk_scope"]["key"]
    for text in ("uninitialized\n", "\n"):
        none = make_host(cache=None)
        none.put("/etc/machine-id", text)
        assert res.machine_id_hash(none) is None
    missing = make_host(cache=None)
    del missing.files["/etc/machine-id"]
    assert res.machine_id_hash(missing) is None
    # a record made while the id was still `uninitialized` (an installer chroot) stays current once the real id exists at first boot
    first = make_host(cache=None)
    first.put("/etc/machine-id", "uninitialized\n")
    assert calibrate(first).result["identity"]["disk_scope"]["machine_id_hash"] is None
    first.put("/etc/machine-id", "0123456789abcdef0123456789abcdef\n")
    assert check(first).state in ("current", "current_unproven")
    stored = read_json(first, f"{STATE}/result.json")["identity"]
    assert res.live_identity(first, stored)["disk_scope"]["key"] != stored["disk_scope"]["key"]
    next_calibration = calibrate(first)
    assert next_calibration.status == "complete"
    assert next_calibration.result["evidence"]["history_rotated"].startswith(f"{HIST}.superseded-")


def test_res_03_a_chroot_weak_record_survives_first_boot_after_its_partition_is_deleted():
    host = make_host(cache=None)
    host.put("/etc/machine-id", "uninitialized\n")
    assert calibrate(host).status == "complete"
    stored = read_json(host, f"{STATE}/result.json")["identity"]
    assert stored["disk_scope"]["machine_id_hash"] is None

    host.put("/etc/machine-id", "0123456789abcdef0123456789abcdef\n")
    host.devices.pop("/dev/vda7")
    for link in ("/sys/dev/block/254:7", "/sys/class/block/vda7"):
        host.symlinks.pop(link, None)

    live = res.live_identity(host, stored)
    assert live is not None and live["target"] is None
    assert res.identity_differs(stored, live) is False
    assert res.target_changed(stored, live) is True
    current = check(host)
    assert current.state in ("current", "current_unproven") and "target_changed" in current.warnings


@pytest.mark.parametrize("live_machine_id", ("missing", "uninitialized"))
@pytest.mark.parametrize("partition_gone", (False, True))
def test_res_03_a_known_weak_host_id_must_still_be_present_and_equal(live_machine_id, partition_gone):
    host = make_host(cache=None)
    assert calibrate(host).status == "complete"
    stored = read_json(host, f"{STATE}/result.json")["identity"]
    assert stored["disk_scope"]["machine_id_hash"] is not None

    if live_machine_id == "missing":
        host.files.pop("/etc/machine-id", None)
    else:
        host.put("/etc/machine-id", "uninitialized\n")
    if partition_gone:
        host.devices.pop("/dev/vda7")
        for link in ("/sys/dev/block/254:7", "/sys/class/block/vda7"):
            host.symlinks.pop(link, None)

    current = check(host)
    assert (current.state, current.exit_code) == ("stale", 3)


# -- raw, file and probe evidence join one history ---------------------------------------------------------------
def test_cei_06_a_raw_calibration_a_file_observation_and_a_probe_read_join_one_history(world):
    host, out = world
    host = clone(host)
    digest = ceilings.identity_digest(out.result["identity"])
    ceilings.append(host, HIST, obs("file", d="write"), digest)  # iogovmon's scratch-file mini write bench
    ceilings.append(host, HIST, obs("probe", d="read", src="probe", bps=4e7), digest)  # its 4 KiB raw read probe
    rows, warnings = ceilings.load(host, HIST, digest)
    assert warnings == [] and {r["kind"] for r in rows} == {"raw", "file", "probe"}
    # a file-kind calibration of the same disk has the same key
    f = file_host()
    assert calibrate(f, target="/bench/target.img", work_dir="/bench/w").result["identity"]["disk_scope"]["key"] == digest
    # every one of them is reported apart
    by_kind = ceilings.summary_by_kind(rows, host.now())
    assert set(by_kind) == {"raw", "file", "probe"} and all(v["kind"] == k for k, v in by_kind.items())
    assert by_kind["file"]["write"]["rand4k"]["n"] == 1 and by_kind["probe"]["read"]["rand4k"]["n"] == 1 and "write" not in by_kind["probe"]


def test_cei_07_only_the_evidence_class_table_decides_what_confirms_stability():
    assert ceilings.STABILITY_KINDS == {"write": ("raw", "file"), "read": ("raw", "probe")}
    for kind, d, counts in (("raw", "write", True), ("raw", "read", True), ("file", "write", True), ("file", "read", False),
                            ("probe", "read", True), ("probe", "write", False), (None, "write", False), ("ssd", "read", False)):
        assert ceilings.counts_for_stability(kind, d) is counts, (kind, d)
    now = 1_792_550_400.0
    rows = [obs("raw", d="write", bps=10.0), obs("file", d="write", bps=20.0), obs("file", d="read", bps=1e12), obs("probe", d="read", bps=30.0),
            obs("probe", d="read", bps=50.0, ts=now - 3600), obs("raw", d="read", bps=40.0)]
    s = ceilings.summary(rows, now)
    # the aggregate leaves out the file read (it is never cache-free) and counts the rest
    assert s["write"]["rand4k"] == {"worst": 10.0, "median": 15.0, "best": 20.0, "n": 2}
    assert s["read"]["rand4k"] == {"worst": 30.0, "median": 40.0, "best": 50.0, "n": 3}
    # one class on its own is reported whatever it counts for, and says which
    f = ceilings.summary(rows, now, 30, "file")
    assert f["kind"] == "file" and f["read"]["rand4k"]["n"] == 1 and f["write"]["rand4k"]["n"] == 1
    assert "kind" not in s and ceilings.summary(rows, now, 30, "probe")["read"]["rand4k"]["n"] == 2
    assert ceilings.summary([obs("file", d="read")], now) == {"history": "single-run", "window_days": 30}  # nothing counts
    assert ceilings.summary([obs("file", d="read")], now, 30, "raw") == {"history": "single-run", "window_days": 30, "kind": "raw"}
    assert ceilings.summary_by_kind([], now) == {}
    # an observation without a kind (a hand-edited line) counts for nothing and belongs to no class
    legacy = {k: v for k, v in obs("raw").items() if k != "kind"}
    assert ceilings.summary([legacy], now)["history"] == "single-run" and ceilings.summary_by_kind([legacy], now) == {}


def test_cei_07_value_rows_are_raw_only_while_stability_rows_follow_the_table():
    now = 1_792_550_400.0
    rows = [obs("raw", d="write", w="seq1m", bps=100.0, ts=now),
            obs("file", d="write", w="seq1m", bps=1.0, src="governor", ts=now),
            obs("raw", d="read", w="rand4k", bps=100.0, ts=now),
            obs("probe", d="read", w="rand4k", bps=1.0, src="probe", ts=now)]
    stability = ceilings.summary(rows, now)
    values = ceilings.value_summary(rows, now)
    assert stability["write"]["seq1m"]["worst"] == 1.0 and stability["read"]["rand4k"]["worst"] == 1.0
    assert values["write"]["seq1m"] == {"worst": 100.0, "median": 100.0, "best": 100.0, "n": 1}
    assert values["read"]["rand4k"] == {"worst": 100.0, "median": 100.0, "best": 100.0, "n": 1}


def test_cei_07_file_and_probe_history_cannot_lower_calibration_model_or_caps():
    host = make_host(cache=INVERSION)
    run = cal_mod.Calibrator(host, config())
    baseline = run.execute()
    assert baseline.status == "complete"
    digest = ceilings.identity_digest(baseline.result["identity"])
    ts = host.now() - 60
    for direction, workload, kind, source in (("write", "seq1m", "file", "governor"),
                                               ("write", "rand4k", "file", "governor"),
                                               ("read", "rand4k", "probe", "probe")):
        ceilings.append(host, HIST, obs(kind, d=direction, w=workload, bps=1.0, src=source, ts=ts), digest)
    run.checks = []
    _, updated = run.assemble(run.safety.report)
    for section in ("caps", "iocost_model", "iocost_qos"):
        before = {key: item.get("value") for key, item in proposals.iter_values(baseline.result["proposals"][section])}
        after = {key: item.get("value") for key, item in proposals.iter_values(updated["proposals"][section])}
        assert after == before, section


def test_cei_07_recalibrate_recommended_counts_only_stability_evidence():
    now = 1_792_550_400.0
    cal = {"read/rand4k": 100.0}
    bad = [obs("file", d="read", bps=1.0, ts=now - 3600), obs("file", d="read", bps=1.0, ts=now - 14 * 3600)]  # a file read says nothing
    assert ceilings.recalibrate_recommended(bad, cal, now) is False
    probes = [obs("probe", d="read", bps=1.0, ts=now - 3600), obs("probe", d="read", bps=1.0, ts=now - 14 * 3600)]
    assert ceilings.recalibrate_recommended(probes, cal, now) is True
    write_cal = {"write/rand4k": 100.0}
    files = [obs("file", d="write", bps=1.0, ts=now - 3600), obs("file", d="write", bps=1.0, ts=now - 14 * 3600)]
    assert ceilings.recalibrate_recommended(files, write_cal, now) is True


# -- a gone partition, a replaced disk ---------------------------------------------------------------------------
def test_res_03_a_deleted_partition_is_still_current_and_its_history_still_joins(world):
    host, out = world
    gone = clone(host)
    del gone.devices["/dev/vda7"]
    for link in ("/sys/dev/block/254:7", "/sys/class/block/vda7"):
        gone.symlinks.pop(link, None)
    c = check(gone)
    assert c.state in ("current", "current_unproven") and c.warnings.count("target_changed") == 1
    assert c.history == "recorded"  # the history is found by the disk, not by the partition
    code, out_, _ = run(gone, "ceilings", "add", "--dir", "read", "--workload", "rand4k", "--bps", "4e7", "--duration-s", "600", "--source", "probe", "--json")
    assert code == 0 and json.loads(out_)["observation"]["kind"] == "probe"
    rows = [json.loads(x) for x in gone.read_text(HIST).splitlines()]
    assert rows[0]["identity_digest"] == ceilings.identity_digest(out.result["identity"]) and rows[-1]["kind"] == "probe"
    live = res.live_identity(gone, read_json(gone, f"{STATE}/result.json")["identity"])
    assert live["target"] is None and live["disk_scope"]["name"] == "vda" and live["disk"]["partuuid"] is None


def test_res_03_a_replaced_weak_disk_is_stale_with_or_without_a_serial():
    for serial, model in (("UNRELATED", "Samsung X"), (None, None)):
        host = make_host(cache=INVERSION)
        out = calibrate(host)
        identity = out.result["identity"]
        host.devices.pop("/dev/vda7")
        for link in ("/sys/dev/block/254:7", "/sys/class/block/vda7", "/sys/block/vda", "/sys/class/block/vda", "/sys/dev/block/254:0"):
            host.symlinks.pop(link, None)
        host.add_disk("vdb", 253, 500 * GIB, serial=serial, model=model)
        assert res.live_identity(host, identity) is None
        assert (check(host).state, check(host).exit_code) == ("stale", 3)


def test_res_03_a_weak_scope_becoming_strong_is_stale_even_when_the_target_is_unchanged():
    host = make_host(cache=INVERSION)
    host.put(VDA + "device/model", "M\n")
    calibrate(host)
    host.put("/sys/block/vda/serial", "NEWDISK-123\n")
    c = check(host)
    assert (c.state, c.exit_code) == ("stale", 3)


def test_res_03_a_replaced_resized_or_foreign_disk_is_stale_with_or_without_its_partition(world):
    host, _ = world
    flips = {"replaced (another serial)": [("/sys/block/vda/serial", "OTHER\n")], "resized": [(VDA + "size", f"{(500 * GIB) // 512 + 2048}\n")],
             "model changed": [(VDA + "device/model", "NEW MODEL\n")], "another host": [("/etc/machine-id", "ffffffffffffffffffffffffffffffff\n")]}
    for name, puts in flips.items():
        base = make_host(cache=INVERSION)
        base.put("/sys/block/vda/serial", "SER-1\n")  # the stored record is strong for the serial and model cases
        base.put(VDA + "device/model", "M\n")
        calibrate(base)
        strong = name in ("replaced (another serial)", "model changed")
        for gone in (False, True):
            h = clone(base if strong else host)
            for path, text in puts:
                h.put(path, text)
            if gone:
                del h.devices["/dev/vda7"]
                h.symlinks.pop("/sys/dev/block/254:7", None)
            c = check(h)
            assert (c.state, c.exit_code) == ("stale", 3), (name, gone)


def test_res_03_a_file_that_was_deleted_leaves_the_result_current():
    host = file_host()
    calibrate(host, target="/bench/target.img", work_dir="/bench/w")
    assert check(host).state in ("current", "current_unproven") and "target_changed" not in check(host).warnings
    host.remove("/bench/target.img.iobench-target")
    host.devices.pop("/bench/target.img")
    c = check(host)
    assert c.state in ("current", "current_unproven") and "target_changed" in c.warnings


def test_res_03_a_removed_work_dir_is_not_a_changed_filesystem(monkeypatch):
    host = make_host(cache=INVERSION)
    calibrate(host)
    real = guard.fs_info

    def gone(h, path):
        if path == "/bench/w":
            raise FileNotFoundError(path)
        return real(h, path)

    monkeypatch.setattr(guard, "fs_info", gone)
    for partition_gone in (False, True):
        h = clone(host)
        if partition_gone:
            del h.devices["/dev/vda7"]
        c = check(h)
        assert c.state in ("current", "current_unproven") and "workdir_fs_changed" not in c.warnings, partition_gone


def test_res_03_a_record_without_a_disk_scope_is_stale_and_names_nothing_to_find(world):
    host, _ = world
    legacy = clone(host)
    doc = read_json(legacy, f"{STATE}/result.json")
    del doc["identity"]["disk_scope"]
    legacy.put(f"{STATE}/result.json", json.dumps(doc))
    assert check(legacy).state == "stale"
    del legacy.devices["/dev/vda7"]  # with its partition gone too there is nothing at all to find the disk with
    assert check(legacy).state == "stale"
    assert res.live_identity(legacy, doc["identity"]) is None
    with pytest.raises(Refusal) as err:
        ceilings.identity_digest(doc["identity"])
    assert err.value.condition == "identity_legacy"


def test_res_03_a_file_on_a_filesystem_without_a_known_disk_has_no_scope(monkeypatch):
    host = file_host()
    target = guard.load_target(host, "/bench/target.img")
    monkeypatch.setattr(guard, "backing_disk", lambda h, p: None)
    ident = res.s0_identity(host, target, None)
    assert ident["disk_scope"] is None and ident["identity_strength"] == "weak"
    live = {**ident}
    stored = {**ident, "disk_scope": {"model": None, "serial": None, "wwn": None, "size_bytes": 1, "strength": "weak", "machine_id_hash": None}}
    assert res.identity_differs(stored, live) is True


# -- finding the disk without a target ----------------------------------------------------------------------------
def two_disks():
    host = make_host(cache=None)
    host.add_disk("vdb", 253, 500 * GIB, serial="B-SER", model="M")
    host.add_disk("vdc", 252, 500 * GIB, serial="C-SER", model="M")
    host.add_disk("vdd", 251, 100 * GIB)
    return host


def test_cei_06_find_disk_matches_every_stored_field_and_the_size():
    host = two_disks()
    find = lambda **kw: guard.find_disk(host, **{"model": None, "serial": None, "wwn": None, "size_bytes": 500 * GIB, "hint": None, **kw})  # noqa: E731
    assert find(serial="B-SER").name == "vdb" and find(serial="C-SER", model="M").name == "vdc"
    assert find(serial="NOPE") is None and find(serial="B-SER", model="OTHER") is None
    assert find(serial="B-SER", size_bytes=1) is None and find(size_bytes=100 * GIB) is None
    host.add_disk("vde", 250, 500 * GIB, wwn="naa.5000")
    assert find(wwn="naa.5000").name == "vde" and find(wwn="naa.5000", serial="B-SER") is None
    # Weak scopes need the remembered name and a machine-id match; a name hint never selects a strong candidate for a weak scope.
    assert find(hint="vda", machine_id_matches=True).name == "vda"
    assert find(hint="vdc", machine_id_matches=True) is None
    assert find() is None and find(hint="vdz", machine_id_matches=True) is None
    assert find(serial="C-SER", model="M", hint="vdc").name == "vdc"
    duplicate = make_host(cache=None)
    duplicate.put("/sys/block/vda/serial", "SHARED-SERIAL\n")
    duplicate.put(VDA + "device/model", "M\n")
    duplicate.add_disk("vdb", 253, 500 * GIB, serial="SHARED-SERIAL", model="M")
    args = {"model": "M", "serial": "SHARED-SERIAL", "wwn": None, "size_bytes": 500 * GIB}
    assert guard.find_disk(duplicate, **args, hint=None) is None
    assert guard.find_disk(duplicate, **args, hint="vdb").name == "vdb"


def test_cei_06_weak_find_disk_needs_the_remembered_name_and_machine_id_match():
    host = make_host(cache=None)
    args = {"model": None, "serial": None, "wwn": None, "size_bytes": 500 * GIB}
    assert guard.find_disk(host, **args, hint=None) is None
    assert guard.find_disk(host, **args, hint="vda", machine_id_matches=False) is None
    assert guard.find_disk(host, **args, hint="vda", machine_id_matches=True).name == "vda"


def test_cei_06_find_disk_skips_what_cannot_be_read_and_survives_no_block_devices():
    host = make_host(cache=None)
    host.symlinks["/sys/block/loop0"] = "/sys/devices/virtual/block/loop0"  # listed, but has no dev and no size file
    assert guard.find_disk(host, model=None, serial=None, wwn=None, size_bytes=500 * GIB, hint="vda", machine_id_matches=True).name == "vda"
    host.put("/sys/devices/virtual/block/loop0/dev", "7:0\n")
    host.put("/sys/devices/virtual/block/loop0/size", "not a number\n")
    assert guard.find_disk(host, model=None, serial=None, wwn=None, size_bytes=500 * GIB, hint="vda", machine_id_matches=True).name == "vda"
    empty = make_host(cache=None)
    for link in [k for k in empty.symlinks if k.startswith("/sys/block/")]:
        del empty.symlinks[link]
    assert guard.find_disk(empty, model=None, serial=None, wwn=None, size_bytes=500 * GIB, hint=None) is None

    def block_list(_path):
        raise OSError("sysfs unavailable")

    empty.listdir = block_list
    assert guard.find_disk(empty, model=None, serial=None, wwn=None, size_bytes=500 * GIB, hint=None) is None


def test_cei_06_backing_disk_of_a_file_and_of_a_filesystem_without_a_block_device():
    host = file_host()
    assert guard.backing_disk(host, "/bench/target.img") == "vda"
    host.add_mount("0:99", "/ovl", "overlay", "overlay")
    host.set_fs("/ovl", 1 << 40, 1 << 39)
    host.makedirs("/ovl")
    assert guard.backing_disk(host, "/ovl/x.img") is None
    facts = guard.disk_facts(host, "vda")
    assert (facts.name, facts.devno, facts.size_bytes, facts.scheduler, facts.rotational) == ("vda", "254:0", 500 * GIB, "[none] mq-deadline kyber", "1")


# -- the CLI: kinds ------------------------------------------------------------------------------------------------
def test_cei_06_ceilings_add_knows_what_each_source_measures_on(world):
    host, _ = world
    host = clone(host)
    add = lambda *extra: run(host, "ceilings", "add", "--dir", "read", "--workload", "rand4k", "--bps", "4e7", "--duration-s", "600", "--json", *extra)  # noqa: E731
    code, out, _ = add("--source", "benchmark")
    assert code == 0 and json.loads(out)["observation"]["kind"] == "raw"  # the calibrated target is a raw partition
    code, out, _ = add("--source", "probe")
    assert code == 0 and json.loads(out)["observation"]["kind"] == "probe"
    code, out, err = add("--source", "governor")
    assert code == 2 and "--kind" in err  # a governor measures on a scratch file or a raw device: it has to say which
    code, out, _ = add("--source", "governor", "--kind", "file")
    assert code == 0 and json.loads(out)["observation"]["kind"] == "file"
    code, out, err = add("--source", "benchmark", "--kind", "probe", "--dry-run")
    assert code == 2 and "cannot use kind" in err
    code, out, err = add("--source", "probe", "--kind", "file", "--dry-run")
    assert code == 2 and "cannot use kind" in err
    code, out, err = add("--source", "governor", "--kind", "probe", "--dry-run")
    assert code == 2 and "cannot use kind" in err
    code, out, err = run(host, "ceilings", "add", "--dir", "write", "--workload", "rand4k", "--bps", "4e7", "--duration-s", "600", "--source", "probe", "--kind", "probe")
    assert code == 2 and "a probe only reads" in err
    # a file-kind calibration's benchmark observations are file observations
    fh = file_host()
    calibrate(fh, target="/bench/target.img", work_dir="/bench/w")
    code, out, _ = run(fh, "ceilings", "add", "--dir", "write", "--workload", "rand4k", "--bps", "4e7", "--duration-s", "600", "--source", "benchmark", "--json")
    assert code == 0 and json.loads(out)["observation"]["kind"] == "file"


def test_cei_06_ceilings_summary_reports_each_kind_apart(world):
    host, out = world
    host = clone(host)
    digest = ceilings.identity_digest(out.result["identity"])
    ceilings.append(host, HIST, obs("file", d="write", ts=host.now() - 60), digest)
    ceilings.append(host, HIST, obs("probe", d="read", src="probe", ts=host.now() - 60), digest)
    code, text, _ = run(host, "ceilings", "summary", "--json")
    doc = json.loads(text)
    assert code == 0 and set(doc["by_kind"]) == {"raw", "file", "probe"} and "kind" not in doc
    code, text, _ = run(host, "ceilings", "summary", "--kind", "file", "--json")
    only = json.loads(text)
    assert only["kind"] == "file" and only["write"]["rand4k"]["n"] == 1 and "read" not in only


def test_cei_06_a_legacy_calibration_is_refused_for_new_observations_and_reads_as_a_foreign_history(world):
    host, _ = world
    legacy = clone(host)
    for name in ("calibration.json", "result.json"):
        doc = read_json(legacy, f"{STATE}/{name}")
        del doc["identity"]["disk_scope"]
        legacy.put(f"{STATE}/{name}", json.dumps(doc))
    code, _, err = run(legacy, "ceilings", "add", "--dir", "read", "--workload", "rand4k", "--bps", "4e7", "--duration-s", "600", "--source", "probe")
    assert code == 2 and "disk-scope" in err
    code, text, _ = run(legacy, "ceilings", "summary", "--json")
    assert code == 0 and json.loads(text).get("warnings") == ["history_ignored_identity_differs"]
    # the history of the old scheme (its header is a digest of devno + PARTUUID + kind) is moved aside by the next calibration, not refused after the run
    rows = legacy.read_text(HIST).splitlines()
    legacy.put(HIST, "\n".join([json.dumps({"header": True, "schema": 1, "identity_digest": "sha256:old-scheme"}), *rows[1:]]) + "\n")
    out = calibrate(legacy)
    assert out.status == "complete" and out.result["evidence"]["history_rotated"].startswith(f"{HIST}.superseded-")


def test_res_03_check_history_matches_the_default_filtered_summary(world):
    host, out = world
    host = clone(host)
    digest = ceilings.identity_digest(out.result["identity"])
    host.write_atomic(HIST, ceilings.header_line(digest) + "\n")
    ceilings.append(host, HIST, obs("file", d="read", ts=host.now() - 60), digest)
    summary = ceilings.summary(ceilings.load(host, HIST, digest)[0], host.now())
    c = check(host)
    assert summary["history"] == c.history == "single-run"
