"""Tests for static dashboard renderer (P05)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from nyxloom import paths, storage, render
from nyxloom.types import (
    TaskStateFile, Attempt, Route, Usage, Basis, AttemptState,
    Receipt, ReceiptResult, TaskState, Actor, ActorKind, Event,
    EventType, Frontmatter, Source, Scope, Oracle, Role,
)
from nyxloom.config import ProjectConfig


@pytest.fixture()
def seed_data(sample_project, tmp_state):
    """Create demo project with two tasks: P01-sample (ACTIVE) and P02-done (MERGED)."""
    project_id = "demo"

    # Create P01-sample (ACTIVE)
    p01_started = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
    p01_attempt = Attempt(
        attempt_id="att-001",
        role=Role.IMPLEMENTER,
        state=AttemptState.RUNNING,
        route=Route(route_id="fake-cli", cli="fake", model="fake-model"),
        started=p01_started,
        log_path=str(tmp_state / "projects" / project_id / "attempts" / "att-001" / "log.txt"),
    )

    # Create log file with password to test redaction
    log_dir = tmp_state / "projects" / project_id / "attempts" / "att-001"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "log.txt"
    log_file.write_text("progress line\npassword=hunter2\n", encoding="utf-8")

    p01_tsf = TaskStateFile(
        schema_version=1,
        task_id="demo-P01-sample",
        project=project_id,
        state=TaskState.ACTIVE,
        since=p01_started,
        handoff_path="handoff/demo-P01-sample.md",
        attempts=[p01_attempt],
        leases_held=["demo.stack"],
        notes="implementing <script>alert(1)</script>",
    )
    storage.save_state(p01_tsf)

    # Create P02-done (MERGED)
    p02_started = datetime(2026, 7, 14, 15, 0, 0, tzinfo=timezone.utc)
    p02_ended = datetime(2026, 7, 14, 16, 0, 0, tzinfo=timezone.utc)
    p02_attempt = Attempt(
        attempt_id="att-002",
        role=Role.IMPLEMENTER,
        state=AttemptState.EXITED,
        route=Route(route_id="fake-cli", cli="fake", model="fake-model"),
        started=p02_started,
        ended=p02_ended,
        receipt=Receipt(
            result=ReceiptResult.DONE,
            exit_code=0,
        ),
        usage=Usage(basis=Basis.ESTIMATED, cost=0.10, currency="USD"),
    )

    p02_tsf = TaskStateFile(
        schema_version=1,
        task_id="demo-P02-done",
        project=project_id,
        state=TaskState.MERGED,
        since=p02_started,
        merge_commit="a" * 40,
        attempts=[p02_attempt],
        progress_units=["R1"],
    )
    storage.save_state(p02_tsf)

    # Add usage to P01
    p01_attempt.usage = Usage(basis=Basis.ACTUAL, cost=0.05, currency="USD")
    p01_tsf.attempts[0] = p01_attempt
    storage.save_state(p01_tsf)

    return tmp_state, project_id


def test_render_all_creates_pages(seed_data, sample_project):
    """Oracle 1: render_all creates all required pages."""
    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}

    www = render.render_all(registry)

    assert www == paths.www_dir()
    assert (www / "index.html").exists()
    assert (www / "history.html").exists()
    assert (www / "dag.html").exists()
    assert (www / "timeline.html").exists()
    assert (www / "quality.html").exists()
    assert (www / "live.html").exists()
    assert (www / "task" / "demo" / "demo-P01-sample.html").exists()
    assert (www / "task" / "demo" / "demo-P02-done.html").exists()


def test_index_html_active_tasks(seed_data, sample_project):
    """Oracle 2: index.html has active-tasks table with correct content."""
    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}

    render.render_all(registry)

    content = (paths.www_dir() / "index.html").read_text(encoding="utf-8")
    assert 'id="active-tasks"' in content
    assert "demo-P01-sample" in content
    assert 'href="task/demo/demo-P01-sample.html"' in content
    assert "0.05 USD (actual)" in content
    assert "demo.stack" in content
    assert "demo-P02-done" not in content  # MERGED should not be in active
    assert "&lt;script&gt;" in content  # HTML escaped
    assert "<script>alert" not in content  # Raw script not present


def test_index_html_pause_banner(seed_data, sample_project):
    """Oracle 3: pause banner appears after touching pause flag."""
    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}

    # First render without pause
    render.render_all(registry)
    content = (paths.www_dir() / "index.html").read_text(encoding="utf-8")
    assert 'id="pause-banner"' not in content

    # Touch pause flag and re-render
    paths.pause_flag("demo").touch()
    render.render_all(registry)
    content = (paths.www_dir() / "index.html").read_text(encoding="utf-8")
    assert 'id="pause-banner"' in content


def test_history_html(seed_data, sample_project):
    """Oracle 4: history.html has correct content for terminal tasks."""
    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}

    render.render_all(registry)

    content = (paths.www_dir() / "history.html").read_text(encoding="utf-8")
    assert 'id="history"' in content
    assert "demo-P02-done" in content
    assert "aaaaaaa" in content  # merge_commit prefix
    assert "R1" in content  # progress_units
    assert "0.10 USD" in content  # cost (estimated)


def test_task_page_redaction(seed_data, sample_project):
    """Oracle 5: task page has log-excerpt with redaction."""
    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}

    render.render_all(registry)

    content = (paths.www_dir() / "task" / "demo" / "demo-P01-sample.html").read_text(
        encoding="utf-8"
    )
    assert 'id="log-excerpt"' in content
    assert "progress line" in content
    assert "[REDACTED]" in content
    assert "hunter2" not in content


def test_task_page_handoff_body_no_markdown(seed_data, sample_project):
    """Oracle 5b: task page renders handoff as <pre> without markdown."""
    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}

    render.render_all(registry)

    content = (paths.www_dir() / "task" / "demo" / "demo-P01-sample.html").read_text(
        encoding="utf-8"
    )
    # Check that the handoff body is in a pre tag
    assert "<pre>" in content
    # Find the handoff pre section (after "Handoff" heading)
    parts = content.split("<h2>Handoff</h2>")
    if len(parts) > 1:
        handoff_section = parts[1]
        # Check that "# Sample bounded package" appears literally (not rendered as <h1>)
        assert "# Sample bounded package" in handoff_section
        # Check that there's no markdown rendering (the # should remain as text)
        # Split by first </pre> to get the handoff content
        pre_content = handoff_section.split("</pre>")[0]
        # The heading text should be in the pre, not rendered as HTML
        assert "Sample bounded package" in pre_content
        # Make sure it's in the pre block itself
        assert "<pre>" in handoff_section.split("Sample bounded package")[0]


def test_dag_html_state_class(seed_data, sample_project):
    """Oracle 6: dag.html has state-ACTIVE class on P01."""
    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}

    render.render_all(registry)

    content = (paths.www_dir() / "dag.html").read_text(encoding="utf-8")
    assert 'id="dag"' in content
    assert 'class="state-ACTIVE"' in content


def test_dag_html_edges(seed_data, sample_project):
    """Oracle 6b: dag.html has mutex edges in edges table."""
    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}

    render.render_all(registry)

    content = (paths.www_dir() / "dag.html").read_text(encoding="utf-8")
    # P01 has mutex "demo.stack" via the seed data
    assert "demo.stack" in content or "demo-P01-sample" in content


def test_timeline_html(seed_data, sample_project):
    """Oracle 7: timeline.html has lanes and bars with attempts."""
    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}

    render.render_all(registry)

    content = (paths.www_dir() / "timeline.html").read_text(encoding="utf-8")
    assert 'id="timeline"' in content
    assert 'class="lane"' in content
    assert "demo-P01-sample" in content
    assert "fake-cli" in content
    assert "att" in content


def test_quality_html_aggregation(seed_data, sample_project):
    """Oracle 8: quality.html aggregates attempts per route."""
    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}

    render.render_all(registry)

    content = (paths.www_dir() / "quality.html").read_text(encoding="utf-8")
    assert 'id="quality"' in content
    assert "fake-cli" in content
    # Both P01 and P02 use fake-cli, so attempts should be 2
    assert "2" in content or "attempts" in content


def test_stale_page_removal(seed_data, sample_project):
    """Oracle 9: stale task pages are removed on re-render."""
    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}

    # First render
    render.render_all(registry)
    assert (paths.www_dir() / "task" / "demo" / "demo-P02-done.html").exists()

    # Delete P02's statefile
    (tmp_state / "projects" / project_id / "state" / "demo-P02-done.json").unlink()

    # Re-render
    render.render_all(registry)

    # P02's page should be gone
    assert not (paths.www_dir() / "task" / "demo" / "demo-P02-done.html").exists()


def test_idempotence(seed_data, sample_project):
    """Oracle 10: two consecutive renders produce byte-identical index.html."""
    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}

    render.render_all(registry)
    content1 = (paths.www_dir() / "index.html").read_bytes()

    render.render_all(registry)
    content2 = (paths.www_dir() / "index.html").read_bytes()

    assert content1 == content2


def test_render_after_event_is_alias(seed_data, sample_project):
    """render_after_event is a cheap alias for render_all."""
    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}

    result = render.render_after_event(registry)
    assert result == paths.www_dir()
    assert (paths.www_dir() / "index.html").exists()


# ---------------------------------------------------------------------------
# P13: real DAG (bug fix + SVG layout), auto-fit timeline, readable live page


def _write_handoff(root: Path, task_id: str, *, depends_on=None, mutexes=None,
                    stack: str = "none") -> str:
    """Write a minimal schema-valid handoff file for `task_id`.

    Returns its path relative to `root` (matching the sample project's
    handoff_globs = ["handoff/*.md"]).
    """
    lines = [
        "---",
        "schema_version: 1",
        f"id: {task_id}",
        "project: demo",
        f"title: \"{task_id} title\"",
        "tier: flash-high",
        'input_revision: "0000000"',
        "source: {kind: roadmap}",
        "scope:",
        '  touch: ["src/demo/thing.py"]',
        "oracles:",
        "  - id: O1",
        "    observable: x",
        "    negative: y",
        "    gate: pytest-q",
        "gates: [pytest-q]",
        'escalate_if: ["x"]',
    ]
    if stack != "none":
        lines.append(f"stack: {stack}")
    if mutexes:
        lines.append("mutexes: [" + ", ".join(mutexes) + "]")
    if depends_on:
        lines.append("depends_on: [" + ", ".join(depends_on) + "]")
    lines += ["---", "", f"# {task_id}", "", "Body."]
    text = "\n".join(lines) + "\n"

    rel = f"handoff/{task_id}.md"
    (root / rel).write_text(text, encoding="utf-8")
    return rel


@pytest.fixture()
def dag_data(sample_project, tmp_state):
    """Two tasks whose HANDOFF FILES (not statefiles) carry the dependency:
    demo-P10-taska depends_on demo-P11-taskb and shares mutex 'shared'."""
    project_id = "demo"
    root = sample_project.root

    rel_a = _write_handoff(root, "demo-P10-taska",
                           depends_on=["demo-P11-taskb"], mutexes=["shared"])
    rel_b = _write_handoff(root, "demo-P11-taskb")

    now = datetime.now(timezone.utc)
    storage.save_state(TaskStateFile(
        schema_version=1, task_id="demo-P10-taska", project=project_id,
        state=TaskState.ACTIVE, since=now, handoff_path=rel_a,
    ))
    storage.save_state(TaskStateFile(
        schema_version=1, task_id="demo-P11-taskb", project=project_id,
        state=TaskState.QUEUED, since=now, handoff_path=rel_b,
    ))
    return root


def test_dag_edges_from_frontmatter(dag_data, sample_project):
    """Oracle 1: edge extraction reads each task's OWN handoff frontmatter
    (depends_on + effective_mutexes) — the statefile carries no deps, so
    this is the confirmed "No edges" bug fix. Asserts an <svg>, an
    edge-dep marker, and edges-table rows for both the dep and mutex."""
    registry = {"demo": sample_project.root}
    render.render_all(registry)

    content = (paths.www_dir() / "dag.html").read_text(encoding="utf-8")

    assert "<svg" in content
    assert content.count('class="edge-dep"') >= 1
    assert content.count('class="edge-mutex"') >= 1
    assert re.search(
        r"<td>demo-P10-taska</td>\s*<td>demo-P11-taskb</td>\s*<td>dep</td>",
        content,
    ), "dep edge row missing from edges table"
    assert re.search(
        r"<td>demo-P10-taska</td>\s*<td>demo\.shared</td>\s*<td>mutex</td>",
        content,
    ), "mutex edge row missing from edges table"


def test_dag_unparseable_handoff_negative(sample_project, tmp_state):
    """Negative (oracle 1): a task whose handoff file fails to parse must
    not crash render_all; its node still renders (with no edges)."""
    project_id = "demo"
    root = sample_project.root
    bad_rel = "handoff/demo-P12-broken.md"
    (root / bad_rel).write_text("not frontmatter at all, no leading '---'\n", encoding="utf-8")

    storage.save_state(TaskStateFile(
        schema_version=1, task_id="demo-P12-broken", project=project_id,
        state=TaskState.ACTIVE, since=datetime.now(timezone.utc),
        handoff_path=bad_rel,
    ))

    registry = {"demo": root}
    www = render.render_all(registry)  # must not raise

    content = (www / "dag.html").read_text(encoding="utf-8")
    assert 'data-node="demo-P12-broken"' in content
    assert ">demo-P12-broken<" in content


def test_dag_layout_places_dependency_left(dag_data, sample_project):
    """Oracle 2: for an A->B edge (A's handoff depends_on B), A's <rect>
    x is strictly less than B's."""
    registry = {"demo": sample_project.root}
    render.render_all(registry)
    content = (paths.www_dir() / "dag.html").read_text(encoding="utf-8")

    def rect_x(node_id: str) -> float:
        m = re.search(rf'data-node="{re.escape(node_id)}"[^>]*\sx="([0-9.]+)"', content)
        assert m, f"no <rect> found for node {node_id!r}"
        return float(m.group(1))

    assert rect_x("demo-P10-taska") < rect_x("demo-P11-taskb")


def test_dag_cycle_does_not_hang_and_marks_edge_red(sample_project, tmp_state):
    """Finding 2 (cycles): a mutual dependency must not hang the layout; the
    cycle-breaking edge is reported with class edge-broken."""
    project_id = "demo"
    root = sample_project.root

    rel_a = _write_handoff(root, "demo-P13-cyclea", depends_on=["demo-P14-cycleb"])
    rel_b = _write_handoff(root, "demo-P14-cycleb", depends_on=["demo-P13-cyclea"])

    now = datetime.now(timezone.utc)
    storage.save_state(TaskStateFile(
        schema_version=1, task_id="demo-P13-cyclea", project=project_id,
        state=TaskState.ACTIVE, since=now, handoff_path=rel_a,
    ))
    storage.save_state(TaskStateFile(
        schema_version=1, task_id="demo-P14-cycleb", project=project_id,
        state=TaskState.ACTIVE, since=now, handoff_path=rel_b,
    ))

    registry = {"demo": root}
    www = render.render_all(registry)  # must terminate promptly, never hang

    content = (www / "dag.html").read_text(encoding="utf-8")
    assert "demo-P13-cyclea" in content
    assert "demo-P14-cycleb" in content
    assert "edge-broken" in content


@pytest.fixture()
def timeline_data(sample_project, tmp_state):
    """demo-P20-recent has an attempt that started 10 minutes ago and is
    still running; demo-P21-idle has zero attempts (empty-window lane)."""
    project_id = "demo"
    started = datetime.now(timezone.utc) - timedelta(minutes=10)
    att = Attempt(
        attempt_id="att-recent",
        role=Role.IMPLEMENTER,
        state=AttemptState.RUNNING,
        route=Route(route_id="fake-cli", cli="fake", model="fake-model"),
        started=started,
    )
    storage.save_state(TaskStateFile(
        schema_version=1, task_id="demo-P20-recent", project=project_id,
        state=TaskState.ACTIVE, since=started, attempts=[att],
    ))
    storage.save_state(TaskStateFile(
        schema_version=1, task_id="demo-P21-idle", project=project_id,
        state=TaskState.QUEUED, since=datetime.now(timezone.utc),
    ))
    return project_id


def test_timeline_autofit_bar_width_and_ticks(timeline_data, sample_project):
    """Oracle 3: a bar for an attempt started 10 minutes ago has width
    >= 6px (MIN_BAR_WIDTH_PX), and the axis row carries >= 4 tick labels."""
    registry = {"demo": sample_project.root}
    render.render_all(registry)
    content = (paths.www_dir() / "timeline.html").read_text(encoding="utf-8")

    assert 'id="timeline"' in content
    assert content.count('class="tick"') >= 4

    m = re.search(r'class="bar" style="left: [0-9.]+px; width: ([0-9.]+)px;"', content)
    assert m, "no attempt bar found in timeline.html"
    assert float(m.group(1)) >= render.MIN_BAR_WIDTH_PX


def test_timeline_empty_window_note(timeline_data, sample_project):
    """Oracle 3: a lane with no attempts in the window shows the
    "No activity in window" note instead of an empty track."""
    registry = {"demo": sample_project.root}
    render.render_all(registry)
    content = (paths.www_dir() / "timeline.html").read_text(encoding="utf-8")

    assert "demo-P21-idle" in content
    assert "No activity in window" in content


def test_live_html_parsed_renderer(seed_data, sample_project):
    """Oracle 4: live.html parses each SSE event client-side, builds rows
    via textContent only (never innerHTML), colors by type, offers a raw
    toggle, and keeps the project-less EventSource URL."""
    registry = {"demo": sample_project.root}
    render.render_all(registry)
    content = (paths.www_dir() / "live.html").read_text(encoding="utf-8")

    assert "textContent" in content
    assert "innerHTML" not in content
    assert 'id="raw-toggle"' in content
    assert "new EventSource('/api/stream')" in content
    assert "?project=" not in content
    assert "TICK_ERROR" in content
    assert "TASK_TRANSITIONED" in content
    assert "ATTEMPT_" in content
    assert "evt-tick-error" in content
    assert "evt-task-transitioned" in content
    assert "evt-attempt" in content
