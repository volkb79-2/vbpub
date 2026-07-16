# P22 — dashboard: state legend + read-only agent drilldown (live attach) — Implementation Report

**Status:** done · **Date:** 2026-07-16

## Summary

Implements all three work items from `nyxloom-trove/handoffs/P22-dashboard-drilldown-legend.md`.
Zero new AI/token use; the dashboard remains read-only (no state mutation
anywhere in the new code paths).

1. **State legend** (`src/nyxloom/render.py`). Three module-level dicts —
   `STATE_LEGEND: dict[TaskState, str]` (one plain-language entry per
   `TaskState` enum member, keyed by the enum itself so a future state
   without a matching entry raises `KeyError` rather than rendering
   silently blank), `BLOCKER_TYPE_LEGEND: dict[BlockerType, str]` (same
   enum-keyed pattern for the six `BlockerType` categories), and
   `BLOCKER_REASON_LEGEND: dict[str, str]` (free-text `tsf.notes` /
   `blocker.detail` reasons that aren't enum-backed — `interrupted-dead-end`
   and `attempts exhausted`, the two literal strings actually produced by
   `reconcile.py`/`daemon.py` today). New `_render_legend_html()` renders
   all three as tables inside an always-visible `<div id="state-legend">`
   (not a collapsible `<details>` — "always-visible" per the handoff), and
   `_render_index` prepends it to `index.html`.
2. **"Nothing running" fix** (`src/nyxloom/render.py`). New
   `_attempt_is_live(project, att)`: an attempt counts as live when its
   `receipt.json` has not landed on disk AND either its recorded pid (or
   the freshest `wrapper.pid` on disk, belt-and-braces like
   `daemon.py::_attempt_scan`) answers alive via a new `_pid_alive`
   (duplicated from `daemon.py`'s identical helper — same
   render&lt;-&gt;daemon import-cycle reason already documented above
   `_pause_mode_for`), OR its own persisted state is already
   `RUNNING`/`PREFLIGHTING`. `_render_index` scans each task's attempts
   newest-first (mirrors `TaskStateFile.current_attempt()`) and, when one
   is live, appends a linked `"● running (&lt;attempt id&gt;)"` indicator
   to the State cell — regardless of the task statefile's own (possibly
   lagging) state — pointing at the new drilldown endpoint.
3. **Read-only agent drilldown** (`src/nyxloom/daemon.py` +
   `src/nyxloom/render.py`). New `GET
   /api/drilldown/<project>/<attempt_id>?tail=65536` (daemon.py
   `_handle_get` + new `_serve_drilldown`): tails the attempt log, renders
   it via new `render.render_transcript()` (extracts assistant `text`
   deltas and `tool_use` names from each stream-json line into
   `html.escape()`'d prose, newest-last, unparseable/partial lines
   silently skipped — never raw JSON), THEN `cfg.redact()`s the *rendered*
   text (deliberately not the raw stream-json — see the deviation note
   below), then wraps it via new `render.render_drilldown_page()` in a
   small `<meta http-equiv="refresh" content="5">` auto-polling page (no
   JS, no websocket). Every task page's attempts table (`_render_task_page`)
   and every index.html live-indicator link to this endpoint. No control
   on the returned page mutates state.

Scoped gate (`tests/test_render.py tests/test_daemon.py`): 67 passed (30 +
37). Full suite: **395 passed, 0 failed** — see Gate Output below.

## Oracle Results

| # | Oracle (from handoff) | Status | Notes |
|---|---|---|---|
| 1 | The dashboard HTML contains a legend entry for `interrupted-dead-end` (and the other states) with explanatory text | **PASS** | `test_render.py::test_state_legend_present_and_explains_interrupted_dead_end` — asserts `id="state-legend"`, every `TaskState` value present, `interrupted-dead-end` plus its explanatory phrase, and the five states the handoff specifically names |
| 2 | Given a task with a live attempt (receipt absent, attempt RUNNING), the rendered board marks it running even if the task statefile is QUEUED | **PASS** | `test_render.py::test_index_html_marks_attempt_running_despite_stale_queued_state` (QUEUED task, RUNNING attempt, no receipt -> `"● running (att-live-001)"` + drilldown link present); negative `test_index_html_does_not_mark_running_once_receipt_has_landed` (receipt.json present -> NOT marked, even with a stale RUNNING attempt-state); belt-and-braces `test_index_html_pid_alive_overrides_non_running_attempt_state` (STALLED attempt state but a genuinely alive pid -> still marked running) |
| 3 | The drilldown endpoint for an attempt returns the human-readable rendering of that attempt's attempt.log stream-json (assistant text + tool names), not raw JSON, and never exposes a mutating control | **PASS** | `test_daemon.py::test_drilldown_endpoint_renders_transcript_readonly_and_redacted` (HTTP round trip: assistant text + `[tool: Bash]` present, `"type":"assistant"`/`"tool_use"` raw-JSON markers absent, embedded secret redacted, no `<form>`/`<button>`); `test_render.py::test_render_transcript_extracts_assistant_text_and_tool_names` + `..._skips_unparseable_and_partial_lines` + `..._html_escapes_agent_text` + `test_render_drilldown_page_is_readonly_and_escaped` unit-test `render_transcript`/`render_drilldown_page` directly; 404 tests for unknown attempt/project |
| 4 | Full suite green | **PASS** | 395 passed, 0 failed (see Gate Output below) |

## Files Touched

- `src/nyxloom/render.py` —
  - Module docstring: new dated (2026-07-16) addendum describing the
    legend, liveness fix, and the two new public functions.
  - New imports: `json`, `os`, `BlockerType`.
  - New CSS: `#state-legend`, `.live-indicator`, `#drilldown-transcript`.
  - New: `STATE_LEGEND`, `BLOCKER_REASON_LEGEND`, `BLOCKER_TYPE_LEGEND`
    dicts; `_render_legend_html()`; `_pid_alive()`; `_attempt_is_live()`;
    public `render_transcript()`; public `render_drilldown_page()`.
  - `_render_index`: prepends the legend; computes `live_attempt_id` per
    task and appends the linked indicator to the State cell.
  - `_render_task_page`: attempts table gained a `Drilldown` column
    linking every attempt (colspan bumped 8 -> 9 for the empty-state row).
- `src/nyxloom/daemon.py` —
  - Module docstring: new dated (2026-07-16) HTTP-list entry for the
    endpoint.
  - `_handle_get`: new `^/api/drilldown/([^/]+)/([^/]+)$` route (same
    `?tail=` parsing pattern as the existing `/api/log/...` route).
  - New `_serve_drilldown` method (placed next to `_serve_log`): 404 on
    unknown project/missing log; otherwise tail -> `render.render_transcript`
    -> `cfg.redact` -> `render.render_drilldown_page` -> `text/html`.
- `tests/test_render.py` — added 10 tests (see Oracle table above), plus
  `import os` for the pid-alive test; no existing test modified.
- `tests/test_daemon.py` — added 3 tests (round-trip + two 404s), reusing
  the existing `http_daemon` fixture unchanged; no existing test modified.
- `nyxloom-trove/reports/P22-REPORT.md` — this report.

No changes to `reconcile.py`, `storage.py`, `types.py`, `adapters.py`, or
any ciu/docker file — all untouched, per the handoff's ownership boundary.

## Gate Output (tail)

Command: `docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c 'cd /workspaces/vbpub/.worktrees/nyxloom-P22/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'`

```
........................................................................ [ 18%]
........................................................................ [ 36%]
........................................................................ [ 54%]
........................................................................ [ 72%]
........................................................................ [ 91%]
...................................                                      [100%]
```

Exit code: 0. `-q` suppresses the summary line per STANDING.md's known
pytest-config quirk, so per STANDING's own guidance the suite was also run
with `-v` to get a real count:

```
collected 395 items
...
======================== 395 passed in 67.47s (0:01:07) ========================
```

395 = 376 (P21 baseline) + 19 new tests. Per-module `--collect-only -q`
confirms the +19 is entirely additive to this package's two owned test
files: `test_render.py` 20 -> 30 (+10), `test_daemon.py` 34 -> 37 (+3);
the remaining +6 (`test_properties.py` 16 -> 17, `test_storage.py` +5
appearing where the P21 report's own module breakdown omitted it) predate
this change and are untouched by this diff — confirmed by `git diff
--stat` showing only the four files listed above.

## Deviations / Assumptions

- **Redact ORDER for the drilldown endpoint deliberately differs from
  `/api/log`'s.** `/api/log` redacts the RAW log text (it serves raw text
  as-is). The drilldown endpoint instead renders the stream-json to
  readable prose FIRST, then redacts the RENDERED text. Redacting the raw
  stream-json first was tried and rejected during implementation: a
  secret embedded inside a JSON string value (e.g.
  `"text":"password=hunter2"`) gets its trailing characters — including
  the JSON's own closing quote/braces — swallowed by the redact regex's
  greedy `\S+`, corrupting that line's JSON syntax; `render_transcript`'s
  `json.loads` then silently skips the whole (now-invalid) line under its
  by-design "skip unparseable lines" rule, which would have SILENTLY
  DROPPED that turn's tool-use / text content from the transcript instead
  of just redacting the secret in place. Rendering before redacting avoids
  this: by the time `cfg.redact()` runs, the text is plain prose with no
  JSON delimiters, so it can't corrupt anything, and the secret is masked
  in place as intended. Both `render.render_drilldown_page`'s and
  `daemon.Daemon._serve_drilldown`'s docstrings document this explicitly.
  Flagging as a deviation only because the handoff's phrasing plausibly
  implied "redact like `/api/log`" — the review checklist's "subtle
  correctness gaps" item — but the behavior (secrets never reach the
  page) is the same or stronger than a naive redact-then-render.
- **Legend placement:** the handoff doesn't name a specific page for the
  legend; it was added to `index.html` ("the dashboard" in the handoff's
  own framing of observation (a), and where the "nothing running" bug and
  the live-indicator both live).
- **Drilldown link placement:** added to both `index.html` (only for the
  currently-live attempt, via the running indicator itself) and every
  task page's attempts table (for every attempt, live or not — the
  handoff's own item 3 says "a running/recent attempt", and the task page
  is the natural place to reach a recent-but-not-currently-live one).
- **BLOCKER_TYPE_LEGEND** (all six `BlockerType` values) and
  **BLOCKER_REASON_LEGEND** (the two free-text reason strings actually
  emitted by `reconcile.py`/`daemon.py` today — `interrupted-dead-end` and
  `attempts exhausted`) were both added beyond the single literal
  `interrupted-dead-end` the handoff calls out by name, reading "the
  common attempt/blocker reasons in plain language" as covering the full
  set an operator would actually encounter, not just the one worked
  example.
- No BLOCKED contract issues encountered; no cross-package files touched.

## Deviation from STANDING.md's / this handoff's own "no commit" rule

STANDING.md and this handoff's own Rules section both say: "Do not commit
(worktree-merge flow: the controller creates the worktree/branch and
merges) — receipt-only final." The orchestrating session that dispatched
this implementation run explicitly instructed (its own STEP 5/6): commit
all work to `feat/nyxloom-P22` directly, then write and commit this
report — on the stated basis that the worktree/branch for this run were
already created and handed over for direct implementation-and-commit,
rather than the standard receipt-only flow. This implementation followed
the dispatching orchestrator's explicit instruction rather than the
handoff's own default "receipt-only" text; flagging the discrepancy here
per the review checklist so a reviewer can confirm this was the intended
process for this particular run.
