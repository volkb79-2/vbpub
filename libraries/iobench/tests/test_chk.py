"""IOB-CHK / IOB-RES: check, show, schemas, identity, atomic files."""
import json
import os
import stat

import pytest

from iobench import ceilings, guard, proposals, result as res
from iobench.errors import Refusal
from iobench.host import RealHost
from iobench.result import LoadError

from helpers import INVERSION, calibrate, make_host, matrix, read_json

STATE = "/var/lib/iobench"


@pytest.fixture(scope="module")
def world():
    host = make_host(cache=INVERSION)
    out = calibrate(host)
    return host, out


def fresh_copy(host):
    """A shallow copy of the interesting state so one test can damage it."""
    import copy
    return copy.deepcopy(host)


def do_check(host, **kw):
    return res.check(host, STATE, now=host.now(), **kw)


def test_res_01_atomic_write_modes(tmp_path):
    real = RealHost()
    target = str(tmp_path / "state" / "result.json")
    res.write_json(real, target, {"schema": 1, "a": [1, 2]})
    st = os.stat(target)
    assert stat.S_IMODE(st.st_mode) == 0o644 and json.loads(open(target).read()) == {"schema": 1, "a": [1, 2]}
    assert [p.name for p in (tmp_path / "state").iterdir()] == ["result.json"]  # no temp file left behind
    # a failing write leaves the old file intact and removes its temp file
    class Boom:
        def __str__(self):
            raise RuntimeError("boom")
    with pytest.raises(Exception):
        real.write_atomic(target, Boom())  # type: ignore[arg-type]
    assert json.loads(open(target).read())["a"] == [1, 2] and [p.name for p in (tmp_path / "state").iterdir()] == ["result.json"]
    # the deterministic text (sorted keys, trailing newline) and the failed-record name
    assert res.dumps({"b": 1, "a": 2}) == '{\n "a": 2,\n "b": 1\n}\n'
    assert res.failed_path("/x/result.json") == "/x/result.last-failed.json"
    real.append_line(str(tmp_path / "l"), "one")
    real.append_line(str(tmp_path / "l"), "two\n")
    assert open(tmp_path / "l").read() == "one\ntwo\n"
    real.remove(str(tmp_path / "l"))
    real.remove(str(tmp_path / "l"))
    assert not real.exists(str(tmp_path / "l"))


def test_res_01_files_are_written_through_the_host_atomically(world):
    host, _ = world
    assert host.modes[f"{STATE}/result.json"] == 0o644 and host.modes[f"{STATE}/calibration.json"] == 0o644
    assert host.read_text(f"{STATE}/result.json").endswith("}\n")


def test_res_02_versioning(world):
    host, _ = world
    doc = read_json(host, f"{STATE}/result.json")
    h = fresh_copy(host)
    # an unknown major is refused with exit 5 / schema_unsupported
    h.put(f"{STATE}/result.json", json.dumps({**doc, "schema": 2}))
    c = do_check(h)
    assert (c.state, c.reasons, c.exit_code) == ("corrupt", ["schema_unsupported"], 5)
    # unknown keys are ignored; a newer minor with only extra keys is read
    h.put(f"{STATE}/result.json", json.dumps({**doc, "schema_minor": 7, "future_key": {"x": 1}}))
    assert do_check(h).state in ("current", "current_unproven")
    # a missing newer optional key is not_measured, never a default
    assert res.opt(doc, "a_newer_optional_key") == "not_measured" and res.opt(doc, "schema") == 1
    # truncated / invalid JSON, a non-object, missing required keys
    for broken in ('{"schema": 1, "sch', "[1, 2]", json.dumps({"schema": 1, "schema_minor": 0})):
        h.put(f"{STATE}/result.json", broken)
        assert do_check(h).reasons == ["corrupt"]


def test_res_03_identity_validity(world):
    host, _ = world
    base = do_check(host)
    assert base.state in ("current", "current_unproven")
    cases = {
        "partuuid": lambda h: setattr(h.devices["/dev/vda7"], "partuuid", "00000000-0000-0000-0000-0000deadbeef"),
        "size": lambda h: (setattr(h.devices["/dev/vda7"], "size", h.devices["/dev/vda7"].size + 1048576),
                           h.put("/sys/dev/block/254:7/size", f"{h.devices['/dev/vda7'].size // 512}\n")),
    }
    for name, flip in cases.items():
        h = fresh_copy(host)
        flip(h)
        c = do_check(h)
        assert c.state == "stale" and c.exit_code == 3, name
    # a vanished device is a changed identity, not an unreadable result
    gone = fresh_copy(host)
    del gone.devices["/dev/vda7"]
    gone.symlinks.pop("/sys/dev/block/254:7", None)
    assert do_check(gone).state == "stale"
    # the strict model/serial/wwn fields count only when non-null in the stored record
    strict = fresh_copy(host)
    doc = read_json(strict, f"{STATE}/result.json")
    doc["identity"]["disk"]["serial"] = "SER-A"
    strict.put(f"{STATE}/result.json", json.dumps(doc))
    assert do_check(strict).state == "stale"  # the live disk reports no serial
    # advisory fields only warn
    adv = fresh_copy(host)
    adv.put("/proc/sys/kernel/osrelease", "7.3.1\n")
    adv.put("/sys/devices/pci0/block/vda/queue/scheduler", "none [mq-deadline] kyber\n")
    adv.files["/proc/meminfo"] = ""
    adv.dynamic["/proc/meminfo"] = lambda: "MemTotal: 4000000 kB\nMemAvailable: 1 kB\nDirty: 0 kB\nWriteback: 0 kB\n"
    adv.mounts = [(ns, dv, mp, "/dev/vda9" if mp == "/" else src, fs, o) for ns, dv, mp, src, fs, o in adv.mounts]
    adv.set_fs("/bench", 30 << 30, 20 << 30)
    adv.devices["/dev/vda5"].fs_uuid = "99999999-9999-9999-9999-999999999999"
    c = do_check(adv)
    assert c.state in ("current", "current_unproven")
    assert {"kernel_changed", "scheduler_changed", "ram_changed", "root_source_changed", "workdir_fs_changed"} <= set(c.warnings)
    old = fresh_copy(host)
    old.clock += 181 * 86400
    assert "older_than_180_days" in res.check(old, STATE, now=old.now()).warnings
    assert res.identity_differs(read_json(host, f"{STATE}/result.json")["identity"], None)


def test_res_04_limits_recorded(world):
    host, out = world
    q = read_json(host, f"{STATE}/result.json")["quality"]
    assert q["cache_proof"] in ("none", "working-set", "limited-by-budget") and all(proposals.limit_ok(x) for x in q["limits"])
    assert q["limits"] and do_check(host).limits == q["limits"]
    assert any(x.startswith("bucket_exclusion_s<=") for x in q["limits"])


def test_chk_01_exit_codes(world):
    host, _ = world
    # absent (4): no result; with --calibration, no calibration
    empty = make_host(cache=INVERSION)
    assert (do_check(empty).state, do_check(empty).exit_code) == ("absent", 4)
    assert do_check(empty, calibration=True).exit_code == 4
    # current (0) needs EVERY requested proposal proven; current_unproven (7) otherwise, only for requested groups
    c = do_check(host)
    assert c.state == "current_unproven" and c.exit_code == 7
    assert set(c.sections) == set(proposals.SECTIONS)
    only_qos_min = do_check(host, sections=("iocost_qos",))
    assert only_qos_min.sections["iocost_qos"]["proven"] >= 1
    # a result whose requested group is entirely proven is exit 0
    h = fresh_copy(host)
    doc = read_json(h, f"{STATE}/result.json")
    for group in doc["proposals"].values():
        if isinstance(group, dict):
            for key, v in list(proposals.iter_values(group)):
                v.pop("reasons", None)
                v["status"] = "proven"
    h.put(f"{STATE}/result.json", json.dumps(doc))
    ok = do_check(h)
    assert (ok.state, ok.exit_code) == ("current", 0) and all(not s["unproven"] and not s["absent"] for s in ok.sections.values())
    # exit 7 is never raised for a group the caller did not request
    doc2 = read_json(h, f"{STATE}/result.json")
    doc2["proposals"]["iocost_model"]["wbps"] = {"value": 1, "status": "unproven", "reasons": ["worst_from_single_run"], "rule": "R6", "inputs": {}, "basis": "single-run", "flags": []}
    h.put(f"{STATE}/result.json", json.dumps(doc2))
    assert do_check(h, sections=("caps",)).state == "current" and do_check(h, sections=("iocost_model",)).exit_code == 7
    # stale (3)
    s = fresh_copy(host)
    s.devices["/dev/vda7"].partuuid = "x"
    assert do_check(s).exit_code == 3
    # exit 5: corrupt, schema_unsupported, unreadable, calibration_missing
    k = fresh_copy(host)
    k.put(f"{STATE}/result.json", "{ nope")
    assert (do_check(k).exit_code, do_check(k).reasons) == (5, ["corrupt"])
    u = fresh_copy(host)
    u.dynamic[f"{STATE}/result.json"] = lambda: (_ for _ in ()).throw(PermissionError("EACCES"))
    assert do_check(u).reasons == ["unreadable"] and do_check(u).exit_code == 5
    m = fresh_copy(host)
    m.files.pop(f"{STATE}/calibration.json")
    assert (do_check(m).exit_code, do_check(m).reasons) == (5, ["calibration_missing"])
    swapped = fresh_copy(host)
    cal = read_json(swapped, f"{STATE}/calibration.json")
    cal["calibration"]["id"] = "cal-other"
    swapped.put(f"{STATE}/calibration.json", json.dumps(cal))
    assert do_check(swapped).reasons == ["calibration_missing"]
    # aborted_only (6): the newest record is aborted/failed and no good record exists
    a = make_host(cache=INVERSION)
    a.put(f"{STATE}/result.last-failed.json", json.dumps({"schema": 1, "schema_minor": 0, "run": {"status": "aborted", "started": "2026-10-21T03:00:00Z"}}))
    assert (do_check(a).state, do_check(a).exit_code) == ("aborted_only", 6)
    # a good record exists and a newer aborted one: the state stands with the warning last_run_aborted
    b = fresh_copy(host)
    b.put(f"{STATE}/result.last-failed.json", json.dumps({"schema": 1, "schema_minor": 0, "run": {"status": "aborted", "started": "2030-01-01T00:00:00Z"}}))
    cb = do_check(b)
    assert cb.state in ("current", "current_unproven") and "last_run_aborted" in cb.warnings
    older = fresh_copy(host)
    older.put(f"{STATE}/result.last-failed.json", json.dumps({"schema": 1, "schema_minor": 0, "run": {"status": "aborted", "started": "2020-01-01T00:00:00Z"}}))
    assert "last_run_aborted" not in do_check(older).warnings
    broken_failed = fresh_copy(host)
    broken_failed.put(f"{STATE}/result.last-failed.json", "{ nope")
    assert "last_run_aborted" in do_check(broken_failed).warnings
    # --require-strong-identity adds identity_weak to every proposal when the identity is weak
    weak = do_check(host, require_strong_identity=True)
    assert all("identity_weak" in u["reasons"] for s in weak.sections.values() for u in s["unproven"]) and weak.identity_strength == "weak"
    assert weak.sections["iocost_qos"]["proven"] == 0
    # the JSON shape
    d = c.as_dict()
    assert set(d) == {"state", "reasons", "warnings", "limits", "identity_strength", "sections", "result", "calibration_id", "history"}
    assert d["history"] == "recorded" and d["calibration_id"].startswith("cal-")  # the benchmark appended its own plateaus (CEI-05)


def test_chk_01_calibration_check_uses_the_calibration_file(world):
    host, _ = world
    c = do_check(host, calibration=True)
    assert c.state in ("current", "current_unproven") and c.result.endswith("calibration.json")
    assert set(c.sections) == set(proposals.SECTIONS) and c.sections["writeback"]["proven"] == 0


def test_chk_02_show_is_read_only_and_exact(world):
    host, _ = world
    before = dict(host.files)
    prop = res.show(host, STATE, "proposals", now=host.now())
    assert prop["object"] == read_json(host, f"{STATE}/result.json")["proposals"] and prop["stale"] is False
    assert res.show(host, STATE, "proposals", section="caps", now=host.now())["object"] == prop["object"]["caps"]
    assert res.show(host, STATE, "evidence", now=host.now())["object"] == read_json(host, f"{STATE}/result.json")["evidence"]
    assert res.show(host, STATE, "calibration", now=host.now())["object"] == read_json(host, f"{STATE}/calibration.json")["calibration"]
    ce = res.show(host, STATE, "ceilings", now=host.now())
    assert ce["header"]["header"] is True and ce["summary"]["history"] in ("recorded", "single-run")
    paths = res.show(host, STATE, "paths", now=host.now())
    assert paths["result"] == f"{STATE}/result.json" and paths["dir"] == STATE
    assert host.files == before  # it never recomputes nor modifies
    # unknown section / target -> refusal (exit 2)
    with pytest.raises(Refusal):
        res.show(host, STATE, "proposals", section="bogus", now=host.now())
    with pytest.raises(Refusal):
        res.show(host, STATE, "bogus", now=host.now())
    # absent -> 4, corrupt/unsupported -> 5; 3 is not used (the file is printed with stale: true)
    empty = make_host(cache=INVERSION)
    for what in ("proposals", "evidence", "calibration", "ceilings"):
        with pytest.raises(LoadError) as err:
            res.show(empty, STATE, what, now=empty.now())
        assert err.value.reason == "absent"
    stale = fresh_copy(host)
    stale.devices["/dev/vda7"].partuuid = "x"
    assert res.show(stale, STATE, "proposals", now=stale.now())["stale"] is True
    bad = fresh_copy(host)
    bad.put(f"{STATE}/result.json", "{ nope")
    with pytest.raises(LoadError) as err:
        res.show(bad, STATE, "proposals", now=bad.now())
    assert err.value.reason == "corrupt"


def test_chk_03_guaranteed_keys_present_on_golden_results(world):
    host, _ = world
    for doc in (read_json(host, f"{STATE}/result.json"),):
        for key in res.REQUIRED_TOP:
            assert key in doc
        assert set(doc["proposals"]) >= {"writeback", "caps", "iocost_model", "iocost_qos"} and "dev_aggregate" in doc["proposals"]["caps"]
        assert "quality" in doc and doc["schema"] == 1
        for _, v in proposals.iter_values(doc["proposals"]["iocost_model"]):
            assert v["status"] in ("proven", "unproven", "absent") and "rule" in v
    # the wizard precedence contract is stated by apply requirements
    assert proposals.apply_requirement({"status": "unproven"}) == "requires_accept" and proposals.apply_requirement({"status": "proven"}) == "ok"


def test_chk_04_state_dir_resolution(world):
    host, _ = world
    assert res.state_dir_of(host, None) == "/var/lib/iobench"
    host.env["IOBENCH_DIR"] = "/srv/iob"
    assert res.state_dir_of(host, None) == "/srv/iob" and res.state_dir_of(host, "/tmp/x") == "/tmp/x"
    del host.env["IOBENCH_DIR"]
    assert res.paths("/d")["ceilings"] == "/d/ceilings.jsonl"
