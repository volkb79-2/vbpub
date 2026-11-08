"""CP-40: ancestors' local memory.events as session and window deltas."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cgprofile import ancestor_events
from tests.conftest import cgroup_files, write_cgroup
from tests.test_serve import (  # noqa: F401 - fixtures shared with the serve tests
    SIMPLE_CONTAINER_ID,
    _start_req,
    simple_proc,
    simple_root,
    simple_server,
)

FIXTURES = Path(__file__).parent / "fixtures" / "rg55"
LEAF = f"/dev.slice/dev-background.slice/docker-{SIMPLE_CONTAINER_ID}.scope"


@pytest.mark.parametrize(
    ("cgroup", "expected"),
    [
        ("/dev.slice/dev-gates.slice/run.scope",
         ["/dev.slice/dev-gates.slice", "/dev.slice"]),
        ("/dev.slice/run.scope", ["/dev.slice"]),
        ("/dev.slice", []),
        ("/run.scope", []),
        # Outside the dev tree the walk ends at the top-level component.
        ("/system.slice/a/b.scope", ["/system.slice/a", "/system.slice"]),
        ("dev.slice/dev-gates.slice/run.scope",
         ["/dev.slice/dev-gates.slice", "/dev.slice"]),
    ],
)
def test_ancestor_paths_stop_at_the_dev_root(cgroup, expected):
    assert ancestor_events.ancestor_paths(cgroup) == expected


def test_ancestor_paths_honour_a_configured_root():
    assert ancestor_events.ancestor_paths(
        "/a.slice/b.slice/c.scope", root="/a.slice/b.slice"
    ) == ["/a.slice/b.slice"]


@pytest.mark.parametrize(
    "content",
    [
        None,                                  # file absent
        "high 1\nmax 0\noom 0\n",              # a required counter missing
        "high 1\nmax x\noom 0\noom_kill 0\n",  # non-numeric
        "high 1 2\nmax 0\noom 0\noom_kill 0\n",  # malformed line
        "high -1\nmax 0\noom 0\noom_kill 0\n",  # not the kernel's decimal grammar
    ],
)
def test_unreadable_event_file_is_none(tmp_path, content):
    if content is not None:
        (tmp_path / "memory.events.local").write_text(content)
    assert ancestor_events.read_local_events(str(tmp_path)) is None


def test_event_file_with_extra_counters_reads_the_four_that_matter(tmp_path):
    (tmp_path / "memory.events.local").write_text(
        "low 9\nhigh 1\nmax 2\noom 3\noom_kill 4\noom_group_kill 5\n"
    )
    assert ancestor_events.read_local_events(str(tmp_path)) == {
        "high": 1, "max": 2, "oom": 3, "oom_kill": 4,
    }


def test_deltas_are_per_ancestor_and_null_when_either_end_is_unreadable():
    zero = {"high": 0, "max": 0, "oom": 0, "oom_kill": 0}
    start = {"/a": zero, "/b": zero, "/c": None, "/d": zero}
    end = {
        "/a": {"high": 2, "max": 0, "oom": 0, "oom_kill": 1},
        "/b": zero, "/c": zero, "/d": None,
    }
    assert ancestor_events.deltas(start, end) == [
        {"path": "/a", "high": 2, "max": 0, "oom": 0, "oom_kill": 1},
        {"path": "/b", "high": 0, "max": 0, "oom": 0, "oom_kill": 0},
        {"path": "/c", "high": None, "max": None, "oom": None, "oom_kill": None},
        {"path": "/d", "high": None, "max": None, "oom": None, "oom_kill": None},
    ]


def _write_events(root: Path, rel: str, **counters: int) -> None:
    values = {"low": 0, "high": 0, "max": 0, "oom": 0, "oom_kill": 0, **counters}
    (root / rel.strip("/") / "memory.events.local").write_text(
        "".join(f"{key} {value}\n" for key, value in values.items())
    )


def test_summary_reports_an_ancestor_event_on_that_ancestor_only(simple_server, simple_root):
    started = simple_server._dispatch(_start_req())
    sess = simple_server._sessions[started["session"]]
    _write_events(simple_root, "/dev.slice/dev-background.slice", high=1)
    # The lane's own limit event is the Summary's `events`, never an ancestor.
    _write_events(simple_root, LEAF, high=7, oom_kill=2)

    with simple_server._lock:
        simple_server._finalize_session_locked(sess, aborted_reason=None)

    assert sess.summary_doc["ancestors_memory_events_local"] == [
        {"path": "/dev.slice/dev-background.slice", "high": 1, "max": 0, "oom": 0, "oom_kill": 0},
        {"path": "/dev.slice", "high": 0, "max": 0, "oom": 0, "oom_kill": 0},
    ]
    assert all(entry["path"] != LEAF for entry in sess.summary_doc["ancestors_memory_events_local"])


def test_summary_counts_events_during_the_session_not_before_it(simple_server, simple_root):
    _write_events(simple_root, "/dev.slice", high=40, max=3)
    started = simple_server._dispatch(_start_req())
    sess = simple_server._sessions[started["session"]]
    _write_events(simple_root, "/dev.slice", high=41, max=3)

    with simple_server._lock:
        simple_server._finalize_session_locked(sess, aborted_reason=None)

    by_path = {e["path"]: e for e in sess.summary_doc["ancestors_memory_events_local"]}
    assert by_path["/dev.slice"]["high"] == 1
    assert by_path["/dev.slice"]["max"] == 0


def test_summary_marks_an_unreadable_ancestor_null_and_keeps_the_others(simple_server, simple_root):
    started = simple_server._dispatch(_start_req())
    sess = simple_server._sessions[started["session"]]
    (simple_root / "dev.slice" / "memory.events.local").unlink()

    with simple_server._lock:
        simple_server._finalize_session_locked(sess, aborted_reason=None)

    by_path = {e["path"]: e for e in sess.summary_doc["ancestors_memory_events_local"]}
    assert by_path["/dev.slice"] == {
        "path": "/dev.slice", "high": None, "max": None, "oom": None, "oom_kill": None,
    }
    assert by_path["/dev.slice/dev-background.slice"]["high"] == 0


def test_golden_ancestor_fixture_matches_what_the_daemon_produces(simple_server, simple_root):
    golden = json.loads((FIXTURES / "summary-v1.1-ancestors.json").read_text())
    base = json.loads((FIXTURES / "summary-v1.1.json").read_text())
    # The ancestor list is the only difference from the live v1.1 golden.
    key = "ancestors_memory_events_local"
    assert {k: v for k, v in golden.items() if k != key} == {k: v for k, v in base.items() if k != key}
    assert golden[key] != base[key]

    started = simple_server._dispatch(_start_req())
    sess = simple_server._sessions[started["session"]]
    _write_events(simple_root, "/dev.slice/dev-background.slice", high=1)
    with simple_server._lock:
        simple_server._finalize_session_locked(sess, aborted_reason=None)
    assert sess.summary_doc["ancestors_memory_events_local"] == golden["ancestors_memory_events_local"]
