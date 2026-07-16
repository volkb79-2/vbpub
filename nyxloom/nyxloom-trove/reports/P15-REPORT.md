# P15 — Configure the Factory from the UI (Policy + Routing Matrix) — Implementation Report

**Status:** done · **Date:** 2026-07-15

## Summary

Added audited HTTP config-mutation endpoints (`POST /api/config/policy`,
`/api/config/pause`, `/api/config/tier`) to `daemon.py`, two surgical
TOML-edit functions to `config.py`, a `config.html` page + a per-agent
last-activity column to `render.py`, factory-state pause MODE semantics
(`run`/`drain-handoffs`/`drain-agents`) to `reconcile.py`'s planner, and the
matching `pause <project> [agents|handoffs]` verb-arg extension to
`commands.py`'s ntfy listener. All five owned modules' existing tests plus
24 new tests are green; full suite is green (354 passed, up from the 330
baseline, 0 failures).

## Oracle Results (per handoff/P15-ui-config.md)

| # | Oracle | Status | Notes |
|---|--------|--------|-------|
| 1 | POST policy change -> project.toml line updated in place (comment intact), CONFIG_CHANGED with old/new, next run_pass uses new cap | **PASS** | `test_config_ui.py::test_policy_update_full_flow` |
| 2 | Bounds: max_active_tasks=0 or 999 -> 400, file untouched, no event | **PASS** | `test_config_ui.py::test_policy_bounds_rejects_zero_and_too_large` (+ `test_policy_unknown_key_rejected`) |
| 3 | Tier remap: POST tier -> rewrites only that line; unknown route id -> 400 | **PASS** | `test_config_ui.py::test_tier_remap_rewrites_only_that_tier`, `test_tier_remap_unknown_route_id_400_no_write`, `test_tier_remap_unknown_tier_404` |
| 4 | Pause via UI -> flag + event; unpause reverses | **PASS** | `test_config_ui.py::test_pause_via_ui_then_unpause`, `test_pause_unknown_mode_rejected` |
| 5 | config.html renders current policy + tier table; no inline secrets; no innerHTML | **PASS** | `test_config_ui.py::test_config_html_renders_policy_and_tiers_no_secrets_no_innerhtml` |
| 6 | Traversal/method safety: GET on POST endpoints -> 405/404; unknown project -> 404; full suite green | **PASS** | `test_config_ui.py::test_get_on_config_endpoints_is_405`, `test_post_config_unknown_project_404`, `test_post_unknown_path_404`; full suite 354 passed (see Gate Output) |
| 7 | Pause modes: drain-handoffs allows resume+launch-review but blocks dispatch; drain-agents blocks all three; run allows all three; legacy empty flag = drain-handoffs; UI/CLI/ntfy each set mode file + event | **PASS** (UI+ntfy; CLI out of scope — see Deviations) | Planner: `test_reconcile.py::test_pause_mode_run_allows_all_three`, `_drain_handoffs_blocks_dispatch_only`, `_drain_agents_blocks_all_three`, `_default_is_run_when_unset`. Legacy flag: `test_daemon.py::test_input_building` (asserts `pause_mode == "drain-handoffs"` from a bare `touch()`), plus `test_pause_mode_absent_flag_is_run`/`_explicit_drain_agents_content`/`_explicit_drain_handoffs_content`. UI surface: `test_config_ui.py::test_pause_via_ui_then_unpause`. ntfy surface: `test_commands.py::test_pause_agents_mode_sets_flag_content_and_event`, `_pause_handoffs_mode_explicit`, `_pause_unknown_mode_rejected_no_flag_no_event` |
| 8 | last-activity: seeded attempt log with known mtime renders expected age string in both tables | **PASS** | `test_config_ui.py::test_last_activity_column_index_and_task_page`, `test_last_activity_dash_when_no_log` |

## Files Touched

- `src/nyxloom/daemon.py` — module docstring extended (P15 CONFIG-mutation
  endpoint contract); `_pause_mode()` (reads the pause flag's CONTENT into
  run/drain-handoffs/drain-agents, legacy empty file = drain-handoffs);
  `_build_input` wires `pause_mode` into `ReconcileInput`; `_CONFIG_POST_PATHS`,
  `_POLICY_BOUNDS`, `_PAUSE_MODES` module constants; `Handler.do_POST`;
  `_handle_post` + `_read_json_body` + `_append_ui_event` (actor OPERATOR
  'ui' — deliberately NOT `_append_ev`, which hardcodes actor TICK/'nyxloomd'
  for reconcile-pass events) + `_post_config_policy`/`_post_config_pause`/
  `_post_config_tier`; `_handle_get` gained a 405 guard for GET on the three
  new POST-only paths.
- `src/nyxloom/render.py` — module docstring extended (config.html
  contract, index.html/task-page last-activity column); NAV gained a
  "Config" link; `_pause_mode_for()` (render-side duplicate of daemon.py's
  mode-reading logic — no cross-import to avoid a render<->daemon cycle;
  paths.py/config.py are frozen so there's no shared home for these 3
  lines); `_format_age()`, `_attempt_log_age_seconds()`,
  `_newest_attempt_log_age()`; `_render_index` gained a "Last Activity"
  column (colspan 9->10) and the pause banner now shows each project's mode;
  `_render_task_page`'s attempts table gained a "Last Activity" column
  (colspan 7->8); new `_render_config()` + `_EDITABLE_POLICY_KEYS` +
  `_CONFIG_JS`, wired into `render_all`.
- `src/nyxloom/reconcile.py` — module docstring extended (pause-mode
  semantics); `ReconcileInput.pause_mode: str = "run"` (purely additive —
  `project_paused` untouched, so every pre-existing test that only sets it
  keeps its old semantics); `INTERRUPTED` branch gained an early
  `pause_mode == "drain-agents"` no-op case (attempt stays parked, no
  `ResumeAttempt`, no `BLOCKED` transition either); the `LaunchReview` wave
  loop gained the same `drain-agents` skip.
- `src/nyxloom/commands.py` — module docstring extended (widened regex
  rationale); `_VERB_RE` widened from one to two optional trailing tokens
  (two explicit capture groups, since Python `re` cannot recover more than
  the last match of one repeated group); `HELP_TEXT` updated;
  `_MODE_WORD_TO_MODE` mapping; `handle_message` parses the second token as
  `pause`'s mode word; `_cmd_pause(project, mode_word)` writes the mode as
  the flag's CONTENT and emits `PAUSE_SET {"mode": ...}` (default mode
  'handoffs' when omitted, matching the legacy bare-pause meaning); rejects
  an unrecognized mode word with a fixed reply, no write, no event.
- `src/nyxloom/config.py` — the two authorized additions:
  `update_project_policy(root, changes)` (surgical `[policy]`-section line
  edit, preserves comments/layout, raises `ValueError` — no partial write —
  if any key's anchor line isn't found) and `update_routes(changes)`
  (surgical `[tiers.<tier>] routes = [...]` line edit in the live
  `routes.toml`, same no-partial-write contract). Nothing else in this
  frozen file was touched.
- `tests/test_config_ui.py` (new) — 14 tests covering oracles 1-6 and 8 (see
  table above); local `cfg_daemon` fixture (ephemeral-port HTTP daemon,
  richer routes.toml with 2 tiers/3 routes, a policy-section comment) and a
  `_post()` JSON-POST helper, both local to this file per STANDING.
- `tests/test_reconcile.py` — 4 new tests (`_pause_mode_composite_input`
  helper + run/drain-handoffs/drain-agents/default-omitted cases) covering
  oracle 7's planner semantics.
- `tests/test_daemon.py` — 1 assertion added to `test_input_building`
  (`inp.pause_mode == "drain-handoffs"` for the legacy empty flag) + 3 new
  tests for `Daemon._pause_mode()` (absent/drain-agents/drain-handoffs
  content).
- `tests/test_commands.py` — `test_pause_sets_flag_and_appends_set_event`
  extended with flag-content and payload assertions; 3 new tests for the
  ntfy `pause <project> [agents|handoffs]` mode-word surface.

## Gate Output (tail)

Scoped gate (the new test file):

```
cd /workspaces/vbpub/nyxloom && /workspaces/vbpub/.venv/bin/python -m pytest tests/test_config_ui.py -q
..............                                                           [100%]
14 passed in 3.63s
```

Full suite (`tests/`):

```
cd /workspaces/vbpub/nyxloom && /workspaces/vbpub/.venv/bin/python -m pytest -q
........................................................................ [ 20%]
........................................................................ [ 40%]
........................................................................ [ 61%]
........................................................................ [ 81%]
..................................................................       [100%]
354 passed in 63.87s
```

## Deviations or Assumptions

- **CLI `pause <project> [agents|handoffs]` (`src/nyxloom/cli.py`,
  PACKAGE P10) was deliberately NOT touched or tested.** The handoff's
  Factory-state section (item 4) mentions the CLI verb alongside the ntfy
  verb, but `cli.py` is not in this package's owned-files list (per the
  handoff's own "Owned files" section and the assigning message's explicit
  ownership summary: daemon.py, render.py, reconcile.py, commands.py,
  config.py's two functions, tests). Since `cli.py` belongs to another
  package (P10) and STANDING.md forbids touching files owned elsewhere,
  `cmd_pause`/`cmd_unpause` still only support the old task-level pause
  (no mode arg) and were left as-is. Oracle 7's "CLI ... sets the mode file
  + event" is therefore satisfied for the UI and ntfy surfaces only.
  Flagging for the reviewer: either a follow-up package should extend
  `cli.py` to accept `[agents|handoffs]` (mirroring the `commands.py`
  change 1:1 — same `_MODE_WORD_TO_MODE` shape), or the handoff's mention
  of "CLI" in that sentence should be reconciled with the ownership split.
- **Policy bounds for the two duration keys are NOT literally "1..64".**
  The handoff text reads "Integer-validated against sane bounds (1..64,
  interval 5..600)" right after listing all 7 editable keys. Applied
  literally, `stall_log_quiet_seconds` (default 300) and
  `attempt_max_wall_seconds` (default 10800) would be permanently
  out-of-bounds under their own defaults. Interpreted `1..64` as applying to
  the four count-like keys (`max_active_tasks`, `ready_queue_target`,
  `max_attempts_per_task`, `wave_max_diffs`) and `5..600` to
  `reconcile_interval_seconds` (oracle 2's literal test target,
  `max_active_tasks`, is unaffected either way); picked generous but sane
  second-denominated ceilings for the two duration keys instead
  (`stall_log_quiet_seconds`: 1..86400; `attempt_max_wall_seconds`:
  1..604800) — see `daemon._POLICY_BOUNDS`'s comment.
- **Pause endpoint emits `PAUSE_SET`/`PAUSE_CLEARED`, not `CONFIG_CHANGED`.**
  The original Mechanics section says "every success appends CONFIG_CHANGED";
  the later Factory-state amendment (dated the same day, explicitly a user
  directive superseding the base mechanics for this endpoint) says "Events:
  PAUSE_SET payload {"mode": ...} / PAUSE_CLEARED" and oracle 7 says "each
  set the mode file + event" uniformly across UI/CLI/ntfy. Treated the
  amendment as authoritative: all three pause surfaces (UI here, ntfy in
  `commands.py`, and — per the CLI deviation above — CLI once extended)
  emit the SAME event shape, matching the pre-existing CLI/ntfy convention
  rather than introducing a fourth event type for the same state change.
- **Tier remap's `CONFIG_CHANGED` is broadcast to every registered
  project's event log**, not appended to a single project's log. `routes.toml`
  is a shared, non-project-scoped state file (`paths.routes_path()`), but
  `storage`'s event log is inherently per-project (`paths.events_path(project)`);
  there is no "global" event log. Broadcasting mirrors the existing
  `Daemon._emit_lifecycle` pattern (`DAEMON_STARTED`/`STOPPED` loop over
  every registered project) so every project's own audit trail sees routing
  changes that affect its own dispatch behaviour.
- **`update_project_policy` only succeeds for keys that already have an
  explicit line in `project.toml`.** This is an inherent, expected
  consequence of "surgical edit only, refuse if the anchor is not found" (the
  handoff's own mechanics rule) — a policy key relying purely on the
  `Policy` dataclass default (no explicit TOML line, e.g. a project that
  never set `attempt_max_wall_seconds` at all) cannot be edited via the UI
  until an operator first adds an explicit line to the file manually. Not a
  bug; flagging so the reviewer/operator docs can note it.
- **`update_routes`/`update_project_policy` accept only the exact anchor
  shape used by this codebase's TOML files** (`^<key> = <value>` with an
  optional trailing `# comment`, and `routes = [...]` under `[tiers.<tier>]`
  respectively) — a hand-edited file using multi-line arrays or non-standard
  spacing around these specific lines would not be matched and would 400.
  Every existing `.nyxloom/project.toml` / `routes.toml` in this repo's
  fixtures uses the plain single-line form, so this wasn't exercised as a
  gap in practice.

## Suggestions for the Reviewer (informational only — not acted on)

- Consider whether `config.html`'s policy/tier forms should show a
  server-computed "last changed" timestamp per key (from the most recent
  matching `CONFIG_CHANGED` event) — not asked for by the handoff, but would
  close the loop with the audit trail this package just added.
- The tier-remap broadcast (every registered project gets a `CONFIG_CHANGED`
  copy) could get noisy with many registered projects sharing one
  `routes.toml`; fine at pilot scale, but worth revisiting if that list
  grows.
- `_render_config`'s per-project policy table only lists a key when
  `getattr(cfg.policy, key, None) is not None` — since every `Policy` field
  has a real int default (never `None`), all 7 keys always render; this
  guard is defensive scaffolding for a hypothetical future optional field,
  not currently load-bearing.
