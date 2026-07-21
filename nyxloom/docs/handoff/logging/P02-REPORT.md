# P02 — verbosity config, bootstrap & runtime control: REPORT

Branch: `feat/logging-p02-runtime-control` · commit: `861854b95ae108087fc6d8e7e70fd7c1f4efd580`
(three commits: `361ec69` core implementation, `bab0575` log-ordering fix,
`861854b` diff-coverage gap closure — see `P02-LOG.md` for the full
narrative of why each follow-up commit exists.)
Worktree: `/workspaces/vbpub/.worktrees/logging-p02-runtime-control` (from
`main` @ `9321dd6`).
Image gated against: `tester-unified:local` (already carried structlog from
P01's rebuild; verified present before running the suite).

**Not merged. Not deployed.**

## Gate (real exit code, no masking pipe)

Final run, against the committed HEAD (`861854b`):

```
$ docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
    bash -lc 'cd /workspaces/vbpub/.worktrees/logging-p02-runtime-control/nyxloom && \
      PYTHONPATH=src /opt/tester-venv/bin/python -m coverage run --source=src/nyxloom -m pytest tests -q && \
      PYTHONPATH=src /opt/tester-venv/bin/python -m coverage json -o /tmp/nyxloom-cov.json && \
      PYTHONPATH=src /opt/tester-venv/bin/python -m nyxloom.coverage_gate --base main \
        --coverage-json /tmp/nyxloom-cov.json --source src/nyxloom' ; echo GATE_EXIT=$?

........................................................................ [  6%]
........................................................................ [ 13%]
........................................................................ [ 20%]
........................................................................ [ 26%]
........................................................................ [ 33%]
........................................................................ [ 40%]
........................................................................ [ 46%]
.......x................................................................ [ 53%]
........................................................................ [ 60%]
........................................................................ [ 66%]
........................................................................ [ 73%]
........................................................................ [ 80%]
........................................................................ [ 86%]
........................................................................ [ 93%]
....................................................................     [100%]
Wrote JSON report to /tmp/nyxloom-cov.json
diff-coverage OK: 67/67 changed executable lines covered (100.0% ≥ 100.0% floor)
GATE_EXIT=0
```

**GATE_EXIT=0. diff-coverage OK: 67/67 (100.0%).** All dots (one pre-existing
`x` = xfail, unrelated to this package, same position as in P01's own
reported run); no `F`/`E` anywhere in this final run. Two earlier runs on
this branch are NOT being reported as the ship signal (a real test failure,
then a real 91.0% coverage gap) — both are narrated in `P02-LOG.md` with
what broke and how it was fixed, per the review checklist's "overclaimed
evidence" concern.

## Oracle-by-oracle evidence (docs/plan-logging.md §6, P02)

1. **Precedence chain — four tests, each removing the top layer.**
   - `test_resolve_level_runtime_file_beats_everything` — runtime-file
     wins over BOTH env and `[logging]` set to different values.
   - `test_resolve_level_env_beats_config_and_default` — runtime-file
     absent; env wins over `[logging]`.
   - `test_resolve_level_config_beats_default` — runtime-file + env
     absent; `[logging] level` wins over hardcoded INFO.
   - `test_resolve_level_default_when_nothing_set` — all three absent →
     `("info", "default")`, including with `registry=None`/`{}`.
   - Plus 4 more tests closing branches the gate's own diff-coverage run
     flagged: `test_resolve_level_treats_corrupt_layers_as_absent`
     (garbage runtime-file + garbage env, valid config → falls through
     twice to `("warning", "config")`),
     `test_resolve_level_runtime_file_read_error_falls_through` (a
     directory where the runtime-file should be → `OSError` → falls to
     env), `test_resolve_level_primary_config_load_failure_falls_through`
     (registry root has no project.toml at all → falls to default),
     `test_resolve_level_config_invalid_level_falls_through_to_default`
     (the project's own `[logging] level` is itself garbage → falls to
     default).

2. **Live flip, no restart + persists (simulated respawn).**
   `test_log_level_post_flips_live_no_restart_and_persists` — boots at
   INFO (a `.debug()` call is dropped, confirmed via `_read_log_records`);
   `POST /api/config/log-level {"level":"DEBUG"}` returns
   `{"ok": true, "level": "debug"}`; the SAME already-running process's
   `daemon.log` (obtained once, at import time) now emits `.debug()`
   records with **no reconfigure/restart** in between; the runtime-
   override file (`paths.daemon_log_level_path()`) now reads `"debug"`;
   and a **simulated respawn** — a fresh, standalone `daemon.resolve_level(
   d.registry)` call, not going through any live daemon state — reads the
   file back and returns `("debug", "runtime-file")`.

3. **Invalid level → 400, unchanged.**
   `test_log_level_post_invalid_level_400_unchanged` — `"not-a-real-level"`
   → HTTP 400; `resolve_level()` before and after the POST are identical
   (`("info", "default")`); the runtime-file was never created. Plus
   `test_log_level_post_missing_level_400` (missing `level` key → 400).

4. **No domain event (D-L4).**
   `test_log_level_post_emits_log_not_domain_event` — snapshots
   `storage.iter_events("demo", since=0)` before and after a successful
   `POST /api/config/log-level`; asserts the list is **byte-for-byte
   identical** (no `CONFIG_CHANGED`/any new event), while
   `_read_log_records` confirms an `{"level":"info", "msg":"log level
   changed", "new_level":"warning", ...}` record WAS written to the log
   file — the log fired, the event log did not move.

### Supporting coverage beyond the four named oracles

- `test_log_level_get_reports_effective_level_and_source` — `GET
  /api/logs/level` returns `{"level":"info","source":"default"}` at boot,
  then `{"level":"warning","source":"runtime-file"}` immediately after a
  live POST flip (no restart in between).
- `test_log_level_config_path_405_on_get` — `/api/config/log-level` joins
  `_CONFIG_POST_PATHS`; a `GET` on it is `405`, same as every sibling
  config-mutation endpoint.
- `test_daemon_run_configures_logging_before_loop_and_logs_started` —
  `Daemon.run()` (via the existing `_stop_event.set()` pre-`run()`
  immediate-exit pattern) emits an INFO `"daemon started"` record with
  `effective_level="info"`, `level_source="default"` before the main loop
  ever starts.
- `test_daemon_run_bootstraps_from_project_logging_level` — same pattern,
  but the project's `.nyxloom/project.toml` carries `[logging] level =
  "debug"`; the "daemon started" record shows `effective_level="debug"`,
  `level_source="config"` — proving D-L3 layer 3 is honoured end-to-end at
  real bootstrap, not just in the standalone `resolve_level()` unit tests.

## Design decisions worth flagging (full rationale in P02-LOG.md)

- **`log` vs `log_module` naming.** `daemon.py` already binds the name
  `log` to `get_logger("daemon")` (a `BoundLogger`, from P01). The
  `nyxloom.log` MODULE (needed for `configure`/`set_level`/
  `_normalize_level`) is imported separately as `log_module` — the
  handoff's prose ("reuse `log._normalize_level`") assumed no such
  shadowing; this repo's actual P01 code already has it, so the module
  needed its own name.
- **Field name collision with structlog's own `level` key.**
  `structlog.stdlib.add_log_level` unconditionally overwrites
  `event_dict["level"]` with the record's severity name, so a custom log
  kwarg literally named `level=` is silently destroyed. Every custom field
  in this package's log calls uses a distinct name (`effective_level`/
  `level_source`, `new_level`/`change_source`) — verified with a
  standalone 5-line smoke script before it ever reached a test (see
  P02-LOG.md item 8).
- **Log-before-flip ordering.** `_post_config_log_level` emits the INFO
  announcement BEFORE calling `log_module.set_level()`, specifically so
  flipping to a STRICTER level doesn't gate out its own announcement. This
  is the one real bug the gate's first run caught (P02-LOG.md).
- **"Primary project" = alphabetically-first registered project id.** The
  handoff/plan text never defines "primary" beyond that phrase. Reused the
  EXACT existing convention this same file's `/api/stream` bare-
  `EventSource` fallback already uses (`next(iter(sorted(self.registry)),
  None)`) rather than inventing a new one — a deliberate, named design
  choice (P02-LOG.md item 5), not an assumption slipped in silently.
- **Defensive fall-through, never a raise.** Every layer of
  `resolve_level()` re-validates its candidate via
  `log_module._normalize_level` and treats a `ValueError` (or, for the
  runtime-file, an `OSError` on read, or, for the config layer, any
  `Exception` from `ProjectConfig.load`) as "layer absent," falling
  through rather than propagating — so a corrupted runtime-file, a
  half-written project toml, or a typo'd `[logging] level` can never crash
  daemon bootstrap. All three failure modes have a direct test (see
  oracle 1's coverage-gap tests above).
- **`config.py`'s `[logging] level` is parsed with NO validation at load
  time** — an unrecognised name is caught by `resolve_level` (treated as
  absent), not by `ProjectConfig.load` itself, so a typo in ONE project's
  toml can never break config loading for every OTHER project sharing this
  frozen-elsewhere module.
- **No `nyxloom-config.schema.json` edit.** Confirmed (by reading the
  schema file) that the top-level object already has
  `"additionalProperties": true`, so an unrecognised `[logging]` table
  passes schema validation without any change — and the schema file is
  outside this package's `scope.touch` regardless.

## Deviations from the handoff

- **`config.py` touched** (handoff said "only if needed") — needed: D-L3
  layer 3 requires *some* code path to read `[logging] level` out of a
  project's config, and `ProjectConfig`/`ProjectConfig.load` is the only
  place that parses `nyxloom.toml`. The change is a single optional field
  + a two-line `.get()` — no existing field, validation, or behaviour
  touched.
- **`daemon.py`'s module docstring extended** (not explicitly listed in
  scope.touch, but the same file) — added documentation entries for the
  two new HTTP endpoints in the existing endpoint-list docstring, matching
  every prior package's own convention of documenting new routes there
  (P15/P16/P18/P22/P30 all did the same). No behavioural code in the
  docstring, purely descriptive.
- Everything else matches the handoff as specified: `resolve_level()`'s
  exact precedence order, the wire-in point in `Daemon.run()` (before the
  main loop, one INFO "daemon started" log), the POST/GET endpoint shapes,
  and D-L4 (log record, not a domain event).
- The `NYXLOOM_LOG_LEVEL` compose/infra bootstrap default (§7) was
  explicitly named as a deploy concern this package "may note ... but need
  not edit compose" — noted here, not implemented: the daemon reads the
  env var if a deploy sets it (layer 2 of `resolve_level`), but no
  `ciu.compose.yml`/`docker-compose.yml` was touched.

## For the controller (next steps, out of this package's scope)

- Merge is a controller action per the handoff's constraints (this session
  never merges/touches the running daemon). Re-validate from `main` post-
  merge via the same gate command, per the review checklist's
  environment-specific-claims item.
- P02 changes **daemon runtime code** (§7) → needs a daemon restart to
  take effect in production; behaviour is additive and stays functional at
  INFO throughout (no `[logging]`/env/runtime-file set anywhere yet in the
  real deployment, so `resolve_level()` will resolve to the same
  `("info", "default")` it always has).
- P04 (log-stream UI) depends on P02's `GET /api/logs/level` +
  `POST /api/config/log-level` being live; nothing in P04 needs further
  changes to this package's surface based on this implementation.
