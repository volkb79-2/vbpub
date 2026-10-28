import json
from pathlib import Path

import pytest

from tnt import matrix
import asyncio

from tnt.actions import Action, Kind, Plan, RpcCall, apply_plan, make_plan, refusal_for
from tnt.domain import Disk, Partition, human_bytes, human_free
from tnt.errors import Refused
from tnt.policy import SafetyState, is_destructive_method, is_read_method, normalise_method
from tnt.transport import GuardedRpc
from tnt.tui.app import TntApp
from tnt.menu import MENU, walk
from tnt.session import open_session
from tnt.testing import GIB, FakeServer

SNAPSHOT = Path(__file__).resolve().parent.parent / "docs" / "ACTION-MATRIX.snapshot.md"


def test_free_space_distinguishes_unknown_from_empty_and_full():
    assert Disk("a", 100).free_upper_bound_bytes is None
    assert Disk("a", 100, partitions=()).free_upper_bound_bytes == 100
    assert Disk("a", 100, partitions=(Partition("a1", 100),)).free_upper_bound_bytes == 0
    assert Disk("a", 100, partitions=(Partition("a1", 150),)).free_upper_bound_bytes == 0
    assert Disk("a", 100, partitions=(Partition("a1", 30),)).as_dict()["free_upper_bound_bytes"] == 70
    assert Disk("a", None, partitions=()).free_upper_bound_bytes is None  # no disk size: unknown, not 0
    assert Disk("a", 100, partitions=(Partition("a1", None),)).free_upper_bound_bytes is None  # a partition of unknown size


def test_human_bytes_and_the_upper_bound_marker():
    assert human_bytes(None) == "?" and human_bytes(512) == "512 B" and human_bytes(2048) == "2.0 KiB"
    assert human_bytes(3 * 1024 ** 5) == "3.0 PiB" and human_bytes(5 * 1024 ** 6) == "5120.0 PiB"
    assert human_free(None) == "?" and human_free(2048) == "<= 2.0 KiB"


def _inventory(disks, parts):
    server = FakeServer(disks=disks, parts=parts)
    server.start()
    try:
        s = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
        out = s.disks()
        s.close()
        return out
    finally:
        server.stop()


def test_disk_inventory_from_fake_server(server):
    s = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
    disks = {d.name: d for d in s.disks()}
    s.close()
    assert disks["sda"].free_upper_bound_bytes == 512 * GIB - (1 << 20) - (512 << 20) - 100 * GIB and disks["sda"].pool == "boot-pool"
    assert disks["sdc"].free_upper_bound_bytes == 256 * GIB and disks["sdc"].partitions == ()
    assert disks["sdd"].partitions is None and disks["sdd"].free_upper_bound_bytes is None


def test_disk_details_flat_list_shape_and_tolerance():
    server = FakeServer(details_as_flat_list=True)
    with server:
        s = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
        assert {d.name: len(d.partitions) for d in s.disks() if d.partitions is not None} == {"sda": 3, "sdb": 1, "sdc": 0}
        s.close()


def test_unparseable_sizes_make_free_space_unknown_never_zero():
    (d,) = _inventory([{"name": "x", "size": "n/a", "serial": None, "type": None}], {"x": [{"name": "x1", "size": "bad", "start": "s"}]})
    assert d.size is None and d.partitions[0].size is None and d.partitions[0].start is None
    assert d.free_upper_bound_bytes is None
    (d,) = _inventory([{"name": "x", "size": 100}], {"x": [{"name": "x1", "size": 10}, {"name": "x2"}]})
    assert d.free_upper_bound_bytes is None  # one partition without a size poisons the sum
    (d,) = _inventory([{"name": "x", "size": -5}], {"x": [{"name": "x1", "size": 1}]})
    assert d.size is None
    (d,) = _inventory([{"name": "x", "size": True}], {"x": [{"name": "x1", "size": 1}]})
    assert d.size is None


def test_missing_disk_size_renders_as_unknown():
    from tnt.query import disk_row
    row = disk_row(Disk("x", None, partitions=()))
    assert row[1] == "?" and row[7] == "?"


def test_empty_partition_lists_from_an_api_that_joins_none_read_unknown():
    # nothing anywhere reports a partition: "no partitions" cannot be told from "cannot report partitions"
    disks = _inventory([{"name": "a", "size": 100, "pool": None}, {"name": "b", "size": 100, "pool": None}], {"a": [], "b": []})
    assert all(d.partitions is None and d.free_upper_bound_bytes is None for d in disks)


def test_a_pooled_disk_with_an_empty_partition_list_reads_unknown():
    disks = {d.name: d for d in _inventory(
        [{"name": "a", "size": 100, "pool": "tank"}, {"name": "b", "size": 100, "pool": None}, {"name": "c", "size": 100, "pool": None}],
        {"a": [], "b": [{"name": "b1", "size": 10}], "c": []})}
    assert disks["a"].partitions is None  # a pool member has partitions: the empty list is a lie
    assert disks["c"].partitions == () and disks["c"].free_upper_bound_bytes == 100  # corroborated: the API joins partitions
    assert disks["b"].free_upper_bound_bytes == 90


def test_disk_json_names_the_bound():
    assert "free_upper_bound_bytes" in Disk("a", 1, partitions=()).as_dict() and "free_bytes" not in Disk("a", 1).as_dict()


# -- safety: S1..S4 --------------------------------------------------------------------------------------
from helpers import SIZE, Stub, destructive, guarded, run_plan  # noqa: E402


@pytest.mark.parametrize("method,params", [
    ("core.call", ("system.reboot",)), ("core.bulk", ("pool.export", [[1]])), ("Core.Call", ()), ("system.reboot", ()), (" System.Reboot ", ()),
    ("system.shutdown", ()), ("zfs.pool.delete", ("boot-pool",)), ("zfs.resource.destroy", ("boot-pool/ROOT",)),
    ("zfs.resource.destroy", ({"path": "BOOT-POOL/ROOT"},)), ("filesystem.setperm", ({"path": "/mnt/boot-pool/ROOT"},)),
    ("pool.dataset.update", ("boot-pool/ROOT", {})), ("boot.environment.destroy", ("default",)), ("pool.export", (2,)),
    ("pool.export", (99,)), ("pool.update", (2, {})), ("pool.delete", (2,)), ("auth.set_password", ()), ("boot.attach", ("sdc",)),
])
def test_refusal_bypasses_are_stopped_through_apply_plan(method, params):
    stub = Stub()
    with pytest.raises(Refused):
        run_plan(stub, destructive(method, *params), typed="x")
    assert method.strip().lower() not in stub.executed()


def test_default_deny_ordinary_calls_run_only_when_declared_and_clean():
    stub = Stub()
    assert run_plan(stub, destructive("pool.export", 1), typed="x") == ["done"]  # tank, resolved from the probe
    assert ("pool.export", (1,)) in stub.log
    assert run_plan(Stub(), destructive("pool.create", {"name": "boot-pool-data"}), typed="x") == ["done"]  # a similar name is not boot-pool


def test_names_are_normalised_and_junk_names_are_refused():
    assert normalise_method("  Pool.Export ") == "pool.export"
    for junk in ("pool export", "pool.export;x", "pool", "", "pool..x", 7, None):
        with pytest.raises(Refused, match="method"):
            normalise_method(junk)
    assert is_read_method("DISK.QUERY") and not is_read_method("pool..query") and not is_read_method(5)
    assert refusal_for("SYSTEM.REBOOT") is None and refusal_for(" core.call ")
    assert is_destructive_method("SYSTEM.REBOOT")
    assert refusal_for("core.get_methods") is None and refusal_for("auth.me") is None and refusal_for("disk.query") is None


@pytest.mark.parametrize("target,refused", [("sda", True), ("/dev/sda", True), ("sdb", True), ("sdc", False), ("/dev/sdc", False),
                                            ("sda3", True), ("nosuch", True)])
def test_disk_wipe_protection_is_computed_from_live_state(target, refused):
    stub = Stub()
    if refused:
        with pytest.raises(Refused):
            run_plan(stub, destructive("disk.wipe", target, "QUICK", job=True), typed="x")
        assert "disk.wipe" not in stub.executed()
    else:
        assert run_plan(stub, destructive("disk.wipe", target, "QUICK", job=True), typed="x") == ["done"]


def test_disk_wipe_resolves_by_id_paths_and_mounted_partitions():
    by_id = {"pool.query": [{"id": 1, "name": "tank", "topology": {"data": [{"path": "/dev/disk/by-id/ata-DATADISK_DATA0002-part1"}]}}],
             "boot.get_state": {"topology": {}}, "disk.query": [{"name": "sdb", "serial": "DATA0002", "size": SIZE}, {"name": "sdc", "serial": "SPARE003", "size": SIZE}]}
    with pytest.raises(Refused, match="wipe"):
        run_plan(Stub(**by_id), destructive("disk.wipe", "sdb"), typed="x")
    assert run_plan(Stub(**by_id), destructive("disk.wipe", "sdc"), typed="x") == ["done"]
    mounted = {"pool.query": [], "boot.get_state": {}, "disk.query": [{"name": "sdc", "size": SIZE, "partitions": [{"name": "sdc1", "mountpoint": "/data"}]}, {"name": "sdd", "size": SIZE, "partitions": [{"name": "sdd1"}, "junk"]}]}
    with pytest.raises(Refused, match="mounted partition"):
        run_plan(Stub(**mounted), destructive("disk.wipe", "sdc"), typed="x")
    assert run_plan(Stub(**mounted), destructive("disk.wipe", "sdd"), typed="x") == ["done"]


@pytest.mark.parametrize("override", [
    {"pool.query": RuntimeError("down")},
    {"pool.query": {"not": "a list"}},
    {"boot.get_state": ["x"]},
    {"disk.query": [{"serial": "no name"}]},
    {"disk.query": ["junk"]},
    {"pool.query": [{"id": "1", "name": "tank"}]},
    {"pool.query": [{"id": 1, "name": "tank", "topology": {"data": [{"path": "/dev/disk/by-id/unknown-thing"}]}}]},
    {"pool.query": [{"id": 1, "name": "tank", "topology": {"data": [{"disk": "zvol-weird"}]}}]},
])
def test_wipe_fails_closed_when_membership_cannot_be_determined(override):
    stub = Stub(**override)
    with pytest.raises(Refused, match="safety-state|membership|unexpected"):
        run_plan(stub, destructive("disk.wipe", "sdc"), typed="x")
    assert "disk.wipe" not in stub.executed()


def test_disk_wipe_with_dict_style_or_missing_params_raises_refused_not_typeerror():
    state = SafetyState({}, frozenset(), frozenset({"sdc"}))
    for params in ([{"dev": "sdc"}], [], [5]):
        with pytest.raises(Refused, match="disk-wipe"):
            refusal_for("disk.wipe", params, state)
    assert "no safety state" in refusal_for("disk.wipe", ["sdc"], None)
    with pytest.raises(Refused, match="disk-wipe"):
        run_plan(Stub(), destructive("disk.wipe", {"dev": "sdc"}), typed="x")


def test_pool_id_without_state_is_refused_and_bool_ids_are_not_ids():
    assert "cannot be resolved" in refusal_for("pool.export", [1], None)
    state = SafetyState({1: "tank", 2: "boot-pool"}, frozenset(), frozenset())
    assert refusal_for("pool.export", [1], state) is None and refusal_for("pool.export", ["tank"], state) is None
    for form in (True, 2.0, 1.0, "2", " 1 ", [2], (1,), {"id": 2}, {"name": "tank"}, "unknown", None, b"1"):
        assert refusal_for("pool.export", [form], state), form
    assert refusal_for("pool.export", [], state) is None  # no parameter, nothing to resolve (the call itself fails on the host)
    assert refusal_for("pool.dataset.create", ["tank/x"], None) is None  # a dataset name, not a pool id
    assert refusal_for("pool.scrub.delete", [5], state) is None  # a task id: pool.scrub.* has no pool-id position
    assert refusal_for("pool.create", [{"name": "boot-pool-data", "topology": {}}], state) is None


# -- S3: the guarded client enforces by itself ------------------------------------------------------------------
def test_write_session_refuses_everything_outside_an_executing_plan():
    stub = Stub()
    rpc = guarded(stub)
    for method in ("pool.create", "pool.export", "core.call", "system.reboot", "zfs.pool.delete", "disk.wipe", "filesystem.setperm"):
        with pytest.raises(Refused):
            rpc.call(method, 1)
    assert stub.log == []
    assert rpc.call("system.info") == "done"  # reads stay open


def test_a_plan_grants_exactly_its_calls_in_order_and_nothing_else():
    stub = Stub()
    rpc = guarded(stub)
    calls = [("pool.scrub", ("tank",), True), ("pool.update", (1, {"a": 1}), False)]
    with rpc.authorized(calls, SafetyState({1: "tank"}, frozenset(), frozenset())):
        with pytest.raises(Refused, match="not-in-plan"):
            rpc.call("pool.update", 1, {"a": 1})  # out of order
        with pytest.raises(Refused, match="not-in-plan"):
            rpc.call("pool.scrub", "tank")  # job flag differs
        with pytest.raises(Refused, match="not-in-plan"):
            rpc.call("pool.scrub", 1, job=True)  # parameters differ
        rpc.call("pool.scrub", "tank", job=True)
        with pytest.raises(Refused, match="nested-plan"):
            with rpc.authorized([], None):
                pass
        rpc.call("pool.update", 1, {"a": 1})
        with pytest.raises(Refused, match="not-in-plan"):
            rpc.call("pool.update", 1, {"a": 1})  # already consumed
    with pytest.raises(Refused):
        rpc.call("pool.scrub", "tank", job=True)  # the grant ends with the plan
    with pytest.raises(Refused, match="no-plan"):
        rpc.call("pool.create", {"name": "x"})


def test_a_granted_call_must_still_pass_the_refusal_list():
    rpc = guarded(Stub())
    with rpc.authorized([("pool.export", (2,), False), ("core.call", ("x",), False)], SafetyState({2: "boot-pool"}, frozenset(), frozenset())):
        with pytest.raises(Refused, match="refusal-list"):
            rpc.call("pool.export", 2)
        with pytest.raises(Refused, match="refusal-list"):
            rpc.call("core.call", "x")
    with pytest.raises(Refused):
        guarded(Stub(), read_only=True).call("core.call", "x")


def test_read_only_sessions_cannot_execute_a_plan():
    with pytest.raises(Refused, match="read-only"):
        with guarded(Stub(), read_only=True).authorized([], None):
            pass
    with pytest.raises(Refused, match="read-only"):
        run_plan(Stub(), destructive("pool.export", 1), typed="x", read_only=True)
    with pytest.raises(Refused, match="read-only"):
        run_plan(Stub(), destructive("pool.export", 1), typed="x", allow_write=False)


def test_the_tui_path_does_not_hand_out_an_unguarded_object(server):
    s = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
    assert isinstance(s.rpc, GuardedRpc) and not s.rpc.read_only

    async def go():
        app = TntApp(s)
        async with app.run_test(size=(120, 30)):
            assert isinstance(app.session.rpc, GuardedRpc)
            for method in ("pool.export", "core.call", "system.reboot", "disk.wipe"):
                with pytest.raises(Refused):
                    app.session.rpc.call(method, 1)
    asyncio.run(go())
    assert not [m for m in server.called() if m in ("pool.export", "core.call", "system.reboot", "disk.wipe")]
    s.close()


def test_apply_plan_requires_a_guarded_client():
    stub = Stub()
    with pytest.raises(Refused, match="unguarded"):
        make_plan(destructive("pool.export", 1), stub)
    plan = make_plan(destructive("pool.export", 1), guarded(stub))
    with pytest.raises(Refused, match="unguarded"):
        apply_plan(plan, stub, allow_write=True, typed_confirmation="x")


# -- S4: action validity, plan and gates -----------------------------------------------------------------------
def test_a_read_action_may_only_call_read_methods():
    bad = Action("R-1", Kind.READ, "sneaky", (RpcCall("pool.export", (1,)),))
    with pytest.raises(Refused, match="read-action"):
        make_plan(bad, guarded(Stub()))
    with pytest.raises(Refused, match="read-action"):
        apply_plan(Plan(bad, "x"), guarded(Stub()), allow_write=True)
    ro = guarded(Stub(), read_only=True)
    good = Action("R-2", Kind.READ, "list", (RpcCall("disk.query"),))
    assert apply_plan(make_plan(good, ro), ro, allow_write=False) == [Stub().results["disk.query"]]
    job = Action("R-3", Kind.READ, "job", (RpcCall("pool.query", job=True),))
    assert apply_plan(make_plan(job, ro), ro, allow_write=False) == [Stub().results["pool.query"]]


def test_a_destructive_action_needs_a_probe():
    with pytest.raises(Refused, match="empty-probe"):
        make_plan(destructive("pool.export", 1, probe=()), guarded(Stub()))
    with pytest.raises(Refused, match="empty-probe"):
        apply_plan(Plan(destructive("pool.export", 1, probe=()), "x"), guarded(Stub()), allow_write=True, typed_confirmation="x")


def test_probes_must_be_reads_even_in_a_write_session_and_actions_need_calls():
    action = destructive("pool.export", 1, probe=(RpcCall("pool.update", (1, {})),))
    stub = Stub()
    with pytest.raises(Refused, match="probe-not-read"):
        make_plan(action, guarded(stub))
    assert "pool.update" not in stub.executed()
    with pytest.raises(Refused, match="empty-action"):
        make_plan(Action("E-1", Kind.MUTATE, "nothing", ()), guarded(Stub()))


def test_the_fingerprint_binds_the_calls_and_the_confirmation_target():
    stub = Stub()
    rpc = guarded(stub)
    plan = make_plan(destructive("pool.export", 1), rpc)
    swapped = Plan(destructive("pool.export", 1, {"force": True}), plan.fingerprint, plan.session_id, plan.nonce)
    with pytest.raises(Refused, match="state-drift"):
        apply_plan(swapped, rpc, allow_write=True, typed_confirmation="x")
    other_target = Plan(Action("T-1", Kind.DESTRUCTIVE, "test", plan.action.calls, plan.action.probe, confirm_target="y"), plan.fingerprint, plan.session_id, plan.nonce)
    with pytest.raises(Refused, match="state-drift"):
        apply_plan(other_target, rpc, allow_write=True, typed_confirmation="y")
    assert "pool.export" not in stub.executed()
    assert apply_plan(plan, rpc, allow_write=True, typed_confirmation="x") == ["done"]


def _action(kind=Kind.DESTRUCTIVE, calls=(RpcCall("pool.export", (1,)),), target="x"):
    return Action("B-99", kind, "Export tank", calls, probe=(RpcCall("disk.query", ([], {})),), confirm_target=target)


def test_plan_renders_the_exact_calls():
    stub = Stub()
    rpc = guarded(stub)
    plan = make_plan(_action(calls=(RpcCall("disk.wipe", ("sdc", "QUICK"), job=True),)), rpc)
    text = plan.render()
    assert 'disk.wipe("sdc", "QUICK")  [job]' in text and "type 'x'" in text and plan.fingerprint[:12] in text
    assert stub.log == [("disk.query", ([], {}))]  # planning only reads
    assert "type " not in make_plan(_action(Kind.MUTATE, (RpcCall("pool.scrub", (1,)),), target=None), rpc).render()


def test_apply_gates_in_order_and_execution():
    stub = Stub()
    rpc = guarded(stub)
    plan = make_plan(_action(), rpc)
    with pytest.raises(Refused, match="read-only"):
        apply_plan(plan, rpc, allow_write=False, typed_confirmation="x")
    with pytest.raises(Refused, match="typed-confirmation"):
        apply_plan(plan, rpc, allow_write=True, typed_confirmation="nope")
    with pytest.raises(Refused, match="empty-confirm"):
        make_plan(_action(target=None), rpc)
    with pytest.raises(Refused, match="empty-confirm"):
        make_plan(_action(target="  "), rpc)
    assert "pool.export" not in stub.executed()
    stub.results["disk.query"] = stub.results["disk.query"] + [{"name": "sde"}]
    with pytest.raises(Refused, match="state-drift"):
        apply_plan(plan, rpc, allow_write=True, typed_confirmation="x")
    assert "pool.export" not in stub.executed()
    stub.results["disk.query"] = stub.results["disk.query"][:-1]
    assert apply_plan(plan, rpc, allow_write=True, typed_confirmation="x") == ["done"]
    mutate = make_plan(_action(Kind.MUTATE, (RpcCall("pool.scrub", ("tank",), True),), None), rpc)
    assert apply_plan(mutate, rpc, allow_write=True) == ["done"] and stub.log[-1] == ("pool.scrub", ("tank",))
    stub.log.clear()
    with pytest.raises(Refused, match="plan-reused"):
        apply_plan(mutate, rpc, allow_write=True)
    assert "pool.scrub" not in stub.executed()


def test_refusal_list_texts():
    assert refusal_for("system.reboot") is None  # executable only inside a guarded, fully preflighted typed plan
    assert "B-07" in refusal_for("boot.attach", ("vda4",))
    assert "credentials" in refusal_for("auth.set_password")
    assert "A-08" in refusal_for("pool.export", (0, {"x": "boot-pool"}))
    assert refusal_for("pool.export", ("tank",), SafetyState({1: "tank"}, frozenset(), frozenset())) is None
    assert refusal_for("pool.dataset.create", ("tank/boot-pool-notes",)) is None


def test_menu_mirrors_the_webui_labels():
    top = [i.label for i in MENU]
    assert top == ["Dashboard", "Storage", "Datasets", "Shares", "Data Protection", "Credentials", "Containers", "Virtual Machines", "Apps",
                   "Reporting", "System"]
    labels = {i.label for _, i in walk()}
    assert {"Disks", "Boot", "Network", "Services", "Local Users", "S3 Access Keys"} <= labels
    keys = [i.key for _, i in walk()]
    assert len(keys) == len(set(keys))
    assert [i.key for _, i in walk() if i.status == "implemented"] == [
        "storage", "storage/disks", "datasets", "sharing", "data-protection", "snapshot-tasks", "snapshots",
        "replication", "cloud-sync", "credentials", "users", "api-keys", "containers", "vm", "apps", "system",
        "update", "reboot", "shutdown", "general", "network", "audit",
    ]
    assert [i.label for _, i in walk() if i.tnt_only] == [
        "Free space and partitions", "Boot pool data", "My API Keys", "Reboot", "Shutdown",
    ]


def test_menu_matrix_ids_exist_in_the_snapshot():
    ids = {r.id for r in matrix.load(SNAPSHOT)}
    used = {m for _, i in walk() for m in i.matrix}
    assert used <= ids


def test_matrix_parser_reads_the_snapshot():
    rows = matrix.load(SNAPSHOT)
    assert len(rows) == 16 and rows[0].id == "A-01" and rows[0].methods == ("pool.dataset.create",)
    assert {r.id for r in matrix.backlog_rows(rows)} == {"A-09", "B-01", "B-02", "B-03", "B-06"}
    b07 = next(r for r in rows if r.id == "B-07")
    assert b07.methods == ("boot.attach",) and b07.verdict == "n/a"
    with pytest.raises(ValueError, match="lacks column"):
        matrix.parse("| id | x |\n|---|---|\n| A-01 | short |\n")
    head = "| " + " | ".join(matrix.REQUIRED) + " |\n"
    assert matrix.parse(head + "|---|\n| nonsense | 1 | 2 | 3 | 4 | 5 | 6 |\n| A-01 | short |\n") == []
    assert matrix.parse("no table at all") == []


BACKLOG = Path(__file__).resolve().parent.parent / "BACKLOG.md"


def test_backlog_matrix_column_covers_every_tui_matrix_row():
    rows = matrix.load(SNAPSHOT)
    text = BACKLOG.read_text()
    assert matrix.uncovered(rows, text) == []
    assert {"B-01", "B-02", "B-03", "B-06", "A-09"} <= set().union(*matrix.backlog_matrix_ids(text).values())


def test_backlog_drift_check_fails_when_a_carrier_item_is_removed():
    rows = matrix.load(SNAPSHOT)
    text = BACKLOG.read_text()
    carriers = matrix.backlog_matrix_ids(text)
    needed = {r.id for r in matrix.backlog_rows(rows)}
    for n in range(1, 9):
        item = f"TNT-{n:02d}"
        removed = "\n".join(line for line in text.splitlines() if not line.startswith(f"| {item} |"))
        only_here = {i for i in carriers[item] & needed if not any(i in ids for k, ids in carriers.items() if k != item)}
        assert set(matrix.uncovered(rows, removed)) == only_here, item
    # the two items that are the sole carrier of a row: dropping them must be caught
    assert set(matrix.uncovered(rows, "\n".join(l for l in text.splitlines() if not l.startswith("| TNT-05 |")))) == {"B-02", "B-03"}
    assert matrix.uncovered(rows, "\n".join(l for l in text.splitlines() if not l.startswith("| TNT-07 |"))) == ["A-09"]


def test_a_row_id_only_in_prose_or_notes_does_not_count():
    rows = matrix.load(SNAPSHOT)
    prose = "A-09 B-01 B-02 B-03 B-06 are all mentioned here in prose.\n| TNT-90 | none | notes mention A-09 and B-06 | S | M1 | x |\n"
    assert set(matrix.uncovered(rows, prose)) == {"A-09", "B-01", "B-02", "B-03", "B-06"}
    assert matrix.uncovered(rows, "") == ["A-09", "B-01", "B-02", "B-03", "B-06"]


def test_every_refused_matrix_method_is_on_the_refusal_list():
    rows = {r.id: r for r in matrix.load(SNAPSHOT)}
    for method in rows["B-07"].methods:
        assert refusal_for(method, ("vda4",)) is not None
