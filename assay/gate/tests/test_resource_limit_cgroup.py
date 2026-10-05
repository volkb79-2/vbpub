"""tester-unified must expose enforcing cgroup v2 counters through ancestors."""

from assay.resource_limits import read_current_cgroup_counters


def test_tester_unified_exposes_pids_and_memory_event_counters():
    counters = read_current_cgroup_counters()

    assert type(counters.pids_max) is int
    assert type(counters.memory_max) is int
    assert type(counters.memory_oom) is int
    assert type(counters.memory_oom_kill) is int
    assert type(counters.memory_oom_group_kill) is int
