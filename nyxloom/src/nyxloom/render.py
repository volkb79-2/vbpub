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
                    leases_held, notes, last-activity age (P15 2026-07-15:
                    from the newest of the task's attempt logs' mtime at
                    render time; '-' when no log — see _format_age /
                    _newest_attempt_log_age). Plus id="pause-banner" div
                    when any project pause flag exists (now shows each
                    paused project's mode too); id="decisions-open" count;
                    id="budget" summary per project.
                    P16 2026-07-15 (carver automation, user directive): an
                    id="carve-toggle" checkbox (default OFF, vanilla JS
                    classList.toggle — mirrors live.html's raw-JSON toggle)
                    interleaves persisted CarveSummary rows (read directly
                    off disk via _load_carve_summaries — never replayed
                    from events.jsonl) among the task rows, both ordered by
                    a comparable timestamp (task rows: tsf.since; carve
                    rows: the daemon-written 'timestamp' field). Each carve
                    row carries class="carve-row" and CSS hides it by
                    default (`#active-tasks .carve-row { display: none }`,
                    revealed only via `#active-tasks.show-carves
                    .carve-row`) — both rows are always present in the
                    static markup (this renderer has no per-request state
                    to conditionally omit them), the toggle only affects
                    client-side visibility, same mechanism as live.html's
                    raw-JSON toggle. Shows: carved ids, the reflection text
                    (html-escaped), headroom estimate, outcome.
    history.html    id="history" table of terminal + MERGED/VALIDATING
                    tasks: task, final state, merge_commit, progress_units,
                    total cost.
    dag.html        id="dag" — inline SVG dependency graph (pure-python
                    Kahn-level layout; no graphviz/JS libs): task nodes as
                    rounded <rect data-node="<id>"> colored by COLORS,
                    carrying class 'state-<STATE>' (dark text); dependency
                    and mutex-resource edges drawn as arrowed <line>
                    elements (class 'edge-dep' solid / 'edge-mutex' dashed;
                    an edge broken to resolve a cycle also gets class
                    'edge-broken' and red stroke). Edges are parsed per
                    task via frontmatter.parse_handoff(root / handoff_path)
                    (root = registry[project]); a task whose handoff file
                    fails to parse is skipped for edges but its node still
                    renders. An edges table (from, to, kind in {dep, mutex,
                    decision}) follows the SVG.
    timeline.html   id="timeline" — one div.lane per task with div.bar
                    children per attempt, absolutely positioned/sized over
                    a shared auto-fit axis (window = min(earliest attempt
                    start, now-1h) to now, padded 5%; TIMELINE_WIDTH_PX
                    wide with tick labels HH:MM). Bar width is floored at
                    MIN_BAR_WIDTH_PX; title attribute '<attempt_id> <route>
                    <state>'; the route id is also rendered inside the bar
                    once it is wider than 60px. A lane with no attempts in
                    the window shows a "No activity in window" note instead
                    of an empty track.
    quality.html    id="quality" table per (tier, route_id): attempts,
                    exited-done, blocked, limit, error, interrupted counts,
                    total + mean cost. Data from all projects' statefiles.
    task/<project>/<task_id>.html
                    frontmatter table (every field), the handoff body
                    rendered as <pre> (NO markdown rendering — injection
                    surface), attempts table (route, state, started/ended,
                    session_handle, receipt result, usage incl. basis,
                    last-activity age — P15 2026-07-15, see below),
                    gate results, id="log-excerpt" <pre> with the REDACTED
                    last 64KB of the newest attempt log, decisions
                    referenced, events tail (last 50 for this task).
    config.html     P15 2026-07-15 (spec amendment, user directive): per-
                    project policy form (current values for the 9 editable
                    Policy keys — 7 int, P16 2026-07-15 adds 2 more int
                    (carve_ahead_target, headroom_warn); one fetch POST
                    /api/config/policy per Save click, plain vanilla JS,
                    page reload on success), Run /
                    Drain handoffs / Drain agents buttons per project
                    showing the current pause mode (fetch POST /api/config/
                    pause), a carve-authority select (branch/main/files,
                    P16 2026-07-15, same POST /api/config/policy endpoint
                    with key='carve_authority'), and a routing tiers table
                    (current tier ->
                    routes, editable, fetch POST /api/config/tier) followed
                    by a READ-ONLY route-definitions table (cli/model/
                    variant/effort/status). No inline secrets, no innerHTML
                    (fetch + textContent-safe static markup only). Carries a
                    visible hint that a routes.toml edit only changes the
                    LIVE state file, not the tracked nyxloom/routes.
                    host.toml copy.
    live.html       SSE client: JS EventSource('/api/stream') (no project
                    param — the server defaults to the first registered
                    project) parses each event and appends one row built
                    via textContent (never innerHTML): 'HH:MM:SS TYPE
                    task_id key-detail'. TICK_ERROR rows are red,
                    TASK_TRANSITIONED green, ATTEMPT_* blue. A raw toggle
                    reveals the original JSON line alongside the parsed
                    row. The view auto-scrolls to the newest event unless
                    the user has scrolled up. Degrades gracefully when
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
import json
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import paths, storage, config, frontmatter
from .types import TaskState, TaskStateFile, AttemptState, Basis, Frontmatter


# --- timeline layout constants ---
TIMELINE_WIDTH_PX = 960
MIN_BAR_WIDTH_PX = 6
TIMELINE_TICKS = 5

# --- DAG layout constants ---
NODE_W = 160
NODE_H = 36
H_GAP = 70
V_GAP = 50
DAG_MARGIN = 20

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
.carve-row {{ display: none; background: #1f2a1a; }}
#active-tasks.show-carves .carve-row {{ display: table-row; }}
.lane {{ margin: 20px 0; padding: 10px; border: 1px solid #333a41; }}
.lane-track {{ background: #1a1e22; }}
.bar {{ position: absolute; top: 2px; height: 20px; min-width: 6px; padding: 2px;
       background: #2f6fb3; color: #eaf2fa; box-sizing: border-box; overflow: hidden;
       white-space: nowrap; font-size: 11px; }}
.bar-label {{ pointer-events: none; }}
.no-activity {{ color: #7a8894; font-style: italic; margin: 4px 0; }}
.axis {{ font-size: 11px; color: #8a98a5; margin-bottom: 6px; }}
.tick {{ white-space: nowrap; }}
#log-excerpt {{ background: #0e1114; padding: 10px; border: 1px solid #333a41; overflow-x: auto; color: #b8c4cc; }}
[class^="state-"], [class*=" state-"] {{ color: #101214; }}
pre {{ color: #b8c4cc; }}
svg .mutex-node {{ fill: #3a3f45; stroke: #14171a; }}
svg text {{ font-family: sans-serif; }}
#events {{ font-family: monospace; font-size: 12px; background: #0e1114; border: 1px solid #333a41;
          padding: 10px; height: 480px; overflow-y: auto; white-space: pre-wrap; }}
.evt-row {{ display: block; }}
.evt-tick-error {{ color: #ff5555; }}
.evt-task-transitioned {{ color: #4caf50; }}
.evt-attempt {{ color: #6ab0ff; }}
.evt-raw {{ display: none; color: #7a8894; }}
#events.show-raw .evt-fmt {{ display: none; }}
#events.show-raw .evt-raw {{ display: inline; }}
</style>
"""

NAV = """
<nav>
  <a href="index.html">Dashboard</a> |
  <a href="history.html">History</a> |
  <a href="dag.html">DAG</a> |
  <a href="timeline.html">Timeline</a> |
  <a href="quality.html">Quality</a> |
  <a href="live.html">Live</a> |
  <a href="config.html">Config</a>
</nav>
"""

# P15 2026-07-15: factory-state pause modes (mirrors daemon.Daemon._pause_mode
# and reconcile.py's pause-mode contract). Duplicated here in miniature
# rather than imported from daemon.py to avoid a render<->daemon import
# cycle (daemon.py already imports render); paths.py/config.py are frozen
# so there is no shared home for this three-line mapping.
def _pause_mode_for(project: str) -> str:
    p = paths.pause_flag(project)
    if not p.exists():
        return "run"
    try:
        content = p.read_text(encoding="utf-8").strip()
    except OSError:
        content = ""
    return "drain-agents" if content == "drain-agents" else "drain-handoffs"


def _format_age(seconds: float | None) -> str:
    """Human units per the P15 handoff's examples: '3m', '2h05m'; '-' when
    seconds is None (no log file found)."""
    if seconds is None:
        return "-"
    total_minutes = int(max(0.0, seconds) // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours > 0:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def _attempt_log_age_seconds(project: str, att: Any, now_ts: float) -> float | None:
    """Age (seconds) of one attempt's own log file mtime; None if the log
    doesn't exist. Mirrors daemon.Daemon._attempt_scan's log-path fallback
    (att.log_path if set, else attempt_dir/'attempt.log') so both surfaces
    agree on which file represents an attempt's activity."""
    attempt_dir = paths.attempt_dir(project, att.attempt_id)
    log_path = Path(att.log_path) if att.log_path else (attempt_dir / "attempt.log")
    if not log_path.exists():
        return None
    return max(0.0, now_ts - log_path.stat().st_mtime)


def _newest_attempt_log_age(project: str, attempts: list[Any], now_ts: float) -> float | None:
    """Per-task last-activity: the FRESHEST (smallest age) log mtime across
    all of the task's attempts; None if none of them have a log file yet."""
    ages = [a for a in (
        _attempt_log_age_seconds(project, att, now_ts) for att in attempts
    ) if a is not None]
    return min(ages) if ages else None


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

    # Render config.html (P15 2026-07-15)
    _render_config(www, registry)

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


def _load_carve_summaries(project: str) -> list[dict[str, Any]]:
    """P16 2026-07-15: read persisted CarveSummary artifacts (daemon-written
    JSON files under $XDG_STATE/nyxloom/<project>/carves/*.json — see
    daemon.py's _consume_carve_exit) for the index.html interleave toggle.
    Tolerant of a missing dir or an unparsable file (skip it, never raise —
    a broken/partial carve artifact must not take the whole dashboard
    down)."""
    carves_dir = paths.project_dir(project) / "carves"
    out: list[dict[str, Any]] = []
    if not carves_dir.exists():
        return out
    for p in sorted(carves_dir.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def _parse_carve_timestamp(value: Any) -> datetime:
    """Best-effort ISO-8601 parse for a persisted carve summary's
    'timestamp' field; falls back to now() (tz-aware) on anything
    unparsable so a malformed timestamp still sorts (near the end) instead
    of crashing the render."""
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _render_carve_row(project: str, summary: dict[str, Any]) -> str:
    """One <tr class="carve-row"> spanning the active-tasks table's 10
    columns: carved ids, the reflection (html-escaped), headroom estimate,
    outcome. Hidden by default via CSS; the show-carves toggle reveals it
    (see _render_index)."""
    seq = summary.get("seq", "?")
    carved = summary.get("carved") or []
    carved_ids = ", ".join(str(c.get("id", "?")) for c in carved) if carved else "—"
    reflection = summary.get("review_reflection") or ""
    headroom = summary.get("headroom_estimate", "—")
    outcome = summary.get("outcome", "—")
    return f"""
      <tr class="carve-row" data-carve-seq="{html.escape(str(seq))}" data-carve-project="{html.escape(project)}">
        <td colspan="10">
          <strong>Carve #{html.escape(str(seq))}</strong> ({html.escape(project)}) —
          outcome: {html.escape(str(outcome))}, headroom: {html.escape(str(headroom))}<br>
          carved: {html.escape(carved_ids)}<br>
          <em>{html.escape(reflection)}</em>
        </td>
      </tr>
    """


def _render_index(www: Path, registry: dict[str, Path], all_states: dict[str, dict[str, TaskStateFile]]) -> None:
    """Render index.html with active tasks, pause banner, and (P16
    2026-07-15) an opt-in carve-summary interleave — see the module
    docstring's index.html section for the full contract."""
    row_entries: list[tuple[datetime, str]] = []
    all_paused: dict[str, str] = {}   # project -> pause mode

    # Check if any project has a pause flag
    for project in registry.keys():
        if paths.pause_flag(project).exists():
            all_paused[project] = _pause_mode_for(project)

    now_ts = datetime.now(timezone.utc).timestamp()

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

                # P15 2026-07-15: per-agent last-activity age (newest of the
                # task's attempt logs' mtime at render time).
                last_activity = _format_age(_newest_attempt_log_age(project, tsf.attempts, now_ts))

                row_html = f"""
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
                    <td>{html.escape(last_activity)}</td>
                  </tr>
                """
                row_entries.append((since, row_html))

        # P16 2026-07-15: interleave this project's persisted carve
        # summaries, positioned by their own timestamp among the task rows.
        for summary in _load_carve_summaries(project):
            ts = _parse_carve_timestamp(summary.get("timestamp"))
            row_entries.append((ts, _render_carve_row(project, summary)))

    row_entries.sort(key=lambda pair: pair[0])
    rows = [row_html for _ts, row_html in row_entries]

    pause_banner = ""
    if all_paused:
        parts = [f"{project} ({mode})" for project, mode in sorted(all_paused.items())]
        pause_banner = f'<div id="pause-banner">Paused: {html.escape(", ".join(parts))}</div>'

    content = f"""
    {pause_banner}
    <p><label><input type="checkbox" id="carve-toggle"> Show carve summaries</label></p>
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
          <th>Last Activity</th>
        </tr>
      </thead>
      <tbody>
        {"".join(rows) if rows else '<tr><td colspan="10">No active tasks</td></tr>'}
      </tbody>
    </table>
    <script>
    document.getElementById('carve-toggle').addEventListener('change', function() {{
        document.getElementById('active-tasks').classList.toggle('show-carves', this.checked);
    }});
    </script>
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


def _load_frontmatter(root: Path, tsf: TaskStateFile) -> Frontmatter | None:
    """Parse a task's handoff frontmatter (root = registry[project]).

    Returns None (never raises) when handoff_path is unset, the file is
    missing, or it fails to parse — callers must still render the task's
    node without deps/mutexes in that case.
    """
    if not tsf.handoff_path:
        return None
    handoff_file = root / tsf.handoff_path
    if not handoff_file.exists():
        return None
    try:
        fm, _body = frontmatter.parse_handoff(handoff_file)
        return fm
    except Exception:
        return None


def _dag_levels(
    nodes: set[str], edges: list[tuple[str, str]]
) -> tuple[dict[str, int], set[tuple[str, str]]]:
    """Kahn-style layered topological levels for a from->to edge list.

    `from` is placed at a level strictly less than `to`. Terminates in
    O(V+E) regardless of cycles: any node left unreached once the queue
    drains is part of a cycle, is pinned to level 0, and each of its
    inbound edges is reported in the returned `broken` set (render red;
    the cycle must never hang the layout).
    """
    succ: dict[str, list[str]] = defaultdict(list)
    indeg: dict[str, int] = {n: 0 for n in nodes}
    real_edges: list[tuple[str, str]] = []
    for f, t in edges:
        if f not in nodes or t not in nodes or f == t:
            continue
        succ[f].append(t)
        indeg[t] += 1
        real_edges.append((f, t))

    level: dict[str, int] = {n: 0 for n in nodes}
    remaining = dict(indeg)
    queue: deque[str] = deque(sorted(n for n in nodes if remaining[n] == 0))
    visited = set(queue)

    while queue:
        n = queue.popleft()
        for s in succ[n]:
            if level[s] < level[n] + 1:
                level[s] = level[n] + 1
            remaining[s] -= 1
            if remaining[s] == 0 and s not in visited:
                visited.add(s)
                queue.append(s)

    broken: set[tuple[str, str]] = set()
    if len(visited) < len(nodes):
        for n in sorted(nodes - visited):
            level[n] = 0
            for f, t in real_edges:
                if t == n:
                    broken.add((f, t))

    return level, broken


def _render_dag_svg(
    nodes: set[str],
    node_state: dict[str, TaskState],
    mutex_nodes: set[str],
    edges: list[tuple[str, str, str]],
    levels: dict[str, int],
    broken: set[tuple[str, str]],
) -> str:
    """Layered SVG: rounded-rect nodes, arrowed edges (dep solid/mutex
    dashed; cycle-broken edges red)."""
    if not nodes:
        return "<p>No tasks.</p>"

    by_level: dict[int, list[str]] = defaultdict(list)
    for n in nodes:
        by_level[levels.get(n, 0)].append(n)
    for names in by_level.values():
        names.sort()

    max_level = max(by_level)
    max_rows = max(len(v) for v in by_level.values())

    width = DAG_MARGIN * 2 + (max_level + 1) * NODE_W + max_level * H_GAP
    height = DAG_MARGIN * 2 + max_rows * NODE_H + max(0, max_rows - 1) * V_GAP

    pos: dict[str, tuple[int, int]] = {}
    for lvl, names in by_level.items():
        x = DAG_MARGIN + lvl * (NODE_W + H_GAP)
        for row, name in enumerate(names):
            y = DAG_MARGIN + row * (NODE_H + V_GAP)
            pos[name] = (x, y)

    parts = [f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">']
    parts.append(
        '<defs>'
        '<marker id="dag-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" '
        'markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
        'fill="#6ab0ff"></path></marker>'
        '<marker id="dag-arrow-red" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" '
        'markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
        'fill="#ff5555"></path></marker>'
        '</defs>'
    )

    # Edges first so nodes draw on top.
    for f, t, kind in edges:
        if f not in pos or t not in pos:
            continue
        x1, y1 = pos[f]
        x2, y2 = pos[t]
        x1c, y1c = x1 + NODE_W, y1 + NODE_H / 2
        x2c, y2c = x2, y2 + NODE_H / 2
        is_broken = (f, t) in broken
        stroke = "#ff5555" if is_broken else "#6ab0ff"
        marker = "dag-arrow-red" if is_broken else "dag-arrow"
        dash = ' stroke-dasharray="6,4"' if kind == "mutex" else ""
        css_class = "edge-mutex" if kind == "mutex" else "edge-dep"
        if is_broken:
            css_class += " edge-broken"
        parts.append(
            f'<line class="{css_class}" x1="{x1c:.1f}" y1="{y1c:.1f}" '
            f'x2="{x2c:.1f}" y2="{y2c:.1f}" stroke="{stroke}" stroke-width="2"{dash} '
            f'marker-end="url(#{marker})"></line>'
        )

    for n in sorted(nodes):
        x, y = pos[n]
        if n in mutex_nodes:
            fill = "#3a3f45"
            state_class = "mutex-node"
            text_color = "#d6dde3"
        elif n in node_state:
            state = node_state[n]
            fill = COLORS.get(state, "#888888")
            state_class = f"state-{state.value}"
            text_color = "#101214"
        else:
            fill = "#555555"
            state_class = "external-node"
            text_color = "#d6dde3"
        label = html.escape(n)
        parts.append(
            f'<rect data-node="{html.escape(n)}" class="{state_class}" x="{x}" y="{y}" '
            f'width="{NODE_W}" height="{NODE_H}" rx="8" ry="8" fill="{fill}" '
            f'stroke="#14171a"></rect>'
        )
        parts.append(
            f'<text x="{x + NODE_W / 2:.1f}" y="{y + NODE_H / 2 + 4:.1f}" '
            f'text-anchor="middle" font-size="12" fill="{text_color}">{label}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def _render_dag(www: Path, registry: dict[str, Path], all_states: dict[str, dict[str, TaskStateFile]]) -> None:
    """Render dag.html: SVG dependency graph (layered layout) + edges table.

    Edges come from each task's OWN handoff frontmatter (parsed via
    frontmatter.parse_handoff), never from the statefile, which carries no
    dependency data.
    """
    edges: list[tuple[str, str, str]] = []
    node_state: dict[str, TaskState] = {}
    mutex_nodes: set[str] = set()

    for project in sorted(registry.keys()):
        root = registry[project]
        states = all_states.get(project, {})
        for task_id in sorted(states.keys()):
            tsf = states[task_id]
            node_state[task_id] = tsf.state

            fm = _load_frontmatter(root, tsf)
            if fm is None:
                continue
            for dep in fm.task_deps():
                edges.append((task_id, dep, "dep"))
            for mutex_name in fm.effective_mutexes():
                mutex_node = f"{project}.{mutex_name}"
                mutex_nodes.add(mutex_node)
                edges.append((task_id, mutex_node, "mutex"))

    dep_targets = {t for _, t, kind in edges if kind == "dep"}
    external_nodes = dep_targets - set(node_state) - mutex_nodes
    all_nodes = set(node_state) | mutex_nodes | external_nodes

    levels, broken = _dag_levels(all_nodes, [(f, t) for f, t, _kind in edges])
    svg = _render_dag_svg(all_nodes, node_state, mutex_nodes, edges, levels, broken)

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
      {svg}
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


def _timeline_axis(all_states: dict[str, dict[str, TaskStateFile]], now: datetime) -> tuple[datetime, datetime]:
    """Auto-fit axis window: min(earliest attempt start, now-1h) to now,
    padded 5% on both ends. Never inverted, even with a clock-skewed or
    empty statefile set."""
    earliest: datetime | None = None
    for states in all_states.values():
        for tsf in states.values():
            for att in tsf.attempts:
                if earliest is None or att.started < earliest:
                    earliest = att.started

    floor = now - timedelta(hours=1)
    axis_start_raw = min(earliest, floor) if earliest is not None else floor
    if axis_start_raw > now:
        axis_start_raw = floor

    span = (now - axis_start_raw).total_seconds()
    if span <= 0:
        span = 3600.0
    pad = span * 0.05

    return axis_start_raw - timedelta(seconds=pad), now + timedelta(seconds=pad)


def _render_timeline(www: Path, all_states: dict[str, dict[str, TaskStateFile]]) -> None:
    """Render timeline.html: one lane per task, attempt bars absolutely
    positioned over a shared auto-fit axis (see _timeline_axis)."""
    now = datetime.now(timezone.utc)
    axis_start, axis_end = _timeline_axis(all_states, now)
    axis_span = (axis_end - axis_start).total_seconds()

    def px(dt: datetime) -> float:
        frac = (dt - axis_start).total_seconds() / axis_span
        frac = max(0.0, min(1.0, frac))
        return frac * TIMELINE_WIDTH_PX

    ticks = []
    for i in range(TIMELINE_TICKS):
        frac = i / (TIMELINE_TICKS - 1)
        tick_dt = axis_start + timedelta(seconds=frac * axis_span)
        tick_px = frac * TIMELINE_WIDTH_PX
        ticks.append(
            f'<span class="tick" style="position: absolute; left: {tick_px:.1f}px;">'
            f'{html.escape(tick_dt.strftime("%H:%M"))}</span>'
        )
    axis_row = (
        f'<div class="axis" style="position: relative; height: 20px; '
        f'width: {TIMELINE_WIDTH_PX}px;">{"".join(ticks)}</div>'
    )

    lanes = []
    for project in sorted(all_states.keys()):
        states = all_states[project]
        for task_id in sorted(states.keys()):
            tsf = states[task_id]

            bars = []
            for att in tsf.attempts:
                end = att.ended or now
                if end < axis_start or att.started > axis_end:
                    continue

                start_px = px(att.started)
                end_px = px(end)
                width_px = max(float(MIN_BAR_WIDTH_PX), end_px - start_px)

                title = f"{att.attempt_id} {att.route.route_id} {att.state.value}"
                label = ""
                if width_px > 60:
                    label = f'<span class="bar-label">{html.escape(att.route.route_id)}</span>'

                bars.append(
                    f'<div class="bar" style="left: {start_px:.1f}px; '
                    f'width: {width_px:.1f}px;" title="{html.escape(title)}">{label}</div>'
                )

            if bars:
                track_body = "".join(bars)
            else:
                track_body = '<p class="no-activity">No activity in window</p>'

            lanes.append(f"""
              <div class="lane">
                <strong>{html.escape(task_id)}</strong>
                <div class="lane-track" style="position: relative; height: 24px; width: {TIMELINE_WIDTH_PX}px;">
                  {track_body}
                </div>
              </div>
            """)

    content = f"""
    <div id="timeline">
      <p>Window: {html.escape(axis_start.strftime("%Y-%m-%d %H:%M"))} to
         {html.escape(axis_end.strftime("%Y-%m-%d %H:%M"))} UTC</p>
      {axis_row}
      {"".join(lanes) if lanes else '<p>No tasks</p>'}
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


# P15 2026-07-15: the 7 Policy keys the UI is allowed to edit (mirrors
# daemon._POLICY_BOUNDS' key set; render.py has no import on daemon.py, so
# this is the render-side copy of the same editable-key list — bounds
# themselves are validated server-side, never trusted from this page).
# P16 2026-07-15: 2 more int keys (carve_ahead_target, headroom_warn).
# carve_authority (the one STRING-valued key) is rendered separately as a
# <select> below, not through this numeric-input list.
_EDITABLE_POLICY_KEYS = [
    "max_active_tasks", "ready_queue_target", "max_attempts_per_task",
    "wave_max_diffs", "stall_log_quiet_seconds", "attempt_max_wall_seconds",
    "reconcile_interval_seconds", "carve_ahead_target", "headroom_warn",
]

# P16 2026-07-15: carve_authority's 3 valid values (mirrors
# daemon._CARVE_AUTHORITIES; render.py has no import on daemon.py).
_CARVE_AUTHORITIES = ["branch", "main", "files"]

_CONFIG_JS = """
<script>
function postJSON(url, body, onDone) {
    fetch(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
    }).then(function(resp) {
        if (resp.ok) {
            window.location.reload();
            return;
        }
        resp.json().then(function(data) {
            onDone(data.error || ('http ' + resp.status));
        }).catch(function() {
            onDone('http ' + resp.status);
        });
    }).catch(function(err) {
        onDone(String(err));
    });
}

function savePolicy(project, key) {
    var input = document.getElementById('policy-' + project + '-' + key);
    var value = parseInt(input.value, 10);
    postJSON('/api/config/policy', {project: project, key: key, value: value}, function(err) {
        alert('policy update failed: ' + err);
    });
}

function setPauseMode(project, mode) {
    postJSON('/api/config/pause', {project: project, mode: mode}, function(err) {
        alert('pause update failed: ' + err);
    });
}

function saveCarveAuthority(project) {
    var select = document.getElementById('carve-authority-' + project);
    postJSON('/api/config/policy', {project: project, key: 'carve_authority', value: select.value}, function(err) {
        alert('carve authority update failed: ' + err);
    });
}

function saveTier(tier) {
    var input = document.getElementById('tier-' + tier);
    var routes = input.value.split(',').map(function(s) { return s.trim(); })
        .filter(function(s) { return s.length > 0; });
    postJSON('/api/config/tier', {tier: tier, routes: routes}, function(err) {
        alert('tier update failed: ' + err);
    });
}
</script>
"""


def _render_config(www: Path, registry: dict[str, Path]) -> None:
    """Render config.html: per-project policy form + pause-mode buttons,
    plus a global routing-tiers editor and a read-only route-definitions
    table (P15 2026-07-15 spec amendment)."""
    project_sections = []
    for project in sorted(registry.keys()):
        root = registry[project]
        try:
            cfg = config.ProjectConfig.load(root)
        except Exception:
            continue

        policy_rows = []
        for key in _EDITABLE_POLICY_KEYS:
            value = getattr(cfg.policy, key, None)
            if value is None:
                continue
            input_id = f"policy-{project}-{key}"
            policy_rows.append(f"""
              <tr>
                <td>{html.escape(key)}</td>
                <td><input type="number" id="{html.escape(input_id)}" value="{html.escape(str(value))}"></td>
                <td><button type="button" onclick="savePolicy('{html.escape(project)}', '{html.escape(key)}')">Save</button></td>
              </tr>
            """)

        mode = _pause_mode_for(project)
        current_authority = getattr(cfg.policy, "carve_authority", "branch")
        authority_options = "".join(
            f'<option value="{html.escape(a)}"'
            + (" selected" if a == current_authority else "")
            + f'>{html.escape(a)}</option>'
            for a in _CARVE_AUTHORITIES
        )
        project_sections.append(f"""
          <div class="config-project" data-project="{html.escape(project)}">
            <h2>{html.escape(project)}</h2>
            <p>Factory state: <strong>{html.escape(mode)}</strong></p>
            <p>
              <button type="button" onclick="setPauseMode('{html.escape(project)}', 'run')">Run</button>
              <button type="button" onclick="setPauseMode('{html.escape(project)}', 'drain-handoffs')">Drain handoffs</button>
              <button type="button" onclick="setPauseMode('{html.escape(project)}', 'drain-agents')">Drain agents</button>
            </p>
            <p>Carve authority:
              <select id="carve-authority-{html.escape(project)}">{authority_options}</select>
              <button type="button" onclick="saveCarveAuthority('{html.escape(project)}')">Save</button>
            </p>
            <table>
              <thead><tr><th>Policy key</th><th>Value</th><th></th></tr></thead>
              <tbody>{"".join(policy_rows) if policy_rows else '<tr><td colspan="3">No editable keys</td></tr>'}</tbody>
            </table>
          </div>
        """)

    try:
        routes_obj = config.Routes.load()
    except Exception:
        routes_obj = None

    tier_rows = []
    route_def_rows = []
    if routes_obj is not None:
        for tier in sorted(routes_obj.tiers.keys()):
            route_ids = routes_obj.tiers[tier]
            joined = ", ".join(route_ids)
            tier_rows.append(f"""
              <tr>
                <td>{html.escape(tier)}</td>
                <td><input type="text" id="tier-{html.escape(tier)}" size="60" value="{html.escape(joined)}"></td>
                <td><button type="button" onclick="saveTier('{html.escape(tier)}')">Save</button></td>
              </tr>
            """)
        for route_id in sorted(routes_obj.routes.keys()):
            r = routes_obj.routes[route_id]
            route_def_rows.append(f"""
              <tr>
                <td>{html.escape(route_id)}</td>
                <td>{html.escape(r.cli)}</td>
                <td>{html.escape(r.model)}</td>
                <td>{html.escape(r.variant or "—")}</td>
                <td>{html.escape(r.effort or "—")}</td>
                <td>{html.escape(r.status or "—")}</td>
              </tr>
            """)

    content = f"""
    {"".join(project_sections) if project_sections else '<p>No registered projects.</p>'}
    <h2>Routing tiers</h2>
    <table id="tiers-table">
      <thead><tr><th>Tier</th><th>Routes (comma-separated route ids)</th><th></th></tr></thead>
      <tbody>{"".join(tier_rows) if tier_rows else '<tr><td colspan="3">No tiers</td></tr>'}</tbody>
    </table>
    <h2>Route definitions (read-only)</h2>
    <table id="route-defs">
      <thead><tr><th>Route</th><th>CLI</th><th>Model</th><th>Variant</th><th>Effort</th><th>Status</th></tr></thead>
      <tbody>{"".join(route_def_rows) if route_def_rows else '<tr><td colspan="6">No routes</td></tr>'}</tbody>
    </table>
    <p><em>Note: routing edits above change ONLY the live state file
    (routes.toml) — the tracked copy nyxloom/routes.host.toml may now
    differ; sync it in git when satisfied.</em></p>
    {_CONFIG_JS}
    """

    html_content = _html_head("Config") + content + _html_foot()
    (www / "config.html").write_text(html_content, encoding="utf-8")


def _render_task_page(www: Path, project: str, tsf: TaskStateFile, root: Path) -> None:
    """Render a task/<project>/<task_id>.html page."""
    task_dir = www / "task" / project
    task_dir.mkdir(parents=True, exist_ok=True)

    # Frontmatter table (P13 review-fix: TaskStateFile never had a
    # .frontmatter attribute — parse the handoff file like dag.html does).
    frontmatter_html = ""
    if tsf.handoff_path:
        try:
            fm, _body = frontmatter.parse_handoff(root / tsf.handoff_path)
            fm_rows = [
                f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>"
                for k, v in fm.to_dict().items()
            ]
            frontmatter_html = f"""
        <h2>Frontmatter</h2>
        <table>
          {"".join(fm_rows)}
        </table>
        """
        except Exception:
            frontmatter_html = "<p><em>handoff frontmatter unavailable</em></p>"

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
    now_ts = datetime.now(timezone.utc).timestamp()
    attempts_rows = []
    for att in tsf.attempts:
        receipt_result = att.receipt.result.value if att.receipt else "—"
        usage_str = f"{att.usage.basis.value}" if att.usage else "—"
        # P15 2026-07-15: per-attempt last-activity age (own log's mtime;
        # cheap -- one stat per attempt).
        last_activity = _format_age(_attempt_log_age_seconds(project, att, now_ts))

        attempts_rows.append(f"""
          <tr>
            <td>{html.escape(att.route.route_id)}</td>
            <td>{html.escape(att.state.value)}</td>
            <td>{html.escape(att.started.isoformat())}</td>
            <td>{html.escape(att.ended.isoformat()) if att.ended else "—"}</td>
            <td>{html.escape(att.session_handle or "—")}</td>
            <td>{html.escape(receipt_result)}</td>
            <td>{html.escape(usage_str)}</td>
            <td>{html.escape(last_activity)}</td>
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
    {frontmatter_html}
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
          <th>Last Activity</th>
        </tr>
      </thead>
      <tbody>
        {"".join(attempts_rows) if attempts_rows else '<tr><td colspan="8">No attempts</td></tr>'}
      </tbody>
    </table>
    <h2>Log</h2>
    <pre id="log-excerpt">{html.escape(log_excerpt)}</pre>
    """

    html_content = _html_head(f"Task: {tsf.task_id}") + content + _html_foot()
    (task_dir / f"{tsf.task_id}.html").write_text(html_content, encoding="utf-8")


def _render_live(www: Path) -> None:
    """Render live.html: SSE client that parses each event into one
    human-readable row (never innerHTML) plus a raw-JSON toggle."""
    content = """
    <h2>Live Stream</h2>
    <p>This page streams events from the nyxloom daemon via Server-Sent Events (SSE).</p>
    <p><label><input type="checkbox" id="raw-toggle"> Show raw JSON</label></p>
    <div id="events"></div>
    <script>
    function fmtTime(iso) {
        var d = new Date(iso);
        if (isNaN(d.getTime())) { return String(iso); }
        function pad(n) { return (n < 10 ? '0' : '') + n; }
        return pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()) + ':' + pad(d.getUTCSeconds());
    }

    function keyDetail(ev) {
        var payload = ev.payload || {};
        if (ev.type === 'TASK_TRANSITIONED') {
            return payload.to || '';
        }
        if (typeof ev.type === 'string' && ev.type.indexOf('ATTEMPT_') === 0) {
            var attempt = payload.attempt || {};
            return attempt.state || '';
        }
        if (ev.type === 'SPEC_ATTENTION') {
            return payload.reason || '';
        }
        if (ev.type === 'TICK_ERROR') {
            var err = payload.error || '';
            return err.length > 80 ? err.slice(0, 80) : err;
        }
        return '';
    }

    function rowClass(type) {
        if (type === 'TICK_ERROR') { return 'evt-tick-error'; }
        if (type === 'TASK_TRANSITIONED') { return 'evt-task-transitioned'; }
        if (typeof type === 'string' && type.indexOf('ATTEMPT_') === 0) { return 'evt-attempt'; }
        return '';
    }

    function appendEvent(raw) {
        var container = document.getElementById('events');
        var pinned = (container.scrollTop + container.clientHeight) >= (container.scrollHeight - 4);

        var ev = null;
        try {
            ev = JSON.parse(raw);
        } catch (e) {
            ev = null;
        }

        var row = document.createElement('div');
        row.className = 'evt-row ' + rowClass(ev ? ev.type : undefined);

        var fmt = document.createElement('span');
        fmt.className = 'evt-fmt';
        if (ev) {
            fmt.textContent = fmtTime(ev.timestamp) + '  ' + ev.type + '  ' +
                (ev.task_id || '') + '  ' + keyDetail(ev);
        } else {
            fmt.textContent = raw;
        }

        var rawSpan = document.createElement('span');
        rawSpan.className = 'evt-raw';
        rawSpan.textContent = raw;

        row.appendChild(fmt);
        row.appendChild(rawSpan);
        container.appendChild(row);

        if (pinned) {
            container.scrollTop = container.scrollHeight;
        }
    }

    if (window.location.protocol === 'file:') {
        var note = document.createElement('p');
        note.textContent = 'Note: This page must be served over HTTP to receive live events.';
        document.getElementById('events').appendChild(note);
    } else {
        var eventSource = new EventSource('/api/stream');
        eventSource.onmessage = function(event) {
            appendEvent(event.data);
        };
        eventSource.onerror = function() {
            var row = document.createElement('div');
            row.className = 'evt-row';
            row.textContent = '[Connection closed]';
            document.getElementById('events').appendChild(row);
        };
    }

    document.getElementById('raw-toggle').addEventListener('change', function() {
        document.getElementById('events').classList.toggle('show-raw', this.checked);
    });
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
