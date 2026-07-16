"""CLI tests (P10). Each oracle is a test case."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from nyxloom import cli
from nyxloom.config import ProjectConfig
from nyxloom.types import (
    DoctorFinding, LintFinding, EventType, Actor, ActorKind,
    TaskStateFile, TaskState, Attempt, AttemptState, Route, Usage, Basis,
)


@pytest.fixture()
def make_statefile():
    """Factory for TaskStateFile objects."""
    def _make(**kwargs):
        defaults = {
            "schema_version": 1,
            "task_id": "demo-P01-test",
            "project": "demo",
            "state": TaskState.ACTIVE,
            "since": __import__("nyxloom.types", fromlist=["utc_now"]).utc_now(),
            "paused": False,
        }
        defaults.update(kwargs)
        return TaskStateFile(**defaults)
    return _make


def test_project_add(sample_project, tmp_state, capsys):
    """Oracle 1: project add demo <root> registers project + event."""
    root = sample_project.root

    # Remove registry to test add
    from nyxloom import config, paths
    paths.registry_path().unlink()

    exit_code = cli.main(["project", "add", "demo", str(root)])

    assert exit_code == 0

    # Check registry
    registry = config.load_registry()
    assert "demo" in registry
    assert registry["demo"] == root

    # Check event was appended
    from nyxloom import storage
    events = list(storage.iter_events("demo"))
    assert len(events) == 1
    assert events[0].type == EventType.PROJECT_REGISTERED
    assert events[0].actor.kind == ActorKind.OPERATOR


def test_project_list(sample_project, tmp_state, capsys):
    """project list shows registry."""
    exit_code = cli.main(["project", "list"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "demo" in out
    assert str(sample_project.root) in out


def test_lint_all_clean(sample_project, tmp_state, capsys, monkeypatch):
    """Oracle 2: lint with no errors prints 'clean'."""
    def mock_lint_project(cfg):
        return {}

    monkeypatch.setattr("nyxloom.lint.lint_project", mock_lint_project)

    exit_code = cli.main(["lint"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "clean" in out


def test_lint_error(sample_project, tmp_state, capsys, monkeypatch):
    """Oracle 2: lint with error finding exits 1 and prints finding."""
    finding = LintFinding(
        rule="L2",
        severity="error",
        message="gate not found",
        path="handoff/demo-P01-sample.md",
        line=10,
    )

    def mock_lint_project(cfg):
        return {"handoff/demo-P01-sample.md": [finding]}

    monkeypatch.setattr("nyxloom.lint.lint_project", mock_lint_project)

    exit_code = cli.main(["lint"])
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "handoff/demo-P01-sample.md:10 L2 error gate not found" in out


def test_lint_specific_paths(sample_project, tmp_state, capsys, monkeypatch):
    """Oracle 2: lint with path args calls lint_file."""
    finding = LintFinding(
        rule="L2",
        severity="error",
        message="test error",
        path="test.md",
        line=5,
    )

    def mock_lint_file(path, cfg):
        return [finding]

    monkeypatch.setattr("nyxloom.lint.lint_file", mock_lint_file)

    exit_code = cli.main(["lint", "test.md"])
    out = capsys.readouterr().out
    # Should see the finding printed
    assert "L2" in out


def test_doctor_clean(sample_project, tmp_state, capsys, monkeypatch):
    """Oracle 3: doctor with no critical/error findings exits 0."""
    def mock_doctor_project(cfg):
        return []

    monkeypatch.setattr("nyxloom.doctor.doctor_project", mock_doctor_project)

    exit_code = cli.main(["doctor"])
    assert exit_code == 0


def test_doctor_error(sample_project, tmp_state, capsys, monkeypatch):
    """Oracle 3: doctor with critical finding exits 1 and shows table."""
    finding = DoctorFinding(
        kind="replay-divergence",
        severity="critical",
        message="task state diverged",
        project="demo",
    )

    def mock_doctor_project(cfg):
        return [finding]

    monkeypatch.setattr("nyxloom.doctor.doctor_project", mock_doctor_project)

    exit_code = cli.main(["doctor"])
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "replay-divergence" in out
    assert "critical" in out


def test_doctor_rebuild(sample_project, tmp_state, capsys, monkeypatch):
    """Oracle 3: doctor --rebuild prints diffs."""
    def mock_doctor_project(cfg):
        return []

    def mock_rebuild(project, write=False):
        return {}, ["task_id: state"]

    monkeypatch.setattr("nyxloom.doctor.doctor_project", mock_doctor_project)
    monkeypatch.setattr("nyxloom.doctor.rebuild", mock_rebuild)

    exit_code = cli.main(["doctor", "--rebuild"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "diffs" in out or "task_id: state" in out


def test_doctor_rebuild_write(sample_project, tmp_state, capsys, monkeypatch):
    """Oracle 3: doctor --rebuild --write calls rebuild with write=True."""
    call_log = []

    def mock_doctor_project(cfg):
        return []

    def mock_rebuild(project, write=False):
        call_log.append(("rebuild", project, write))
        return {}, []

    monkeypatch.setattr("nyxloom.doctor.doctor_project", mock_doctor_project)
    monkeypatch.setattr("nyxloom.doctor.rebuild", mock_rebuild)

    exit_code = cli.main(["doctor", "--rebuild", "--write"])
    assert exit_code == 0
    assert any(call[2] for call in call_log)  # write=True was passed


def test_status_empty(sample_project, tmp_state, capsys):
    """status with no tasks outputs nothing."""
    exit_code = cli.main(["status"])
    assert exit_code == 0
    out = capsys.readouterr().out
    # Empty is okay


def test_status_one_task(sample_project, tmp_state, capsys, make_statefile, monkeypatch):
    """Oracle 4: status shows task row with id, state, route, cost."""
    from nyxloom import storage

    # Create a statefile
    route = Route(route_id="fake-route", cli="fake", model="fake-model")
    attempt = Attempt(
        attempt_id="att-001",
        role=__import__("nyxloom.types", fromlist=["Role"]).Role.IMPLEMENTER,
        state=AttemptState.RUNNING,
        route=route,
        started=__import__("nyxloom.types", fromlist=["utc_now"]).utc_now(),
        usage=Usage(basis=Basis.ACTUAL, cost=1.5),
    )
    tsf = make_statefile(attempts=[attempt])
    storage.save_state(tsf)

    exit_code = cli.main(["status"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "demo-P01-test" in out
    assert "ACTIVE" in out
    assert "fake-route" in out


def test_status_project_filter(sample_project, tmp_state, capsys, make_statefile):
    """status --project filters by project."""
    from nyxloom import storage

    route = Route(route_id="fake-route", cli="fake", model="fake-model")
    attempt = Attempt(
        attempt_id="att-001",
        role=__import__("nyxloom.types", fromlist=["Role"]).Role.IMPLEMENTER,
        state=AttemptState.RUNNING,
        route=route,
        started=__import__("nyxloom.types", fromlist=["utc_now"]).utc_now(),
    )
    tsf = make_statefile(attempts=[attempt])
    storage.save_state(tsf)

    exit_code = cli.main(["status", "--project", "demo"])
    assert exit_code == 0


def test_render(sample_project, tmp_state, capsys, monkeypatch):
    """Oracle 5: render calls render_all and prints www path."""
    from pathlib import Path as PathlibPath

    sentinel_path = PathlibPath("/www/sentinel")

    def mock_render_all(registry):
        return sentinel_path

    monkeypatch.setattr("nyxloom.render.render_all", mock_render_all)

    exit_code = cli.main(["render"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert str(sentinel_path) in out


def test_tick(sample_project, tmp_state, capsys, monkeypatch):
    """Oracle 6: tick calls daemon.run_once and prints result."""
    def mock_run_once(project):
        return 7

    monkeypatch.setattr("nyxloom.daemon.run_once", mock_run_once)

    exit_code = cli.main(["tick"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "7" in out


def test_decide_success(sample_project, tmp_state, capsys, monkeypatch):
    """Oracle 7: decide calls decisions.decide and appends DECISION_RESOLVED event."""
    from nyxloom import storage

    call_log = []

    def mock_decide(cfg, decision_id, choice, note, authority):
        call_log.append((decision_id, choice, note, authority))

    monkeypatch.setattr("nyxloom.decisions.decide", mock_decide)

    exit_code = cli.main(["decide", "demo", "D-002", "--choose", "b", "--note", "why"])
    assert exit_code == 0

    # Check that decide was called
    assert len(call_log) == 1
    assert call_log[0][0] == "D-002"
    assert call_log[0][1] == "b"
    assert call_log[0][2] == "why"

    # Check event was appended
    events = list(storage.iter_events("demo"))
    decision_events = [e for e in events if e.type == EventType.DECISION_RESOLVED]
    assert len(decision_events) == 1
    assert decision_events[0].decision_id == "D-002"


def test_decide_error(sample_project, tmp_state, capsys, monkeypatch):
    """Oracle 7: decide with DecisionError exits 1, prints error, no event."""
    from nyxloom import storage, decisions

    def mock_decide(cfg, decision_id, choice, note, authority):
        raise decisions.DecisionError("not found")

    monkeypatch.setattr("nyxloom.decisions.decide", mock_decide)

    exit_code = cli.main(["decide", "demo", "D-002", "--choose", "b"])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "not found" in err

    # No DECISION_RESOLVED event should exist
    events = list(storage.iter_events("demo"))
    decision_events = [e for e in events if e.type == EventType.DECISION_RESOLVED]
    assert len(decision_events) == 0


def test_discuss(sample_project, tmp_state, capsys, monkeypatch):
    """Oracle 8: discuss prints command string."""
    cmd_str = "claude --append-system-prompt 'test'"

    def mock_discuss(cfg, decision_id):
        return cmd_str

    monkeypatch.setattr("nyxloom.decisions.discuss", mock_discuss)

    exit_code = cli.main(["discuss", "demo", "D-002"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert cmd_str in out


def test_reject_success(sample_project, tmp_state, capsys, make_statefile):
    """P17 Gap 2: reject transitions MERGE_READY -> REVIEW_REJECTED via a
    TASK_TRANSITIONED event; the task's statefile reflects the new state."""
    from nyxloom import storage

    tsf = make_statefile(state=TaskState.MERGE_READY)
    storage.save_state(tsf)

    exit_code = cli.main(["reject", "demo", "demo-P01-test", "--note", "gate re-run failed"])
    assert exit_code == 0

    events = list(storage.iter_events("demo"))
    trans = [e for e in events if e.type == EventType.TASK_TRANSITIONED]
    assert len(trans) == 1
    assert trans[0].payload["from"] == "MERGE_READY"
    assert trans[0].payload["to"] == "REVIEW_REJECTED"
    assert trans[0].payload["notes"] == "gate re-run failed"
    assert trans[0].actor.kind == ActorKind.OPERATOR

    states = storage.list_states("demo")
    assert states["demo-P01-test"].state == TaskState.REVIEW_REJECTED


def test_reject_then_requeue(sample_project, tmp_state, make_statefile):
    """Regression (Gap 2): a rejected MERGE_READY task can re-enter QUEUED
    -- the REVIEW_REJECTED -> QUEUED edge already existed; this proves the
    full round trip works once the new MERGE_READY -> REVIEW_REJECTED edge
    is in place."""
    from nyxloom import storage
    from nyxloom.types import Actor, ActorKind

    tsf = make_statefile(state=TaskState.MERGE_READY)
    storage.save_state(tsf)

    assert cli.main(["reject", "demo", "demo-P01-test"]) == 0

    states = storage.list_states("demo")
    assert states["demo-P01-test"].state == TaskState.REVIEW_REJECTED

    storage.append_and_apply(
        "demo", states, actor=Actor(ActorKind.OPERATOR, "op"),
        type=EventType.TASK_TRANSITIONED,
        payload={"from": "REVIEW_REJECTED", "to": "QUEUED", "notes": "requeued for rework"},
        task_id="demo-P01-test",
    )
    assert states["demo-P01-test"].state == TaskState.QUEUED


def test_reject_wrong_state_rejected(sample_project, tmp_state, capsys, make_statefile):
    """A task not in MERGE_READY -> reject exits 1, no event written."""
    from nyxloom import storage

    tsf = make_statefile(state=TaskState.QUEUED)
    storage.save_state(tsf)

    exit_code = cli.main(["reject", "demo", "demo-P01-test"])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "error:" in err

    events = list(storage.iter_events("demo"))
    assert not [e for e in events if e.type == EventType.TASK_TRANSITIONED]
    states = storage.list_states("demo")
    assert states["demo-P01-test"].state == TaskState.QUEUED


def test_reject_unknown_task(sample_project, tmp_state, capsys):
    """Unknown task -> reject exits 1 with a clear error, no event."""
    from nyxloom import storage

    exit_code = cli.main(["reject", "demo", "nonexistent-task"])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "nonexistent-task" in err

    events = list(storage.iter_events("demo"))
    assert not [e for e in events if e.type == EventType.TASK_TRANSITIONED]


def test_merge_success_records_real_commit(sample_project, tmp_state, capsys, make_statefile):
    """P17 fold-in: merge records the REAL `git rev-parse HEAD` of the
    project root, not a hand-padded placeholder."""
    import subprocess

    from nyxloom import storage

    tsf = make_statefile(state=TaskState.MERGE_READY)
    storage.save_state(tsf)

    real_head = subprocess.run(
        ["git", "-C", str(sample_project.root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    exit_code = cli.main(["merge", "demo", "demo-P01-test"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert real_head in out

    events = list(storage.iter_events("demo"))
    trans = [e for e in events if e.type == EventType.TASK_TRANSITIONED]
    merged = [e for e in events if e.type == EventType.MERGE_RECORDED]
    assert len(trans) == 1
    assert trans[0].payload["to"] == "MERGED"
    assert len(merged) == 1
    assert merged[0].payload["merge_commit"] == real_head
    assert merged[0].payload["merge_commit"] != "0" * 40

    states = storage.list_states("demo")
    assert states["demo-P01-test"].state == TaskState.MERGED
    assert states["demo-P01-test"].merge_commit == real_head


def test_merge_explicit_commit_override(sample_project, tmp_state, capsys, make_statefile):
    """--commit overrides the git rev-parse HEAD default."""
    from nyxloom import storage

    tsf = make_statefile(state=TaskState.MERGE_READY)
    storage.save_state(tsf)

    explicit = "a" * 40
    exit_code = cli.main(["merge", "demo", "demo-P01-test", "--commit", explicit])
    assert exit_code == 0

    states = storage.list_states("demo")
    assert states["demo-P01-test"].merge_commit == explicit


def test_merge_wrong_state_rejected(sample_project, tmp_state, capsys, make_statefile):
    """A task not in MERGE_READY -> merge exits 1, no event written."""
    from nyxloom import storage

    tsf = make_statefile(state=TaskState.QUEUED)
    storage.save_state(tsf)

    exit_code = cli.main(["merge", "demo", "demo-P01-test"])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "error:" in err

    events = list(storage.iter_events("demo"))
    assert not [e for e in events if e.type in (EventType.TASK_TRANSITIONED, EventType.MERGE_RECORDED)]
    states = storage.list_states("demo")
    assert states["demo-P01-test"].state == TaskState.QUEUED
    assert states["demo-P01-test"].merge_commit is None


def test_pause_project(sample_project, tmp_state, capsys, monkeypatch):
    """Oracle 9: pause <project> creates flag + PAUSE_SET event."""
    from nyxloom import paths, storage

    exit_code = cli.main(["pause", "demo"])
    assert exit_code == 0

    # Check flag exists
    flag_path = paths.pause_flag("demo")
    assert flag_path.exists()

    # Check event
    events = list(storage.iter_events("demo"))
    pause_events = [e for e in events if e.type == EventType.PAUSE_SET]
    assert len(pause_events) == 1


def test_pause_task(sample_project, tmp_state, capsys, make_statefile, monkeypatch):
    """Oracle 9: pause <project> <task> creates task flag + PAUSE_SET event with task_id."""
    from nyxloom import paths, storage

    # Create a statefile first
    tsf = make_statefile()
    storage.save_state(tsf)

    exit_code = cli.main(["pause", "demo", "demo-P01-test"])
    assert exit_code == 0

    # Check flag exists
    flag_path = paths.pause_flag("demo", "demo-P01-test")
    assert flag_path.exists()

    # Check event and statefile update
    events = list(storage.iter_events("demo"))
    pause_events = [e for e in events if e.type == EventType.PAUSE_SET]
    assert len(pause_events) == 1
    assert pause_events[0].task_id == "demo-P01-test"

    # Check statefile.paused is True
    states = storage.list_states("demo")
    assert states["demo-P01-test"].paused is True


def test_unpause_project(sample_project, tmp_state, capsys):
    """Oracle 9: unpause <project> removes flag + PAUSE_CLEARED event."""
    from nyxloom import paths, storage

    # Create pause first
    flag_path = paths.pause_flag("demo")
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.touch()

    exit_code = cli.main(["unpause", "demo"])
    assert exit_code == 0

    # Check flag gone
    assert not flag_path.exists()

    # Check event
    events = list(storage.iter_events("demo"))
    clear_events = [e for e in events if e.type == EventType.PAUSE_CLEARED]
    assert len(clear_events) == 1


def test_unpause_task(sample_project, tmp_state, capsys, make_statefile):
    """unpause <project> <task> removes task flag + event."""
    from nyxloom import paths, storage

    # Create statefile and flag
    tsf = make_statefile(paused=True)
    storage.save_state(tsf)

    flag_path = paths.pause_flag("demo", "demo-P01-test")
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.touch()

    exit_code = cli.main(["unpause", "demo", "demo-P01-test"])
    assert exit_code == 0

    # Check flag gone
    assert not flag_path.exists()

    # Check event and statefile update
    events = list(storage.iter_events("demo"))
    clear_events = [e for e in events if e.type == EventType.PAUSE_CLEARED]
    assert len(clear_events) == 1

    # Check statefile.paused is False
    states = storage.list_states("demo")
    assert states["demo-P01-test"].paused is False


def test_leases_empty(sample_project, tmp_state, capsys, monkeypatch):
    """Oracle 10: leases with no held leases."""
    def mock_holder_info(name, capacity=1):
        return [{"slot": 0, "held": False}]

    monkeypatch.setattr("nyxloom.leases.holder_info", mock_holder_info)

    exit_code = cli.main(["leases"])
    assert exit_code == 0


def test_leases_held(sample_project, tmp_state, capsys, monkeypatch):
    """Oracle 10: leases shows held lease with owner."""
    def mock_holder_info(name, capacity=1):
        return [{
            "slot": 0,
            "held": True,
            "owner": "test-owner",
            "since": "2026-07-15T00:00:00+00:00",
        }]

    monkeypatch.setattr("nyxloom.leases.holder_info", mock_holder_info)

    exit_code = cli.main(["leases"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "True" in out
    assert "test-owner" in out


def test_digest(sample_project, tmp_state, capsys, monkeypatch):
    """Oracle 11: digest prints notify.digest string."""
    digest_text = "Summary of events"

    def mock_digest(cfg, project, since_seq):
        return digest_text

    monkeypatch.setattr("nyxloom.notify.digest", mock_digest)

    exit_code = cli.main(["digest", "demo"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert digest_text in out


def test_events_all(sample_project, tmp_state, capsys):
    """Oracle 11: events prints all event lines as JSON."""
    from nyxloom import storage

    # Append a test event
    actor = Actor(kind=ActorKind.OPERATOR, id="test")
    storage.append_event("demo", actor=actor, type=EventType.PAUSE_SET, payload={})

    exit_code = cli.main(["events", "demo"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "PAUSE_SET" in out


def test_events_filtered_by_type(sample_project, tmp_state, capsys):
    """Oracle 11: events --type filters by type."""
    from nyxloom import storage

    actor = Actor(kind=ActorKind.OPERATOR, id="test")
    storage.append_event("demo", actor=actor, type=EventType.PAUSE_SET, payload={})
    storage.append_event("demo", actor=actor, type=EventType.PAUSE_CLEARED, payload={})

    exit_code = cli.main(["events", "demo", "--type", "PAUSE_SET"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "PAUSE_SET" in out
    assert "PAUSE_CLEARED" not in out


def test_version(capsys, monkeypatch):
    """Oracle 12: version prints __version__ even with broken modules."""
    import sys

    # Monkeypatch a module to be broken
    monkeypatch.setitem(sys.modules, "nyxloom.daemon", None)

    exit_code = cli.main(["version"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "0.1.0a0" in out


def test_unknown_subcommand(capsys):
    """Oracle 13: unknown subcommand exits 2 with usage."""
    exit_code = cli.main(["unknown"])
    assert exit_code == 2


def test_init_scaffolds_trove(tmp_path, capsys):
    """Oracle O1: `init <dir>` creates <dir>/nyxloom-trove/ with the full
    STANDARD.md tree, scaffolded from the bundled templates; nyxloom.toml
    is valid TOML with a [project] id."""
    import tomllib

    project_folder = tmp_path / "myproj"

    exit_code = cli.main(["init", str(project_folder)])
    assert exit_code == 0

    trove = project_folder / "nyxloom-trove"
    assert trove.is_dir()

    for name in ("nyxloom.toml", "STANDARD.md", "AUTHORING.md", "decisions.md",
                 "roadmap.md", "backlog.md", ".gitignore"):
        assert (trove / name).is_file(), name

    for name in ("handoffs", "reports", "archive", "agent-logs"):
        assert (trove / name).is_dir(), name

    assert (trove / "archive" / ".gitkeep").is_file()
    assert (trove / "agent-logs" / ".gitkeep").is_file()
    assert "agent-logs/" in (trove / ".gitignore").read_text()

    data = tomllib.loads((trove / "nyxloom.toml").read_text())
    assert data["project"]["id"] == "myproj"

    # STANDARD.md/AUTHORING.md copied verbatim from the repo's canonical trove
    canonical = Path(__file__).resolve().parent.parent / "nyxloom-trove"
    assert (trove / "STANDARD.md").read_text() == (canonical / "STANDARD.md").read_text()
    assert (trove / "AUTHORING.md").read_text() == (canonical / "AUTHORING.md").read_text()

    out = capsys.readouterr().out
    assert str(trove) in out


def test_init_refuses_existing_trove(tmp_path, capsys):
    """Oracle O1-negative: init into a dir that already has a nyxloom-trove/
    exits non-zero WITHOUT overwriting existing files (idempotent-safe)."""
    project_folder = tmp_path / "myproj"
    trove = project_folder / "nyxloom-trove"
    trove.mkdir(parents=True)
    marker = trove / "marker.txt"
    marker.write_text("do not touch")

    exit_code = cli.main(["init", str(project_folder)])
    assert exit_code != 0

    err = capsys.readouterr().err
    assert "error:" in err

    # untouched: no scaffolded files, marker survives
    assert marker.read_text() == "do not touch"
    assert not (trove / "nyxloom.toml").exists()
    assert not (trove / "STANDARD.md").exists()


def test_init_missing_project_folder_exits_2(capsys):
    """Oracle O2-negative: init with no <project_folder> arg exits 2 with a
    usage message."""
    exit_code = cli.main(["init"])
    assert exit_code == 2


def test_decide_debug_reraises(sample_project, tmp_state, monkeypatch):
    """Guidance: --debug flag re-raises exceptions."""
    from nyxloom import decisions

    def mock_decide(cfg, decision_id, choice, note, authority):
        raise decisions.DecisionError("test error")

    monkeypatch.setattr("nyxloom.decisions.decide", mock_decide)

    with pytest.raises(decisions.DecisionError):
        cli.main(["--debug", "decide", "demo", "D-002", "--choose", "b"])
