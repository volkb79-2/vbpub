from dataclasses import replace

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
    ("user.create", ({"username": "operator", "home": "/mnt/boot-pool/home/operator"},)),
])
def test_boot_pool_path_refusal_covers_every_non_read_family(method, params):
    from tnt.adapters.v27 import V27

    assert V27.method_supported(method)
    assert "boot-pool and its datasets are never changed" in refusal_for(method, params, POOL_STATE)


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
    ("users", "user.set_password", "write"),
    ("apps", "app.stop", "destructive"),
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
    "pool.snapshot.rollback", "pool.snapshot.delete", "user.create", "user.update", "user.delete", "app.stop",
    "user.set_password",
])
def test_new_v27_writes_are_validated_against_the_manifest(method):
    kind = Kind.DESTRUCTIVE if is_destructive_method(method) else Kind.MUTATE
    probe = ((RpcCall("pool.dataset.details"), RpcCall("container.query", ([], {})), RpcCall("vm.query", ([], {})))
             if method == "pool.dataset.delete" else (RpcCall("system.info"),))
    action = Action("T-TEST", kind, method, (RpcCall(method, ({"name": "tank/test"},)),),
                    probe, "tank/test" if kind is Kind.DESTRUCTIVE else None)
    validate_action(action)


@pytest.mark.parametrize("method", [
    "container.delete", "container.stop", "vm.delete", "vm.stop", "vm.poweroff", "pool.dataset.delete",
    "pool.snapshot.delete", "pool.snapshot.rollback", "user.delete", "app.stop",
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


def test_unlisted_suffix_shaped_names_are_not_reads_or_supported_calls():
    from tnt.policy import is_read_method

    for method in ("widget.query", "mystery.config", "made_up.get_instance"):
        assert not is_read_method(method)
        assert refusal_for(method) is not None


def test_pool_delete_and_namespace_scrub_are_not_v27_callables():
    from tnt.adapters.v27 import V27

    assert not V27.method_supported("pool.delete")
    assert not V27.method_supported("pool.scrub")
    for method in ("pool.delete", "pool.scrub"):
        action = Action("T-UNVERIFIED", Kind.DESTRUCTIVE, method, (RpcCall(method, (1,)),),
                        (RpcCall("pool.query"),), "tank")
        with pytest.raises(Refused, match="not in a supported adapter manifest"):
            validate_action(action)


def test_mutating_actions_require_a_live_target_probe():
    action = Action("T-NO-PROBE", Kind.MUTATE, "update", (RpcCall("user.update", (1, {"full_name": "A"})),))
    with pytest.raises(Refused, match="empty-probe"):
        validate_action(action)


@pytest.mark.parametrize(("pool_ref", "message"), [
    (1, "cannot be resolved to a name"),
    ("tank", "cannot be resolved without live pool state"),
    (True, "boolean and is ambiguous"),
])
def test_pool_mapping_references_fail_closed_without_live_state(pool_ref, message):
    reason = refusal_for("vm.create", ({"pool": pool_ref},), None)
    assert message in reason


def test_core_download_allows_only_the_exact_private_config_archive_shape():
    valid = ("config.save", [{"secretseed": True, "root_authorized_keys": False}], "config.tar", False)
    assert refusal_for("core.download", valid) is None
    assert "limited to an explicit" in refusal_for("core.download", ("config.export", valid[1], "config.tar", False))
    assert "limited to an explicit" in refusal_for("core.download", ("config.save", valid[1], "../config.tar", False))


def test_action_safety_metadata_rejects_malformed_self_confirmation_and_unpreflighted_force():
    action = Action("T-METADATA", Kind.MUTATE, "update user", (RpcCall("user.update", (8, {"full_name": "Alice"})),),
                    (RpcCall("user.query", ([], {})),))
    with pytest.raises(Refused, match="safety metadata is malformed"):
        validate_action(replace(action, confirm_self=1))
    with pytest.raises(Refused, match="preflighted system action"):
        validate_action(replace(action, force_with_jobs=True))


def test_method_spec_rejects_unknown_classifications():
    from tnt.adapters.base import MethodSpec

    with pytest.raises(ValueError, match="invalid method classification"):
        MethodSpec("unknown")

