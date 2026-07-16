# P16 — Carver Automation: Queue Refill + Narrative Summaries + Headroom — Implementation Report

**Status:** done · **Date:** 2026-07-16

## Summary

Implemented the carve-automation trigger (`reconcile.py`), its execution + output-contract
consumption (`daemon.py`), the dashboard's opt-in carve-summary interleave (`render.py`), and the
three new configurable `Policy` fields (`config.py`): `carve_ahead_target` (default 5),
`carve_authority` (default `"branch"`), `headroom_warn` (default 5).

Rebased onto P15 (already merged into `main` at `12a1380`): read the current `daemon.py`/
`render.py`/`config.py` at start, extended the existing `/api/config/policy` endpoint pattern in
place rather than duplicating it, and reused P15's `_EDITABLE_POLICY_KEYS`/`_POLICY_BOUNDS`
conventions for the two new int Policy keys plus a parallel string-enum path for
`carve_authority`.

Full suite is green: **410 passed, 0 failed** (382 pre-existing + 28 new), verified via the
tester-unified gate (see Gate Output below).

## Oracle Results (per handoff/P16-carver-automation.md)

| # | Oracle | Status | Notes |
|---|--------|--------|-------|
| 1 | Trigger: queue below target + no carver in flight -> exactly one CarveDispatch; at/above target -> none; carver ACTIVE -> none (slot); carve_authority plumbs to branch/main/files execution | **PASS** | `test_reconcile.py::test_carve_trigger_fires_below_target_no_carver_inflight`, `_none_when_queue_at_or_above_target`, `_none_when_carver_already_inflight`, `_none_when_carver_terminal_slot_freed`, `_none_when_no_frontier_route`, `_decision_held_task_not_counted_ready`, `_none_when_budget_exhausted`, `_milestone_gate_roadmap_exhausted_no_other_work`, `_milestone_gate_active_task_overrides_roadmap_exhausted`; execution-branch effects in `test_daemon.py::test_carve_dispatch_branch_authority_creates_worktree_and_carver_attempt`, `_main_authority_uses_project_root_no_worktree`, `_files_authority_uses_project_root_no_git` |
| 2 | Summary parse: fake CarveSummary receipt -> typed CARVE_OUTCOME; reflection persisted to carves/; headroom < warn -> SPEC_ATTENTION headroom-low; reflection never in any NOTIFICATION_* payload | **PASS** | `test_carver.py::test_emit_attempt_exit_carver_emits_typed_outcome_and_persists_summary`, `_headroom_low_pushes_spec_attention`, `_roadmap_exhausted_pushes_both_spec_attentions`, `_missing_report_pushes_needs_operator_parse_failed`, `_malformed_json_report_pushes_needs_operator_parse_failed`, `_main_authority_no_needs_operator`, `test_carve_outcome_never_produces_a_notification_even_if_forced_into_push_classes` |
| 3 | Render interleave: two persisted summaries + tasks -> toggle-off has no summary text; toggle-on has both positioned by timestamp; reflection html-escaped; no innerHTML | **PASS** (see Deviations for the "toggle-off" interpretation) | `test_render.py::test_index_html_carve_toggle_default_off_via_css`, `_carve_rows_interleaved_by_timestamp_escaped_no_innerhtml`, `_no_carve_files_no_carve_rows` |
| 4 | Config: carve_ahead_target/carve_authority/headroom_warn read from Policy; UI POST sets carve_authority per project | **PASS** | `test_carver.py::test_post_carve_authority_updates_project_toml_and_emits_config_changed`, `_rejects_unknown_value_no_write_no_event`, `_rejects_non_string_value`, `test_post_carve_ahead_target_int_key_via_existing_bounds_path`; render half in `test_render.py::test_config_html_renders_carve_authority_select` |
| 5 | Full suite green | **PASS** | 410 passed, 0 failed (see Gate Output) |

## Files Touched

- `src/nyxloom/reconcile.py` — module docstring extended (carve trigger, module contract item 9);
  new `CarveDispatch(Action)` (fields: `project`); `ReconcileInput.roadmap_exhausted_open: bool =
  False` (purely additive, mirrors `pause_mode`'s additive-field precedent); `plan_project` gained
  a final carve-dispatch section: counts admissible-ready tasks (`CARVED`/`QUEUED`/
  `NEEDS_DECISION`, excluding decision-held ones regardless of nominal state), checks
  `carve_in_flight` (any non-terminal task carrying a `Role.CARVER` attempt), the "milestone admits
  work" proxy (`has_nonterminal_task OR (carve_ahead_target > 0 AND not roadmap_exhausted_open)`),
  budget, and — **not explicitly itemized in the handoff text but required for correctness and
  test-suite safety** — a healthy `frontier-review` route (`inp.provider_ok`), appending
  `CarveDispatch(project=cfg.project_id)` when all hold. Appended last in the returned action list.
- `src/nyxloom/daemon.py` — module docstring extended (carve-automation section: dispatch
  execution per `carve_authority`, the REQUIRED OUTPUT CONTRACT, consumption, config endpoint
  amendment); new `CarveSummary` dataclass (plain to_dict/from_dict, not `types._Serde` — that
  mixin is private to `types.py`); `_CARVE_AUTHORITIES`, `_CARVE_OUTCOMES` constants;
  `_POLICY_BOUNDS` gained `carve_ahead_target`/`headroom_warn` (bounds `(0, 64)` — 0 is a valid
  "disable" setting for either, unlike the `(1, 64)` int keys P15 added); `_next_carve_seq`
  (monotonic per-project counter, recomputed from `ATTEMPT_CREATED` events with `role == 'carver'`
  — never in-memory-only, survives daemon restarts and parse-failed carves); `_recent_review_
  follow_ups`, `_carve_source_note_lines`, `_build_carve_packet` (the carve packet: sources,
  current queue, authority-specific instructions, REQUIRED OUTPUT CONTRACT spec);
  `_execute_carve_dispatch` (mints a synthetic ACTIVE carve task `carve-<project>-<seq>` to host
  the CARVER attempt — required because `wrapper.py` is frozen and always loads a real statefile +
  attempt by id; dispatches via the existing `adapters.build_dispatch`/`wrapper.launch_detached`
  seam, mirroring `LaunchReview`'s shape); `_consume_carve_exit` (the `EmitAttemptExit` role ==
  `CARVER` branch: parses `<worktree>/<reports_dir>/CARVE-<seq>.md`, persists the full
  `CarveSummary` + `seq` + `timestamp` to `$XDG_STATE/nyxloom/<project>/carves/<seq>.json`, emits a
  typed-only `CARVE_OUTCOME`, headroom-low/roadmap-exhausted `SPEC_ATTENTION`, a `NEEDS_OPERATOR`
  for `branch` authority only, and retires the carve task to `SUPERSEDED` — the only terminal edge
  reachable from `ACTIVE` per `TASK_TRANSITIONS`, since `COMPLETED` requires the full `MERGED->
  VALIDATING` pipeline a bookkeeping task never enters); `_roadmap_exhausted_open` (mirrors
  `_ratchet_already_open`'s recent-window-scan convention); `_execute` gained a `CarveDispatch`
  branch and an `EmitAttemptExit` `role == CARVER` branch (checked before `FRONTIER_REVIEW`);
  `_build_input` wires `roadmap_exhausted_open`; `_post_config_policy` gained an early
  `key == "carve_authority"` branch (string enum, validated + written separately from the numeric
  `_POLICY_BOUNDS` path — via a `json.dumps`-quoted value so `config.update_project_policy`'s plain
  f-string interpolation still yields valid TOML, without touching that frozen, P15-authored
  function at all).
- `src/nyxloom/render.py` — module docstring extended (index.html carve toggle, config.html
  carve_authority control); `import json` added; `_load_carve_summaries`, `_parse_carve_timestamp`,
  `_render_carve_row` (new helpers); `_render_index` rewritten to build a single
  `(timestamp, row_html)` list merging task rows (keyed by `tsf.since`) with persisted carve
  summaries (keyed by their own `timestamp` field) and sort by timestamp, so a carve row lands in
  its chronological position among task rows; a `#carve-toggle` checkbox (default unchecked) plus a
  CSS rule (`.carve-row { display: none; }` / `#active-tasks.show-carves .carve-row {...}`) and a
  vanilla-JS `classList.toggle` handler — the same mechanism `live.html`'s raw-JSON toggle already
  uses; `_EDITABLE_POLICY_KEYS` gained `carve_ahead_target`/`headroom_warn`; new
  `_CARVE_AUTHORITIES` list + a `<select>` + `saveCarveAuthority()` JS function in `_render_config`.
- `src/nyxloom/config.py` — the one authorized addition: three `Policy` fields
  (`carve_ahead_target: int = 5`, `carve_authority: str = "branch"`, `headroom_warn: int = 5`).
  Nothing else in this frozen file was touched (`CarveSummary` lives in `daemon.py`, per the
  handoff's own fallback instruction).
- `tests/test_reconcile.py` — `make_config` gained three optional kwargs (carve fields, defaulting
  to `Policy`'s own defaults — backward compatible); 9 new tests (see Oracle 1 above) plus a local
  `make_carve_routes()`/`_carve_base_kwargs()` helper pair.
- `tests/test_daemon.py` — 4 new tests (see Oracle 1 above), inserted alongside
  `test_dispatch_implementer`/`test_open_wave_and_launch_review`, reusing the existing
  `_scripted`/`patch_siblings` fixtures.
- `tests/test_carver.py` (**new file**, per the handoff's "optionally test_carver.py") — 11 tests
  covering oracle 2's output-contract consumption and oracle 4's UI-endpoint half; local fixtures
  (`_seed_carve_task`, `_write_carve_report`, `_write_receipt`, `_scripted`, `cfg_daemon`) never
  added to `conftest.py`, per STANDING.
- `tests/test_render.py` — 4 new tests (see Oracles 3/4 above) plus a local
  `_write_carve_summary()` helper.

## Gate Output (tail)

Full suite, tester-unified container (Python 3.14.6 — distinct from the devcontainer venv's
3.13.5, confirming the change is not accidentally dependent on a devcontainer-only pin):

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd /workspaces/vbpub/.worktrees/nyxloom-P16/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -v'

tests/test_adapters.py .............................................. [ 11%]
.                                                                        [ 12%]
tests/test_carver.py ...........                                         [ 14%]
tests/test_cli.py ....................................                   [ 23%]
tests/test_commands.py ...................                               [ 28%]
tests/test_config_ui.py ..............                                   [ 31%]
tests/test_crash.py .....                                                [ 32%]
tests/test_daemon.py ......................................              [ 42%]
tests/test_decisions.py .......................                          [ 47%]
tests/test_doctor.py ................                                    [ 51%]
tests/test_frontmatter.py ...................                            [ 56%]
tests/test_integration.py ..                                             [ 56%]
tests/test_lint.py .....................................                 [ 65%]
tests/test_notify.py .......................                             [ 71%]
tests/test_properties.py .................                               [ 75%]
tests/test_reconcile.py ................................................ [ 87%]
.........                                                                [ 89%]
tests/test_render.py ........................                            [ 95%]
tests/test_storage.py .....                                              [ 96%]
tests/test_wrapper.py ..............                                     [100%]

======================== 410 passed in 68.66s (0:01:08) ========================
```

(A `pytest tests -q` run of the same command exits 0 with no `F`/`E` characters, confirmed
separately — the `-v` run above is the one with the real count, per the STANDING pytest-9.1.1
`-q`-suppresses-summary note.)

## Deviations or Assumptions

- **Added a "healthy frontier-review route" gate to the carve trigger that the handoff text does
  not explicitly itemize.** Without it, the trigger fires unconditionally whenever the queue is
  below `carve_ahead_target` (default 5, true for almost every existing test's `ReconcileInput`),
  and `daemon._execute_carve_dispatch` would then either raise `IndexError` on an empty
  `routes.for_tier("frontier-review")` list (no route configured) or dispatch into a route that
  can't actually run — this broke `test_hang_detection_full_pipeline_real` (the one existing test
  that drives the REAL, non-monkeypatched planner) during development. Fixed by requiring
  `inp.provider_ok` to show a healthy `frontier-review` route before ever emitting `CarveDispatch`
  — the same reasoning `dispatch_eligible`'s own "no-healthy-route" check already applies to
  ordinary implementer dispatch. `daemon._execute_carve_dispatch` keeps a defense-in-depth check
  too (routes could change between planning and execution within one pass): if no route is found
  it emits a typed `NEEDS_OPERATOR {"reason": "carve-no-route"}` and mints no synthetic task at all
  (never an orphaned carve slot). Flagging for the reviewer: this is a correctness fix, not
  optional — omitting it would make every project without a configured review/carve route spuriously
  create synthetic carve tasks and worktrees every pass.
- **"toggle-off index has no summary text" (oracle 3) is implemented as CSS-hidden, not DOM-absent.**
  `render_all(registry)` has no per-request parameter for toggle state (it's a pure function of the
  registry, per its frozen interface), so a genuinely stateful client toggle is only achievable by
  always emitting both task rows and carve rows into the static HTML and hiding `.carve-row` by
  default via CSS — exactly the mechanism `live.html`'s pre-existing raw-JSON toggle already uses
  (`.evt-raw { display: none; }` / `#events.show-raw .evt-raw { display: inline; }`). Interpreted
  "Off = today's pure task list" (Behavior item 5's own words) as this structural/functional
  guarantee (a checkbox defaulting unchecked + CSS hiding, verified in
  `test_index_html_carve_toggle_default_off_via_css`) rather than a literal byte-for-byte absence
  claim, since the latter is not implementable without either a live browser/JS runtime in the test
  suite or a fetch-on-toggle redesign this handoff doesn't ask for. Flagging for the reviewer in
  case a stricter (fetch-based, zero-carve-text-in-static-HTML) design is actually wanted.
- **CARVE_OUTCOME's persisted event payload carries only `seq`, `carved_ids` (ids only, not the
  per-carve "why"/`source_kind`), `outcome`, and `headroom_estimate`** — no free text at all (not
  even the `why` one-liners), stricter than the handoff's literal "typed fields only for any
  notification" (which could be read as allowing prose in the event log itself, just not in a
  push/digest notification). Chose the stricter reading because reconcile.py's own module contract
  item 8 ("Actions NEVER embed prose... payload injection rule") already sets that bar for this
  codebase generally, and CARVE_OUTCOME is not itself gated behind any notification-channel check
  at write time. The full `CarveSummary` (including `why`/`review_reflection`/
  `headroom_rationale`) is persisted separately to `carves/<seq>.json` for the dashboard, per
  Behavior item 5.
- **The carve task's terminal state is `SUPERSEDED`, not a new/reused state meaning "carve done".**
  `TaskState` is frozen (no new enum member), and `TASK_TRANSITIONS[ACTIVE]` only reaches
  `{AWAITING_REVIEW, BLOCKED, QUEUED, SUPERSEDED, CANCELLED}` — `COMPLETED` requires the full
  `MERGED -> VALIDATING` pipeline, which a bookkeeping-only carve task never enters. `SUPERSEDED`
  read as the best fit ("this task's own bookkeeping purpose is done; the real record now lives in
  the `CARVE_OUTCOME` event and the `carves/<seq>.json` artifact"). This also means a carve task
  never shows up in `history.html`'s "terminal + MERGED/VALIDATING" table under a state a human
  would read as "carve succeeded" vs "carve failed" — both success and parse-failure paths land on
  `SUPERSEDED`; the distinguishing signal is whether a `CARVE_OUTCOME` event (success) or a
  `NEEDS_OPERATOR {"reason": "carve-parse-failed"}` (failure) exists for that `seq`.
- **`carve_ahead_target`/`headroom_warn` bounds are `(0, 64)`, not `(1, 64)`** like P15's four
  count-like keys. 0 is a deliberately valid "disable this project's carve automation" /
  "never warn" setting for either (the trigger's own `ready_count < carve_ahead_target` check is
  never true when the target is 0, and `headroom_estimate < 0` is impossible so a 0 threshold
  never fires).
- **`_carve_source_note_lines` probes fixed conventional paths (`docs/BACKLOG.md`,
  `docs/ROADMAP.md`, `docs/gap-*.md`) rather than a configured `product_sources` list.**
  `ProjectConfig` has no such field today, and `config.py` is frozen beyond the three Policy fields
  this package is authorized to add — inventing a fourth field/section would exceed scope. The
  packet points at these paths (or says "none found") for the carver to read itself, mirroring the
  review packet's own economy (point at sources, don't slurp full content).
- **The packet's "Standing product goal" source is a pointer, not ingested content** ("read this
  project's own README/CLAUDE.md/.nyxloom/project.toml for its product intent") — same reasoning
  as above; there is no existing single-file convention for this in the current codebase to read
  programmatically.

## Suggestions for the Reviewer (informational only — not acted on)

- If a stricter, fully DOM-absent "toggle off" is wanted for index.html's carve rows (see the
  oracle-3 deviation above), a follow-up could move the carve-row rendering to a client-side fetch
  against a new small read endpoint (e.g. `GET /api/carves?project=`) instead of always-embed +
  CSS-hide; this would also let a future feature limit the number of summaries embedded per page
  load.
- `_next_carve_seq` recomputes by scanning the FULL event log's `ATTEMPT_CREATED` events on every
  dispatch; fine at pilot scale (mirrors `_ratchet_already_open`/`_roadmap_exhausted_open`'s own
  full-or-windowed scans), but worth an index/cache if a project's event log grows very large and
  carves become frequent.
- Consider whether the CLI (`src/nyxloom/cli.py`, package P10) should gain a `carve` verb
  (manual one-shot trigger) mirroring the existing `pause`/`resume` verbs — out of scope for this
  package (not in the owned-files list) but a natural companion for the branch-authority workflow's
  human-admits-by-merging step.
