"""B206: where :class:`~assay.liveness.LivenessRunner` closes the observer window.

The window must be closed on every exit path -- normal completion (while the
root is still an unreaped zombie, so the daemon reads exact CPU), the hung
kill and the budget kill (both BEFORE the process group is killed), and the
final ``finally`` -- and a close is never sent twice.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from assay import liveness, observer_windows as ow
from observer_support import FakeObserver, golden


@pytest.fixture(autouse=True)
def _no_ambient_observer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ASSAY_RESOURCE_OBSERVER_SOCKET", raising=False)


def _clear_snapshot() -> dict:
    zero = {"some": 0, "full": 0}
    return {
        "schema_version": 1,
        "status": "available",
        "cgroup_identity": "fixture-cgroup",
        "host_psi": {"cpu": {"some": 0}, "memory": dict(zero), "io": dict(zero)},
        "cgroup_psi": {"cpu": {"some": 0}, "memory": dict(zero), "io": dict(zero)},
        "cgroup_cpu": {"nr_throttled": 0, "throttled_usec": 0},
    }


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class _Proc:
    """A never-finishing fake child; ``wait`` (the reap after a kill) is logged."""

    def __init__(self, order: list[str], pid: int = 4242) -> None:
        self.pid = pid
        self.returncode = None
        self._order = order

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self._order.append("kill")
        return self.returncode


def _fake_runner(tmp_path: Path, proc: _Proc, clock: _Clock, cpu_reader):
    runner = liveness.LivenessRunner(
        events_dir=tmp_path / "candidates",
        expect_next_event_within_s=15.0,
        monotonic=clock.now,
        sleep=clock.advance,
        cpu_reader=cpu_reader,
        resource_reader=lambda pid: _clear_snapshot(),
        popen=lambda argv, **kwargs: proc,  # a window adds preexec_fn
        process_group_killer=lambda pgid, sig: None,
    )
    cwd = tmp_path / "cand"
    cwd.mkdir()
    return runner, cwd


def _observer(tmp_path: Path, order: list[str]) -> FakeObserver:
    opened = golden("open")["response"]
    closed = golden("close-cgroup")["response"]

    def handle(request):
        if request["verb"] == "window-open":
            order.append("open")
            return {**opened, "mode": request["args"]["mode"]}
        order.append("close")
        return closed

    return FakeObserver(tmp_path / "o.sock", handle)


def _proc_state(pid: int) -> str:
    stat = Path(f"/proc/{pid}/stat").read_text()
    return stat[stat.rindex(")") + 2]


def test_a_normally_exiting_child_is_a_zombie_when_its_window_closes(tmp_path: Path):
    seen: dict[str, object] = {}

    def handle(request):
        if request["verb"] == "window-open":
            seen["pid"] = request["args"]["root_pid"]
            return {**golden("open")["response"], "mode": request["args"]["mode"]}
        seen["state"] = _proc_state(seen["pid"])  # type: ignore[arg-type]
        return golden("close-cgroup")["response"]

    cwd = tmp_path / "cand"
    cwd.mkdir()
    with FakeObserver(tmp_path / "o.sock", handle) as observer:
        collector = ow.ProfileCollector(socket_path=str(observer.path), jobs=1)
        runner = liveness.LivenessRunner(
            events_dir=tmp_path / "candidates",
            expect_next_event_within_s=15.0,
            resource_reader=lambda pid: _clear_snapshot(),
        )
        with ow.activate(collector.config()) as slot:
            result = runner(
                (sys.executable, "-c", "import time; time.sleep(0.3)"),
                env=dict(os.environ), cwd=cwd, timeout=60.0,
            )
        assert result.returncode == 0
        assert seen["state"] == "Z"  # closed before the root was reaped
        assert [r["verb"] for r in observer.requests] == ["window-open", "window-close"]
        assert slot.result is not None and slot.result.reply is not None


def test_a_hung_kill_closes_the_window_before_the_group_is_killed(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ow, "exited_unreaped", lambda pid: False)
    order: list[str] = []
    clock = _Clock()
    proc = _Proc(order)
    runner, cwd = _fake_runner(tmp_path, proc, clock, cpu_reader=lambda pid: 3.0)
    with _observer(tmp_path, order) as observer:
        collector = ow.ProfileCollector(socket_path=str(observer.path), jobs=1)
        with ow.activate(collector.config()) as slot:
            with pytest.raises(liveness.LivenessHungExpired):
                runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
        assert [r["verb"] for r in observer.requests] == ["window-open", "window-close"]
        assert slot.result is not None and slot.result.reply is not None
    # `kill` is the reap inside a kill; the one close precedes the first.
    assert order[:3] == ["open", "close", "kill"]
    assert order.count("close") == 1


def test_a_budget_kill_closes_the_window_before_the_group_is_killed(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ow, "exited_unreaped", lambda pid: False)
    order: list[str] = []
    clock = _Clock()
    proc = _Proc(order)
    ticks = {"n": 0}

    def growing(pid: int) -> float:
        ticks["n"] += 1
        return ticks["n"] * 10.0  # CPU keeps growing: never hung

    runner, cwd = _fake_runner(tmp_path, proc, clock, cpu_reader=growing)
    with _observer(tmp_path, order) as observer:
        collector = ow.ProfileCollector(socket_path=str(observer.path), jobs=1)
        with ow.activate(collector.config()) as slot:
            with pytest.raises(subprocess.TimeoutExpired) as caught:
                runner(("pytest", "-q"), env={}, cwd=cwd, timeout=5.0)
        assert not isinstance(caught.value, liveness.LivenessHungExpired)
        assert [r["verb"] for r in observer.requests] == ["window-open", "window-close"]
        assert slot.result is not None and slot.result.reply is not None
    assert order[:3] == ["open", "close", "kill"]
    assert order.count("close") == 1
