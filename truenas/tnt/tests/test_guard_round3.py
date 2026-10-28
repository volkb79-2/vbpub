"""Independent round-three regression cases for the plan/apply safety guard."""
from dataclasses import replace

import pytest

from helpers import SIZE, Stub, destructive, guarded
from tnt.actions import Action, Kind, RpcCall, apply_plan, make_plan
from tnt.errors import Refused
from tnt.policy import SafetyState, _alias_map, _members, compute_state, is_destructive_method, refusal_for


def test_boot_pool_paths_normalise_before_prefix_removal():
    for path in ("/./mnt/boot-pool/x", "/dev/./zvol/boot-pool", "mnt/boot-pool/x"):
        assert refusal_for("filesystem.setperm", [{"path": path}])
    assert refusal_for("filesystem.setperm", [{"path": "/mnt/./tank/../boot-pool/x"}])


@pytest.mark.parametrize("method", [
    "pool.dataset.promote", "zfs.resource.rollback", "pool.snapshot.rollback", "pool.replace", "pool.attach",
    "pool.offline", "pool.import_pool", "zfs.resource.rename", "pool.dataset.recursive_destroy", "vm.stop",
    "service.stop",
])
def test_new_destructive_method_families_require_destructive_kind(method):
    assert is_destructive_method(method)
    with pytest.raises(Refused, match="kind-mismatch"):
        make_plan(Action("R3", Kind.MUTATE, "unsafe", (RpcCall(method, ("tank",)),)), guarded(Stub()))


def test_unknown_writes_are_not_accepted_as_actions():
    with pytest.raises(Refused, match="unknown-write"):
        make_plan(Action("R3", Kind.MUTATE, "unknown", (RpcCall("pool.new_method", ()),)), guarded(Stub()))
    with pytest.raises(Refused, match="unknown-write"):
        make_plan(destructive("service.stop", "ssh"), guarded(Stub()))


@pytest.mark.parametrize("method,args", [
    ("pool.scrub.create", ({"pool": 1},)),
    ("pool.scrub.run", (1,)),
    ("filesystem.setperm", ({"pool_id": 1},)),
    ("zfs.resource.create", ({"pool_name": "boot-pool"},)),
    ("pool.dataset.update", ({"dataset": "boot-pool/ROOT"},)),
])
def test_boot_pool_references_are_refused_across_method_shapes(method, args):
    state = SafetyState({1: "boot-pool", 2: "tank"}, frozenset(), frozenset())
    assert refusal_for(method, args, state)


def test_nested_pool_id_mapping_is_checked():
    state = SafetyState({1: "boot-pool"}, frozenset(), frozenset())
    assert refusal_for("pool.scrub.create", ({"outer": {"pool_id": 1}},), state)


@pytest.mark.parametrize("topology", [None, {}])
def test_pool_without_usable_topology_blocks_disk_wipe(topology):
    stub = Stub(**{
        "pool.query": [{"id": 1, "name": "tank", "topology": topology}],
        "boot.get_state": {"topology": {"data": [{"disk": "sda"}]}},
        "disk.query": [{"name": "sda", "size": SIZE}, {"name": "sdc", "size": SIZE}],
    })
    state = compute_state(guarded(stub))
    assert state.unresolved
    assert refusal_for("disk.wipe", ["sdc"], state)


def test_exported_zpool_disks_are_protected():
    stub = Stub(**{
        "pool.query": [],
        "boot.get_state": {"topology": {"data": [{"disk": "sda"}]}},
        "disk.query": [{"name": "sda", "size": SIZE}, {"name": "sdc", "size": SIZE, "exported_zpool": "oldpool"}],
    })
    state = compute_state(guarded(stub))
    assert "sdc" in state.protected_disks
    assert refusal_for("disk.wipe", ["sdc"], state)


def test_a_vdev_unions_every_resolvable_reference():
    disks = {"sdb": {"name": "sdb"}, "sdc": {"name": "sdc"}}
    aliases = _alias_map(disks)
    node = {"type": "DISK", "disk": "sdb", "path": "/dev/sdc1"}
    members, unresolved = _members({"data": [node]}, disks, aliases)
    assert members == {"sdb", "sdc"}
    assert unresolved == []


def test_a_spent_plan_cannot_be_replayed_with_a_replaced_nonce():
    stub = Stub()
    rpc = guarded(stub)
    plan = make_plan(destructive("pool.export", 1), rpc)
    another_plan = make_plan(destructive("pool.export", 1), rpc)
    assert apply_plan(plan, rpc, allow_write=True, typed_confirmation="x") == ["done"]
    before = stub.executed().count("pool.export")
    for nonce in ("zzz", another_plan.nonce):
        with pytest.raises(Refused, match="plan-reused"):
            apply_plan(replace(plan, nonce=nonce), rpc, allow_write=True, typed_confirmation="x")
    assert stub.executed().count("pool.export") == before
