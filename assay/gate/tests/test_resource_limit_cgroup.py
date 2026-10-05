"""tester-unified must expose the cgroup v2 counters native R2 needs."""

from assay.resource_limits import read_current_cgroup_counters


def test_tester_unified_exposes_pids_and_oom_event_counters():
    counters = read_current_cgroup_counters()

    assert type(counters.pids_max) is int
    assert type(counters.memory_oom_kill) is int
    assert type(counters.memory_oom_group_kill) is int
