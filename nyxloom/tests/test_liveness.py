"""CR-16 (RISK-007): liveness, channel health, and silent-failure detection.

Cross-cutting acceptance tests for the package's four acceptance bullets
(nyxloom-trove/reports/CORE-REDESIGN-IMPLEMENTATION-PLAN-2026-08-02-
AMENDMENT.md, "CR-16"). Unit coverage for the individual pieces lives
closer to the code they test: tests/test_watchdog.py (the tick-error-
streak PATTERN, pure), tests/test_notify.py (the transport PROBE),
tests/test_doctor.py (the liveness FINDINGS and the `--liveness` CLI
path). This file drives the REAL Daemon.run_pass, the REAL
doctor.liveness_findings, and the REAL cli.main together -- the same
"real driving, fault injection at a real boundary, assert on real
observable output" shape test_snapshot_faults.py uses (see its own module
docstring) -- to prove the four acceptance bullets end to end rather than
piecewise.

Evidence for the package (the whole reason it exists): nyxloomd sat
`Exited (143)` for ten days and the notification channel was crash-
looping, and NOTHING reported either.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from nyxloom import cli, daemon, reconcile, storage, storage_sqlite, wrapper
from nyxloom.config import NotifyConfig
from nyxloom.doctor import liveness_findings
from nyxloom.types import EventType, utc_now


@pytest.fixture()
def fault_mp():
    """A MonkeyPatch instance DEDICATED to fault injection, separate from
    the shared `monkeypatch` fixture -- same rule test_snapshot_faults.py's
    own `fault_mp` documents: a case that lifts its fault before reading
    the event log back must not also revert `tmp_state`'s NYXLOOM_STATE (set
    on the SHARED monkeypatch by the `tmp_state` fixture), which would
    silently point the read-back at a different state root entirely."""
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture()
def quiet_daemon(monkeypatch):
    """A Daemon whose every side-effecting port is stubbed -- no process
    launch, no push notification actually sent over the network, no
    dashboard render -- so a test can drive real `run_pass()` calls and
    look only at the event log they produce. Deliberately does NOT stub
    `notify.send`/`notify.probe_transport`: several tests below need those
    to run for real against a real (possibly broken) local socket."""
    monkeypatch.setattr(wrapper, "launch_detached", lambda spec: 4242)
    monkeypatch.setattr("nyxloom.adapters.probe", lambda route: (True, "ok"))
    monkeypatch.setattr("nyxloom.render.render_after_event", lambda *a, **k: None)
    monkeypatch.setattr("nyxloom.render.render_all", lambda *a, **k: None)


def _events(project: str = "demo") -> list:
    return list(storage.iter_events(project))


def _types(project: str = "demo") -> list:
    return [e.type for e in _events(project)]


# ---------------------------------------------------------------------------
# _record_heartbeat: wired into EVERY path through run_pass.


def test_run_pass_records_heartbeat_on_a_clean_idle_pass(
        tmp_state, sample_project, quiet_daemon):
    d = daemon.Daemon({"demo": sample_project.root})
    d.run_pass("demo")
    assert EventType.RECONCILE_HEARTBEAT in _types()


def test_run_pass_records_heartbeat_on_snapshot_unavailable(
        tmp_state, sample_project, quiet_daemon, fault_mp):
    def _raise(*a, **k):
        raise RuntimeError("boom")
    fault_mp.setattr(storage, "list_states", _raise)

    d = daemon.Daemon({"demo": sample_project.root})
    assert d.run_pass("demo") == 0

    fault_mp.undo()  # lift the fault before reading the log back
    types = _types()
    assert EventType.SNAPSHOT_UNAVAILABLE in types
    assert EventType.RECONCILE_HEARTBEAT in types, (
        "a fail-closed pass is STILL a completed pass for deadman purposes")


def test_run_pass_records_heartbeat_on_the_pass_level_exception_net(
        tmp_state, sample_project, quiet_daemon, monkeypatch):
    def _raise(inp):
        raise RuntimeError("boom")
    monkeypatch.setattr(reconcile, "plan_project", _raise)

    d = daemon.Daemon({"demo": sample_project.root})
    assert d.run_pass("demo") == 0

    types = _types()
    assert EventType.TICK_ERROR in types
    assert EventType.RECONCILE_HEARTBEAT in types


def test_heartbeat_write_failure_is_contained_and_does_not_mask_the_pass(
        tmp_state, sample_project, quiet_daemon, monkeypatch):
    """Fault-injection-matrix style (CR-02's shape): the store's
    append_and_apply raises SPECIFICALLY for the heartbeat's own write --
    every other call in the SAME pass goes through real -- so run_pass must
    still return its real result and its own events must still land. This
    is the daemon-side half of acceptance bullet 4 ("the alarm path is
    itself covered by the fault-injection matrix in CR-02")."""
    real = storage.append_and_apply

    def _flaky(project, states, **kwargs):
        if kwargs.get("type") is EventType.RECONCILE_HEARTBEAT:
            raise RuntimeError("store hiccup")
        return real(project, states, **kwargs)
    monkeypatch.setattr(storage, "append_and_apply", _flaky)

    d = daemon.Daemon({"demo": sample_project.root})
    result = d.run_pass("demo")  # must not raise

    assert isinstance(result, int)
    assert EventType.RECONCILE_HEARTBEAT not in _types(), "the flaky write never landed"


# ---------------------------------------------------------------------------
# Acceptance bullet 1: "Killing the daemon mid-pass produces an
# operator-visible alarm within a bounded interval, through a path that
# does not require the daemon to be alive."


def test_killing_the_daemon_mid_pass_is_detected_through_a_daemon_free_path(
        tmp_state, sample_project, quiet_daemon, fault_mp):
    """A real Daemon ticks twice (writing two real heartbeats -- the daemon
    behaving normally, a long while ago), then is simply never called
    again ("killed"): no signal, no special teardown, nothing -- exactly
    what a SIGKILL or a fully-exited container looks like from the store's
    point of view. The check that detects this is `doctor.liveness_
    findings`, called with NO Daemon object in scope at all in this half
    of the test -- the structural proof that the alarm path does not
    require the daemon to be alive.

    "A long while ago" is simulated by making the two real ticks' own
    RECONCILE_HEARTBEAT writes carry an old timestamp (storage_sqlite's
    own clock, monkeypatched only for the duration of the two run_pass
    calls) rather than a wall-clock sleep (AUTHORING.md 3b: no sleep
    decides a verdict) -- the liveness check afterwards reads the REAL
    current time, so the staleness it reports is genuine, not asserted by
    fiat."""
    old_now = utc_now() - timedelta(seconds=999)
    fault_mp.setattr(storage_sqlite, "utc_now", lambda: old_now)
    d = daemon.Daemon({"demo": sample_project.root})
    d.run_pass("demo")
    d.run_pass("demo")
    fault_mp.undo()
    assert EventType.RECONCILE_HEARTBEAT in _types()
    # The daemon is "killed" here: simply never called again. Nothing below
    # constructs a Daemon, calls run_pass, or otherwise touches `d`.

    findings = liveness_findings(sample_project)
    assert any(f.kind == "reconcile-deadman" and f.severity == "critical"
               for f in findings)

    # The same alarm is reachable through a PLAIN CLI invocation -- the
    # actual escape path an operator or a healthcheck uses. Still no
    # Daemon anywhere in this call.
    exit_code = cli.main(["doctor", "--liveness"])
    assert exit_code == 1


# ---------------------------------------------------------------------------
# Acceptance bullet 2: "Breaking the notification transport produces an
# operator-visible alarm through a different path."


def test_breaking_the_notification_transport_is_caught_by_a_second_path(
        tmp_state, sample_project, quiet_daemon):
    """The normal path -- notify_event() over a broken ntfy -- fails
    SILENTLY into the durable log (NOTIFICATION_FAILED; no push reaches
    anyone, because the channel that would carry the alarm is the one that
    is down). The SECOND path -- doctor.liveness_findings' active probe,
    reported via DoctorFinding + `nyxloom doctor`'s own exit code -- is
    what actually surfaces it."""
    sample_project.notify = NotifyConfig(
        ntfy_url="http://127.0.0.1:1", ntfy_topic="alerts")
    # cli.main reloads ProjectConfig from disk, so the in-memory mutation
    # above (used for the direct liveness_findings() call below) is not
    # enough on its own -- the on-disk config needs the same channel.
    toml_path = sample_project.root / ".nyxloom" / "project.toml"
    toml_path.write_text(
        toml_path.read_text(encoding="utf-8").replace(
            "[notify]\n", '[notify]\nntfy_url = "http://127.0.0.1:1"\nntfy_topic = "alerts"\n'),
        encoding="utf-8")

    d = daemon.Daemon({"demo": sample_project.root})
    # A real NEEDS_OPERATOR escalation over the broken channel: this is the
    # NORMAL path, and it degrades into the durable log silently -- a
    # NOTIFICATION_FAILED record, never a push, because the channel that
    # would carry the alarm is the one that is down.
    d._append_ev("demo", sample_project, {}, EventType.NEEDS_OPERATOR,
                 {"reason": "test"}, task_id=None)
    assert EventType.NOTIFICATION_FAILED in _types()
    assert EventType.NOTIFICATION_DELIVERED not in _types()

    # The SECOND, independent path: it does not go through notify_event at
    # all, and it does not require the Daemon `d` above (constructed only
    # to prove the normal path degrades silently first).
    findings = liveness_findings(sample_project)
    unreachable = [f for f in findings if f.kind == "notify-transport-unreachable"]
    assert len(unreachable) == 1
    assert unreachable[0].severity == "critical"

    exit_code = cli.main(["doctor", "--liveness"])
    assert exit_code == 1


# ---------------------------------------------------------------------------
# Acceptance bullet 3: "A daemon whose every pass raises is reported as
# failing, not as healthy."


def test_a_daemon_whose_every_pass_raises_is_reported_failing_not_healthy(
        tmp_state, sample_project, quiet_daemon, monkeypatch):
    """`reconcile.plan_project` raises on EVERY pass -- "up, dispatching
    its HTTP healthcheck fine" is simulated by the fact that run_pass keeps
    completing and keeps writing heartbeats (the deadman check stays
    silent, on purpose: this is the gap ONLY the tick-error-streak pattern
    closes)."""
    def _raise(inp):
        raise RuntimeError("boom")
    monkeypatch.setattr(reconcile, "plan_project", _raise)

    d = daemon.Daemon({"demo": sample_project.root})
    for _ in range(5):
        assert d.run_pass("demo") == 0

    errors = [e for e in _events() if e.type is EventType.TICK_ERROR]
    assert len(errors) == 5, "every pass really did raise"

    findings = liveness_findings(sample_project)
    kinds = {f.kind for f in findings}
    assert "tick-error-streak" in kinds
    streak = next(f for f in findings if f.kind == "tick-error-streak")
    assert streak.severity == "critical"
    # The deadman stays silent: the daemon IS looping (heartbeats keep
    # landing), which is exactly why a SEPARATE detector is required.
    assert "reconcile-deadman" not in kinds

    exit_code = cli.main(["doctor", "--liveness"])
    assert exit_code == 1


# ---------------------------------------------------------------------------
# Acceptance bullet 4: "The alarm path is itself covered by the
# fault-injection matrix in CR-02" -- real driving, fault injection at a
# real boundary, no mock of the code under test. See
# test_heartbeat_write_failure_is_contained_and_does_not_mask_the_pass
# above and test_doctor.py's test_liveness_event_log_unreadable_is_a_
# critical_finding_not_a_crash / test_liveness_one_check_failing_does_
# not_silence_the_others for the rest of this matrix.


def test_liveness_findings_never_raises_even_with_a_completely_unconfigured_project(
        tmp_state, sample_project):
    """The zero-fault baseline every fault case above is measured against:
    a project with no events, no notify config, default policy -- must
    read as healthy, not merely as non-crashing."""
    assert liveness_findings(sample_project) == []
