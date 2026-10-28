"""Exercise fail-closed policy outcomes for ambiguous pool references."""
from __future__ import annotations

import builtins

from tnt.policy import SafetyState, _manifest_spec, _pool_reference_key, compute_state, refusal_for
from tnt.transport import GuardedRpc


class Rpc:
    def __init__(self, results):
        self.results = results

    def call(self, method, *params, job=False):
        return self.results[method]


def test_pool_reference_value_types_fail_closed_and_known_data_pool_resolves():
    state = SafetyState({1: "tank", 2: "boot-pool"}, frozenset(), frozenset())
    for value in (None, True, 99, "99", "missing", [], {}, object()):
        assert refusal_for("vm.create", [{"pool": value}], state)
    assert refusal_for("vm.create", [{"pool": 2}], state)
    assert refusal_for("vm.create", [{"pool": "boot-pool"}], state)
    assert refusal_for("vm.create", [{"pool": [1]}], state)
    assert refusal_for("vm.create", [{"pool": {"id": 1}}], state)
    assert refusal_for("vm.create", [{"pool": "tank"}], state) is None
    assert _pool_reference_key(1) == ""
    assert _pool_reference_key("poolId") == "pool_id"
    assert _pool_reference_key("Pool-Names") == "pool_names"


def test_pool_name_refusal_requires_known_live_state_and_rejects_boot_pool():
    assert refusal_for("pool.scrub.run", ["tank"], None)
    assert refusal_for("pool.scrub.run", ["boot-pool"], SafetyState({1: "boot-pool"}))
    assert refusal_for("pool.scrub.run", ["unknown"], SafetyState({1: "tank"}))
    assert refusal_for("pool.scrub.run", ["tank"], SafetyState({1: "tank"})) is None


def test_named_boot_pool_without_topology_is_recorded_as_unresolved():
    state = compute_state(GuardedRpc(Rpc({"pool.query": [], "boot.get_state": {"name": "boot-pool", "topology": {}},
                                          "disk.query": []})))
    assert "boot-pool has no usable topology" in state.unresolved


def test_manifest_import_failure_is_a_supported_unknown_classification(monkeypatch):
    real_import = builtins.__import__

    def fail_adapter(name, *args, **kwargs):
        if name == "tnt.adapters":
            raise ImportError("adapter package unavailable")
        return real_import(name, *args, **kwargs)

    _manifest_spec.cache_clear()
    monkeypatch.setattr(builtins, "__import__", fail_adapter)
    try:
        assert _manifest_spec("test.unlisted") is None
    finally:
        _manifest_spec.cache_clear()
