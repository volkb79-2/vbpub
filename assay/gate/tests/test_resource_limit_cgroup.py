"""tester-unified must expose enforcing cgroup v2 counters through ancestors."""

import os

from assay.resource_limits import read_candidate_resource_counters


def test_tester_unified_exposes_pids_and_memory_event_counters():
    counters = read_candidate_resource_counters(
        observer_socket_path=os.environ.get("ASSAY_RESOURCE_OBSERVER_SOCKET")
    )

    assert type(counters.pids_max) is int
    assert type(counters.memory_max) is int
    assert type(counters.memory_oom) is int
    assert type(counters.memory_oom_kill) is int
    assert type(counters.memory_oom_group_kill) is int
