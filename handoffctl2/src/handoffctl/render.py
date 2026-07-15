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

from pathlib import Path


HOURS = 48


def render_all(registry: dict[str, Path]) -> Path:
    raise NotImplementedError


def render_after_event(registry: dict[str, Path]) -> Path:
    raise NotImplementedError
