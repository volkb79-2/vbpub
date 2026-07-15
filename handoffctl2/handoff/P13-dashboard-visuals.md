# P13 — dashboard visuals: real DAG, usable timeline, readable live page

> Tier: sonnet · Date: 2026-07-15 · Source: live pwmcp inspection with the
> user (screenshots confirmed). Read handoff/STANDING.md first. You own
> src/handoffctl/render.py and tests/test_render.py ONLY.

## Findings to fix (all confirmed live)

1. **BUG — DAG shows "No edges"** while groop has real dependencies
   (groop-P92→P91, P94→P90, P95→P91+P93, P77→P73). Root-cause first: the
   edge builder almost certainly reads statefiles (which carry no deps)
   instead of parsing each task's frontmatter via
   statefile.handoff_path + the project root from the registry
   (frontmatter.parse_handoff; skip files that fail to parse). Mutex edges
   likewise from fm.effective_mutexes().
2. **DAG is a flat colored list** — replace with a real graph: an inline
   SVG, layered topological layout (Kahn levels left→right; nodes as
   rounded rects colored by the existing COLORS state map with dark text;
   orthogonal or straight edge lines with arrowheads; dependency edges
   solid, mutex edges dashed). Pure-python layout — no graphviz/JS libs
   (self-contained rule stands). Keep the edges table below the SVG.
   Cycles (shouldn't happen) must not hang the layout — break arbitrarily
   and mark the edge red.
3. **Timeline bars invisible**: the axis is fixed 48h, so 30-minute-old
   attempts render as ~4px slivers pinned right. Fix: axis window =
   from min(first attempt start, now-1h) to now, padded 5%; bars get
   min-width 6px; add axis tick labels (HH:MM, 4-6 ticks) and per-bar
   start→end tooltips (existing title attr) plus the route id INSIDE the
   bar when width > 60px. Lanes for tasks with no attempts in-window:
   render a subtle "no activity in window" note instead of an empty box.
4. **Live page is a raw JSON firehose**: keep SSE but parse each event
   client-side (vanilla JS, inline): render as one row per event —
   `HH:MM:SS  TYPE  task_id  key-detail` (key-detail: payload.to for
   transitions, attempt state for ATTEMPT_*, reason for SPEC_ATTENTION,
   first 80 chars of payload.error for TICK_ERROR — payload text is
   pre-redacted server-side but STILL html-escape everything client-side
   via textContent, never innerHTML). Color TICK_ERROR red,
   TASK_TRANSITIONED green, ATTEMPT_* blue. Add a "raw" toggle that shows
   the original JSON line. Auto-scroll pinned to bottom unless the user
   scrolled up.

## Oracles (extend tests/test_render.py; keep all existing tests green)

1. Edge extraction: seed two tasks whose handoff files carry
   depends_on [other] and a mutex -> dag.html contains an `<svg` element,
   a dependency edge (assert a marker like `class="edge-dep"` count >= 1)
   and the edges table rows (from/to/kind) for both dep and mutex kinds.
   Negative: task with unparseable handoff file -> page still renders,
   node present, no crash.
2. Layout sanity: A->B dependency places A's node x strictly left of B's
   (parse the two rect x= values).
3. Timeline: attempt started 10 min ago -> its bar width >= 6px AND the
   axis label row contains at least 4 tick labels; empty-window lane shows
   the no-activity note.
4. Live page: contains an html-escaping-safe renderer (assert
   `textContent` usage in the inline JS and NO `innerHTML` except for the
   static skeleton), the raw toggle string, and the EventSource URL keeps
   working without a project param.
5. Idempotence + escaping tests from P05 must still pass unchanged.

## Rules

STANDING.md applies (gate: pytest tests/test_render.py -q, then full
suite). Do not touch daemon.py/frontmatter.py (frontmatter.parse_handoff is
implemented — import and use it). Do not commit. REPORT to
handoff/reports/P13-REPORT.md; receipt-only final message.
