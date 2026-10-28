import json
from pathlib import Path

import pytest

from tnt import matrix
from tnt.actions import Action, Kind, RpcCall, apply_plan, make_plan, refusal_for
from tnt.domain import Disk, Partition, human_bytes
from tnt.errors import Refused
from tnt.menu import MENU, walk
from tnt.session import open_session
from tnt.testing import GIB, FakeServer

SNAPSHOT = Path(__file__).resolve().parent.parent / "docs" / "ACTION-MATRIX.snapshot.md"


def test_free_bytes_distinguishes_unknown_from_empty_and_full():
    assert Disk("a", 100).free_bytes is None
    assert Disk("a", 100, partitions=()).free_bytes == 100
    assert Disk("a", 100, partitions=(Partition("a1", 100),)).free_bytes == 0
    assert Disk("a", 100, partitions=(Partition("a1", 150),)).free_bytes == 0
    assert Disk("a", 100, partitions=(Partition("a1", 30),)).as_dict()["free_bytes"] == 70


def test_human_bytes():
    assert human_bytes(None) == "?" and human_bytes(512) == "512 B" and human_bytes(2048) == "2.0 KiB"
    assert human_bytes(3 * 1024 ** 5) == "3.0 PiB" and human_bytes(5 * 1024 ** 6) == "5120.0 PiB"


def test_disk_inventory_from_fake_server(server):
    s = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
    disks = {d.name: d for d in s.disks()}
    s.close()
    assert disks["sda"].free_bytes == 512 * GIB - (1 << 20) - (512 << 20) - 100 * GIB and disks["sda"].pool == "boot-pool"
    assert disks["sdc"].free_bytes == 256 * GIB and disks["sdc"].partitions == ()
    assert disks["sdd"].partitions is None and disks["sdd"].free_bytes is None


def test_disk_details_flat_list_shape_and_tolerance():
    server = FakeServer(details_as_flat_list=True)
    with server:
        s = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
        assert {d.name: len(d.partitions) for d in s.disks() if d.partitions is not None} == {"sda": 3, "sdb": 1, "sdc": 0}
        s.close()


def test_garbage_in_disk_rows_does_not_crash():
    server = FakeServer(disks=[{"name": "x", "size": "n/a", "serial": None, "type": None}], parts={"x": [{"name": "x1", "size": "bad", "start": "s"}]})
    with server:
        s = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
        (d,) = s.disks()
        assert d.size == 0 and d.partitions[0].size == 0 and d.partitions[0].start is None
        s.close()


def test_refusal_list():
    assert "reboots" in refusal_for(RpcCall("system.reboot"))
    assert "B-07" in refusal_for(RpcCall("boot.attach", ("vda4",)))
    assert "credentials" in refusal_for(RpcCall("auth.set_password"))
    assert refusal_for(RpcCall("auth.me")) is None
    assert "A-08" in refusal_for(RpcCall("pool.export", (0, {"x": "boot-pool"})))
    assert refusal_for(RpcCall("pool.export", ("tank",))) is None
    assert "belongs to a pool" in refusal_for(RpcCall("disk.wipe", ("sda",)), protected_disks=["sda"])
    assert refusal_for(RpcCall("disk.wipe", ("sdc",)), protected_disks=["sda"]) is None


def _action(kind=Kind.DESTRUCTIVE, calls=(RpcCall("disk.wipe", ("sdc", "QUICK"), job=True),), target="sdc"):
    return Action("B-99", kind, "Wipe the spare", calls, probe=(RpcCall("disk.query", ([], {})),), confirm_target=target)


class Recorder:
    def __init__(self, probe="state-1"):
        self.probe, self.log = probe, []

    def call(self, method, *params, job=False):
        self.log.append((method, params, job))
        return self.probe if method == "disk.query" else "done"


def test_plan_renders_the_exact_calls():
    rpc = Recorder()
    plan = make_plan(_action(), rpc)
    text = plan.render()
    assert 'disk.wipe("sdc", "QUICK")  [job]' in text and "type 'sdc'" in text and plan.fingerprint[:12] in text
    assert rpc.log == [("disk.query", ([], {}), False)]  # planning only reads
    assert "type " not in make_plan(_action(Kind.MUTATE, target=None), rpc).render()


def test_apply_gates_in_order():
    rpc = Recorder()
    plan = make_plan(_action(), rpc)
    with pytest.raises(Refused, match="read-only"):
        apply_plan(plan, rpc, allow_write=False, typed_confirmation="sdc")
    with pytest.raises(Refused, match="typed-confirmation"):
        apply_plan(plan, rpc, allow_write=True, typed_confirmation="sda")
    with pytest.raises(Refused, match="typed-confirmation"):
        apply_plan(make_plan(_action(target=None), rpc), rpc, allow_write=True, typed_confirmation=None)
    with pytest.raises(Refused, match="refusal-list"):
        apply_plan(plan, rpc, allow_write=True, typed_confirmation="sdc", protected_disks=["sdc"])
    assert ("disk.wipe", ("sdc", "QUICK"), True) not in rpc.log
    rpc.probe = "state-2"
    with pytest.raises(Refused, match="state-drift"):
        apply_plan(plan, rpc, allow_write=True, typed_confirmation="sdc")
    assert not any(m == "disk.wipe" for m, *_ in rpc.log)


def test_apply_executes_when_every_gate_passes():
    rpc = Recorder()
    plan = make_plan(_action(), rpc)
    assert apply_plan(plan, rpc, allow_write=True, typed_confirmation="sdc") == ["done"]
    assert rpc.log[-1] == ("disk.wipe", ("sdc", "QUICK"), True)
    mutate = make_plan(_action(Kind.MUTATE, (RpcCall("pool.scrub", ("tank",)),), None), rpc)
    assert apply_plan(mutate, rpc, allow_write=True) == ["done"] and rpc.log[-1] == ("pool.scrub", ("tank",), False)
    read = make_plan(_action(Kind.READ, (RpcCall("disk.query"),), None), rpc)
    assert apply_plan(read, rpc, allow_write=False) == [rpc.probe]


def test_menu_mirrors_the_webui_labels():
    top = [i.label for i in MENU]
    assert top == ["Dashboard", "Storage", "Datasets", "Shares", "Data Protection", "Credentials", "Containers", "Virtual Machines", "Apps",
                   "Reporting", "System"]
    labels = {i.label for _, i in walk()}
    assert {"Disks", "Boot", "Network", "Services", "Users", "S3 Access Keys"} <= labels
    keys = [i.key for _, i in walk()]
    assert len(keys) == len(set(keys))
    assert [i.key for _, i in walk() if i.status == "implemented"] == ["storage", "storage/disks"]
    assert [i.label for _, i in walk() if i.tnt_only] == ["Free space and partitions", "Boot pool data"]


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


def test_backlog_covers_every_tui_matrix_row():
    backlog = (Path(__file__).resolve().parent.parent / "BACKLOG.md").read_text()
    for row in matrix.backlog_rows(matrix.load(SNAPSHOT)):
        assert row.id in backlog, f"BACKLOG.md does not mention {row.id}"


def test_every_refused_matrix_method_is_on_the_refusal_list():
    rows = {r.id: r for r in matrix.load(SNAPSHOT)}
    for method in rows["B-07"].methods:
        assert refusal_for(RpcCall(method, ("vda4",))) is not None
