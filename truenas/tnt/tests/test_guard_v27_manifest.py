import pytest

from tnt.actions import Action, Kind, RpcCall, validate_action
from tnt.errors import Refused
from tnt.policy import SafetyState, is_destructive_method, refusal_for


POOL_STATE = SafetyState({1: "boot-pool", 2: "tank"}, frozenset(), frozenset())


@pytest.mark.parametrize(("method", "params"), [
    ("container.create", ({"name": "web", "pool": "boot-pool/containers"},)),
    ("container.update", ({"id": 1, "dataset": "boot-pool/containers/web"},)),
    ("vm.create", ({"name": "guest", "devices": [{"path": "/mnt/boot-pool/vms/guest"}]},)),
    ("vm.device.create", ({"vm": 1, "attributes": {"path": "/dev/zvol/boot-pool/vms/guest"}},)),
    ("sharing.nfs.create", ({"path": "/mnt/boot-pool/shares/export"},)),
    ("app.install", ({"pool": "boot-pool"},)),
    ("user.create", ({"username": "operator", "home": "/mnt/boot-pool/home/operator"},)),
])
def test_boot_pool_path_refusal_covers_every_non_read_family(method, params):
    assert refusal_for(method, params, POOL_STATE)


@pytest.mark.parametrize(("method", "params"), [
    ("container.create", ({"name": "web", "pool": "tank"},)),
    ("container.update", ({"id": 1, "dataset": "tank/containers/web"},)),
    ("vm.create", ({"name": "guest", "devices": [{"path": "/mnt/tank/vms/guest"}]},)),
    ("vm.device.create", ({"vm": 1, "attributes": {"path": "/dev/zvol/tank/vms/guest"}},)),
    ("sharing.nfs.create", ({"path": "/mnt/tank/shares/export"},)),
    ("user.create", ({"username": "operator", "home": "/mnt/tank/home/operator"},)),
])
def test_boot_pool_path_refusal_does_not_match_regular_target_strings(method, params):
    assert refusal_for(method, params, POOL_STATE) is None


@pytest.mark.parametrize("params", [
    ({"pool": "1"},),
    ({"pool": [1]},),
    ({"poolId": 1},),
    ({"pools": [1]},),
    ({"pool": [2]},),
    ({"pool": None},),
    ({"pool": "missing"},),
])
def test_ambiguous_nested_pool_reference_spellings_refuse(params):
    assert refusal_for("vm.create", params, POOL_STATE)


@pytest.mark.parametrize("params", [({"pool": 2},), ({"pool": "tank"},)])
def test_resolved_non_boot_pool_mapping_references_remain_legal(params):
    assert refusal_for("vm.create", params, POOL_STATE) is None


def test_pool_scrub_task_id_is_not_treated_as_a_pool_id():
    assert refusal_for("pool.scrub.delete", (1,), POOL_STATE) is None
    assert refusal_for("pool.scrub", (1,), POOL_STATE)
    assert refusal_for("pool.scrub.run", ("tank",), POOL_STATE) is None
    assert refusal_for("pool.scrub.scrub", ("boot-pool", "STOP"), POOL_STATE)
    assert refusal_for("pool.scrub.run", (1,), POOL_STATE)


@pytest.mark.parametrize(("feature", "method", "classification"), [
    ("containers", "container.query", "read"),
    ("containers", "container.create", "write"),
    ("containers", "container.delete", "destructive"),
    ("vms", "vm.restart", "write"),
    ("vms", "vm.poweroff", "destructive"),
    ("datasets", "pool.dataset.update", "write"),
    ("snapshots", "pool.snapshot.rollback", "destructive"),
    ("users", "user.set_password", "destructive"),
])
def test_v27_manifest_classifies_supported_methods_explicitly(feature, method, classification):
    from tnt.adapters.v27 import V27

    assert V27.manifest[feature][method].classification == classification
    assert V27.manifest[feature][method].supported is True


@pytest.mark.parametrize("method", [
    "container.create", "container.update", "container.delete", "container.start", "container.stop",
    "vm.create", "vm.update", "vm.delete", "vm.start", "vm.stop", "vm.restart", "vm.poweroff",
    "vm.suspend", "vm.resume", "pool.dataset.create", "pool.dataset.update", "pool.dataset.delete",
    "pool.snapshot.create", "pool.snapshot.clone", "pool.snapshot.hold", "pool.snapshot.release",
    "pool.snapshot.rollback", "pool.snapshot.delete", "user.create", "user.update", "user.delete",
    "user.set_password",
])
def test_new_v27_writes_are_validated_against_the_manifest(method):
    kind = Kind.DESTRUCTIVE if is_destructive_method(method) else Kind.MUTATE
    action = Action("T-TEST", kind, method, (RpcCall(method, ({"name": "tank/test"},)),),
                    (RpcCall("system.info"),), "tank/test" if kind is Kind.DESTRUCTIVE else None)
    validate_action(action)


@pytest.mark.parametrize("method", [
    "container.delete", "container.stop", "vm.delete", "vm.stop", "vm.poweroff", "pool.dataset.delete",
    "pool.snapshot.delete", "pool.snapshot.rollback", "user.delete", "user.set_password",
    "app.uninstall", "pool.dataset.destroy_snapshots", "filesystem.rmdir", "filesystem.unlink", "filesystem.remove",
])
def test_destructive_classification_is_explicit_or_conservative(method):
    assert is_destructive_method(method)


def test_policy_only_destructive_methods_are_classified_but_not_enabled():
    from tnt.adapters.v27 import V27

    for method in ("app.uninstall", "pool.dataset.destroy_snapshots", "filesystem.rmdir", "filesystem.unlink", "filesystem.remove"):
        assert V27.method_classification(method) == "destructive"
        assert not V27.method_supported(method)
        with pytest.raises(Refused, match="not in a supported adapter manifest"):
            validate_action(Action("T-UNVERIFIED", Kind.DESTRUCTIVE, method, (RpcCall(method, ("tank/x",)),),
                                   (RpcCall("system.info"),), "tank/x"))


def test_container_restart_is_a_workflow_operation_not_an_rpc_method():
    from tnt.adapters.v27 import V27

    assert V27.method_classification("container.restart") == "destructive"
    assert not V27.method_supported("container.restart")
    with pytest.raises(Refused, match="not in a supported adapter manifest"):
        validate_action(Action("T-RESTART", Kind.DESTRUCTIVE, "restart", (RpcCall("container.restart", (1,)),),
                               (RpcCall("container.query"),), "web"))


def test_unknown_write_stays_default_deny():
    with pytest.raises(Refused, match="not in a supported adapter manifest"):
        validate_action(Action("T-UNKNOWN", Kind.MUTATE, "unknown", (RpcCall("user.magic", ({},)),),
                               (RpcCall("user.query"),)))

