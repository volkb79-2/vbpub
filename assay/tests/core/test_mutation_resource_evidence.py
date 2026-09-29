"""B111 measurement evidence: baseline setup/teardown forwarding (O9), the
resource sidecar and event-time readers, `tree_sample` (O7b) and the
end-to-end candidate resource evidence (O8).

Nothing here asserts a duration or a sample count: the values are properties
of records the tests write themselves or of kernel-reported counters.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import GitRepo, make_lane, make_r2_judge

from assay import liveness, runner
from assay.adapters.python import PythonAdapter
from assay.config import MutationConfig
from assay.errors import Outcome


def _write(path: Path, records: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def _phase(nodeid: Any, when: str, duration: Any, pid: int = 100) -> dict[str, Any]:
    return {"event": "phase", "when": when, "nodeid": nodeid, "duration_s": duration, "t": 1.0, "pid": pid}


def _call(nodeid: Any, pid: int = 100) -> dict[str, Any]:
    return {
        "event": "test",
        "when": "call",
        "nodeid": nodeid,
        "outcome": "passed",
        "duration_s": 1.0,
        "t": 1.0,
        "pid": pid,
    }


# --------------------------------------------------------------------------
# O9: baseline phase durations
# --------------------------------------------------------------------------


def test_o9_setup_and_teardown_pair_positionally_in_the_owner_stream(tmp_path: Path) -> None:
    events = _write(
        tmp_path / "baseline.ndjson",
        [
            {"event": "session_start", "t": 0.0, "pid": 100},
            _phase("a", "setup", 0.1),  # a setup error: no call follows it
            _phase("a", "setup", 0.2),
            _call("a"),
            _phase("a", "teardown", 0.3),
            _phase("a", "teardown", 9.9),  # a second teardown is never paired
            _phase("a", "setup", 0.4),  # the duplicate run of the same nodeid
            _call("a"),
            _phase("a", "teardown", 0.5),
            _phase("b", "setup", True),
            _call("b"),
            _phase("b", "teardown", -1.0),
            _phase("c", "setup", 0.6),
            _call("c"),  # the final call has no teardown
            _phase("d", "setup", 10**400),
            _call("d"),
            _phase("zz", "teardown", 0.7),  # no call is waiting for it
            _phase("a", "other", 0.8),
            _phase(["unhashable"], "setup", 0.9),
            _call(["unhashable"]),
            {"event": "phase", "when": "setup", "duration_s": 5.0, "pid": 100},
            _phase("a", "setup", 8.0, pid=200),  # a foreign process
            _call("a", pid=200),
            _phase("a", "teardown", 8.5, pid=200),
        ],
    )
    assert liveness.baseline_phase_durations(events) == [
        {"setup_s": 0.2, "teardown_s": 0.3},
        {"setup_s": 0.4, "teardown_s": 0.5},
        {"setup_s": None, "teardown_s": None},
        {"setup_s": 0.6, "teardown_s": None},
        {"setup_s": None, "teardown_s": None},
        {"setup_s": None, "teardown_s": None},
    ]
    assert len(liveness.baseline_phase_durations(events)) == len(
        liveness.baseline_test_events(events)
    )


def test_o9_a_legacy_file_without_pids_keeps_the_merged_view(tmp_path: Path) -> None:
    events = _write(
        tmp_path / "legacy.ndjson",
        [
            {"event": "phase", "when": "setup", "nodeid": "a", "duration_s": 0.1},
            {"event": "test", "nodeid": "a", "outcome": "passed", "duration_s": 1.0},
            {"event": "phase", "when": "teardown", "nodeid": "a", "duration_s": 0.2},
        ],
    )
    assert liveness.baseline_phase_durations(events) == [{"setup_s": 0.1, "teardown_s": 0.2}]


def test_o9_a_missing_or_absent_events_file_has_no_rows(tmp_path: Path) -> None:
    assert liveness.baseline_phase_durations(None) == []
    assert liveness.baseline_phase_durations(tmp_path / "absent.ndjson") == []


# --------------------------------------------------------------------------
# read_resource_sidecar / first_event_times
# --------------------------------------------------------------------------

_SIDECAR = {
    "format": 1,
    "samples": 3,
    "cpu_seconds": 1.5,
    "peak_rss_bytes": 1024,
    "spawned_at": 1790000000.5,
}


def test_read_resource_sidecar_is_none_unless_the_file_is_a_format_1_object(
    tmp_path: Path,
) -> None:
    events = tmp_path / "events.ndjson"
    sidecar = events.with_suffix(liveness.RESOURCE_SIDECAR_SUFFIX)
    assert liveness.read_resource_sidecar(events) is None  # absent
    for content in (b"\xff", b"[]", b'{"format": 2}', b"{not json"):
        sidecar.write_bytes(content)
        assert liveness.read_resource_sidecar(events) is None
    sidecar.write_text(json.dumps(_SIDECAR), encoding="utf-8")
    assert liveness.read_resource_sidecar(events) == _SIDECAR


def test_first_event_times_of_a_missing_file_is_none_none(tmp_path: Path) -> None:
    assert liveness.first_event_times(None) == (None, None)
    assert liveness.first_event_times(tmp_path / "absent.ndjson") == (None, None)


def test_first_event_times_uses_the_owners_first_test_record(tmp_path: Path) -> None:
    events = tmp_path / "events.ndjson"
    _write(
        events,
        [
            {"event": "session_start", "t": 5.0, "pid": 9},
            {"event": "test", "t": 6.0, "pid": 8, "nodeid": "foreign"},
            {"event": "test", "t": 7, "pid": 9, "nodeid": "owned"},
        ],
    )
    assert liveness.first_event_times(events) == (5.0, 7)


def test_first_event_times_without_a_test_record_is_start_only(tmp_path: Path) -> None:
    events = _write(
        tmp_path / "events.ndjson",
        [
            {"event": "session_start", "t": 5.0, "pid": 9},
            {"event": "phase", "when": "setup", "t": 6.0, "pid": 9},
        ],
    )
    assert liveness.first_event_times(events) == (5.0, None)


@pytest.mark.parametrize("raw", ["true", '"1"', "1e999", "null"])
def test_first_event_times_rejects_a_t_that_is_not_a_finite_number(
    tmp_path: Path, raw: str
) -> None:
    events = tmp_path / "events.ndjson"
    events.write_text(
        '{"event": "session_start", "t": %s, "pid": 9}\n'
        '{"event": "test", "t": %s, "pid": 9, "nodeid": "a"}\n' % (raw, raw),
        encoding="utf-8",
    )
    assert liveness.first_event_times(events) == (None, None)


# --------------------------------------------------------------------------
# tree_sample (O7b)
# --------------------------------------------------------------------------


def test_tree_sample_of_this_process_has_cpu_and_memory() -> None:
    sample = liveness.tree_sample(os.getpid())
    assert sample.cpu_seconds > 0
    assert sample.rss_bytes > 0


def test_tree_sample_of_a_dead_pid_raises_oserror() -> None:
    with pytest.raises(OSError):
        liveness.tree_sample(999_999_999)


_REAPING_HELPER = """
import os, sys

def ticks():
    raw = open('/proc/self/stat').read()
    fields = raw[raw.rfind(')') + 2:].split()
    return int(fields[11]) + int(fields[12])

child = os.fork()
if child == 0:
    while ticks() < 50:
        pass
    os._exit(0)
os.waitpid(child, 0)
sys.stdout.write('ready\\n')
sys.stdout.flush()
sys.stdin.read()
"""


def _stat_ticks(pid: int) -> tuple[int, int]:
    """``(utime + stime, cutime + cstime)`` as the kernel reports them."""
    raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    fields = raw[raw.rfind(")") + 2 :].split()
    return int(fields[11]) + int(fields[12]), int(fields[13]) + int(fields[14])


def test_o7b_cpu_of_a_reaped_descendant_is_counted_through_its_live_parent() -> None:
    helper = subprocess.Popen(
        [sys.executable, "-c", _REAPING_HELPER],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert helper.stdout is not None
        # The readiness line is the synchronization point; 60 s only guards a hang.
        ready, _, _ = select.select([helper.stdout], [], [], 60)
        assert ready, "the helper never reported that its child was reaped"
        assert helper.stdout.readline() == "ready\n"
        own_ticks, reaped_ticks = _stat_ticks(helper.pid)
        # The fixture only discriminates when the reaped child out-burned the
        # helper itself: a live-only sum is then strictly smaller.
        assert reaped_ticks > own_ticks
        assert liveness.tree_sample(helper.pid).cpu_seconds >= reaped_ticks / os.sysconf(
            "SC_CLK_TCK"
        )
    finally:
        assert helper.stdin is not None
        helper.stdin.close()
        helper.stdout.close()
        helper.wait(timeout=60)


# --------------------------------------------------------------------------
# O8: end-to-end candidate resource evidence on a real liveness R2 lane
# --------------------------------------------------------------------------


def _seed_two_candidate_lane(repo: GitRepo) -> tuple[str, str]:
    repo.write(".gitignore", "__pycache__/\n.pytest_cache/\n")
    repo.write("src/mod.py", "def flag(value):\n    return value >= 0\n")
    repo.write(
        "tests/test_behavior.py",
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
        "from mod import flag\n\n"
        "def test_behavior():\n"
        "    assert flag(1)\n"
        "    assert not flag(0)\n"
        "    assert flag(9)\n"
        "    assert not flag(10)\n",
    )
    base = repo.commit_all("seed test project")
    repo.write("src/mod.py", "def flag(value):\n    return value > 0 and value < 10\n")
    return base, repo.commit_all("two compare-swap mutation sites")


def test_o8_every_candidate_carries_resource_evidence_in_progress_and_state(
    git_repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, head = _seed_two_candidate_lane(git_repo)
    lane = make_lane(
        rigor=("R0", "R2"),
        argv=(sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"),
        judge=make_r2_judge(
            source_root_paths=(git_repo.path / "src",),
            base=base,
            mutation=MutationConfig(
                jobs=1,
                max_mutants=10,
                operators=("python:compare-swap",),
                liveness="true",
            ),
        ),
        budget="2m",
        budget_seconds=120,
    )
    state_dir = tmp_path / "state"
    progress = tmp_path / "progress.jsonl"
    constructed: list[dict[str, Any]] = []

    class SpyRunner(liveness.LivenessRunner):
        def __init__(self, **kwargs: Any) -> None:
            constructed.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(liveness, "LivenessRunner", SpyRunner)

    def judge_lane(**extra: Any):
        return runner.run_lane(
            lane,
            commit=head,
            repo=git_repo.path,
            project_root=git_repo.path,
            adapter=PythonAdapter(),
            assay_version="0.1.0",
            resume=True,
            state_dir=state_dir,
            **extra,
        )

    verdict = judge_lane(progress_artifact=progress)
    assert verdict.outcome is Outcome.PASS
    assert [kwargs["sampler"] for kwargs in constructed] == [liveness.tree_sample]

    events = [
        json.loads(line)
        for line in progress.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["event"] == "candidate"
    ]
    records = [
        json.loads(path.read_text(encoding="utf-8")) for path in sorted(state_dir.glob("*.json"))
    ]
    assert len(events) == 2
    assert len(records) == len(events)
    keys = ("cpu_seconds", "peak_rss_bytes", "phase_seconds", "startup_seconds")
    for event in events:
        assert set(keys) <= set(event)
        cpu, rss = event["cpu_seconds"], event["peak_rss_bytes"]
        assert cpu is None or (isinstance(cpu, float) and cpu >= 0)
        assert rss is None or (isinstance(rss, int) and not isinstance(rss, bool) and rss > 0)
        phases = event["phase_seconds"]
        assert set(phases) == {"materialize", "command", "integrity", "teardown"}
        assert all(isinstance(value, float) and value >= 0 for value in phases.values())
        startup = event["startup_seconds"]
        assert set(startup) == {"to_session_start", "to_first_test"}
        assert all(value is None or isinstance(value, float) for value in startup.values())
    assert sorted(
        (record["resources"] for record in records), key=lambda item: json.dumps(item, sort_keys=True)
    ) == sorted(
        ({key: event[key] for key in keys} for event in events),
        key=lambda item: json.dumps(item, sort_keys=True),
    )

    # A resumed run reuses the state records (the loader accepts `resources`)
    # and classifies identically.
    resumed = judge_lane()
    assert resumed.outcome is Outcome.PASS
    assert resumed.to_dict()["claims"] == verdict.to_dict()["claims"]
