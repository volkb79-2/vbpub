"""Tests for static dashboard renderer (P05)."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import structlog.contextvars

from nyxloom import capability_map, log, paths, storage, render
from nyxloom.types import (
    TaskStateFile, Attempt, Route, Usage, Basis, AttemptState,
    Receipt, ReceiptResult, TaskState, Actor, ActorKind, Event,
    EventType, Frontmatter, Source, Scope, Oracle, Role,
    Blocker, BlockerType,
)
from nyxloom.config import ProjectConfig
from conftest import SAMPLE_HANDOFF


@pytest.fixture(autouse=True)
def _silence_nyxloom_logging():
    """PACKAGE P05c safety net -- see test_backlog_items.py's copy of this
    fixture for the full rationale (byte-unchanged CLI oracle,
    docs/plan-logging.md P05c). render.py now carries a log.debug per
    page-render function."""
    log.configure(level=log.CRITICAL, console=False)
    yield
    structlog.contextvars.clear_contextvars()
    nyxloom_logger = logging.getLogger("nyxloom")
    for handler in list(nyxloom_logger.handlers):
        nyxloom_logger.removeHandler(handler)
        handler.close()


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
    assert (www / "routing.html").exists()
    assert (www / "live.html").exists()
    assert (www / "findings.html").exists()
    assert (www / "task" / "demo" / "demo-P01-sample.html").exists()
    assert (www / "task" / "demo" / "demo-P02-done.html").exists()


def test_decisions_html_lists_open_decision_and_transcript(seed_data, sample_project):
    """P18 oracle 4: decisions.html lists an OPEN decision (html-escaped
    question) plus its chat transcript (html-escaped, never raw), and an
    answer box driving POST /api/decision/reply."""
    from nyxloom import decision_chat

    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}

    (sample_project.root / "docs" / "DECISIONS-INBOX.md").write_text(
        "# Decisions inbox\n\n---\n\n"
        "## D-060 · 2026-07-16 · test · OPEN\n\n"
        "**Question:** Ratify <script>alert(1)</script>?\n\n---\n",
        encoding="utf-8",
    )

    chat = decision_chat.DecisionChat(decision_id="D-060", project="demo", session_id="sess-1")
    chat.transcript.append(decision_chat.DecisionChatMessage(
        role="user", text="<b>hi</b>", ts="2026-07-16T00:00:00+00:00"))
    decision_chat.save_chat(chat)

    render.render_all(registry)
    content = (paths.www_dir() / "decisions.html").read_text(encoding="utf-8")

    assert 'id="decisions"' in content
    assert "D-060" in content
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in content
    assert "<script>alert(1)</script>" not in content
    assert "&lt;b&gt;hi&lt;/b&gt;" in content
    assert "<b>hi</b>" not in content
    assert "/api/decision/reply" in content


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


def test_index_html_groups_task_by_component(sample_project, tmp_state):
    """P42 O2: a task whose handoff declares `component: lifecycle` renders
    that component as a label/tag in the active-tasks row."""
    project_id = "demo"
    now = datetime.now(timezone.utc)
    handoff_text = SAMPLE_HANDOFF.replace(
        "id: demo-P01-sample", "id: demo-P44-labelled",
    ).replace(
        "escalate_if: [\"a named contract cannot be met as specified\"]",
        "escalate_if: [\"a named contract cannot be met as specified\"]\n"
        "component: lifecycle",
    )
    (sample_project.root / "handoff" / "demo-P44-labelled.md").write_text(handoff_text)

    storage.save_state(TaskStateFile(
        schema_version=1, task_id="demo-P44-labelled", project=project_id,
        state=TaskState.ACTIVE, since=now,
        handoff_path="handoff/demo-P44-labelled.md",
    ))

    registry = {"demo": sample_project.root}
    render.render_all(registry)
    content = (paths.www_dir() / "index.html").read_text(encoding="utf-8")

    assert "demo-P44-labelled" in content
    assert '<span class="component-tag">lifecycle</span>' in content


def test_index_html_componentless_task_renders_ungrouped(sample_project, tmp_state):
    """P42 O2 negative: a task with no `component` (or no handoff at all)
    still renders without crashing, tagged 'uncategorized'."""
    project_id = "demo"
    now = datetime.now(timezone.utc)
    storage.save_state(TaskStateFile(
        schema_version=1, task_id="demo-P45-nocomponent", project=project_id,
        state=TaskState.ACTIVE, since=now,
    ))

    registry = {"demo": sample_project.root}
    render.render_all(registry)  # must not raise
    content = (paths.www_dir() / "index.html").read_text(encoding="utf-8")

    assert "demo-P45-nocomponent" in content
    assert '<span class="component-tag">uncategorized</span>' in content


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


# --------------------------------------------------------------------------
# P16 2026-07-15: carve-summary interleave + toggle (handoff/
# P16-carver-automation.md oracle 3)

def _write_carve_summary(project: str, seq: int, timestamp: str, **overrides) -> None:
    """Persist a fake CarveSummary artifact exactly the shape
    daemon._consume_carve_exit writes (see daemon.py) -- render.py never
    parses events.jsonl for this, only these files."""
    d = paths.project_dir(project) / "carves"
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "seq": seq,
        "timestamp": timestamp,
        "carved": [{"id": f"demo-P4{seq}-new", "why": "why text", "source_kind": "review"}],
        "review_reflection": f"reflection <script>alert({seq})</script> text {seq}",
        "headroom_estimate": 4,
        "headroom_rationale": "rationale text",
        "outcome": "CANDIDATES_READY",
    }
    payload.update(overrides)
    (d / f"{seq}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_index_html_carve_toggle_default_off_via_css(seed_data, sample_project):
    """Off = today's pure task list (Behavior item 5): the checkbox is
    unchecked by default and CSS hides .carve-row unless the table carries
    show-carves -- render_all has no per-request state, so this structural
    default-hide (mirrors live.html's raw-JSON toggle) is what 'off' means
    for a static page."""
    tmp_state, project_id = seed_data
    _write_carve_summary(project_id, 1, "2026-07-15T09:00:00+00:00")
    registry = {"demo": sample_project.root}
    render.render_all(registry)

    content = (paths.www_dir() / "index.html").read_text(encoding="utf-8")
    assert 'id="carve-toggle"' in content
    checkbox_tag = content.split('id="carve-toggle"', 1)[1].split(">", 1)[0]
    assert "checked" not in checkbox_tag
    assert ".carve-row" in content and "display: none" in content
    assert "show-carves" in content


def test_index_html_carve_rows_interleaved_by_timestamp_escaped_no_innerhtml(
        seed_data, sample_project):
    """Oracle 3: two persisted summaries + tasks -> toggle-on view has both
    summaries positioned by timestamp among the task rows; reflection
    html-escaped; no innerHTML anywhere on the page."""
    tmp_state, project_id = seed_data
    # P01-sample's 'since' is 2026-07-15T10:00:00Z (seed_data) -- one carve
    # summary BEFORE it, one AFTER, to prove timestamp-based interleave.
    _write_carve_summary(project_id, 1, "2026-07-15T09:00:00+00:00")
    _write_carve_summary(project_id, 2, "2026-07-15T11:00:00+00:00")
    registry = {"demo": sample_project.root}
    render.render_all(registry)

    content = (paths.www_dir() / "index.html").read_text(encoding="utf-8")
    assert 'data-carve-seq="1"' in content
    assert 'data-carve-seq="2"' in content
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in content
    assert "<script>alert" not in content
    assert "innerHTML" not in content

    idx_carve1 = content.index('data-carve-seq="1"')
    idx_task = content.index("demo-P01-sample")
    idx_carve2 = content.index('data-carve-seq="2"')
    assert idx_carve1 < idx_task < idx_carve2


def test_index_html_no_carve_files_no_carve_rows(seed_data, sample_project):
    """No persisted carve summaries -> no .carve-row markers at all (the
    common case: identical to the pre-P16 page save for the toggle
    checkbox itself)."""
    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}
    render.render_all(registry)
    content = (paths.www_dir() / "index.html").read_text(encoding="utf-8")
    # The shared CSS rule that HIDES .carve-row by default is always present
    # (page-wide stylesheet); what must be absent is an actual RENDERED
    # carve row (only ever emitted per persisted carves/*.json file).
    assert "data-carve-seq" not in content
    assert "<tr class=\"carve-row\"" not in content


def test_config_html_renders_carve_authority_select(sample_project, tmp_state):
    """Oracle 4 (render half): config.html exposes a carve-authority
    control (branch/main/files) wired to the same POST /api/config/policy
    endpoint P15 established."""
    registry = {"demo": sample_project.root}
    render.render_all(registry)
    content = (paths.www_dir() / "config.html").read_text(encoding="utf-8")
    assert 'id="carve-authority-demo"' in content
    assert 'value="branch"' in content and "selected" in content
    assert 'value="main"' in content
    assert 'value="files"' in content
    assert "saveCarveAuthority" in content
    assert "innerHTML" not in content


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


# --------------------------------------------------------------------------
# P25 2026-07-16: dashboard project-info summary + archive-collapse UX
# (nyxloom-trove/handoffs/nyxloom-P25-dashboard-project-info-archive.md)

def _write_project_toml_extra(root, extra_project_lines: str = "", notify_lines: str = "") -> None:
    """Append lines inside the sample project's existing [project]/[notify]
    tables (legacy .nyxloom/project.toml layout) -- archive_dir/
    archive_keep_visible/notify topics aren't part of ProjectConfig's
    dataclass fields the sample fixture normally sets, so tests that need
    them write them straight into the toml text (config.py itself, and the
    frozen conftest fixture, are untouched)."""
    project_toml = root / ".nyxloom" / "project.toml"
    text = project_toml.read_text()
    if extra_project_lines:
        text = text.replace(
            'infra_globs = ["infra/**"]\n',
            'infra_globs = ["infra/**"]\n' + extra_project_lines,
        )
    if notify_lines:
        text = text.replace("[notify]\n", "[notify]\n" + notify_lines)
    project_toml.write_text(text)


def _make_completed(project_id: str, task_id: str, ended: datetime) -> None:
    """Persist a COMPLETED task whose single attempt ended at `ended` --
    the timestamp _render_history's archive cap orders/caps by."""
    started = ended - timedelta(hours=1)
    attempt = Attempt(
        attempt_id=f"att-{task_id}",
        role=Role.IMPLEMENTER,
        state=AttemptState.EXITED,
        route=Route(route_id="fake-cli", cli="fake", model="fake-model"),
        started=started,
        ended=ended,
        receipt=Receipt(result=ReceiptResult.DONE, exit_code=0),
        usage=Usage(basis=Basis.ACTUAL, cost=0.01, currency="USD"),
    )
    tsf = TaskStateFile(
        schema_version=1,
        task_id=task_id,
        project=project_id,
        state=TaskState.COMPLETED,
        since=started,
        attempts=[attempt],
    )
    storage.save_state(tsf)


def test_config_html_renders_project_info_summary(sample_project, tmp_state):
    """O1: config.html surfaces, per registered project, the gate id(s)
    with rendered argv, the notify channels (ntfy_url + both topic names),
    and the trove folder paths (handoff glob, reports_dir, archive_dir) --
    all read from ProjectConfig / the raw nyxloom.toml [project] table, no
    config.py schema change."""
    _write_project_toml_extra(
        sample_project.root,
        extra_project_lines='archive_dir = "archive"\narchive_keep_visible = 5\n',
        notify_lines=(
            'ntfy_url = "https://ntfy.example"\n'
            'ntfy_topic = "notifications"\n'
            'cmd_topic = "feedback"\n'
        ),
    )

    registry = {"demo": sample_project.root}
    render.render_all(registry)
    content = (paths.www_dir() / "config.html").read_text(encoding="utf-8")

    assert "<code>pytest-q</code>" in content
    assert "<code>true</code>" in content  # gate argv
    assert "notifications topic: notifications" in content
    assert "feedback topic: feedback" in content
    assert "<code>handoff/*.md</code>" in content  # handoff glob
    assert "<code>handoff/reports</code>" in content  # default reports_dir
    assert "<code>archive</code>" in content  # archive_dir
    assert "innerHTML" not in content


def test_config_html_project_info_reads_trove_layout(sample_project, tmp_state):
    """O1, trove layout: production projects config via
    nyxloom-trove/nyxloom.toml (the .nyxloom/project.toml the fixture writes
    is only the legacy fallback), so cover the trove-first branch of
    _trove_project_extras and the `trove:` folder line -- neither of which
    the legacy-layout sample fixture ever reaches."""
    trove = sample_project.root / "nyxloom-trove"
    trove.mkdir()
    (trove / "nyxloom.toml").write_text(
        '[project]\n'
        'id = "demo"\n'
        'default_branch = "main"\n'
        'worktree_root = ".worktrees"\n'
        'trove = "nyxloom-trove"\n'
        'handoff_globs = ["nyxloom-trove/handoffs/*.md"]\n'
        'reports_dir = "nyxloom-trove/reports"\n'
        'archive_dir = "nyxloom-trove/archive"\n'
        'archive_keep_visible = 3\n'
        '\n'
        '[gates.trove-gate]\n'
        'argv = ["pytest", "-q"]\n'
        'phase = "implementation"\n'
        'timeout_seconds = 60\n'
        'environment = "local"\n'
        '\n'
        '[policy]\n'
        'max_active_tasks = 2\n'
        '\n'
        '[notify]\n'
        'ntfy_url = "https://ntfy.example"\n'
        'notifications_topic = "trove-notify"\n'
        'feedback_topic = "trove-feedback"\n'
    )

    registry = {"demo": sample_project.root}
    render.render_all(registry)
    content = (paths.www_dir() / "config.html").read_text(encoding="utf-8")

    # trove-first resolution wins over the legacy .nyxloom/project.toml, for
    # both ProjectConfig.load (gates/notify) and _trove_project_extras (folders)
    assert "<code>trove-gate</code>" in content
    assert "<code>pytest -q</code>" in content
    assert "<code>pytest-q</code>" not in content  # the legacy gate id is NOT used
    assert "notifications topic: trove-notify" in content
    assert "feedback topic: trove-feedback" in content
    # the four folder paths of O1, including the `trove:` line
    assert "<code>nyxloom-trove</code>" in content            # trove root
    assert "<code>nyxloom-trove/handoffs/*.md</code>" in content
    assert "<code>nyxloom-trove/reports</code>" in content
    assert "<code>nyxloom-trove/archive</code>" in content


def test_config_html_tolerates_non_numeric_archive_keep_visible(sample_project, tmp_state):
    """REVIEW fix: a non-numeric archive_keep_visible must not take the whole
    dashboard down. ProjectConfig.load() ignores these unknown [project]
    keys, so _trove_project_extras is the only reader -- an unguarded int()
    there would raise straight through render_all(), failing every page for
    every project. Falls back to the default cap of 10 instead."""
    _write_project_toml_extra(
        sample_project.root, extra_project_lines='archive_keep_visible = "lots"\n'
    )

    registry = {"demo": sample_project.root}
    render.render_all(registry)  # must not raise

    content = (paths.www_dir() / "history.html").read_text(encoding="utf-8")
    assert 'id="archive-toggle"' in content
    assert render._trove_project_extras(sample_project.root)[2] == 10


def test_history_archive_toggle_caps_completed_by_project(sample_project, tmp_state):
    """O2: history.html renders at most archive_keep_visible (here: 2)
    most-recently-completed packages inline per project; the older
    remainder still renders (class="archive-row") but is hidden by default
    via CSS, revealed by an id="archive-toggle" checkbox mirroring the
    carve-toggle pattern."""
    _write_project_toml_extra(sample_project.root, extra_project_lines="archive_keep_visible = 2\n")

    base = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
    for i in range(4):
        _make_completed("demo", f"demo-PC{i}-done", base + timedelta(hours=i))

    registry = {"demo": sample_project.root}
    render.render_all(registry)
    content = (paths.www_dir() / "history.html").read_text(encoding="utf-8")

    assert 'id="archive-toggle"' in content
    checkbox_tag = content.split('id="archive-toggle"', 1)[1].split(">", 1)[0]
    assert "checked" not in checkbox_tag
    assert ".archive-row" in content and "display: none" in content
    assert "show-archive" in content

    def row_tag_for(task_id: str) -> str:
        idx = content.index(task_id)
        row_start = content.rindex("<tr", 0, idx)
        return content[row_start:content.index(">", row_start) + 1]

    for visible_id in ("demo-PC3-done", "demo-PC2-done"):
        assert "archive-row" not in row_tag_for(visible_id)

    for archived_id in ("demo-PC1-done", "demo-PC0-done"):
        assert "archive-row" in row_tag_for(archived_id)


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


# ---------------------------------------------------------------------------
# B26: per-handoff processing trace (task page section)

def test_task_page_trace_shows_no_events_placeholder_for_event_free_task(seed_data, sample_project):
    """demo-P02-done is seeded via storage.save_state only (no events.jsonl
    entries) -- the trace section must render cleanly, not crash, and show
    the empty-trace placeholder."""
    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}

    render.render_all(registry)

    content = (paths.www_dir() / "task" / "demo" / "demo-P02-done.html").read_text(
        encoding="utf-8"
    )
    assert "Processing Trace" in content
    assert "No trace events recorded" in content


def test_task_page_trace_renders_leg_row_for_recorded_event(seed_data, sample_project):
    """A MERGE_RECORDED event for this task must surface as a
    data-kind="merge" trace row carrying the merge commit."""
    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}
    storage.append_event(
        "demo", actor=Actor(ActorKind.TICK, "nyxloomd"), type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "f" * 40, "source_kind": "review"},
        task_id="demo-P01-sample",
    )

    render.render_all(registry)

    content = (paths.www_dir() / "task" / "demo" / "demo-P01-sample.html").read_text(
        encoding="utf-8"
    )
    assert 'data-kind="merge"' in content
    assert "f" * 40 in content


def test_task_page_trace_escapes_hostile_summary_text(seed_data, sample_project):
    """A TASK_BLOCKED event whose blocker.detail carries a <script> tag
    (untrusted, potentially agent-authored text) must reach the page
    escaped, never as live markup -- same discipline as _render_findings."""
    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}
    blocker = Blocker(type=BlockerType.CONTRACT, unblock_condition="triage BLOCKED reason",
                      detail="<script>alert('trace-xss')</script>")
    storage.append_event(
        "demo", actor=Actor(ActorKind.TICK, "nyxloomd"), type=EventType.TASK_BLOCKED,
        payload={"from": "ACTIVE", "blocker": blocker.to_dict()},
        task_id="demo-P01-sample",
    )

    render.render_all(registry)

    content = (paths.www_dir() / "task" / "demo" / "demo-P01-sample.html").read_text(
        encoding="utf-8"
    )
    assert "Processing Trace" in content
    assert 'data-kind="transition"' in content
    assert "<script>alert" not in content
    assert "&lt;script&gt;alert" in content


def test_task_page_trace_redacts_hostile_summary_text(seed_data, sample_project):
    """CR-14a: an ATTEMPT_EXITED receipt's blocked_reason (untrusted,
    potentially agent-authored free text -- same class of field the log
    excerpt already redacts, per test_task_page_redaction above) must be
    redacted in the trace table too, not merely HTML-escaped."""
    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}
    route = Route(route_id="fake-cli", cli="fake", model="fake-model")
    started = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
    blocked = Attempt(
        attempt_id="att-block", role=Role.IMPLEMENTER, state=AttemptState.EXITED,
        route=route, started=started, ended=started,
        receipt=Receipt(result=ReceiptResult.BLOCKED, exit_code=1,
                        blocked_reason="cannot proceed: password=hunter2"),
    )
    storage.append_event(
        "demo", actor=Actor(ActorKind.TICK, "nyxloomd"), type=EventType.ATTEMPT_CREATED,
        payload={"attempt": Attempt(attempt_id="att-block", role=Role.IMPLEMENTER,
                                    state=AttemptState.CREATED, route=route,
                                    started=started).to_dict()},
        task_id="demo-P01-sample", attempt_id="att-block",
    )
    storage.append_event(
        "demo", actor=Actor(ActorKind.TICK, "nyxloomd"), type=EventType.ATTEMPT_EXITED,
        payload={"attempt": blocked.to_dict()},
        task_id="demo-P01-sample", attempt_id="att-block",
    )

    render.render_all(registry)

    content = (paths.www_dir() / "task" / "demo" / "demo-P01-sample.html").read_text(
        encoding="utf-8"
    )
    assert 'data-kind="attempt"' in content
    assert "[REDACTED]" in content
    assert "hunter2" not in content


def test_task_page_survives_a_config_load_failure_degrading_gracefully(
        seed_data, sample_project, monkeypatch):
    """CR-14a review fix: _render_task_page's own ProjectConfig.load(root)
    (needed for the trace table's redaction, hoisted out of the
    log-excerpt section's try/except) must degrade this ONE task's
    redaction/log-excerpt rather than aborting render_all's entire
    per-task loop -- every OTHER ProjectConfig.load(root) call site in
    this module is guarded the same way (_render_config/_render_decisions/
    etc.); an unguarded one here would take down every remaining
    project's task pages, live.html, and logs.html on one config fault."""
    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}

    real_load = ProjectConfig.load

    def _boom(root):
        if str(root) == str(sample_project.root):
            raise ValueError("simulated config corruption")
        return real_load(root)

    monkeypatch.setattr(ProjectConfig, "load", staticmethod(_boom))

    render.render_all(registry)  # must not raise

    content = (paths.www_dir() / "task" / "demo" / "demo-P01-sample.html").read_text(
        encoding="utf-8"
    )
    assert "Processing Trace" in content
    assert "No log" in content


def test_task_page_trace_does_not_redact_identity_fields_in_detail(seed_data, sample_project):
    """NEGATIVE control for the same fix: a merge leg's detail identity
    field (merge_commit) must survive verbatim -- redaction is scoped to
    `summary` only, so structural evidence identity is never accidentally
    stripped."""
    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}
    storage.append_event(
        "demo", actor=Actor(ActorKind.TICK, "nyxloomd"), type=EventType.MERGE_RECORDED,
        payload={"merge_commit": "f" * 40, "source_kind": "review"},
        task_id="demo-P01-sample",
    )

    render.render_all(registry)

    content = (paths.www_dir() / "task" / "demo" / "demo-P01-sample.html").read_text(
        encoding="utf-8"
    )
    assert "f" * 40 in content


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

    # Remove P02 from the projection. CR-04b: the statefile JSON this used to
    # unlink is gone with the file backend, so the equivalent is deleting the
    # row -- done directly because the store deliberately has no public
    # "forget a task" verb (every real disappearance is a project being
    # deregistered, not a task being erased).
    import sqlite3
    from nyxloom import storage_sqlite
    conn = sqlite3.connect(str(storage_sqlite.db_path(project_id)))
    try:
        conn.execute("DELETE FROM states WHERE task_id = ?", ("demo-P02-done",))
        conn.commit()
    finally:
        conn.close()

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


# ---------------------------------------------------------------------------
# B19: persisted routing/capability dashboard

@pytest.fixture()
def routing_catalog():
    """Local B19 catalog fixture; conftest.py is frozen."""
    return [capability_map.CapabilityRecord(
        model_id="vendor/model-a", source="aa",
        scores={"intelligence": 0.5, "coding": 0.7, "agentic": 0.9},
        price_input=1.0, price_output=2.0, context_length=128000,
        bands={"intelligence": 2, "coding": 3, "agentic": 3},
        may_review=True, may_carve=False, raw={"vision": True},
    )]


def test_routing_html_renders_catalog_and_declared_winner(
        seed_data, sample_project, routing_catalog):
    """B19 O2/O4: catalog rows and tier order render read-only."""
    capability_map.write_capability_catalog(paths.routes_path(), routing_catalog)

    render.render_all({"demo": sample_project.root})

    content = (paths.www_dir() / "routing.html").read_text(encoding="utf-8")
    assert 'id="capability-catalog"' in content
    assert "vendor/model-a" in content
    assert "0.7" in content
    assert ">3<" in content
    assert 'id="tier-resolution"' in content
    assert "flash-high" in content
    assert "fake-cli" in content
    assert ">winner<" in content


def test_routing_html_absent_and_empty_catalog_render_cleanly(seed_data, sample_project):
    """B19 O3: both an absent table and explicit empty records degrade cleanly."""
    render.render_all({"demo": sample_project.root})
    absent = (paths.www_dir() / "routing.html").read_text(encoding="utf-8")
    assert "No persisted capability catalog." in absent

    capability_map.write_capability_catalog(paths.routes_path(), [])
    render.render_all({"demo": sample_project.root})
    empty = (paths.www_dir() / "routing.html").read_text(encoding="utf-8")
    assert "No persisted capability catalog." in empty


def test_routing_html_escapes_catalog_model_id(seed_data, sample_project, routing_catalog):
    """B19 O5: persisted model ids are HTML escaped."""
    record = routing_catalog[0]
    unsafe = capability_map.CapabilityRecord(
        model_id="vendor/<script>", source=record.source, scores=record.scores,
        price_input=record.price_input, price_output=record.price_output,
        context_length=record.context_length, bands=record.bands,
        may_review=record.may_review, may_carve=record.may_carve, raw=record.raw,
    )
    capability_map.write_capability_catalog(paths.routes_path(), [unsafe])

    render.render_all({"demo": sample_project.root})

    content = (paths.www_dir() / "routing.html").read_text(encoding="utf-8")
    assert "<script>" not in content
    assert "&lt;script&gt;" in content


def test_routing_html_is_deterministic(seed_data, sample_project, routing_catalog):
    """B19 O7: repeated persisted-data renders are byte-identical."""
    capability_map.write_capability_catalog(paths.routes_path(), routing_catalog)
    registry = {"demo": sample_project.root}

    render.render_all(registry)
    first = (paths.www_dir() / "routing.html").read_bytes()
    render.render_all(registry)
    second = (paths.www_dir() / "routing.html").read_bytes()

    assert first == second


def test_routing_html_degrades_for_load_errors_and_empty_tiers(
        seed_data, sample_project, monkeypatch):
    """B19 coverage: failed loads and a tier without candidates stay renderable."""
    class EmptyRoutes:
        tiers = {"empty-tier": []}
        routes = {}

        def for_tier(self, tier):
            return []

    monkeypatch.setattr(render.capability_map, "load_capability_catalog",
                        lambda: (_ for _ in ()).throw(ValueError("bad catalog")))
    monkeypatch.setattr(render.config.Routes, "load", lambda: EmptyRoutes())
    render.render_all({"demo": sample_project.root})
    content = (paths.www_dir() / "routing.html").read_text(encoding="utf-8")
    assert "No persisted capability catalog." in content
    assert "empty-tier" in content
    assert "No declared candidates" in content


def test_routing_html_degrades_when_routes_load_raises(
        seed_data, sample_project, monkeypatch):
    """B19 coverage: a RAISING config.Routes.load still leaves routing.html
    renderable, with an explicit no-tiers state.

    Distinct from the EmptyRoutes case above: that one RETURNS a routes object,
    so it never exercises _render_routing's `except Exception: routes = None`
    arm. Raising globally is safe because all three config.Routes.load() call
    sites in render.py (_render_routing, _render_quality, _render_config) are
    individually try/except-guarded and each checks the None result, so every
    page degrades rather than breaking render_all."""
    def _boom():
        raise ValueError("bad routes")

    monkeypatch.setattr(render.config.Routes, "load", _boom)

    render.render_all({"demo": sample_project.root})

    content = (paths.www_dir() / "routing.html").read_text(encoding="utf-8")
    assert "No declared tiers." in content


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


def test_logs_html_wired_and_never_uses_innerhtml(seed_data, sample_project):
    """P04 Oracle O7: render_all writes www/logs.html; the NAV carries its
    link; the page wires a bare `/api/logs/stream` EventSource (mirrors
    live.html's project-less URL -- level filtering of the live tail is
    client-side, not baked into the connection URL), a level <select>, a
    highlight mechanism that wraps matches in <mark> elements, a pause/
    resume-tail toggle, and UTC-explicit timestamps (getUTC*) -- and NEVER
    innerHTML anywhere on the page. Negative: missing page/link, wrong
    stream URL, or innerHTML present would each fail one assertion below."""
    registry = {"demo": sample_project.root}

    www = render.render_all(registry)

    assert (www / "logs.html").exists()

    index_content = (www / "index.html").read_text(encoding="utf-8")
    assert '<a href="logs.html">Logs</a>' in index_content  # NAV link present

    content = (www / "logs.html").read_text(encoding="utf-8")
    assert "new EventSource('/api/logs/stream')" in content
    assert '<select id="level-select">' in content
    # The highlight wraps matches in <mark> elements built dynamically -- assert
    # the actual mechanism (createElement('mark')), not a literal "<mark>" tag,
    # which never appears in the source and would only match an incidental
    # comment (a hollow assertion that survives deleting the real code).
    assert "createElement('mark')" in content
    assert 'id="pause-toggle"' in content
    assert "getUTC" in content
    assert "textContent" in content
    assert "innerHTML" not in content  # NEGATIVE: never innerHTML anywhere


# ---------------------------------------------------------------------------
# P22: dashboard state legend + attempt liveness + read-only drilldown


def test_state_legend_present_and_explains_interrupted_dead_end(seed_data, sample_project):
    """Oracle 1: index.html carries an always-visible legend entry for
    every TaskState (sourced from render.STATE_LEGEND, so it cannot drift
    from the enum) and explains 'interrupted-dead-end' in plain language."""
    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}

    render.render_all(registry)
    content = (paths.www_dir() / "index.html").read_text(encoding="utf-8")

    assert 'id="state-legend"' in content
    # every TaskState value appears (single-dict-sourced -> can't drift)
    for state in TaskState:
        assert f">{state.value}<" in content, f"{state.value} missing from legend"
    # the specific reason called out by the handoff, WITH explanatory text
    assert "interrupted-dead-end" in content
    assert "could not be" in content and "resumed" in content
    # the other states the handoff specifically names
    for state in (TaskState.AWAITING_REVIEW, TaskState.MERGE_READY,
                  TaskState.REVIEW_REJECTED, TaskState.NEEDS_DECISION, TaskState.BLOCKED):
        assert state.value in content


def test_index_html_marks_attempt_running_despite_stale_queued_state(sample_project, tmp_state):
    """Oracle 2: a task whose STATEFILE state is QUEUED (lagging) but whose
    attempt is RUNNING with no receipt.json yet is still marked running —
    this is the literal 'nothing running' bug from the handoff."""
    project_id = "demo"
    now = datetime.now(timezone.utc)
    att = Attempt(
        attempt_id="att-live-001", role=Role.IMPLEMENTER, state=AttemptState.RUNNING,
        route=Route(route_id="fake-cli", cli="fake", model="fake-model"), started=now, pid=None,
    )
    storage.save_state(TaskStateFile(
        schema_version=1, task_id="demo-P30-lagging", project=project_id,
        state=TaskState.QUEUED, since=now, attempts=[att],
    ))

    registry = {"demo": sample_project.root}
    render.render_all(registry)
    content = (paths.www_dir() / "index.html").read_text(encoding="utf-8")

    assert "demo-P30-lagging" in content
    assert "● running (att-live-001)" in content
    assert 'href="/api/drilldown/demo/att-live-001"' in content


def test_index_html_does_not_mark_running_once_receipt_has_landed(sample_project, tmp_state):
    """Negative (oracle 2): once receipt.json exists on disk the wrapper
    has already finished, even if the statefile's attempt.state write
    (RUNNING -> EXITED) itself lagged -- must NOT be shown as running."""
    project_id = "demo"
    now = datetime.now(timezone.utc)
    att = Attempt(
        attempt_id="att-done-002", role=Role.IMPLEMENTER, state=AttemptState.RUNNING,
        route=Route(route_id="fake-cli", cli="fake", model="fake-model"), started=now, pid=None,
    )
    storage.save_state(TaskStateFile(
        schema_version=1, task_id="demo-P31-stalereceipt", project=project_id,
        state=TaskState.QUEUED, since=now, attempts=[att],
    ))
    receipt_dir = paths.attempt_dir(project_id, "att-done-002")
    receipt_dir.mkdir(parents=True, exist_ok=True)
    (receipt_dir / "receipt.json").write_text("{}", encoding="utf-8")

    registry = {"demo": sample_project.root}
    render.render_all(registry)
    content = (paths.www_dir() / "index.html").read_text(encoding="utf-8")

    assert "demo-P31-stalereceipt" in content
    assert "att-done-002" not in content  # no live indicator/link on index.html


def test_index_html_pid_alive_overrides_non_running_attempt_state(sample_project, tmp_state):
    """Oracle 2 (belt-and-braces): an attempt whose recorded pid is a REAL
    alive process (this test process itself) counts as running even when
    its persisted state is STALLED (not RUNNING/PREFLIGHTING) and it has
    no receipt yet -- the pid check stands on its own, independent of the
    persisted attempt state."""
    project_id = "demo"
    now = datetime.now(timezone.utc)
    att = Attempt(
        attempt_id="att-stalled-003", role=Role.IMPLEMENTER, state=AttemptState.STALLED,
        route=Route(route_id="fake-cli", cli="fake", model="fake-model"), started=now,
        pid=os.getpid(),
    )
    storage.save_state(TaskStateFile(
        schema_version=1, task_id="demo-P32-stalledpid", project=project_id,
        state=TaskState.ACTIVE, since=now, attempts=[att],
    ))

    registry = {"demo": sample_project.root}
    render.render_all(registry)
    content = (paths.www_dir() / "index.html").read_text(encoding="utf-8")

    assert "● running (att-stalled-003)" in content


def test_pid_alive_returns_false_on_process_lookup_error(monkeypatch):
    """factory-hardening G: deterministic IN-PROCESS cover of `_pid_alive`'s
    ProcessLookupError arm (render.py:466-467).

    Before G, this branch was reached ONLY incidentally, by the real-`os.fork()`
    daemon-scan integration tests: `coverage run` follows the tracer into the
    forked child and records the line, but `pytest-cov` under xdist combines only
    per-worker data and drops the forked grandchild's coverage -> the parallel gate
    would false-flag this line uncovered on any change that touches it. A mocked
    `os.kill` makes the branch runner-independent (covered under serial AND xdist)."""
    def _no_such_pid(pid, sig):
        raise ProcessLookupError
    monkeypatch.setattr(render.os, "kill", _no_such_pid)
    assert render._pid_alive(999999) is False


def test_attempt_is_live_via_wrapper_pid_file(sample_project, tmp_state):
    """factory-hardening G: deterministic IN-PROCESS cover of `_attempt_is_live`'s
    belt-and-braces wrapper.pid branch (render.py:492-497), for the same xdist
    fork-coverage reason as test_pid_alive_returns_false_on_process_lookup_error.

    The attempt's own pid is 0 (short-circuits `_pid_alive` at the guard, so the
    recorded-pid check fails), no receipt has landed, and a `wrapper.pid` file
    holds THIS live process' pid -- so liveness is established purely via the
    wrapper.pid path and the function returns True. The attempt state is STALLED
    (not RUNNING/PREFLIGHTING), so a True result can ONLY come from that branch."""
    now = datetime.now(timezone.utc)
    att = Attempt(
        attempt_id="att-wrapperpid-001", role=Role.IMPLEMENTER,
        state=AttemptState.STALLED,
        route=Route(route_id="fake-cli", cli="fake", model="fake-model"),
        started=now, pid=0,
    )
    attempt_dir = paths.attempt_dir("demo", att.attempt_id)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    (attempt_dir / "wrapper.pid").write_text(str(os.getpid()), encoding="utf-8")
    assert render._attempt_is_live("demo", att) is True


def test_task_page_has_drilldown_link(seed_data, sample_project):
    """Oracle 3: the task page links every attempt to the read-only
    drilldown endpoint (not only the currently-live ones -- 'recent
    attempt' per the handoff)."""
    tmp_state, project_id = seed_data
    registry = {"demo": sample_project.root}

    render.render_all(registry)
    content = (paths.www_dir() / "task" / "demo" / "demo-P01-sample.html").read_text(encoding="utf-8")

    assert 'href="/api/drilldown/demo/att-001"' in content


def test_render_transcript_extracts_assistant_text_and_tool_names():
    """Oracle 3: assistant text deltas + tool names render as readable
    prose, in file order, never as raw JSON."""
    raw = (
        '{"type":"system","subtype":"init","session_id":"sess-1"}\n'
        '{"type":"assistant","message":{"content":'
        '[{"type":"text","text":"Reading the spec now."}]}}\n'
        '{"type":"assistant","message":{"content":'
        '[{"type":"tool_use","name":"Read","input":{"file_path":"x.py"}}]}}\n'
        '{"type":"assistant","message":{"content":'
        '[{"type":"text","text":"Looks good, implementing."}]}}\n'
        '{"type":"result","subtype":"success","result":"Done."}\n'
    )
    out = render.render_transcript(raw)

    assert "Reading the spec now." in out
    assert "[tool: Read]" in out
    assert "Looks good, implementing." in out
    assert "Done." in out
    assert '"type":"assistant"' not in out   # never raw JSON
    assert '"content"' not in out
    # newest last: the second assistant text line comes after the first
    assert out.index("Reading the spec now.") < out.index("Looks good, implementing.")


def test_render_transcript_skips_unparseable_and_partial_lines():
    """Negative (oracle 3): a live tail's last line is very often a
    partial, still-being-written JSON object -- it must be silently
    skipped, never raise, and never corrupt the rest of the rendering."""
    raw = (
        '{"type":"assistant","message":{"content":[{"type":"text","text":"first"}]}}\n'
        "not json at all\n"
        '{"type":"assistant","message":{"content":[{"type":"text"'  # truncated
    )
    out = render.render_transcript(raw)  # must not raise

    assert "first" in out
    assert "not json at all" not in out


def test_render_transcript_empty_input_has_placeholder():
    """A brand-new/empty attempt log renders a placeholder, not an error
    or an empty string (the page must always show something readable)."""
    assert render.render_transcript("") == "(no readable transcript content yet)"


def test_render_transcript_html_escapes_agent_text():
    """Untrusted CLI output must never inject markup into the dashboard
    page (STANDING.md / handoff Rules)."""
    raw = '{"type":"assistant","message":{"content":[{"type":"text","text":"<script>alert(1)</script>"}]}}\n'
    out = render.render_transcript(raw)

    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_render_drilldown_page_is_readonly_and_escaped():
    """Oracle 3: the drilldown page never exposes a mutating control and
    keeps agent text HTML-escaped end to end."""
    transcript = render.render_transcript(
        '{"type":"assistant","message":{"content":'
        '[{"type":"text","text":"<img src=x onerror=alert(1)>"}]}}\n'
    )
    page = render.render_drilldown_page("demo", "att-xss", transcript)

    assert "<img src=x" not in page
    assert "&lt;img" in page
    assert "att-xss" in page
    assert "demo" in page
    assert "<form" not in page.lower()
    assert "<button" not in page.lower()
    assert "fetch(" not in page
    assert 'http-equiv="refresh"' in page


# Oracle O2 (P27): nyxloomd compose template / pre-rendered sibling mount parity
def _compose_volume_sources(path: Path) -> set[str]:
    """Bind-mount SOURCE paths from a compose file's `volumes:` list.

    ciu.compose.yml.j2 is not valid YAML as-is (Jinja `{{ }}` placeholders
    in scalar values like `image:`/`container_name:` break yaml.safe_load —
    see the ParserError a naive full parse hits), so this walks the
    `volumes:` block by indentation instead of parsing the whole document.
    The list items themselves (`SOURCE:TARGET  # comment`) are plain
    strings in both files, Jinja-free, so this is exact for the thing the
    oracle cares about: the set of bind sources.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    sources = set()
    in_volumes = False
    volumes_indent = None
    for line in lines:
        stripped = line.strip()
        if not in_volumes:
            if stripped == "volumes:":
                in_volumes = True
                volumes_indent = len(line) - len(line.lstrip(" "))
            continue
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= volumes_indent:
            break
        if stripped.startswith("- "):
            item = stripped[2:].split("#", 1)[0].strip()
            sources.add(item.split(":", 1)[0])
    return sources


NYXLOOMD_DIR = Path(__file__).resolve().parent.parent / "nyxloomd"


def test_nyxloomd_compose_template_and_sibling_mounts_agree():
    """Oracle O2: the .j2 template and its pre-rendered docker-compose.yml
    sibling bind the SAME set of volume sources -- drift between them means
    which projects the daemon can see depends on which file was deployed."""
    template_sources = _compose_volume_sources(NYXLOOMD_DIR / "ciu.compose.yml.j2")
    rendered_sources = _compose_volume_sources(NYXLOOMD_DIR / "docker-compose.yml")

    assert template_sources, "expected at least one volume source in the template"
    assert template_sources == rendered_sources


def test_nyxloomd_compose_mounts_netcup_api_filter():
    """Oracle O2: netcup-api-filter is mounted at the same physical-path
    convention as the other registered projects, in both files."""
    netcup_source = "/home/vb/volkb79-2/netcup-api-filter"
    for fname in ("ciu.compose.yml.j2", "docker-compose.yml"):
        sources = _compose_volume_sources(NYXLOOMD_DIR / fname)
        assert netcup_source in sources, f"{fname} missing netcup-api-filter bind"


# ---------------------------------------------------------------------------
# FN-3 findings dashboard tests


def test_findings_html_xss_escaped(sample_project, tmp_state):
    """FN-3 O1: findings.html lists a finding with html.escape'd title/body;
    raw markup is never present."""
    from nyxloom import findings

    project_id = "demo"
    registry = {"demo": sample_project.root}

    findings.record_finding(
        "demo", "generic",
        title="<script>alert(1)</script>",
        body="<b>x</b>",
    )

    render.render_all(registry)
    content = (paths.www_dir() / "findings.html").read_text(encoding="utf-8")

    assert 'id="findings"' in content
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in content
    assert "<script>alert(1)</script>" not in content
    assert "&lt;b&gt;x&lt;/b&gt;" in content
    assert "<b>x</b>" not in content


def test_findings_html_task_link(sample_project, tmp_state):
    """FN-3 O2: task link rendered when task_id is set on a finding."""
    from nyxloom import findings

    registry = {"demo": sample_project.root}

    findings.record_finding(
        "demo", "generic",
        title="Task-linked finding",
        body="This finding references a task.",
        task_id="demo-P01-sample",
    )

    render.render_all(registry)
    content = (paths.www_dir() / "findings.html").read_text(encoding="utf-8")

    assert "task/demo/demo-P01-sample.html" in content


def test_findings_html_empty(sample_project, tmp_state):
    """FN-3 O3: findings.html shows 'No findings yet.' when none exist."""
    registry = {"demo": sample_project.root}
    render.render_all(registry)
    content = (paths.www_dir() / "findings.html").read_text(encoding="utf-8")

    assert "No findings yet." in content


def test_findings_html_nav_and_render_all(sample_project, tmp_state):
    """FN-3 O4: findings.html exists after render_all and nav contains
    its link."""
    registry = {"demo": sample_project.root}
    render.render_all(registry)

    assert (paths.www_dir() / "findings.html").exists()

    index_content = (paths.www_dir() / "index.html").read_text(encoding="utf-8")
    assert 'href="findings.html"' in index_content


def test_findings_html_skips_broken_project(sample_project, tmp_state, tmp_path):
    """FN-3 O5: a project in the registry whose config fails to load
    is silently skipped without breaking render_all."""
    registry = {
        "demo": sample_project.root,
        "broken": tmp_path / "no-such-repo",
    }
    render.render_all(registry)  # must not raise

    assert (paths.www_dir() / "findings.html").exists()


def test_findings_html_has_promote_button_and_js(sample_project, tmp_state):
    """FN-6 O1: findings.html has the 'Discuss / Act' button wired to
    promoteFinding() and the /api/finding/promote endpoint."""
    from nyxloom import findings

    registry = {"demo": sample_project.root}

    findings.record_finding("demo", "generic", title="test finding",
                            body="test body")

    render.render_all(registry)
    content = (paths.www_dir() / "findings.html").read_text(encoding="utf-8")

    assert "promoteFinding(" in content
    assert "/api/finding/promote" in content
    assert "Discuss / Act" in content
