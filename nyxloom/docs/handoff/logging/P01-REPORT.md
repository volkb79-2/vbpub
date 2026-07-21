# P01 — logging core: REPORT

Branch: `feat/logging-p01-core` · commit: `9f63b8cd280ec3403947aa4f22065adcb6afbc17`
(parent `25d552e` — see `P01-LOG.md` for the two-commit sequence: core
implementation, then a follow-up closing diff-coverage gaps found by the
gate's own first real run).
Worktree: `/workspaces/vbpub/.worktrees/logging-p01-core` (from `main` @ `622d4cb`).
Image gated against: `tester-unified:structlog` (NOT `:local` — two sibling
agents were gating against that tag concurrently, per the handoff's explicit
instruction).

**Not merged. Not deployed.** Per the handoff: the controller must rebuild
`tester-unified:local` (= this `:structlog` build) and the nyxloomd runtime
image (`/opt/nyxloom-venv`) at merge/deploy time, since structlog is now a
runtime dependency (§7 of `docs/plan-logging.md`).

## Structlog smoke line

```
$ docker build -f tester-unified/Dockerfile -t tester-unified:structlog .
...
#10 25.49 tester-unified closure OK
...
$ docker run --rm tester-unified:structlog /opt/tester-venv/bin/python -c \
    'import structlog; print("structlog", structlog.__version__)'
structlog 26.1.0
```

(`pyproject.toml` declares `structlog>=24,<27`; the image resolves the
current latest, 26.1.0.)

## Gate (real exit code, no masking pipe)

Final run, against the committed HEAD (`9f63b8c`):

```
$ docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:structlog \
    bash -lc 'cd /workspaces/vbpub/.worktrees/logging-p01-core/nyxloom && \
      PYTHONPATH=src /opt/tester-venv/bin/python -m coverage run --source=src/nyxloom -m pytest tests -q && \
      PYTHONPATH=src /opt/tester-venv/bin/python -m coverage json -o /tmp/nyxloom-cov.json && \
      PYTHONPATH=src /opt/tester-venv/bin/python -m nyxloom.coverage_gate --base main \
        --coverage-json /tmp/nyxloom-cov.json --source src/nyxloom' ; echo GATE_EXIT=$?

........................................................................ [  7%]
........................................................................ [ 14%]
........................................................................ [ 21%]
........................................................................ [ 28%]
........................................................................ [ 35%]
........................................................................ [ 43%]
......................................................x................. [ 50%]
........................................................................ [ 57%]
........................................................................ [ 64%]
........................................................................ [ 71%]
........................................................................ [ 78%]
........................................................................ [ 86%]
........................................................................ [ 93%]
....................................................................     [100%]
Wrote JSON report to /tmp/nyxloom-cov.json
diff-coverage OK: 110/110 changed executable lines covered (100.0% ≥ 100.0% floor)
GATE_EXIT=0
```

**GATE_EXIT=0. diff-coverage OK: 110/110 (100.0%).** All dots (one
pre-existing `x` = xfail, unrelated to this package — present in the same
position before and after this branch's changes, part of the existing
suite); no `F`/`E` anywhere in the run. (Note: this project's pytest
configuration does not print the usual trailing "N passed in Ys" summary
line — verified pre-existing/ambient, reproduces identically on an
unrelated single test file with zero P01 code involved; the dot stream +
`GATE_EXIT=0` + the coverage_gate's own explicit pass/fail line are the
authoritative signals per the handoff's stated PASS criteria, and both are
green.)

### First run caught a real process mistake (documented, not hidden)

The very first gate invocation (before anything was committed) reported
`GATE_EXIT=0` with `diff-coverage OK: 0/0 changed executable lines covered`
— because the branch had no commits yet (`git worktree add -b` only moves
the branch pointer to `main`'s tip; HEAD == main until a commit lands), so
`coverage_gate`'s `git diff <merge-base> HEAD` was comparing identical
trees. Not a tool bug. Fixed by committing, then re-running — see
`P01-LOG.md` for the full narrative. The **second** run (post-commit, first
real diff) surfaced a genuine gap — 92.7% (102/110) — which is the run that
led to the coverage-gap-closing follow-up commit; the **third** run (above)
is the one that actually passed and is the one being reported as the ship
signal.

## Oracle-by-oracle evidence (docs/plan-logging.md §6, P01)

1. **Processor chain renders bound context + UTC `ts`.**
   `tests/test_log.py::test_record_carries_bound_context_and_utc_ts` —
   asserts `re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", rec["ts"])`,
   plus `project`/`task` (bound via `log.bind()`) and an ad-hoc `extra=42`
   kwarg all present in the rendered JSONL record.

2. **`bind()` nests and clears on exit, including on exception.**
   `test_bind_nests_and_clears_including_on_exception` — checks
   `structlog.contextvars.get_contextvars()` at each nesting level (empty →
   `{project}` → `{project, task}` → back to `{project}` → back to `{}`),
   then repeats with a `RuntimeError` raised inside the `with` block,
   confirming full reset even on the exception path.

3. **Level gating: DEBUG dropped at INFO, emitted at DEBUG.**
   `test_level_gating_debug_dropped_at_info_emitted_at_debug` — same logger
   object, file empty after `configure(INFO)` + `.debug()`, one record after
   `configure(DEBUG)` + `.debug()`.

4. **`set_level()` changes the effective level live.**
   `test_set_level_changes_effective_level_live` — a single already-obtained
   logger (`lg = log.get_logger("live")`, obtained BEFORE the level flip)
   drops a `.debug()` call at INFO, then emits a `.debug()` call on the SAME
   `lg` object after `log.set_level(log.DEBUG)` — no re-`get_logger()` call
   in between. This is the mechanism daemon-wide `set_level()` needs to work
   without a restart (module-level loggers obtained once at import time).

5. **File (JSON) vs console (human) hold independent levels.**
   `test_file_and_console_handlers_hold_independent_levels` — with the
   effective level at DEBUG, the file contains both `"debug line"` and
   `"info line"`; `capsys`-captured stderr contains `"info line"` but NOT
   `"debug line"` (console is pinned at INFO regardless of the global
   effective level, per §4.1).

6. **TRACE works and sits below DEBUG.**
   `test_trace_sits_below_debug` — at effective TRACE, both `.trace()` and
   `.debug()` emit; after reconfiguring to effective DEBUG, `.trace()` is
   dropped while `.debug()` still emits. Plus
   `test_trace_supports_percent_style_args` (`%`-style interpolation) and
   `test_trace_returns_none_on_drop_event` (direct unit test of the
   DropEvent branch structlog's own `_proxy_to_logger` pattern requires).

7. **`configure()` is idempotent; never mutates the stdlib root.**
   `test_configure_idempotent_and_does_not_touch_stdlib_root` — snapshots
   `logging.getLogger()` (real root)'s handlers+level and a sibling
   `logging.getLogger("other")`'s handlers+level before two back-to-back
   `configure()` calls; asserts both are byte-identical after. Also asserts
   `logging.getLogger("nyxloom")` ends up with exactly ONE handler (not two)
   after the repeat call, and that a subsequent log call produces exactly
   one JSONL line (no duplicate-handler double-write).

8. **The converted http_bind notice emits via `log.warning`; its 2 tests
   are green.** `tests/test_daemon.py::test_nonloopback_bind_prints_unauthenticated_notice`
   and `::test_loopback_bind_prints_no_notice_THE_NEGATIVE` — both updated
   to call `log.configure(level=log.INFO, log_dir=tmp_state/"logs",
   console=False)` and read back the JSONL record (`_read_log_records`
   helper added directly above them) instead of capturing stderr. Positive
   test asserts a `level == "warning"` record exists with `"UNAUTHENTICATED"
   in msg` and `http_bind == "0.0.0.0"`; negative test asserts no such
   record exists for the loopback-default case. Both pass in the gate run
   above (part of the green 110/110 + full suite pass).

## Design decisions worth flagging (full rationale in P01-LOG.md)

- **`cache_logger_on_first_use=False` is load-bearing**, not an oversight:
  it's what makes `set_level()` reach already-`get_logger()`'d module-level
  loggers without a restart (verified by oracle 4's test, and by reading
  structlog 26.1.0's own `BoundLoggerLazyProxy.bind()`/`__getattr__`
  source — its docstring for `make_filtering_bound_logger` warns a
  *resolved* instance can't change level, but an *unresolved* lazy proxy
  re-resolves on every call as long as caching stays off).
- **`logging.getLogger("nyxloom").setLevel(1)`, not `logging.NOTSET`** — a
  bug caught by local smoke-testing before the docker gate: for a
  *non-root* logger, `NOTSET` means "delegate to the parent chain" (not
  "accept everything"), which silently walked up to the real stdlib root's
  default `WARNING` and dropped every record structlog's own gate had
  already allowed through. Commented in the source to prevent recurrence.
- **`TRACE(5)`'s `.trace()` method can't reuse structlog's
  `_proxy_to_logger`** as-is (it calls `getattr(stdlib_logger,
  method_name)`, and stdlib has no `.trace`) — uses the documented
  lower-level `_process_event()` + `self._logger.log(<numeric level>, ...)`
  extension point instead.
- **`structlog>=24,<27`**, not the handoff's example `<26` — 26.1.0 is the
  actual current latest and what pip resolves into the image; capping at
  `<26` would have pinned to a stale `25.x` release for no reason.

## Deviations from the handoff

None functional. The only process deviation is the one documented above
(forgot to commit before the first gate run) — self-caught, corrected, and
left in the LOG rather than silently redone, per the review checklist's
"overclaimed evidence" concern (a first `GATE_EXIT=0` that meant nothing is
exactly the kind of misleading-if-uncommented result that checklist warns
about).

## For the controller (next steps, out of this package's scope)

- Rebuild `tester-unified:local` from `main` post-merge (this `:structlog`
  build IS that rebuild, just under a non-colliding tag while sibling
  agents were also gating).
- Rebuild the nyxloomd runtime image (`ciu up --dir nyxloom/nyxloomd`) at
  deploy so `/opt/nyxloom-venv` carries structlog — P01 does not wire
  `configure()` into `Daemon.run()` (that's P02), so production behaviour
  is unaffected until P02 lands; the only observable change post-deploy is
  the one converted `log.warning` call (inert unless something actually
  binds non-loopback, same trigger condition as before).
