"""Tests for cgprofile.cli.legacy: the collector child (``_collect``), the
daemon entry (``serve``) and the report-tier subprocess (``report``).

Nothing here starts a real container, calls the real docker binary, touches
the real /sys/fs/cgroup or /proc, or sleeps in real wall-clock time:

* the fake cgroup tree comes from conftest.py's ``cgroup_root`` fixture and
  ``access.CGROUP_ROOT`` is monkeypatched to point at it;
* ``metrics.sample_host`` / ``limits.mount_flags`` are monkeypatched to fixed
  fakes for every cmd_collect test;
* the real adaptive-interval ``sampler.Sampler`` sleeps in real time by
  construction; it is replaced with ``_FakeSampler`` (make_fake_sampler),
  which drives cmd_collect's own closures from a scripted list of actions.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
from pathlib import Path
from typing import Any, Dict, List

import pytest

from cgprofile.cli import legacy as cg
from cgprofile import access, targets as targets_mod
from cgprofile import damon as damon_lib
from cgprofile import limits as limits_lib
from cgprofile import metrics as metrics_lib
from cgprofile import phases as phases_lib
from cgprofile import sampler as sampler_lib
from cgprofile import store as store_lib

def make_fake_sampler(script: List[tuple]):
    """A lib.sampler.Sampler replacement driven by a scripted action list.

    Each action is ``("sample", seq, mono)`` — calls ``sample_fn`` for real
    against whatever membership/root the test set up, then feeds the record
    to ``on_sample`` — or ``("topology", appeared, disappeared)`` — calls
    ``on_topology`` directly. Nothing here sleeps or reads a clock; the test
    controls every tick explicitly.
    """

    class _FakeSampler:
        instances: List["_FakeSampler"] = []

        def __init__(self, membership, config, sample_fn, clock=None, sleep=None):
            self.membership = membership
            self.config = config
            self.sample_fn = sample_fn
            self.force_hot_calls = 0
            _FakeSampler.instances.append(self)

        def force_hot(self):
            self.force_hot_calls += 1

        def run(self, should_stop, on_sample, on_topology):
            for action in script:
                if should_stop():
                    return
                if action[0] == "sample":
                    _, seq, mono = action
                    raw = self.sample_fn(self.membership) or {}
                    record: Dict[str, Any] = {
                        "seq": seq, "t": 1_754_325_600.0 + mono, "mono": mono,
                        "cg": raw.get("cg", {}),
                    }
                    if "proc" in raw:
                        record["proc"] = raw["proc"]
                    if "host" in raw:
                        record["host"] = raw["host"]
                    on_sample(record)
                elif action[0] == "topology":
                    _, appeared, disappeared = action
                    on_topology(appeared, disappeared)
                else:
                    raise AssertionError(f"unknown fake-sampler action {action!r}")

    return _FakeSampler


class FakeSignalRegistry:
    def __init__(self):
        self.handlers: Dict[int, Any] = {}

    def set_signal(self, sig, handler):
        prev = self.handlers.get(sig, "DEFAULT")
        self.handlers[sig] = handler
        return prev


# ── cmd_collect ───────────────────────────────────────────────────────────

@pytest.fixture
def collect_env(monkeypatch, cgroup_root: Path):
    """Point the collector at the fake tree, and stub the two readers that
    have no root override plumbed through cmd_collect at all."""
    monkeypatch.setattr(access, "CGROUP_ROOT", str(cgroup_root))
    monkeypatch.setattr(metrics_lib, "sample_host", lambda *a, **k: {"meminfo": {"MemTotal": 1}})
    monkeypatch.setattr(limits_lib, "mount_flags", lambda *a, **k: set())
    return cgroup_root


def make_collect_args(run_dir: Path, **overrides) -> argparse.Namespace:
    ns = argparse.Namespace(
        run_dir=str(run_dir),
        target=["cgroup:/dev.slice/dev-background.slice"],
        observe=[],
        follow_children=False,
        max_depth=4,
        hot_interval=0.25,
        idle_interval=2.0,
        discovery_interval=2.0,
        duration=None,
        damon=False,
        caps=None,
    )
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


def _fake_proc_stat(start_ticks: int, pid: int = 4242) -> str:
    fields = ["S", *(["0"] * 18), str(start_ticks)]
    return f"{pid} (worker (fixture)) {' '.join(fields)}\n"


class TestCmdCollect:
    def test_no_target_resolved_is_an_error(self, collect_env, tmp_path: Path):
        args = make_collect_args(tmp_path / "run-empty", target=[], observe=[])
        with pytest.raises(SystemExit):
            cg.cmd_collect(args)

    def test_happy_path_writes_manifest_ready_and_done(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-1"
        args = make_collect_args(run_dir)
        monkeypatch.setattr(sampler_lib, "Sampler", make_fake_sampler([("sample", 0, 0.0)]))
        assert cg.cmd_collect(args) == 0

        assert os.path.exists(os.path.join(str(run_dir), cg.READY_FILE))
        assert os.path.exists(os.path.join(str(run_dir), cg.DONE_FILE))
        run = store_lib.RunDir(str(tmp_path), run_id="run-1", create=False)
        manifest = run.read_manifest()
        assert manifest["mode"] == "collector"
        assert manifest["cgroup_root"] == str(collect_env)
        assert "duration" in manifest and manifest["duration"] >= 0
        samples = list(run.read("samples"))
        assert len(samples) == 1
        assert samples[0]["seq"] == 0

    def test_observer_targets_are_tagged_and_sampled_alongside_subjects(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-obs"
        args = make_collect_args(
            run_dir,
            target=["cgroup:/dev.slice/dev-background.slice"],
            observe=["cgroup:/wings.slice/wings-prod.slice"],
        )
        monkeypatch.setattr(sampler_lib, "Sampler", make_fake_sampler([("sample", 0, 0.0)]))
        cg.cmd_collect(args)
        run = store_lib.RunDir(str(tmp_path), run_id="run-obs", create=False)
        manifest = run.read_manifest()
        roles = {t["cgroup"]: t["role"] for t in manifest["targets"]}
        assert roles["/dev.slice/dev-background.slice"] == "subject"
        assert roles["/wings.slice/wings-prod.slice"] == "observer"
        sample = next(iter(run.read("samples")))
        assert "/wings.slice/wings-prod.slice" in sample["cg"]

    def test_a_second_sample_hits_the_event_detection_path(self, collect_env, tmp_path: Path, monkeypatch):
        # Only from a second sample onward does cmd_collect's on_sample
        # closure have a `prev` to diff against.
        run_dir = tmp_path / "run-2"
        args = make_collect_args(run_dir)
        monkeypatch.setattr(
            sampler_lib, "Sampler",
            make_fake_sampler([("sample", 0, 0.0), ("sample", 1, 0.25)]),
        )
        cg.cmd_collect(args)
        run = store_lib.RunDir(str(tmp_path), run_id="run-2", create=False)
        samples = list(run.read("samples"))
        assert len(samples) == 2
        # events.jsonl may legitimately be empty (nothing crossed a
        # threshold in the fake tree's static data) — what matters is that
        # writing it didn't raise, i.e. the detector.observe() path ran.
        list(run.read("events"))

    def test_event_detection_actually_fires_when_a_counter_moves(self, collect_env, tmp_path: Path, monkeypatch):
        # The two prior samples used static fixture files, so every delta was
        # zero and detector.observe() never actually returned an event; this
        # one mutates memory.events.local between ticks so run.append(
        # "events", ...) genuinely executes.
        run_dir = tmp_path / "run-event"
        events_file = collect_env / "dev.slice/dev-background.slice/memory.events.local"

        class MutatingSampler(make_fake_sampler([("sample", 0, 0.0)])):
            def run(self, should_stop, on_sample, on_topology):
                super().run(should_stop, on_sample, on_topology)
                events_file.write_text(
                    "low 0\nhigh 5\nmax 0\noom 0\noom_kill 0\noom_group_kill 0\n"
                )
                raw = self.sample_fn(self.membership) or {}
                record = {"seq": 1, "t": 1_754_325_601.0, "mono": 1.0, "cg": raw.get("cg", {})}
                on_sample(record)

        args = make_collect_args(run_dir)
        monkeypatch.setattr(sampler_lib, "Sampler", MutatingSampler)
        cg.cmd_collect(args)
        run = store_lib.RunDir(str(tmp_path), run_id="run-event", create=False)
        events = list(run.read("events"))
        assert any(e["kind"] == "memory_high_breach" for e in events)

    def test_a_new_mark_snaps_the_sampler_to_hot_on_the_next_tick(self, collect_env, tmp_path: Path, monkeypatch):
        # DESIGN.md §4.4: "any... phase mark ⇒ snap straight to hot_interval
        # on the next tick." A mark is written by a different process sharing
        # this run directory (cgprofile mark, a wrapper boundary, a log-tail
        # match — DESIGN.md §3) — the sampler has no way to notice one on its
        # own, so cmd_collect's on_sample must poll marks.jsonl each tick and
        # call sampler.force_hot() when it grows. No mark exists for the
        # first tick; one is written between the first and second, mirroring
        # test_event_detection_actually_fires_when_a_counter_moves' mid-run
        # mutation pattern above.
        run_dir = tmp_path / "run-mark-forces-hot"

        class MarkWritingSampler(make_fake_sampler([("sample", 0, 0.0)])):
            def run(self, should_stop, on_sample, on_topology):
                super().run(should_stop, on_sample, on_topology)
                phases_lib.emit_mark(str(run_dir), "loading-world", kind="phase")
                raw = self.sample_fn(self.membership) or {}
                record = {"seq": 1, "t": 1_754_325_600.25, "mono": 0.25, "cg": raw.get("cg", {})}
                on_sample(record)

        monkeypatch.setattr(sampler_lib, "Sampler", MarkWritingSampler)
        cg.cmd_collect(make_collect_args(run_dir))

        instance = MarkWritingSampler.instances[-1]
        assert instance.force_hot_calls == 1

    def test_no_new_marks_never_calls_force_hot(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-no-marks"
        monkeypatch.setattr(
            sampler_lib, "Sampler",
            make_fake_sampler([("sample", 0, 0.0), ("sample", 1, 0.25)]),
        )
        cg.cmd_collect(make_collect_args(run_dir))
        # make_fake_sampler's _FakeSampler is a fresh class per call, so its
        # own instances list holds exactly this run's construction.
        sampler_cls = sampler_lib.Sampler
        assert sampler_cls.instances[-1].force_hot_calls == 0

    def test_topology_change_before_any_sample_anchors_to_mono_zero(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-topo-first"
        args = make_collect_args(run_dir, follow_children=True)
        monkeypatch.setattr(
            sampler_lib, "Sampler",
            make_fake_sampler([("topology", ["/dev.slice/dev-background.slice/x"], [])]),
        )
        cg.cmd_collect(args)
        run = store_lib.RunDir(str(tmp_path), run_id="run-topo-first", create=False)
        events = list(run.read("events"))
        appeared = [e for e in events if e["kind"] == "cgroup_appeared"]
        assert appeared and appeared[0]["mono"] == 0.0

    def test_topology_change_after_a_sample_anchors_to_the_last_sample(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-topo-after"
        args = make_collect_args(run_dir, follow_children=True)
        monkeypatch.setattr(
            sampler_lib, "Sampler",
            make_fake_sampler([
                ("sample", 0, 3.5),
                ("topology", [], ["/dev.slice/dev-background.slice/x"]),
            ]),
        )
        cg.cmd_collect(args)
        run = store_lib.RunDir(str(tmp_path), run_id="run-topo-after", create=False)
        events = list(run.read("events"))
        gone = [e for e in events if e["kind"] == "cgroup_disappeared"]
        assert gone and gone[0]["mono"] == 3.5

    def test_caps_are_applied_during_the_run_and_restored_after(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-caps"
        target_file = collect_env / "dev.slice/dev-background.slice/memory.high"
        before = target_file.read_text().strip()
        seen_during = {}

        script = [("sample", 0, 0.0)]

        class ObservingSampler(make_fake_sampler(script)):
            def run(self, should_stop, on_sample, on_topology):
                seen_during["value"] = target_file.read_text().strip()
                super().run(should_stop, on_sample, on_topology)

        args = make_collect_args(
            run_dir,
            caps=json.dumps({"/dev.slice/dev-background.slice": {"memory.high": "1073741824"}}),
        )
        monkeypatch.setattr(sampler_lib, "Sampler", ObservingSampler)
        cg.cmd_collect(args)

        assert seen_during["value"] == "1073741824"
        assert target_file.read_text().strip() == before  # restored

    def test_pid_targets_are_sampled_and_empty_proc_data_is_filtered(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-pid"

        def fake_cgroup_of_pid(pid, root=access.CGROUP_ROOT, proc_root=access.PROC_ROOT):
            return "/dev.slice/dev-background.slice"

        monkeypatch.setattr(targets_mod, "cgroup_of_pid", fake_cgroup_of_pid)
        monkeypatch.setattr(targets_mod.util, "read_text", lambda p: "fakeproc")

        def fake_sample_proc(pid, proc_root="/proc"):
            return {"rss_anon": 100} if pid == 4242 else {}

        monkeypatch.setattr(metrics_lib, "sample_proc", fake_sample_proc)
        args = make_collect_args(run_dir, target=["pid:4242", "pid:5555"])
        monkeypatch.setattr(sampler_lib, "Sampler", make_fake_sampler([("sample", 0, 0.0)]))
        cg.cmd_collect(args)

        run = store_lib.RunDir(str(tmp_path), run_id="run-pid", create=False)
        sample = next(iter(run.read("samples")))
        assert sample["proc"] == {"4242": {"rss_anon": 100}}  # 5555 dropped: {} is falsy

    def test_host_snapshot_is_taken_on_the_configured_cadence(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-host-cadence"
        calls = []
        monkeypatch.setattr(metrics_lib, "sample_host", lambda *a, **k: calls.append(1) or {"MemTotal": 1})
        # idle/hot chosen so host_every == 2: every other sample carries "host".
        args = make_collect_args(run_dir, hot_interval=1.0, idle_interval=2.0)
        monkeypatch.setattr(
            sampler_lib, "Sampler",
            make_fake_sampler([("sample", 0, 0.0), ("sample", 1, 1.0), ("sample", 2, 2.0)]),
        )
        cg.cmd_collect(args)
        run = store_lib.RunDir(str(tmp_path), run_id="run-host-cadence", create=False)
        samples = list(run.read("samples"))
        assert "host" in samples[0]
        assert "host" not in samples[1]
        assert "host" in samples[2]

    def test_damon_requested_but_unavailable_notes_and_continues(self, collect_env, tmp_path: Path, monkeypatch, capsys):
        run_dir = tmp_path / "run-damon-unavail"
        monkeypatch.setattr(damon_lib, "available", lambda: False)
        args = make_collect_args(run_dir, damon=True)
        monkeypatch.setattr(sampler_lib, "Sampler", make_fake_sampler([("sample", 0, 0.0)]))
        assert cg.cmd_collect(args) == 0
        assert "unavailable" in capsys.readouterr().err

    @pytest.mark.parametrize("failure_stage", ["construct", "enter"])
    def test_damon_start_failure_still_signals_ready_and_collects(
        self, collect_env, tmp_path: Path, monkeypatch, capsys, failure_stage,
    ):
        run_dir = tmp_path / f"run-damon-start-failure-{failure_stage}"

        class FailingDamonSession:
            def __init__(self, targets):
                if failure_stage == "construct":
                    raise damon_lib.DamonSessionError("synthetic kernel EBUSY")

            def __enter__(self):
                raise damon_lib.DamonSessionError("synthetic kernel EBUSY")

            def __exit__(self, *_exc):
                return False

        monkeypatch.setattr(damon_lib, "available", lambda: True)
        monkeypatch.setattr(damon_lib, "DamonSession", FailingDamonSession)
        monkeypatch.setattr(
            sampler_lib, "Sampler", make_fake_sampler([("sample", 0, 0.0)]),
        )
        args = make_collect_args(run_dir, damon=True)

        assert cg.cmd_collect(args) == 0
        assert (run_dir / cg.READY_FILE).is_file()
        assert (run_dir / cg.DONE_FILE).is_file()
        run = store_lib.RunDir(str(tmp_path), run_id=run_dir.name, create=False)
        assert len(list(run.read("samples"))) == 1
        assert list(run.read("damon")) == []
        assert "could not start" in capsys.readouterr().err

    def test_damon_available_collects_on_every_twentieth_sample(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-damon-ok"

        class FakeDamonSession:
            instances: List["FakeDamonSession"] = []

            def __init__(self, targets, **kwargs):
                self.targets = targets
                self.collect_calls = 0
                FakeDamonSession.instances.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def collect(self):
                self.collect_calls += 1
                return [{"region": "hot"}]

        monkeypatch.setattr(damon_lib, "available", lambda: True)
        monkeypatch.setattr(damon_lib, "DamonSession", FakeDamonSession)
        args = make_collect_args(run_dir, damon=True)
        # seq=0 -> 0 % 20 == 0 (collect); seq=1 -> skipped.
        monkeypatch.setattr(
            sampler_lib, "Sampler",
            make_fake_sampler([("sample", 0, 0.0), ("sample", 1, 0.25)]),
        )
        cg.cmd_collect(args)
        assert FakeDamonSession.instances[0].collect_calls == 1
        # No pid targets resolved => the paddr fallback target is used.
        assert FakeDamonSession.instances[0].targets[0].kind == "paddr"
        run = store_lib.RunDir(str(tmp_path), run_id="run-damon-ok", create=False)
        damon_records = list(run.read("damon"))
        assert len(damon_records) == 1

    @pytest.mark.parametrize("cleanup_fails", [False, True])
    def test_damon_collection_failure_disables_only_damon_and_finishes_run(
        self, collect_env, tmp_path: Path, monkeypatch, capsys, cleanup_fails,
    ):
        run_dir = tmp_path / f"run-damon-collect-failure-{cleanup_fails}"

        class FailingDamonSession:
            instances: List["FailingDamonSession"] = []

            def __init__(self, targets):
                self.collect_calls = 0
                self.exit_calls = 0
                FailingDamonSession.instances.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                self.exit_calls += 1
                if cleanup_fails:
                    raise RuntimeError("synthetic DAMON cleanup failure")
                return False

            def collect(self):
                self.collect_calls += 1
                raise OSError("synthetic DAMON read failure")

        monkeypatch.setattr(damon_lib, "available", lambda: True)
        monkeypatch.setattr(damon_lib, "DamonSession", FailingDamonSession)
        monkeypatch.setattr(
            sampler_lib,
            "Sampler",
            make_fake_sampler([("sample", seq, float(seq)) for seq in range(22)]),
        )
        args = make_collect_args(run_dir, damon=True)

        assert cg.cmd_collect(args) == 0

        session = FailingDamonSession.instances[0]
        assert session.collect_calls == 1  # seq 20 is skipped after DAMON is disabled
        assert session.exit_calls == 1
        assert (run_dir / cg.READY_FILE).is_file()
        assert (run_dir / cg.DONE_FILE).is_file()
        run = store_lib.RunDir(str(tmp_path), run_id=run_dir.name, create=False)
        assert len(list(run.read("samples"))) == 22
        assert list(run.read("damon")) == []
        manifest = run.read_manifest()
        assert manifest["ended"] is not None
        assert manifest["duration"] >= 0
        stderr = capsys.readouterr().err
        assert "DAMON collection failed" in stderr
        if cleanup_fails:
            assert "DAMON cleanup after collection failure also failed" in stderr

    def test_damon_available_with_pid_targets_uses_vaddr_targets(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-damon-pid"

        class FakeDamonSession:
            instances: List["FakeDamonSession"] = []

            def __init__(self, targets, **kwargs):
                self.targets = targets
                FakeDamonSession.instances.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def collect(self):
                return []

        monkeypatch.setattr(damon_lib, "available", lambda: True)
        monkeypatch.setattr(damon_lib, "DamonSession", FakeDamonSession)
        monkeypatch.setattr(
            targets_mod, "cgroup_of_pid",
            lambda pid, root=access.CGROUP_ROOT, proc_root=access.PROC_ROOT: "/dev.slice/dev-background.slice",
        )
        monkeypatch.setattr(targets_mod.util, "read_text", lambda p: "fakeproc")
        monkeypatch.setattr(metrics_lib, "sample_proc", lambda pid, proc_root="/proc": {})
        args = make_collect_args(run_dir, target=["pid:4242"], damon=True)
        monkeypatch.setattr(sampler_lib, "Sampler", make_fake_sampler([("sample", 0, 0.0)]))
        cg.cmd_collect(args)

        # A resolved pid target must steer DamonSession toward per-process
        # (vaddr) monitoring instead of the whole-system (paddr) fallback.
        targets_used = FakeDamonSession.instances[0].targets
        assert len(targets_used) == 1
        assert targets_used[0].kind == "vaddr"
        assert targets_used[0].pid == 4242

    def test_sigterm_during_the_run_sets_stopping_and_is_observable(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-sigterm"
        registry = FakeSignalRegistry()
        monkeypatch.setattr(cg.signal, "signal", registry.set_signal)

        observed = {}

        class SignalProbingSampler(make_fake_sampler([("sample", 0, 0.0)])):
            def run(self, should_stop, on_sample, on_topology):
                observed["before"] = should_stop()
                handler = registry.handlers[signal.SIGTERM]
                handler(signal.SIGTERM, None)
                observed["after"] = should_stop()
                super().run(should_stop, on_sample, on_topology)

        args = make_collect_args(run_dir)
        monkeypatch.setattr(sampler_lib, "Sampler", SignalProbingSampler)
        cg.cmd_collect(args)
        assert observed["before"] is False
        assert observed["after"] is True

    def test_should_stop_also_sees_the_stop_sentinel_file(self, collect_env, tmp_path: Path, monkeypatch):
        run_dir = tmp_path / "run-stopfile"
        observed = {}

        class StopFileProbingSampler(make_fake_sampler([])):
            def run(self, should_stop, on_sample, on_topology):
                observed["before"] = should_stop()
                open(os.path.join(run_dir, cg.STOP_FILE), "w").close()
                observed["after"] = should_stop()

        args = make_collect_args(run_dir)
        monkeypatch.setattr(sampler_lib, "Sampler", StopFileProbingSampler)
        cg.cmd_collect(args)
        assert observed["before"] is False
        assert observed["after"] is True


# ── _launch_helper / _start_run ─────────────────────────────────────────

class TestLimitsSnapshot:
    """The manifest is the only channel between the collector and the
    analyser, so what it records about limits is a contract, not a detail.
    Writing only the prose form left every real run producing no proposals.
    """

    @staticmethod
    def _eff():
        from cgprofile import limits

        return limits.Effective(
            cgroup="/dev.slice/dev-background.slice",
            chain=[],
            memory_max=8 * 1024**3, memory_max_by="/dev.slice/dev-background.slice",
            memory_high=6 * 1024**3, memory_high_by="/dev.slice/dev-background.slice",
            memory_swap_max=48 * 1024**3, memory_swap_max_by="/dev.slice/dev-background.slice",
            strict_min=0, recursive_min=0, strict_low=0, recursive_low=0,
            protection_mode="strict",
            cpu_cores=None, cpu_cores_by=None,
            io_max={}, io_max_by={},
            pids_max=None,
            warnings=["something was discarded"],
        )

    def test_snapshot_has_all_three_forms(self):
        from cgprofile import limits
        from cgprofile.cli import legacy as cgprofile

        snap = cgprofile._limits_snapshot(limits, self._eff())
        assert set(snap) == {"resolved", "described", "fingerprint"}
        assert isinstance(snap["described"], list)
        assert isinstance(snap["fingerprint"], str)

    def test_resolved_carries_the_scalars_a_proposal_needs(self):
        from cgprofile import limits
        from cgprofile.cli import legacy as cgprofile

        resolved = cgprofile._limits_snapshot(limits, self._eff())["resolved"]
        assert resolved["memory_max"] == 8 * 1024**3
        assert resolved["memory_high_by"] == "/dev.slice/dev-background.slice"
        assert resolved["protection_mode"] == "strict"
        assert resolved["warnings"] == ["something was discarded"]

    def test_chain_is_excluded_because_it_dominates_the_manifest(self):
        from cgprofile import limits
        from cgprofile.cli import legacy as cgprofile

        assert "chain" not in cgprofile._limits_snapshot(limits, self._eff())["resolved"]

    def test_snapshot_is_json_serialisable(self):
        # It goes straight into manifest.json; a non-serialisable value here
        # loses the whole manifest, and with it the run.
        import json
        from cgprofile import limits
        from cgprofile.cli import legacy as cgprofile

        json.dumps(cgprofile._limits_snapshot(limits, self._eff()))

    def test_analyze_can_actually_read_what_we_write(self):
        """The end-to-end point: the shape the writer emits is the shape the
        proposal generator consumes. These two were mismatched, and nothing
        failed — proposals just silently never appeared."""
        from cgprofile import analyze, limits
        from cgprofile.model import Analysis
        from cgprofile.cli import legacy as cgprofile

        snap = cgprofile._limits_snapshot(limits, self._eff())
        analysis = Analysis(limits={"/dev.slice/dev-background.slice": snap})
        got = analyze._effective_limits(analysis)
        assert got, "analyze could not read the writer's own output"
        assert got["/dev.slice/dev-background.slice"]["memory_max"] == 8 * 1024**3




