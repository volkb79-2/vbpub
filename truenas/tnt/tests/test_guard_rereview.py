"""Re-review items: lazy and realistic membership, concurrency, kind/method binding, path and pool-id forms,
single-use plans, exact serial matching."""
import asyncio
import threading

import pytest
from helpers import SIZE, Stub, destructive, guarded, run_plan

from tnt.actions import Action, Kind, RpcCall, apply_plan, make_plan
from tnt.adapters.v27 import V27
from tnt.errors import Refused
from tnt.policy import DESTRUCTIVE_METHODS, SafetyState, compute_state, refusal_for


def rows(*disks):
    return [dict({"size": SIZE}, **d) for d in disks]


def state_of(pools, boot=None, disk_rows=None):
    stub = Stub(**{"pool.query": pools, "boot.get_state": boot or {"topology": {}}, "disk.query": disk_rows})
    return compute_state(guarded(stub))


# -- 1. membership: realistic topologies, lazy computation -------------------------------------------------------
def test_topology_uses_the_disk_key_before_the_path():
    pools = [{"id": 1, "name": "tank", "topology": {"data": [
        {"type": "DISK", "disk": "sdb", "path": "/dev/disk/by-partuuid/0f0e0d0c-aaaa-bbbb-cccc-111122223333", "guid": "1"}]}}]
    state = state_of(pools, disk_rows=rows({"name": "sdb"}, {"name": "sdc"}))
    assert state.protected_disks == {"sdb"} and state.unresolved == ()


def test_a_partuuid_path_alone_resolves_through_the_disk_partition_table():
    uuid = "0f0e0d0c-aaaa-bbbb-cccc-111122223333"
    pools = [{"id": 1, "name": "tank", "topology": {"data": [{"type": "DISK", "path": f"/dev/disk/by-partuuid/{uuid}"}]}}]
    disk_rows = rows({"name": "sdb", "partitions": [{"name": "sdb1", "partuuid": uuid}]}, {"name": "sdc"})
    state = state_of(pools, disk_rows=disk_rows)
    assert state.protected_disks == {"sdb"} and state.unresolved == ()


def test_an_unresolvable_member_blocks_wipe_only_not_other_pool_calls():
    pools = [{"id": 1, "name": "tank", "topology": {"data": [{"type": "DISK", "path": "/dev/disk/by-partuuid/never-heard-of-it"}]}}]
    stub = Stub(**{"pool.query": pools, "boot.get_state": {}, "disk.query": rows({"name": "sdc"})})
    state = compute_state(guarded(stub))
    assert state.unresolved == ("/dev/disk/by-partuuid/never-heard-of-it",)
    assert "unknown" in refusal_for("disk.wipe", ["sdc"], state)
    assert refusal_for("pool.export", [1], state) is None  # a pool id does not depend on membership
    assert run_plan(Stub(**{"pool.query": pools, "boot.get_state": {}, "disk.query": rows({"name": "sdc"})}), destructive("pool.export", 1), typed="x") == ["done"]


def test_unrelated_actions_never_read_pool_membership():
    stub = Stub(**{"pool.query": RuntimeError("pool.query is down")})
    action = Action("S-1", Kind.MUTATE, "start a container", (RpcCall("container.start", ("demo",)),))
    rpc = guarded(stub)
    assert apply_plan(make_plan(action, rpc), rpc, allow_write=True) == ["done"]
    assert "pool.query" not in stub.executed() and "boot.get_state" not in stub.executed()
    assert stub.executed() == ["container.start"]


def test_actions_that_touch_pools_or_disks_do_read_membership_and_fail_closed():
    stub = Stub(**{"pool.query": RuntimeError("down")})
    with pytest.raises(Refused, match="safety-state"):
        run_plan(stub, Action("S-2", Kind.MUTATE, "scrub", (RpcCall("pool.scrub", (1,)),)))
    assert "pool.scrub" not in stub.executed()


def test_nvme_and_mmc_partitions_map_to_their_disk():
    pools = [{"id": 1, "name": "tank", "topology": {"data": [{"type": "DISK", "disk": "nvme0n1p2", "path": "/dev/nvme0n1p2"},
                                                             {"type": "DISK", "path": "/dev/mmcblk0p1"}]}}]
    state = state_of(pools, disk_rows=rows({"name": "nvme0n1"}, {"name": "nvme1n1"}, {"name": "mmcblk0"}))
    assert state.protected_disks == {"nvme0n1", "mmcblk0"} and state.unresolved == ()


def test_nested_and_auxiliary_vdevs_are_all_members():
    def disk(name):
        return {"type": "DISK", "disk": name, "path": f"/dev/{name}1", "children": []}
    pools = [{"id": 1, "name": "tank", "topology": {
        "data": [{"type": "RAIDZ2", "children": [disk("sda"), disk("sdb"), {"type": "MIRROR", "children": [disk("sdc"), disk("sdd")]}]}],
        "cache": [disk("sde")], "log": [{"type": "MIRROR", "children": [disk("sdf"), disk("sdg")]}], "spare": [disk("sdh")],
        "special": [disk("sdi")], "dedup": [disk("sdj")]}}]
    names = [f"sd{c}" for c in "abcdefghijk"]
    state = state_of(pools, disk_rows=rows(*({"name": n} for n in names)))
    assert state.protected_disks == set(names[:-1]) and "sdk" not in state.protected_disks
    assert refusal_for("disk.wipe", ["sdk"], state) is None and refusal_for("disk.wipe", ["sdh"], state)


def test_boot_topology_nodes_named_by_type_disk_count():
    boot = {"topology": {"data": [{"type": "MIRROR", "name": "mirror-0", "children": [{"type": "DISK", "name": "sda3", "path": "/dev/sda3"}]}]}}
    state = state_of([], boot, rows({"name": "sda"}, {"name": "sdb"}))
    assert state.protected_disks == {"sda"} and state.unresolved == ()  # "mirror-0" is not a disk reference


def test_serial_matching_is_exact_or_a_whole_token_never_a_substring():
    pools = [{"id": 1, "name": "tank", "topology": {"data": [{"type": "DISK", "path": "/dev/disk/by-id/ata-MODEL_DATA00021-part1"}]}}]
    state = state_of(pools, disk_rows=rows({"name": "sdb", "serial": "DATA0002"}, {"name": "sdc", "serial": "SPARE003"}))
    assert state.protected_disks == frozenset() and len(state.unresolved) == 1  # DATA00021 is not DATA0002
    exact = [{"id": 1, "name": "tank", "topology": {"data": [{"type": "DISK", "path": "/dev/disk/by-id/ata-MODEL_DATA0002-part1"}]}}]
    assert state_of(exact, disk_rows=rows({"name": "sdb", "serial": "DATA0002"})).protected_disks == {"sdb"}


def test_an_identifier_shared_by_two_disks_resolves_to_neither():
    pools = [{"id": 1, "name": "tank", "topology": {"data": [{"type": "DISK", "path": "/dev/disk/by-id/ata-X_SAMESERIAL-part1"}]}}]
    state = state_of(pools, disk_rows=rows({"name": "sdb", "serial": "SAMESERIAL"}, {"name": "sdc", "serial": "SAMESERIAL"}))
    assert state.protected_disks == frozenset() and state.unresolved


# -- 2. concurrency --------------------------------------------------------------------------------------------
GRANT_STATE = SafetyState({1: "tank"}, frozenset(), frozenset())


def test_another_thread_cannot_consume_a_plan_call():
    rpc = guarded(Stub())
    inside, release, outcome = threading.Event(), threading.Event(), {}

    def owner():
        with rpc.authorized([("pool.scrub", (1,), False)], GRANT_STATE):
            inside.set()
            release.wait(10)
            outcome["owner"] = rpc.call("pool.scrub", 1)

    def intruder():
        inside.wait(10)
        try:
            rpc.call("pool.scrub", 1)
        except Refused as exc:
            outcome["intruder"] = exc.rule
        release.set()

    threads = [threading.Thread(target=owner), threading.Thread(target=intruder)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    assert outcome == {"intruder": "foreign-caller", "owner": "done"}


def test_another_asyncio_task_cannot_consume_a_plan_call():
    rpc = guarded(Stub())

    async def go():
        inside, release = asyncio.Event(), asyncio.Event()
        outcome = {}

        async def owner():
            with rpc.authorized([("pool.scrub", (1,), False)], GRANT_STATE):
                inside.set()
                await release.wait()
                outcome["owner"] = rpc.call("pool.scrub", 1)

        async def intruder():
            await inside.wait()
            try:
                rpc.call("pool.scrub", 1)
            except Refused as exc:
                outcome["intruder"] = exc.rule
            release.set()
        await asyncio.gather(owner(), intruder())
        return outcome
    assert asyncio.run(go()) == {"intruder": "foreign-caller", "owner": "done"}


def test_calls_are_serialised_by_a_lock():
    active, peak = [0], [0]
    gate = threading.Lock()

    class Slow(Stub):
        def call(self, method, *params, job=False):
            with gate:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            threading.Event().wait(0.01)
            with gate:
                active[0] -= 1
            return "done"
    rpc = guarded(Slow())
    threads = [threading.Thread(target=lambda: [rpc.call("system.info") for _ in range(5)]) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert peak[0] == 1


# -- 3. kind bound to method -------------------------------------------------------------------------------------
@pytest.mark.parametrize("method", ["disk.wipe", "pool.export", "pool.dataset.delete", "zfs.resource.destroy", "pool.snapshot.delete",
                                    "pool.scrub.delete", "app.delete", "vm.device.delete", "whatever.destroy", "disk.wipe "])
@pytest.mark.parametrize("kind", [Kind.MUTATE, Kind.READ])
def test_destructive_methods_need_a_destructive_action(method, kind):
    with pytest.raises(Refused, match="kind-mismatch|read-action"):
        make_plan(Action("K-1", kind, "t", (RpcCall(method, ("sdc",)),)), guarded(Stub()))


def test_the_manifest_exposes_the_destructive_set():
    assert V27.destructive == DESTRUCTIVE_METHODS and "disk.wipe" in V27.destructive


def test_a_destructive_action_needs_a_non_empty_confirmation_target():
    for target in (None, "", "   "):
        with pytest.raises(Refused, match="empty-confirm"):
            make_plan(Action("K-2", Kind.DESTRUCTIVE, "t", (RpcCall("pool.export", (1,)),), (RpcCall("pool.query"),), target), guarded(Stub()))


# -- 4. paths, boot family, pool-id forms ----------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["//mnt//boot-pool//x", "/mnt/./boot-pool/x", "/dev/zvol/boot-pool/x", "boot-pool@snap", "boot-pool#bookmark",
                                  "BOOT-POOL/ROOT", "/mnt/boot-pool", "boot-pool/", "/mnt/boot-pool/../boot-pool/x", "/mnt/tank/../boot-pool"])
def test_boot_pool_paths_are_refused_after_normalisation(path):
    assert refusal_for("filesystem.setperm", [{"path": path}], None)
    assert refusal_for("pool.dataset.update", [path, {}], None)


@pytest.mark.parametrize("path", ["/mnt/boot-pool-data/x", "boot-pool-data", "/mnt/tank/boot-pool", "tank/boot-pool-notes", "/mnt/tank/data"])
def test_similar_names_and_deeper_components_are_allowed(path):
    assert refusal_for("filesystem.setperm", [{"path": path}], None) is None


def test_paths_with_dotdot_are_refused_as_ambiguous():
    assert "ambiguous" in refusal_for("filesystem.setperm", [{"path": "/mnt/tank/../data"}], None)
    assert refusal_for("filesystem.setperm", [{"path": "/mnt/tank/..data"}], None) is None


def test_a_boot_pool_name_used_only_as_a_dict_key_is_refused():
    assert refusal_for("pool.create", [{"boot-pool": {"anything": 1}}], None)
    assert refusal_for("pool.create", [{"outer": {"inner": [{"boot-pool": 1}]}}], None)


@pytest.mark.parametrize("method", ["boot.format", "boot.install_loader", "boot.scrub", "boot.set_scrub_interval", "boot.environment.activate",
                                    "boot.environment.clone", "boot.environment.keep", "boot.anything_new"])
def test_the_boot_family_is_default_deny(method):
    assert refusal_for(method, ["x"], None)
    with pytest.raises(Refused):
        guarded(Stub()).call(method, "x")


def test_boot_reads_stay_allowed():
    for method in ("boot.get_state", "boot.get_disks", "boot.environment.query"):
        assert refusal_for(method, [], None) is None
        assert guarded(Stub(), read_only=True).call(method) is not None


# -- 5. single-use, session-bound plans; wipe size ---------------------------------------------------------------
def test_a_plan_is_bound_to_the_session_that_made_it():
    one, two = guarded(Stub()), guarded(Stub())
    plan = make_plan(destructive("pool.export", 1), one)
    with pytest.raises(Refused, match="foreign-session"):
        apply_plan(plan, two, allow_write=True, typed_confirmation="x")
    assert apply_plan(plan, one, allow_write=True, typed_confirmation="x") == ["done"]


def test_a_plan_runs_once_even_when_replayed_through_a_fresh_plan_object():
    stub = Stub()
    rpc = guarded(stub)
    plan = make_plan(destructive("pool.export", 1), rpc)
    apply_plan(plan, rpc, allow_write=True, typed_confirmation="x")
    stub.log.clear()
    with pytest.raises(Refused, match="plan-reused"):
        apply_plan(plan, rpc, allow_write=True, typed_confirmation="x")
    assert "pool.export" not in stub.executed()
    with pytest.raises(Refused, match="plan-reused"):
        rpc.spend("")
    from dataclasses import replace
    with pytest.raises(Refused, match="plan-reused"):
        apply_plan(replace(plan), rpc, allow_write=True, typed_confirmation="x")


def test_a_refused_gate_does_not_burn_the_plan():
    rpc = guarded(Stub())
    plan = make_plan(destructive("pool.export", 1), rpc)
    with pytest.raises(Refused, match="typed-confirmation"):
        apply_plan(plan, rpc, allow_write=True, typed_confirmation="wrong")
    assert apply_plan(plan, rpc, allow_write=True, typed_confirmation="x") == ["done"]


@pytest.mark.parametrize("size", [None, 0, -1, "1000", True, 1.5])
def test_a_disk_without_a_usable_size_is_not_wipeable(size):
    stub = Stub(**{"pool.query": [], "boot.get_state": {}, "disk.query": [{"name": "sdc", "size": size}]})
    with pytest.raises(Refused, match="usable size"):
        run_plan(stub, destructive("disk.wipe", "sdc"), typed="x")
    assert "disk.wipe" not in stub.executed()
    stub = Stub(**{"pool.query": [], "boot.get_state": {}, "disk.query": [{"name": "sdc", "size": SIZE}]})
    assert run_plan(stub, destructive("disk.wipe", "sdc"), typed="x") == ["done"]


def test_pool_scrub_task_ids_are_not_pool_ids():
    state = SafetyState({1: "tank"}, frozenset(), frozenset())
    assert refusal_for("pool.scrub.delete", [5], state) is None
    assert refusal_for("pool.scrub.delete", [5], None) is None
    assert refusal_for("pool.scrub", [5], state)  # pool.scrub's first parameter IS a pool id, and 5 is unknown
