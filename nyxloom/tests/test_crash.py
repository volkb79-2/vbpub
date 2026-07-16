"""Crash drills over the frozen core (SPEC §14.4). Package P11.

Oracles 6-10 from handoff/P11-properties-crash.md:
  6.  append-without-save heals (SPEC §5.6 'event wins').
  7.  statefile atomicity (no partial reads, no surviving .tmp).
  8.  flock release on SIGKILL (leases.acquire is kernel-mediated).
  9.  wrapper SIGKILL drill (skip-guarded: wrapper.py is package P04).
  10. event-log fsync visibility across a fork.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import signal
import threading
import time
from pathlib import Path

import pytest

from nyxloom import leases, paths, storage, wrapper
from nyxloom.types import (
    Actor,
    ActorKind,
    Attempt,
    AttemptState,
    EventType,
    Role,
    Route,
    TaskState,
    TaskStateFile,
    utc_now,
)

# ---------------------------------------------------------------------------
# Oracle 6: append-without-save heals


def test_append_without_save_heals(tmp_state):
    project = "crash-heal"
    task_id = "t-heal"
    actor = Actor(kind=ActorKind.TICK, id="drill")
    states: dict[str, TaskStateFile] = {}

    tsf = TaskStateFile(schema_version=1, task_id=task_id, project=project,
                         state=TaskState.CARVED, since=utc_now())
    storage.append_and_apply(
        project, states, actor=actor, type=EventType.TASK_CREATED,
        payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )

    # Simulate a crash between append and save: append the transition event
    # directly, WITHOUT going through apply_event/save_state.
    storage.append_event(
        project, actor=actor, type=EventType.TASK_TRANSITIONED,
        payload={"from": "CARVED", "to": "QUEUED", "notes": None}, task_id=task_id,
    )

    on_disk = storage.load_state(project, task_id)
    assert on_disk.state is TaskState.CARVED, "statefile must still show the pre-crash state"

    replayed = storage.replay(project)[task_id]
    assert replayed.state is TaskState.QUEUED, "the event log wins over the stale statefile"

    storage.save_state(replayed)
    healed = storage.load_state(project, task_id)
    assert healed.state is TaskState.QUEUED


# ---------------------------------------------------------------------------
# Oracle 7: statefile atomicity


def test_statefile_atomicity_under_concurrent_saves(tmp_state):
    project = "atomic-proj"
    task_id = "t-atomic"
    paths.ensure_layout(project)

    base = TaskStateFile(schema_version=1, task_id=task_id, project=project,
                          state=TaskState.CARVED, since=utc_now())
    storage.save_state(base)

    stop = threading.Event()
    errors: list[str] = []

    def reader() -> None:
        while not stop.is_set():
            try:
                s = storage.load_state(project, task_id)
                if s is None:
                    errors.append("missing file")
                    return
            except json.JSONDecodeError as exc:
                errors.append(f"partial json: {exc}")
                return

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        for i in range(200):
            tsf = TaskStateFile(schema_version=1, task_id=task_id, project=project,
                                 state=TaskState.CARVED, since=utc_now(), notes=f"iteration-{i}")
            storage.save_state(tsf)
    finally:
        stop.set()
        t.join(timeout=5)

    assert errors == []
    p = paths.statefile_path(project, task_id)
    assert p.exists()
    assert not p.with_suffix(".tmp").exists()


# ---------------------------------------------------------------------------
# Oracle 8: flock release on SIGKILL


def _acquire_and_hold(name: str) -> None:
    lease = leases.acquire(name, owner="child", purpose="drill")
    if lease is None:
        os._exit(1)
    time.sleep(60)


def test_flock_release_on_sigkill(tmp_state):
    name = "drill"

    p = multiprocessing.Process(target=_acquire_and_hold, args=(name,))
    p.start()
    try:
        deadline = time.monotonic() + 3.0
        held = False
        while time.monotonic() < deadline:
            if leases.holder_info(name)[0]["held"]:
                held = True
                break
            time.sleep(0.1)
        assert held, "child never acquired the lease within 3s"

        p.kill()  # SIGKILL
        p.join(timeout=5)
        assert not p.is_alive()

        deadline = time.monotonic() + 3.0
        free = False
        while time.monotonic() < deadline:
            if not leases.holder_info(name)[0]["held"]:
                free = True
                break
            time.sleep(0.1)
        assert free, "holder_info still reports held 3s after SIGKILL"

        lease = leases.acquire(name, owner="parent", purpose="post-kill")
        assert lease is not None, "acquire must succeed once the kernel released the flock"
        lease.release()
    finally:
        if p.is_alive():
            p.kill()
            p.join(timeout=5)


# ---------------------------------------------------------------------------
# Oracle 9: wrapper SIGKILL drill (skip-guarded — wrapper.py is package P04)


def test_wrapper_sigkill_drill(tmp_state, tmp_path):
    project = "wrapper-drill"
    task_id = "t-wrapper-drill"
    attempt_id = "att-wrapper-drill"
    actor = Actor(kind=ActorKind.TICK, id="drill")

    paths.ensure_layout(project)
    attempt_dir = paths.attempt_dir(project, attempt_id)
    attempt_dir.mkdir(parents=True, exist_ok=True)

    tsf = TaskStateFile(schema_version=1, task_id=task_id, project=project,
                         state=TaskState.ACTIVE, since=utc_now())
    states: dict[str, TaskStateFile] = {}
    storage.append_and_apply(
        project, states, actor=actor, type=EventType.TASK_CREATED,
        payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )
    att = Attempt(attempt_id=attempt_id, role=Role.IMPLEMENTER, state=AttemptState.CREATED,
                  route=Route(route_id="fake-cli", cli="fake", model="fake-model"),
                  started=utc_now())
    storage.append_and_apply(
        project, states, actor=Actor(kind=ActorKind.WRAPPER, id=f"wrapper-{attempt_id}"),
        type=EventType.ATTEMPT_CREATED, payload={"attempt": att.to_dict()},
        task_id=task_id, attempt_id=attempt_id,
    )

    spec = wrapper.WrapperSpec(
        project=project, task_id=task_id, attempt_id=attempt_id,
        argv=["sleep", "30"], cwd=str(tmp_path),
        log_path=str(attempt_dir / "wrapper.log"),
        receipt_path=str(attempt_dir / "receipt.json"),
        attempt_dir=str(attempt_dir),
        route_def={"route_id": "fake-cli", "cli": "fake", "model": "fake-model"},
        leases=[{"name": "wrapper-drill-lease", "capacity": 1}],
    )

    try:
        pid = wrapper.launch_detached(spec)
    except NotImplementedError:
        pytest.skip("P04 pending")
        return

    try:
        # Give the wrapper a moment to reach RUNNING (lease acquired, child
        # spawned) before pulling the trigger.
        time.sleep(1.0)
        os.kill(pid, signal.SIGKILL)

        receipt_path = Path(spec.receipt_path)
        time.sleep(0.5)  # let a would-be (buggy) writer finish, if any
        assert not receipt_path.exists(), "no receipt.json may be written on a hard kill"

        deadline = time.monotonic() + 3.0
        free = False
        while time.monotonic() < deadline:
            if not leases.holder_info("wrapper-drill-lease")[0]["held"]:
                free = True
                break
            time.sleep(0.1)
        assert free, "every spec lease must be free within 3s of the wrapper's death"

        types = [ev.type for ev in storage.iter_events(project) if ev.attempt_id == attempt_id]
        assert EventType.ATTEMPT_STARTED in types
        assert EventType.ATTEMPT_EXITED not in types
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        # Best-effort cleanup: the CLI child (sleep 30) is orphaned once the
        # wrapper dies (start_new_session=True detaches it); reap it early
        # instead of leaving it to run out its 30s on its own.
        child_pid_file = attempt_dir / "child.pid"
        if child_pid_file.exists():
            try:
                os.kill(int(child_pid_file.read_text(encoding="utf-8").strip()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass


# ---------------------------------------------------------------------------
# Oracle 10: event-log fsync visibility across a fork


def _read_events_text(project: str, q) -> None:
    q.put(paths.events_path(project).read_text(encoding="utf-8"))


def test_event_log_fsync_visibility(tmp_state):
    project = "fsync-proj"
    actor = Actor(kind=ActorKind.TICK, id="drill")

    ev = storage.append_event(
        project, actor=actor, type=EventType.PROGRESS_RECORDED, payload={"units": ["u1"]},
    )

    ctx = multiprocessing.get_context("fork")
    q: multiprocessing.Queue = ctx.Queue()
    p = ctx.Process(target=_read_events_text, args=(project, q))
    p.start()
    text = q.get(timeout=5)
    p.join(timeout=5)

    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines, "a separate process must see the appended line immediately"
    last = json.loads(lines[-1])
    assert last["sequence"] == ev.sequence
