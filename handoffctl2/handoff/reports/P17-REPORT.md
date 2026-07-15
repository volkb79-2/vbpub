# P17 — stream-json session capture + merge-gate rejection path — Implementation Report

**Status:** done · **Date:** 2026-07-15

## Summary

Gap 1: `adapters.capture_session` now reads `session_id` directly from a
claude route's stream-json FIRST log line (`json.loads(first_line).get(
"session_id")`) instead of the unreliable `newest-jsonl` directory scan;
`wrapper.py` passes the exact current-run log path (`spec.log_path`) into
the call so this is correct on both first dispatch and resume. `newest-jsonl`
/ `session_discover` behavior for non-claude routes is untouched.

Gap 2: added the single authorized frozen-core edge
`TaskState.MERGE_READY -> TaskState.REVIEW_REJECTED` to
`types.TASK_TRANSITIONS`, plus a new `reject <project> <task> [--note TEXT]`
CLI verb (`cli.py`) that performs it via a `TASK_TRANSITIONED` event (actor
OPERATOR `$USER`), pre-validating the transition with `check_task_transition`
so an invalid call never corrupts the event log.

Fold-in: added a `merge <project> <task> [--commit SHA]` CLI verb that
records the REAL merge commit (`git rev-parse HEAD` of the project root, or
an explicit `--commit` override) instead of a hand-padded placeholder:
`TASK_TRANSITIONED` MERGE_READY -> MERGED + `MERGE_RECORDED{merge_commit}`.
See "Deviations" below — no such step existed anywhere in the codebase
before this change; I built the minimal CLI verb this ask requires.

Scoped gate (adapters/wrapper/cli/properties): 115 passed. Full suite: 369
passed, 0 failures (up from the pre-change baseline; every pre-existing test
still green, including the exhaustive `TASK_TRANSITIONS` shape/soundness
tests in `test_properties.py`, unmodified).

## Oracle Results

| # | Oracle (from handoff) | Status | Notes |
|---|---|---|---|
| 1 | Gap 1: stream-json fixture log -> `capture_session` returns the embedded `session_id` | **PASS** | `test_adapters.py::test_capture_session_claude_stream_json_first_line`, `_defaults_to_attempt_log` |
| 1n | Gap 1 negative: malformed first line / missing `session_id` / missing log file -> `None`, never raises; non-claude routes still use `newest-jsonl`/`session_discover` unchanged | **PASS** | `test_adapters.py::test_capture_session_claude_stream_json_malformed_first_line`, `_missing_session_id`, `_missing_log_file`; pre-existing `test_capture_session_newest_jsonl*`/`_discover*` untouched and still green |
| 1w | Gap 1: wrapper records the captured id on `ATTEMPT_STARTED` | **PASS** | `test_wrapper.py::TestStreamJsonSessionCapture::test_wrapper_records_session_handle_from_stream_json` (real, unmocked `adapters`, real subprocess) |
| 1wn | Gap 1 negative, wrapper level | **PASS** | `test_wrapper.py::TestStreamJsonSessionCapture::test_wrapper_session_handle_none_on_malformed_first_line` |
| 2 | Gap 2: `MERGE_READY -> REVIEW_REJECTED` transition is allowed | **PASS** | Covered by the existing exhaustive `test_properties.py::test_check_task_transition_exhaustive` (unmodified — it iterates the live `TASK_TRANSITIONS` dict, so the new edge is automatically checked both directions); also exercised concretely via `test_cli.py::test_reject_success` |
| 2b | Gap 2: a rejected MERGE_READY task can re-enter QUEUED | **PASS** | `test_cli.py::test_reject_then_requeue` (reject via CLI, then a `TASK_TRANSITIONED` REVIEW_REJECTED->QUEUED — the pre-existing edge — succeeds) |
| 2c | Gap 2: `reject` verb rejects an invalid call cleanly (no event written) | **PASS** | `test_cli.py::test_reject_wrong_state_rejected`, `test_reject_unknown_task` |
| 3 | Fold-in: `MERGE_RECORDED.merge_commit` is the REAL merge commit, not a placeholder | **PASS** | `test_cli.py::test_merge_success_records_real_commit` (asserts equality with `git rev-parse HEAD` run independently in the test, and inequality with `"0"*40`) |
| 3b | Fold-in: `merge` verb rejects an invalid call cleanly; `--commit` override works | **PASS** | `test_cli.py::test_merge_wrong_state_rejected`, `test_merge_explicit_commit_override` |
| — | Full suite green | **PASS** | 369 passed, 0 failed (see Gate Output) |

## Files Touched

- `src/handoffctl/types.py` — the single authorized frozen-core edit:
  `TASK_TRANSITIONS[TaskState.MERGE_READY]` gained `TaskState.REVIEW_REJECTED`
  (was `{MERGED, SUPERSEDED, CANCELLED}`, now also `REVIEW_REJECTED`). No
  other line in this file touched.
- `src/handoffctl/adapters.py` — module docstring's `capture_session`
  contract rewritten to describe the new claude-route stream-json path (and
  the new `log_path` parameter); new private helper
  `_stream_json_session_id(log_path)` (reads the first line, tolerant of
  any I/O/parse failure); `capture_session` gained `log_path: str | Path |
  None = None` (keyword-only, backward compatible — every existing caller/
  test that omits it is unaffected) and a new `if route.cli == "claude":`
  branch at the top that returns the stream-json result directly (no
  `newest-jsonl` fallback for claude — per the handoff, that heuristic is
  "now both unnecessary and unreliable" for stream routes); the pre-existing
  `newest-jsonl`/`session_discover` code is untouched and still serves
  non-claude routes.
- `src/handoffctl/wrapper.py` — module docstring's step 5 updated to note
  `log_path=spec.log_path` is now passed; the single call site (inside the
  post-launch capture-session try block) now passes
  `log_path=spec.log_path` alongside the existing `attempt_dir`/`worktree`/
  `launched_at` arguments. No other logic changed (the 5s
  `SESSION_CAPTURE_DELAY` timing, the interruptible sleep loop, and the
  upsert re-emit of `ATTEMPT_STARTED` are all unmodified).
- `src/handoffctl/cli.py` — module docstring's subcommand list gained
  `reject` and `merge` entries; two new handlers, `cmd_reject` and
  `cmd_merge`, inserted between `cmd_discuss` and `cmd_pause`; two new
  argparse subparsers (`reject <project> <task> [--note TEXT]`, `merge
  <project> <task> [--commit SHA]`) and their `main()` dispatch branches.
  Both handlers: load `states = storage.list_states(project)`, look up the
  task (unknown -> `error: unknown task: ...`, exit 1), pre-validate the
  transition via `types.check_task_transition` (invalid -> `error: ...`,
  exit 1, **no event written** — avoids ever appending an event that would
  make a later `replay()` raise), then `storage.append_and_apply` with actor
  `Actor(OPERATOR, $USER)` (same pattern as `cmd_decide`/`cmd_pause`).
  `cmd_merge` additionally shells out to `git -C <project root> rev-parse
  HEAD` when `--commit` is omitted, and appends a second event
  (`MERGE_RECORDED{merge_commit}`, matching `storage.py`'s documented
  projection contract exactly — no `progress_units`/`source_kind` added,
  both already default correctly downstream in `daemon.py::_history`).
- `tests/test_adapters.py` — 5 new tests (see Oracle table rows 1/1n).
- `tests/test_wrapper.py` — `import sys` added; new `TestStreamJsonSessionCapture`
  class (2 tests, see Oracle table rows 1w/1wn) using a local
  `_claude_stream_script` helper (an unbuffered `python3 -u` child, not the
  shared `fake_cli` `/bin/sh` fixture — see Deviations) rather than editing
  the shared fixture.
- `tests/test_cli.py` — 7 new tests (see Oracle table rows 2/2b/2c/3/3b).
- No changes to `tests/conftest.py`, `schemas/`, `docs/`, `reconcile.py`,
  `daemon.py`, `storage.py`, `notify.py`, `render.py`, `doctor.py`,
  `commands.py`, or any other package's owned files.

## Gate Output (tail)

Scoped gate (adapters + wrapper + cli + properties):

```
cd /workspaces/vbpub/handoffctl2 && /workspaces/vbpub/.venv/bin/python -m pytest tests/test_adapters.py tests/test_wrapper.py tests/test_cli.py tests/test_properties.py -v
tests/test_adapters.py ................................................. [ 42%]
                                                                         [ 42%]
tests/test_wrapper.py ..............                                     [ 54%]
tests/test_cli.py ....................................                   [ 86%]
tests/test_properties.py ................                                [100%]

============================= 115 passed in 44.82s =============================
```

Full suite (`tests/`):

```
cd /workspaces/vbpub/handoffctl2 && /workspaces/vbpub/.venv/bin/python -m pytest tests/ -v
tests/test_adapters.py ................................................. [ 13%]
                                                                         [ 13%]
tests/test_cli.py ....................................                   [ 23%]
tests/test_commands.py ...................                               [ 28%]
tests/test_config_ui.py ..............                                   [ 32%]
tests/test_crash.py .....                                                [ 33%]
tests/test_daemon.py ............................                        [ 40%]
tests/test_decisions.py .......................                          [ 47%]
tests/test_doctor.py ................                                    [ 51%]
tests/test_frontmatter.py ...................                            [ 56%]
tests/test_integration.py ..                                             [ 57%]
tests/test_lint.py .....................................                 [ 67%]
tests/test_notify.py .......................                             [ 73%]
tests/test_properties.py ................                                [ 77%]
tests/test_reconcile.py ................................................ [ 90%]
                                                                         [ 90%]
tests/test_render.py ....................                                [ 96%]
tests/test_wrapper.py ..............                                     [100%]

======================== 369 passed in 66.25s (0:01:06) ========================
```

(Note: this repo's `pyproject.toml` `addopts = "-q"` combined with this
environment's pytest 9.1.1 suppresses the trailing summary line under plain
`-q` runs — confirmed independently of these changes by running the exact
same suite both ways; `-v` reliably prints it, hence its use above. Both
`-q` runs I performed during implementation showed exit code 0 with zero
`F`/`E` characters and a dot count matching `--collect-only`'s total, before
I switched to `-v` for the authoritative tail pasted here.)

## Deviations or Assumptions

- **"a daemon path/CLI verb `reject <project> <task>`" was implemented as a
  CLI-only verb, not a `reconcile.py` action / `daemon.py` execution-map
  entry.** Rejecting at the merge gate is an operator/merge-authority
  judgment call (same class as `decide`/`pause`), not something the pure
  reconcile planner should infer from disk state — and unlike `pause`
  (whose flag file `reconcile.py` actively reads every pass),
  `REVIEW_REJECTED` needs no daemon-side interpretation beyond the state
  transition itself, which flows through the same canonical
  `storage.append_and_apply` path the daemon uses, so it is immediately
  visible on the daemon's next pass/dashboard render. I did not add any new
  `reconcile.py`/`daemon.py` code. Flagging for the reviewer in case "daemon
  path" was intended literally (a new `reconcile.Action` + `daemon.py`
  execution-map case) — I judged that a needless expansion of a
  purely-manual gate decision into the automated planner, but it's a
  judgment call worth a second look.
- **No REVIEW_REJECTED -> QUEUED (or onward) automation exists, before or
  after this change.** I confirmed (by grep) that `reconcile.py` has *zero*
  existing references to `REVIEW_REJECTED` — even the pre-existing
  frontier-review rejection path (`daemon.py`, `EmitAttemptExit` for
  `Role.FRONTIER_REVIEW`) already left a rejected task there with no
  automatic recovery (this is the exact "SUPERSEDE + statefile reset"
  workaround the handoff cites for groop-P89). This package does not add
  that automation either — out of scope for a "small, same area" fold-in
  and not requested. The regression test (`test_reject_then_requeue`)
  demonstrates the *edge* works by performing the follow-on
  `TASK_TRANSITIONED` REVIEW_REJECTED->QUEUED event directly (that edge
  already existed), not by adding new reconcile logic.
- **The `merge` CLI verb did not exist anywhere before this change — I
  built it from scratch, which is the largest judgment call in this
  package.** I searched exhaustively (`cli.py`, `daemon.py`, `commands.py`,
  `reconcile.py`, `docs/SPEC.md`, this repo's sibling `handoffctl` v1 tree —
  which turned out not to exist on disk) for any existing "merge step" the
  fold-in's "wire the merge step to record `git rev-parse HEAD`" could be
  referring to, and found none: `MERGE_READY -> MERGED` and
  `MERGE_RECORDED` are valid per the type graph and the storage projection
  contract, but nothing in the codebase ever appends that event — every
  test fixture that exercises `MERGE_RECORDED` (`test_notify.py`,
  `test_commands.py`) does so via a raw `storage.append_event(...,
  payload={"merge_commit": "abc123"})` in test setup, i.e. exactly the
  "hand-padded" pattern the handoff describes, confirming this really is
  done out-of-band today. Given `reject` and `merge` are natural
  complements at the same `MERGE_READY` gate and the fix had to land
  *somewhere* runnable, I added a symmetric `merge <project> <task>
  [--commit SHA]` verb in the same file already being touched for `reject`.
  This is the one piece of this package that is a genuine invention rather
  than a literal reading of the handoff text — please double-check this
  interpretation before merge; an alternative would be to defer the `merge`
  verb to a dedicated future package and land only the `git rev-parse HEAD`
  *helper* now.
- **`cmd_merge`'s `MERGE_RECORDED` payload carries only `merge_commit`** —
  no `progress_units`/`source_kind`, matching `storage.py`'s documented,
  frozen projection contract (`MERGE_RECORDED payload {"merge_commit":
  str}`) literally. `daemon.py::_history` already defaults both fields
  (`progress_units` -> `[]`, `source_kind` -> `"review"`) when absent, so
  the progress ratchet keeps working unchanged; adding those fields here
  was out of scope for this fold-in.
- **`capture_session` gained a new keyword-only `log_path` parameter.** The
  pre-existing signature (`route, *, attempt_dir, worktree, launched_at`)
  had no way to know the CURRENT run's exact log file path (needed for
  correctness on resume, where the log is `attempt.resume-N.log`, not
  `attempt.log`) — `attempt_dir` alone can't disambiguate. Made it optional
  with a same-behavior-as-before default (`<attempt_dir>/attempt.log`) so
  every pre-existing caller/test is unaffected; `wrapper.py`'s one call site
  was updated to pass it explicitly.
- **`test_wrapper.py`'s new stream-json tests use a bespoke unbuffered
  Python child, not the shared `fake_cli` fixture.** A `/bin/sh` script
  redirected to a regular file is not guaranteed to flush its first `echo`
  before an arbitrarily small fixed capture-delay elapses (shell output
  buffering semantics for non-tty stdout are not line-buffered), which
  would make a real-subprocess assertion on the captured `session_id`
  timing-dependent/flaky. Used `python3 -u` (unbuffered) with an explicit
  sleep after the first flush so the ordering is deterministic instead of a
  timing gamble; kept the shared `fake_cli` fixture itself untouched
  (per-file-local helper, matching STANDING's "local fixtures go in YOUR
  test file" rule) and left every other `mock_adapters`-based wrapper test
  as-is.

## Suggestions for the Reviewer (informational only — not acted on)

- Consider whether `reject`/`merge` should also live behind the P15 UI
  config-mutation endpoints (`POST /api/config/...`) for parity with
  `pause`/`unpause`, which got both a CLI and a UI surface. Not requested by
  this handoff; flagging only because the precedent exists.
- If the `merge` verb invention above is rejected, the minimal fallback is:
  keep only a small `git rev-parse HEAD` helper (e.g.
  `paths.git_head(root)` or similar) for a future package to call, and drop
  `cmd_merge`/its tests from this diff.
- `REVIEW_REJECTED` (both the pre-existing frontier-review path and this
  package's new merge-gate path) still has no automatic forward motion in
  `reconcile.py` — worth a dedicated future package if the "SUPERSEDE +
  statefile reset" manual workaround (groop-P89) keeps recurring in
  production.
