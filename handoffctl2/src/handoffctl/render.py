"""Static dashboard renderer (ARCHITECTURE §7, SPEC §12). PACKAGE P05.

Regenerates paths.www_dir() from statefiles/events/config. Pure stdlib HTML
generation; NO external assets (no CDN, no JS frameworks — inline CSS and,
where needed, minimal inline vanilla JS only for the SSE live page). Every
log excerpt passes cfg.redact() BEFORE it is written under www/. Raw logs
are never copied or linked.

INTERFACE CONTRACT (frozen):

- render_all(registry: dict[str, Path]) -> Path: writes the whole site,
  returns www dir. Idempotent; stale task pages for deleted tasks removed.
- Pages and their REQUIRED content (tests assert on marker strings /
  element ids listed below):
    index.html      id="active-tasks" table: one row per non-terminal task:
                    project, task_id (link to task page), state, current
                    attempt route_id, started, minutes since statefile
                    'since', cost-so-far as '<sum> <CCY> (<basis-mix>)'
                    where basis-mix is 'actual'/'estimated'/'mixed'/'unknown',
                    leases_held, notes. Plus id="pause-banner" div when any
                    project pause flag exists; id="decisions-open" count;
                    id="budget" summary per project.
    history.html    id="history" table of terminal + MERGED/VALIDATING
                    tasks: task, final state, merge_commit, progress_units,
                    total cost.
    dag.html        id="dag" — dependency edges rendered as an HTML nested
                    list per project: each task li carries class
                    'state-<STATE>' and lists 'depends on: <ids>' and
                    'mutex: <names>'; plus an edges table (from, to, kind
                    in {dep, mutex, decision}). (No graphviz/mermaid — CSS
                    coloring by state class is the visualization.)
    timeline.html   id="timeline" — one div.lane per task with div.bar
                    children per attempt, width proportional to duration,
                    title attribute '<attempt_id> <route> <state>'; CSS
                    grid over a time axis of the last 48h (configurable
                    constant HOURS=48).
    quality.html    id="quality" table per (tier, route_id): attempts,
                    exited-done, blocked, limit, error, interrupted counts,
                    total + mean cost. Data from all projects' statefiles.
    task/<project>/<task_id>.html
                    frontmatter table (every field), the handoff body
                    rendered as <pre> (NO markdown rendering — injection
                    surface), attempts table (route, state, started/ended,
                    session_handle, receipt result, usage incl. basis),
                    gate results, id="log-excerpt" <pre> with the REDACTED
                    last 64KB of the newest attempt log, decisions
                    referenced, events tail (last 50 for this task).
    live.html       minimal SSE client: JS EventSource('/api/stream?...')
                    appending events to a <pre>; degrade gracefully when
                    served from file:// (show note).
- All pages share one inline CSS block (COLORS constant maps TaskState ->
  color) and a nav header; valid HTML5; every dynamic string passes
  html.escape.
- render_after_event(registry) — cheap alias for render_all (the daemon
  calls it after passes with events; full regeneration IS the design at
  pilot scale).
"""

from __future__ import annotations

import html
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from . import paths, storage, config
from .types import TaskState, TaskStateFile, AttemptState, Basis


HOURS = 48

COLORS = {
    TaskState.DRAFT: "#cccccc",
    TaskState.NEEDS_DECISION: "#ff9900",
    TaskState.READY_TO_CARVE: "#ffcc00",
    TaskState.CARVED: "#ffff00",
    TaskState.QUEUED: "#ffff99",
    TaskState.ACTIVE: "#00ff00",
    TaskState.AWAITING_REVIEW: "#0099ff",
    TaskState.REVIEW_REJECTED: "#ff6600",
    TaskState.MERGE_READY: "#00ccff",
    TaskState.MERGED: "#0066ff",
    TaskState.VALIDATING: "#0033ff",
    TaskState.COMPLETED: "#00ff99",
    TaskState.BLOCKED: "#ff0000",
    TaskState.SUPERSEDED: "#999999",
    TaskState.CANCELLED: "#666666",
}

CSS = f"""
<style>
body {{ font-family: sans-serif; margin: 20px; background: #14171a; color: #d6dde3; }}
table {{ border-collapse: collapse; margin: 20px 0; }}
th, td {{ border: 1px solid #333a41; padding: 8px; text-align: left; }}
th {{ background: #1d2126; }}
a {{ color: #6ab0ff; }}
.state-DRAFT {{ background: #cccccc; }}
.state-NEEDS_DECISION {{ background: #ff9900; }}
.state-READY_TO_CARVE {{ background: #ffcc00; }}
.state-CARVED {{ background: #ffff00; }}
.state-QUEUED {{ background: #ffff99; }}
.state-ACTIVE {{ background: #00ff00; }}
.state-AWAITING_REVIEW {{ background: #0099ff; }}
.state-REVIEW_REJECTED {{ background: #ff6600; }}
.state-MERGE_READY {{ background: #00ccff; }}
.state-MERGED {{ background: #0066ff; }}
.state-VALIDATING {{ background: #0033ff; }}
.state-COMPLETED {{ background: #00ff99; }}
.state-BLOCKED {{ background: #ff0000; }}
.state-SUPERSEDED {{ background: #999999; }}
.state-CANCELLED {{ background: #666666; }}
nav {{ background: #1b1f24; padding: 10px; margin: -20px -20px 20px -20px; }}
nav a {{ margin: 0 10px; }}
#pause-banner {{ background: #3a1214; border: 2px solid #c0392b; padding: 10px; margin: 10px 0; }}
.lane {{ margin: 20px 0; padding: 10px; border: 1px solid #333a41; }}
.bar {{ display: inline-block; margin: 0 2px; padding: 2px; background: #2f6fb3; color: #eaf2fa; }}
#log-excerpt {{ background: #0e1114; padding: 10px; border: 1px solid #333a41; overflow-x: auto; color: #b8c4cc; }}
[class^="state-"], [class*=" state-"] {{ color: #101214; }}
pre {{ color: #b8c4cc; }}
</style>
"""

NAV = """
<nav>
  <a href="index.html">Dashboard</a> |
  <a href="history.html">History</a> |
  <a href="dag.html">DAG</a> |
  <a href="timeline.html">Timeline</a> |
  <a href="quality.html">Quality</a> |
  <a href="live.html">Live</a>
</nav>
"""


def _html_head(title: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>{html.escape(title)}</title>
  {CSS}
</head>
<body>
{NAV}
<h1>{html.escape(title)}</h1>
"""


def _html_foot() -> str:
    return """</body>
</html>
"""


def _cost_string(attempts: list[Any]) -> tuple[str, str]:
    """Aggregate cost from all attempts, return (cost_str, basis_mix)."""
    by_currency = defaultdict(float)
    bases = set()

    for att in attempts:
        if att.usage and att.usage.cost is not None:
            ccy = att.usage.currency or "USD"
            by_currency[ccy] += att.usage.cost
            bases.add(att.usage.basis.value)

    if not by_currency:
        return "unknown", "unknown"

    if len(bases) == 1:
        basis_mix = list(bases)[0]
    elif len(bases) > 1:
        basis_mix = "mixed"
    else:
        basis_mix = "unknown"

    parts = []
    for ccy in sorted(by_currency.keys()):
        cost = by_currency[ccy]
        parts.append(f"{cost:.2f} {ccy}")

    cost_str = " + ".join(parts) if len(parts) > 1 else parts[0] if parts else "0.00 USD"
    return cost_str, basis_mix


def _is_non_terminal(state: TaskState) -> bool:
    """Check if state is non-terminal (not COMPLETED, SUPERSEDED, CANCELLED, MERGED, VALIDATING)."""
    return state not in {TaskState.COMPLETED, TaskState.SUPERSEDED, TaskState.CANCELLED,
                         TaskState.MERGED, TaskState.VALIDATING}


def _is_terminal_or_validating(state: TaskState) -> bool:
    """Check if state is terminal or MERGED/VALIDATING."""
    return (state in {TaskState.COMPLETED, TaskState.SUPERSEDED, TaskState.CANCELLED}
            or state in {TaskState.MERGED, TaskState.VALIDATING})


def render_all(registry: dict[str, Path]) -> Path:
    """Render all HTML pages under www_dir(); return www_dir path."""
    www = paths.www_dir()
    www.mkdir(parents=True, exist_ok=True)

    # Load all statefiles from all projects
    all_states = {}
    for project in registry.keys():
        states = storage.list_states(project)
        all_states[project] = states

    # Render index.html
    _render_index(www, registry, all_states)

    # Render history.html
    _render_history(www, all_states)

    # Render dag.html
    _render_dag(www, registry, all_states)

    # Render timeline.html
    _render_timeline(www, all_states)

    # Render quality.html
    _render_quality(www, registry, all_states)

    # Render task pages
    for project in registry.keys():
        states = all_states.get(project, {})
        (www / "task" / project).mkdir(parents=True, exist_ok=True)
        for task_id, tsf in states.items():
            _render_task_page(www, project, tsf, registry[project])

    # Render live.html
    _render_live(www)

    # Remove stale task pages
    _clean_stale_pages(www, all_states)

    return www


def render_after_event(registry: dict[str, Path]) -> Path:
    """Cheap alias for render_all."""
    return render_all(registry)


def _render_index(www: Path, registry: dict[str, Path], all_states: dict[str, dict[str, TaskStateFile]]) -> None:
    """Render index.html with active tasks and pause banner."""
    rows = []
    all_paused = set()

    # Check if any project has a pause flag
    for project in registry.keys():
        if paths.pause_flag(project).exists():
            all_paused.add(project)

    for project in sorted(registry.keys()):
        states = all_states.get(project, {})
        for task_id in sorted(states.keys()):
            tsf = states[task_id]
            if _is_non_terminal(tsf.state):
                # Get current attempt
                att = tsf.current_attempt()
                route_id = att.route.route_id if att else "—"
                started = att.started.isoformat() if att else "—"

                # Calculate minutes since 'since'
                now = datetime.now(timezone.utc)
                since = tsf.since
                minutes = int((now - since).total_seconds() / 60)

                # Cost string
                cost_str, basis_mix = _cost_string(tsf.attempts)

                # Leases
                leases = ", ".join(tsf.leases_held) if tsf.leases_held else "—"

                # Notes
                notes = tsf.notes or "—"

                rows.append(f"""
                  <tr>
                    <td>{html.escape(project)}</td>
                    <td><a href="task/{html.escape(project)}/{html.escape(task_id)}.html">{html.escape(task_id)}</a></td>
                    <td>{html.escape(tsf.state.value)}</td>
                    <td>{html.escape(route_id)}</td>
                    <td>{html.escape(started)}</td>
                    <td>{minutes}</td>
                    <td>{html.escape(cost_str)} ({html.escape(basis_mix)})</td>
                    <td>{html.escape(leases)}</td>
                    <td>{html.escape(notes)}</td>
                  </tr>
                """)

    pause_banner = ""
    if all_paused:
        pause_banner = f'<div id="pause-banner">Paused: {html.escape(", ".join(sorted(all_paused)))}</div>'

    content = f"""
    {pause_banner}
    <table id="active-tasks">
      <thead>
        <tr>
          <th>Project</th>
          <th>Task</th>
          <th>State</th>
          <th>Route</th>
          <th>Started</th>
          <th>Minutes</th>
          <th>Cost</th>
          <th>Leases</th>
          <th>Notes</th>
        </tr>
      </thead>
      <tbody>
        {"".join(rows) if rows else '<tr><td colspan="9">No active tasks</td></tr>'}
      </tbody>
    </table>
    """

    html_content = _html_head("Dashboard") + content + _html_foot()
    (www / "index.html").write_text(html_content, encoding="utf-8")


def _render_history(www: Path, all_states: dict[str, dict[str, TaskStateFile]]) -> None:
    """Render history.html with terminal and validating tasks."""
    rows = []

    for project in sorted(all_states.keys()):
        states = all_states[project]
        for task_id in sorted(states.keys()):
            tsf = states[task_id]
            if _is_terminal_or_validating(tsf.state):
                merge_commit = tsf.merge_commit or "—"
                if merge_commit != "—":
                    merge_commit = merge_commit[:7]
                progress_units = ", ".join(tsf.progress_units) if tsf.progress_units else "—"
                cost_str, _ = _cost_string(tsf.attempts)

                rows.append(f"""
                  <tr>
                    <td>{html.escape(project)}/{html.escape(task_id)}</td>
                    <td>{html.escape(tsf.state.value)}</td>
                    <td>{html.escape(merge_commit)}</td>
                    <td>{html.escape(progress_units)}</td>
                    <td>{html.escape(cost_str)}</td>
                  </tr>
                """)

    content = f"""
    <table id="history">
      <thead>
        <tr>
          <th>Task</th>
          <th>State</th>
          <th>Merge Commit</th>
          <th>Progress Units</th>
          <th>Total Cost</th>
        </tr>
      </thead>
      <tbody>
        {"".join(rows) if rows else '<tr><td colspan="5">No history</td></tr>'}
      </tbody>
    </table>
    """

    html_content = _html_head("History") + content + _html_foot()
    (www / "history.html").write_text(html_content, encoding="utf-8")


def _render_dag(www: Path, registry: dict[str, Path], all_states: dict[str, dict[str, TaskStateFile]]) -> None:
    """Render dag.html with dependency graph and edges."""
    # Collect all edges
    edges = []

    for project in sorted(registry.keys()):
        states = all_states.get(project, {})
        for task_id in sorted(states.keys()):
            tsf = states[task_id]
            # Add dependency edges
            for dep in tsf.frontmatter.task_deps() if hasattr(tsf, 'frontmatter') else []:
                edges.append((task_id, dep, "dep"))
            # Add mutex edges
            for mutex_name in (tsf.frontmatter.effective_mutexes() if hasattr(tsf, 'frontmatter') else []):
                edges.append((task_id, f"{project}.{mutex_name}", "mutex"))

    # Build DAG tree
    dag_html = ""
    for project in sorted(all_states.keys()):
        states = all_states[project]
        dag_html += f"<h2>{html.escape(project)}</h2>\n<ul>\n"

        for task_id in sorted(states.keys()):
            tsf = states[task_id]
            state_class = f"state-{tsf.state.value}"

            # Get dependencies
            deps = []
            mutexes = []
            if hasattr(tsf, 'frontmatter'):
                deps = tsf.frontmatter.task_deps()
                mutexes = tsf.frontmatter.effective_mutexes()

            deps_str = ", ".join(deps) if deps else ""
            mutexes_str = ", ".join(mutexes) if mutexes else ""

            detail = ""
            if deps_str:
                detail += f"; depends on: {html.escape(deps_str)}"
            if mutexes_str:
                detail += f"; mutex: {html.escape(mutexes_str)}"

            dag_html += f'<li class="{state_class}">{html.escape(task_id)}{detail}</li>\n'

        dag_html += "</ul>\n"

    # Edges table
    edges_rows = []
    for from_id, to_id, kind in sorted(edges):
        edges_rows.append(f"""
          <tr>
            <td>{html.escape(from_id)}</td>
            <td>{html.escape(to_id)}</td>
            <td>{html.escape(kind)}</td>
          </tr>
        """)

    content = f"""
    <div id="dag">
      {dag_html}
      <h2>Edges</h2>
      <table>
        <thead>
          <tr>
            <th>From</th>
            <th>To</th>
            <th>Kind</th>
          </tr>
        </thead>
        <tbody>
          {"".join(edges_rows) if edges_rows else '<tr><td colspan="3">No edges</td></tr>'}
        </tbody>
      </table>
    </div>
    """

    html_content = _html_head("DAG") + content + _html_foot()
    (www / "dag.html").write_text(html_content, encoding="utf-8")


def _render_timeline(www: Path, all_states: dict[str, dict[str, TaskStateFile]]) -> None:
    """Render timeline.html with task attempt bars."""
    # Determine time window (last HOURS hours)
    now = datetime.now(timezone.utc)
    time_start = now - timedelta(hours=HOURS)

    lanes = []
    for project in sorted(all_states.keys()):
        states = all_states[project]
        for task_id in sorted(states.keys()):
            tsf = states[task_id]

            bars = []
            for att in tsf.attempts:
                if att.started < time_start:
                    continue

                start = att.started
                end = att.ended or now

                # Duration as percentage of window
                window_seconds = (now - time_start).total_seconds()
                start_offset = (start - time_start).total_seconds() / window_seconds * 100
                duration = (end - start).total_seconds() / window_seconds * 100

                title = f"{att.attempt_id} {att.route.route_id} {att.state.value}"
                bars.append(f'<div class="bar" style="width: {max(1, duration)}%; margin-left: {start_offset}%;" title="{html.escape(title)}"></div>')

            if bars:
                lanes.append(f"""
                  <div class="lane">
                    <strong>{html.escape(task_id)}</strong>
                    {"".join(bars)}
                  </div>
                """)

    content = f"""
    <div id="timeline">
      <p>Last {HOURS} hours</p>
      {"".join(lanes) if lanes else '<p>No attempts</p>'}
    </div>
    """

    html_content = _html_head("Timeline") + content + _html_foot()
    (www / "timeline.html").write_text(html_content, encoding="utf-8")


def _render_quality(www: Path, registry: dict[str, Path], all_states: dict[str, dict[str, TaskStateFile]]) -> None:
    """Render quality.html with stats per tier/route."""
    # Load routes to get tiers
    try:
        routes = config.Routes.load()
    except Exception:
        routes = None

    # Collect stats
    stats = defaultdict(lambda: {
        "attempts": 0,
        "exited_done": 0,
        "blocked": 0,
        "limit": 0,
        "error": 0,
        "interrupted": 0,
        "cost": 0.0,
    })

    for project in all_states.keys():
        states = all_states[project]
        for task_id in sorted(states.keys()):
            tsf = states[task_id]
            for att in tsf.attempts:
                route_id = att.route.route_id
                key = route_id

                stats[key]["attempts"] += 1

                if att.state == AttemptState.EXITED:
                    if att.receipt and att.receipt.result.value == "done":
                        stats[key]["exited_done"] += 1
                elif att.state == AttemptState.FAILED:
                    stats[key]["error"] += 1
                elif att.state == AttemptState.INTERRUPTED:
                    stats[key]["interrupted"] += 1

                if att.usage and att.usage.cost:
                    stats[key]["cost"] += att.usage.cost

    rows = []
    for key in sorted(stats.keys()):
        s = stats[key]
        mean_cost = s["cost"] / s["attempts"] if s["attempts"] > 0 else 0
        rows.append(f"""
          <tr>
            <td>{html.escape(key)}</td>
            <td>{s["attempts"]}</td>
            <td>{s["exited_done"]}</td>
            <td>{s["blocked"]}</td>
            <td>{s["limit"]}</td>
            <td>{s["error"]}</td>
            <td>{s["interrupted"]}</td>
            <td>{s["cost"]:.2f}</td>
            <td>{mean_cost:.2f}</td>
          </tr>
        """)

    content = f"""
    <table id="quality">
      <thead>
        <tr>
          <th>Route</th>
          <th>Attempts</th>
          <th>Done</th>
          <th>Blocked</th>
          <th>Limit</th>
          <th>Error</th>
          <th>Interrupted</th>
          <th>Total Cost</th>
          <th>Mean Cost</th>
        </tr>
      </thead>
      <tbody>
        {"".join(rows) if rows else '<tr><td colspan="9">No data</td></tr>'}
      </tbody>
    </table>
    """

    html_content = _html_head("Quality") + content + _html_foot()
    (www / "quality.html").write_text(html_content, encoding="utf-8")


def _render_task_page(www: Path, project: str, tsf: TaskStateFile, root: Path) -> None:
    """Render a task/<project>/<task_id>.html page."""
    task_dir = www / "task" / project
    task_dir.mkdir(parents=True, exist_ok=True)

    # Frontmatter table
    frontmatter = ""
    if hasattr(tsf, 'frontmatter'):
        fm_rows = []
        for key in ['id', 'project', 'title', 'tier', 'state']:
            val = getattr(tsf, key, None) or getattr(tsf.frontmatter, key, "")
            fm_rows.append(f"<tr><td>{html.escape(key)}</td><td>{html.escape(str(val))}</td></tr>")
        frontmatter = f"""
        <h2>Frontmatter</h2>
        <table>
          {"".join(fm_rows)}
        </table>
        """

    # Handoff body (read from handoff_path if available)
    handoff_body = "Handoff file missing"
    if tsf.handoff_path:
        handoff_file = root / tsf.handoff_path
        if handoff_file.exists():
            try:
                content = handoff_file.read_text(encoding="utf-8")
                # Extract body (after frontmatter)
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    handoff_body = parts[2].strip()
                else:
                    handoff_body = content
            except Exception:
                handoff_body = "Error reading handoff file"

    # Attempts table
    attempts_rows = []
    for att in tsf.attempts:
        receipt_result = att.receipt.result.value if att.receipt else "—"
        usage_str = f"{att.usage.basis.value}" if att.usage else "—"

        attempts_rows.append(f"""
          <tr>
            <td>{html.escape(att.route.route_id)}</td>
            <td>{html.escape(att.state.value)}</td>
            <td>{html.escape(att.started.isoformat())}</td>
            <td>{html.escape(att.ended.isoformat()) if att.ended else "—"}</td>
            <td>{html.escape(att.session_handle or "—")}</td>
            <td>{html.escape(receipt_result)}</td>
            <td>{html.escape(usage_str)}</td>
          </tr>
        """)

    # Log excerpt
    log_excerpt = "No log"
    for att in reversed(tsf.attempts):
        if att.log_path:
            try:
                log_file = Path(att.log_path)
                if log_file.exists():
                    content = log_file.read_bytes()
                    if len(content) > 65536:
                        content = content[-65536:]
                    log_text = content.decode("utf-8", errors="replace")
                    cfg = config.ProjectConfig.load(root)
                    log_excerpt = cfg.redact(log_text)
                    break
            except Exception:
                pass

    content = f"""
    {frontmatter}
    <h2>Handoff</h2>
    <pre>{html.escape(handoff_body)}</pre>
    <h2>Attempts</h2>
    <table>
      <thead>
        <tr>
          <th>Route</th>
          <th>State</th>
          <th>Started</th>
          <th>Ended</th>
          <th>Session</th>
          <th>Receipt</th>
          <th>Basis</th>
        </tr>
      </thead>
      <tbody>
        {"".join(attempts_rows) if attempts_rows else '<tr><td colspan="7">No attempts</td></tr>'}
      </tbody>
    </table>
    <h2>Log</h2>
    <pre id="log-excerpt">{html.escape(log_excerpt)}</pre>
    """

    html_content = _html_head(f"Task: {tsf.task_id}") + content + _html_foot()
    (task_dir / f"{tsf.task_id}.html").write_text(html_content, encoding="utf-8")


def _render_live(www: Path) -> None:
    """Render live.html with SSE client."""
    content = """
    <h2>Live Stream</h2>
    <p>This page streams events from the handoffctl daemon via Server-Sent Events (SSE).</p>
    <pre id="events"></pre>
    <script>
    if (window.location.protocol === 'file:') {
        document.getElementById('events').textContent = 'Note: This page must be served over HTTP to receive live events.';
    } else {
        const eventSource = new EventSource('/api/stream');
        eventSource.onmessage = function(event) {
            document.getElementById('events').textContent += event.data + '\\n';
            document.getElementById('events').scrollTop = document.getElementById('events').scrollHeight;
        };
        eventSource.onerror = function() {
            document.getElementById('events').textContent += '\\n[Connection closed]\\n';
        };
    }
    </script>
    """

    html_content = _html_head("Live") + content + _html_foot()
    (www / "live.html").write_text(html_content, encoding="utf-8")


def _clean_stale_pages(www: Path, all_states: dict[str, dict[str, TaskStateFile]]) -> None:
    """Remove task pages for tasks that no longer exist."""
    task_dir = www / "task"
    if not task_dir.exists():
        return

    # Build set of current task pages
    current_pages = set()
    for project in all_states.keys():
        states = all_states[project]
        for task_id in states.keys():
            current_pages.add((project, task_id))

    # Remove stale pages
    for project_dir in task_dir.iterdir():
        if not project_dir.is_dir():
            continue
        project = project_dir.name

        for task_file in project_dir.iterdir():
            if not task_file.is_file():
                continue
            task_id = task_file.stem

            if (project, task_id) not in current_pages:
                task_file.unlink()
