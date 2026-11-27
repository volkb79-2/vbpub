from pathlib import Path
from types import SimpleNamespace

import pytest

from fleetd.agent_capacity import _field_fact, host_capacity_from_facts
from fleetd.contracts import ContractError, HostFacts, validate_host_facts
from fleetd.facts import collect_host_facts


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "facts-root"


def _statvfs(_path):
    return SimpleNamespace(f_frsize=4096, f_bsize=4096, f_blocks=100, f_bavail=40)


def _collect(root=FIXTURE_ROOT):
    return validate_host_facts(collect_host_facts(root, disk_statvfs_reader=_statvfs))


def _unknown(reason):
    return {"v": None, "reason": reason}


def test_host_capacity_projects_validated_facts_and_configured_policy(tmp_path):
    capacity = host_capacity_from_facts(_collect())

    assert capacity.cpu.online.value == 4
    assert capacity.cpu.planning_cap.value == 5.0
    assert capacity.load1.value == 0.62
    assert capacity.memory.total_bytes.value == 8 * 1024**3
    assert capacity.memory.host_reserve_bytes.value == 671088640
    assert capacity.psi.memory.full_avg10_pct.value == 0.2
    assert capacity.gate_slice.memory_warm_bytes.value == 157286400
    assert capacity.gate_slice.cpu_max.value == "500000 100000"
    assert capacity.disks.value[0].name.value == "checkout"
    assert capacity.cgroup_v2_mounted.value is True
    assert capacity.policy.footprint_charges_bytes.value["small"] == 268435456
    assert capacity.policy.priority_classes_by_principal.value["worker"] == ("dev", "gate", "campaign")
    assert capacity.policy.disk_floors.value["checkout"] == 1073741824
    assert capacity.policy.max_upload_store_bytes.value == 10 * 1024**3
    assert capacity.policy.admission.value["queue_path"] == "/run/fleetd/queue"
    assert capacity.policy.aging_s.value == 60.0
    assert capacity.policy.legacy_charge_bytes.value == 536870912
    assert capacity.policy.legacy_planned_cores.value == 1.5
    assert capacity.guard.guard_state.value == "report"
    assert capacity.guard.frozen_units.value is None
    assert "hostobs" in capacity.guard.frozen_units.reason
    assert capacity.warm_samples_bytes_by_id.value is None
    assert "not in host-facts/2" in capacity.warm_samples_bytes_by_id.reason


def test_host_capacity_preserves_unknown_reasons_and_never_invents_values():
    facts = _collect().to_dict()
    facts["policy"]["aging_s"] = {
        "v": None, "unit": "seconds", "source": "/etc/fleetd/facts.toml",
        "reason": "aging setting missing on this host",
    }
    facts["policy"]["legacy_charge_bytes"] = {
        "v": None, "unit": "bytes", "source": "/etc/fleetd/facts.toml",
        "reason": "legacy charge setting missing on this host",
    }
    facts["guard"]["frozen_units"] = _unknown("hostobs reader unavailable")
    validated = validate_host_facts(facts)
    capacity = host_capacity_from_facts(validated)

    assert capacity.policy.aging_s.value is None
    assert capacity.policy.aging_s.reason == "aging setting missing on this host"
    assert capacity.policy.legacy_charge_bytes.value is None
    assert capacity.policy.legacy_charge_bytes.reason == "legacy charge setting missing on this host"
    assert capacity.guard.frozen_units.reason == "hostobs reader unavailable"
    assert capacity.disks.value is not None


def test_host_capacity_keeps_null_collection_groups_and_limits_unknown():
    facts = _collect().to_dict()
    facts["disks"] = _unknown("disk collection unavailable")
    facts["gate_slice"]["cpu_max"] = _unknown("cpu.max unreadable")
    capacity = host_capacity_from_facts(validate_host_facts(facts))

    assert capacity.disks.value is None
    assert capacity.disks.reason == "disk collection unavailable"
    assert capacity.gate_slice.cpu_max.value is None
    assert capacity.gate_slice.cpu_max.reason == "cpu.max unreadable"


@pytest.mark.parametrize("wrapper", [{"v": None}, {"v": None, "reason": "  "}, {"v": None, "reason": 4}])
def test_capacity_fact_refuses_null_values_without_a_nonempty_reason(wrapper):
    with pytest.raises(ValueError, match="unavailable without a reason"):
        _field_fact(wrapper, "policy.fact")


def test_capacity_mapping_preserves_unknown_policy_reason():
    facts = _collect().to_dict()
    facts["policy"]["footprint_charges_bytes"] = _unknown("footprint charges are not configured")
    facts["policy"]["max_upload_store_bytes"] = _unknown("upload store is not configured")

    capacity = host_capacity_from_facts(validate_host_facts(facts))

    assert capacity.policy.footprint_charges_bytes.value is None
    assert capacity.policy.footprint_charges_bytes.reason == "footprint charges are not configured"
    assert capacity.policy.max_upload_store_bytes.value is None
    assert capacity.policy.max_upload_store_bytes.reason == "upload store is not configured"


def test_host_capacity_ignores_frozen_count_without_hostobs_reader():
    facts = _collect().to_dict()
    facts["guard"]["frozen_units"] = {"v": 3}
    capacity = host_capacity_from_facts(validate_host_facts(facts))

    assert capacity.guard.frozen_units.value is None
    assert capacity.guard.frozen_units.reason == "not-collected: hostobs freeze events are not merged"


def test_host_capacity_requires_validated_host_facts_v2():
    with pytest.raises(TypeError, match="validated HostFacts"):
        host_capacity_from_facts({})
    with pytest.raises(ValueError, match="host-facts/2"):
        host_capacity_from_facts(HostFacts({"schema": "host-facts/1"}))
