"""CLI tests (P10). Each oracle is a test case."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest import mock

import pytest
import structlog.contextvars

from nyxloom import cli, log
from nyxloom.config import ProjectConfig
from nyxloom.types import (
    DoctorFinding, LintFinding, EventType, Actor, ActorKind,
    TaskStateFile, TaskState, Attempt, AttemptState, Route, Usage, Basis,
)


@pytest.fixture(autouse=True)
def _silence_nyxloom_logging():
    """PACKAGE P05c safety net -- see test_backlog_items.py's copy of this
    fixture for the full rationale (byte-unchanged CLI stdout/stderr oracle,
    docs/plan-logging.md P05c). cli.main() now bootstraps `log.configure()`
    itself (see cli.py's `_bootstrap_logging`), which already routes every
    level to a JSONL file rather than stdout/stderr -- this fixture is
    belt-and-braces on top of that, and load-bearing for any test here that
    calls a library function directly rather than through `cli.main`."""
    log.configure(level=log.CRITICAL, console=False)
    yield
    structlog.contextvars.clear_contextvars()
    nyxloom_logger = logging.getLogger("nyxloom")
    for handler in list(nyxloom_logger.handlers):
        nyxloom_logger.removeHandler(handler)
        handler.close()


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
    """status with no tasks outputs nothing.

    PACKAGE P05c (docs/plan-logging.md): tightened to an EXACT byte-for-byte
    empty-string assertion (was a vacuous "# Empty is okay" comment with no
    real check) -- this is the simplest possible instance of oracle 1's
    byte-unchanged CLI stdout contract: `status`'s new-instrumented
    dependencies (config.py et al, now logging) must add NOTHING to stdout,
    not even whitespace."""
    exit_code = cli.main(["status"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert out == ""


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


def test_init_scaffolds_a_loadable_trove(tmp_path):
    """Review (P23): the scaffolded nyxloom.toml is not merely valid TOML --
    nyxloom itself must be able to load it, or `init` hands the operator a
    trove the tool cannot read. The REPORT claimed this was verified by hand;
    this pins it. Gates/[refs] are deliberately left empty for the operator,
    so an empty gate map is the expected outcome, not a failure."""
    project_folder = tmp_path / "loadable"
    assert cli.main(["init", str(project_folder)]) == 0

    cfg = ProjectConfig.load(project_folder)

    assert cfg.project_id == "loadable"
    assert cfg.default_branch == "main"
    assert cfg.handoff_globs == ["nyxloom-trove/handoffs/*.md"]
    assert cfg.reports_dir == "nyxloom-trove/reports"
    assert cfg.decisions_inbox == "nyxloom-trove/decisions.md"
    assert cfg.gates == {}


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

    err = capsys.readouterr().err
    assert "usage:" in err
    assert "project_folder" in err


# ---------------------------------------------------------------------------
# onboard (PACKAGE F2 2026-07-17) -- the non-AI wizard + spine instantiation
# CLI verb. Engine-level coverage lives in tests/test_onboarding.py; these
# exercise argparse wiring + the init-reuse path.

def test_onboard_greenfield_scaffolds_and_instantiates(tmp_path, capsys):
    """Oracle: `onboard <folder>` with no existing trove scaffolds one (via
    the same path `init` uses) AND instantiates a lint-clean spine, in one
    deterministic, non-interactive call -- no AI/LLM invoked."""
    from nyxloom import lint

    project_folder = tmp_path / "greenfield"

    exit_code = cli.main(["onboard", str(project_folder)])
    assert exit_code == 0

    trove = project_folder / "nyxloom-trove"
    for name in ("1-north-star.md", "2-product-definition.md",
                 "3-roadmap.md", "4-backlog.md", "onboarding-answers.json",
                 "STANDARD.md", "nyxloom.toml"):
        assert (trove / name).is_file(), name

    out = capsys.readouterr().out
    assert "1-north-star.md" in out
    assert "answers recorded:" in out

    cfg = ProjectConfig.load(project_folder)
    findings = lint.lint_project(cfg)
    all_findings = [f for v in findings.values() for f in v]
    assert not lint.has_blocking(all_findings), all_findings


def test_onboard_is_idempotent_and_answers_reflect_latest_call(tmp_path):
    from nyxloom import onboarding

    project_folder = tmp_path / "proj"
    assert cli.main(["onboard", str(project_folder)]) == 0
    assert cli.main([
        "onboard", str(project_folder),
        "--maturity", "mature", "--mode", "derive-from-code", "--docs", "present",
    ]) == 0

    answers = onboarding.load_answers(project_folder / "nyxloom-trove")
    assert answers.maturity == "mature"
    assert answers.mode == "derive-from-code"
    assert answers.docs_present is True


def test_onboard_reuses_existing_trove_from_init(tmp_path):
    """A project already scaffolded via `init` gets ONLY the spine added --
    `onboard` must not refuse or duplicate the trove scaffold."""
    project_folder = tmp_path / "proj"
    assert cli.main(["init", str(project_folder)]) == 0

    exit_code = cli.main(["onboard", str(project_folder)])
    assert exit_code == 0

    trove = project_folder / "nyxloom-trove"
    assert (trove / "1-north-star.md").is_file()
    assert (trove / "4-backlog.md").is_file()


def test_onboard_scan_path_repeatable(tmp_path):
    from nyxloom import onboarding

    project_folder = tmp_path / "proj"
    exit_code = cli.main([
        "onboard", str(project_folder),
        "--scan-path", "src", "--scan-path", "docs",
    ])
    assert exit_code == 0

    answers = onboarding.load_answers(project_folder / "nyxloom-trove")
    assert answers.scan_paths == ["src", "docs"]


def test_onboard_rejects_invalid_mode_exits_2(tmp_path, capsys):
    project_folder = tmp_path / "proj"
    exit_code = cli.main(["onboard", str(project_folder), "--mode", "bogus"])
    assert exit_code == 2

    err = capsys.readouterr().err
    assert "invalid choice" in err


def test_decide_debug_reraises(sample_project, tmp_state, monkeypatch):
    """Guidance: --debug flag re-raises exceptions."""
    from nyxloom import decisions

    def mock_decide(cfg, decision_id, choice, note, authority):
        raise decisions.DecisionError("test error")

    monkeypatch.setattr("nyxloom.decisions.decide", mock_decide)

    with pytest.raises(decisions.DecisionError):
        cli.main(["--debug", "decide", "demo", "D-002", "--choose", "b"])


# ==========================================================================
# PACKAGE P05c (docs/plan-logging.md, logging sweep): the load-bearing
# byte-unchanged CLI oracle, plus direct coverage of cli.py's own
# _bootstrap_logging (the ONE addition this package makes to cli.py itself
# -- see the module's own docstring on the user-facing-print distinction).
# ==========================================================================

def test_doctor_full_stdout_byte_exact(sample_project, tmp_state, capsys, monkeypatch):
    """Oracle 1: `doctor`'s FULL stdout is byte-for-byte the pre-
    instrumentation composition of _format_table's findings table + the
    fixed dashboard-URL line -- nothing from this package's new log.* calls
    (in config.py, lint.py, decisions.py, ...) leaks in. The expected value
    is computed independently via the SAME _format_table (unchanged code)
    cmd_doctor itself calls, rather than a hand-maintained literal, so this
    pins "nothing EXTRA appears" rather than freezing the table's exact
    column widths forever."""
    from nyxloom.types import DoctorFinding

    monkeypatch.delenv("NYXLOOM_HTTP_BIND", raising=False)
    finding = DoctorFinding(
        kind="replay-divergence", severity="critical",
        message="task state diverged", project="demo",
    )
    monkeypatch.setattr("nyxloom.doctor.doctor_project", lambda cfg: [finding])

    expected_table = cli._format_table(
        [{
            "kind": "replay-divergence", "severity": "critical",
            "message": "task state diverged", "project": "demo", "refs": "",
        }],
        ["kind", "severity", "message", "project", "refs"],
    )
    expected = (
        expected_table
        + "\n\ndashboard: http://127.0.0.1:8942  (read-only; loopback on the daemon host)\n"
    )

    exit_code = cli.main(["doctor"])
    assert exit_code == 1
    assert capsys.readouterr().out == expected


def test_status_full_stdout_byte_exact(sample_project, tmp_state, capsys, make_statefile):
    """Oracle 1 (status half): the SAME byte-exact treatment as the doctor
    test above, for a non-empty status table."""
    from nyxloom import storage

    tsf = make_statefile()
    storage.save_state(tsf)

    expected = cli._format_table(
        [{
            "task_id": "demo-P01-test", "state": "ACTIVE", "since": tsf.since.isoformat(),
            "route": "", "cost": "", "notes": "",
        }],
        ["task_id", "state", "since", "route", "cost", "notes"],
    ) + "\n"

    exit_code = cli.main(["status"])
    assert exit_code == 0
    assert capsys.readouterr().out == expected


def test_bootstrap_logging_writes_jsonl_not_stdout(sample_project, tmp_state, capsys):
    """The bootstrap this package adds to cli.main() (_bootstrap_logging)
    must be invisible on stdout/stderr -- it should ONLY ever produce a
    nyxloom.jsonl file. Drives `lint` (unmocked -- the fixture handoff has
    pre-existing L7/L11 findings, so exit 1 is the correct, UNCHANGED
    outcome; this test is about the log plumbing, not lint's own verdict)
    which reaches config.py/frontmatter.py/lint.py's new log.debug calls at
    PACKAGE P05c's default 'info' bootstrap level -- DEBUG itself is
    dropped, but this proves the plumbing runs end-to-end with zero
    stdout/stderr leakage even so."""
    from nyxloom import paths

    exit_code = cli.main(["lint"])
    assert exit_code == 1
    # No raw structlog line (e.g. a bare "logger" key, as an unconfigured
    # PrintLogger/ConsoleRenderer line would carry) ever reached stdout/stderr.
    captured = capsys.readouterr()
    assert "logger" not in captured.out and "logger" not in captured.err
    assert paths.logs_dir().exists()


def test_bootstrap_logging_invalid_env_level_falls_back_to_info(
    sample_project, tmp_state, capsys, monkeypatch
):
    """cli.py's _bootstrap_logging ValueError fallback: an invalid
    NYXLOOM_LOG_LEVEL must never crash the CLI (or leak onto stdout) --
    it silently falls back to INFO, same as an unset env var."""
    monkeypatch.setenv("NYXLOOM_LOG_LEVEL", "not-a-real-level")

    exit_code = cli.main(["version"])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() != ""
