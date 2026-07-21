# SP04 — `nyxloom events` greppability bridge — REPORT

Branch: `feat/state-sp04-events-bridge`
Worktree: `/workspaces/vbpub/.worktrees/state-sp04-events-bridge`
Base: `main` @ `facd686`
Commit (this package): `096c467` — feat(nyxloom): SP04 — `nyxloom events` greppability bridge

## Gate evidence (the ONLY ship signal)

Ran in `tester-unified:local`, exactly the command given in the handoff,
from `main` via the worktree bind mount:

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -lc 'cd /workspaces/vbpub/.worktrees/state-sp04-events-bridge/nyxloom && \
    PYTHONPATH=src /opt/tester-venv/bin/python -m coverage run --source=src/nyxloom -m pytest tests -q && \
    PYTHONPATH=src /opt/tester-venv/bin/python -m coverage json -o /tmp/nyxloom-cov.json && \
    PYTHONPATH=src /opt/tester-venv/bin/python -m nyxloom.coverage_gate --base main \
      --coverage-json /tmp/nyxloom-cov.json --source src/nyxloom' ; echo GATE_EXIT=$?
```

Result (run against commit `096c467`, the only commit in this package —
committed BEFORE this run, so the diff-coverage denominator is real, not
the vacuous 0/0 an uncommitted worktree would produce):

```
........................................................................ [  6%]
........................................................................ [ 13%]
........................................................................ [ 20%]
........................................................................ [ 27%]
........................................................................ [ 34%]
........................................................................ [ 41%]
...............................................................x........ [ 48%]
........................................................................ [ 55%]
........................................................................ [ 62%]
........................................................................ [ 69%]
........................................................................ [ 76%]
........................................................................ [ 83%]
........................................................................ [ 90%]
........................................................................ [ 97%]
......................                                                   [100%]
Wrote JSON report to /tmp/nyxloom-cov.json
diff-coverage OK: 19/19 changed executable lines covered (100.0% ≥ 100.0% floor)
GATE_EXIT=0
```

**PASS: `GATE_EXIT=0`, `diff-coverage OK: 19/19 changed executable lines
covered (100.0% ≥ 100.0% floor)`.** The 19 changed executable lines are
`cli.py`'s new `cmd_events` body (the docstring itself is non-executable and
correctly excluded) plus the two new `events_parser.add_argument` calls —
every one of them ran under test.

Full-suite count, confirmed separately with a local devcontainer venv run
(Python 3.14.6) for a human-readable summary line (the gate command's own
`-q` stacks with `pyproject.toml`'s `addopts = "-q"` into a quieter mode
that suppresses it — this does not affect the gate's verdict):

```
1029 passed, 1 xfailed, 1 warning in 224.11s (0:03:44)
```

The one xfail is the same pre-existing, unrelated
`tests/test_invariants.py::test_no_dead_end_draft` noted in SP01/RP01's own
reports (a documented out-of-scope `TaskState.DRAFT` tracking gap,
`xfail(strict=True)`) — untouched by this package. Zero failures.

The devcontainer venv run above (and an earlier local pass used only to
iterate while writing the tests) is NOT a ship signal per CLAUDE.md's
cockpit-vs-gating-test-runner rule — the `tester-unified` run is.

### First gate attempt (informational — not the ship signal)

The first invocation of the exact gate command hit the *shell tool's own*
2-minute default timeout (the suite itself runs ~4 minutes, the same order
as SP01's ~227s and RP01's ~227s reports) — not a test or coverage failure.
Re-ran with an explicit longer timeout; the run above is the real,
complete result.

## What was built

`nyxloom/src/nyxloom/cli.py` — extended the **pre-existing** `events` verb
(added long before this plan, in the original P10 CLI package `d7e553a`;
NOT new SP04 scaffolding) rather than adding a new subparser/dispatch
branch:

- `cmd_events(args)` rewritten: kept `--since SEQ` / `--type T` behavior
  unchanged (backward compatible with `tests/test_cli.py`'s
  `test_events_all` / `test_events_filtered_by_type`, which are out of
  scope.touch and were left untouched — both still pass, see below).
  Dropped the previously-unused `_cfg(args.project)` registry lookup so an
  unknown/never-written project is not an error (oracle 4). Added:
  - `--json` — an explicit, documented no-op alias for the already-JSONL
    default output (there is no other output mode in this package).
  - `--tail` — after the initial dump, polls `time.sleep(1.0)` then
    re-queries `storage.iter_events(project, since=last_seq)` for the
    delta, printing any new lines; `last_seq` advances on every event
    iterated (not just type-filtered-in ones) so the frontier tracking
    stays exact across `--type` + `--tail` combined. `KeyboardInterrupt`
    during the poll is caught and the command returns `0`.
- `events_parser` gained the corresponding `--tail`/`--json`
  `action="store_true"` arguments.
- The module's top-of-file "INTERFACE CONTRACT (frozen)" docstring updated
  to describe the new flags and the read-only/no-crash-on-unknown-project
  contract.

`nyxloom/tests/test_events_cmd.py` (new, 11 tests).

## Deviation from the handoff prompt (documented, not a scope violation)

The handoff instructed anchoring the new `events` verb "immediately after
the `resync` subcommand" to minimize merge conflict with SP02 (which
anchors `migrate-store` after `render`). Investigation (LOG step 3) found
an **`events` verb already exists** in `cli.py` — added in the original
P10 CLI package, well before this plan — with `--since`/`--type` already
dumping JSONL via `storage.iter_events`. It lives near `digest`/`version`,
nowhere near `render`. Relocating it to sit after `resync` would have
required deleting the subparser registration and dispatch branch from
their current position and re-inserting them elsewhere — MORE diff, for
zero actual conflict-avoidance benefit, since the existing location
already doesn't overlap SP02's `render`-adjacent edits. Left the
subparser/dispatch position untouched; only extended `cmd_events`'s body
and `events_parser`'s argument list in place. `git diff --stat main...HEAD`
confirms the only file touched is `cli.py` (plus the new test file and
this package's own handoff docs) — no subparser/dispatch-chain lines moved.

## Oracle-by-oracle evidence

All in `tests/test_events_cmd.py`, run primarily against the SQLite backend
(`sqlite_backend` fixture: `tmp_state` + `monkeypatch.setenv
("NYXLOOM_STATE_BACKEND", "sqlite")`, mirroring `test_storage_sqlite.py`'s
own fixture) — the backend SP04 exists to restore greppability for — with
the round-trip and `--since` oracles ALSO run against the default file
backend to prove genuine backend-agnosticism (`cmd_events` only ever calls
`storage.iter_events`, never a backend module directly).

1. **Round-trip.** `test_round_trip_sqlite_backend` and
   `test_round_trip_file_backend`: seed N standalone events via
   `storage.append_event`, dump via `cli.main(["events", project])` (and
   `--json`), parse each captured stdout line with `json.loads`, and assert
   the parsed list is `==` to `[ev.to_dict() for ev in
   storage.iter_events(project)]` — same count (4 / 3), same seq order,
   same payloads, full dict equality, not a loose substring check.
   `test_json_flag_is_explicit_alias_for_default_output` additionally
   proves `--json` and the bare default produce byte-identical stdout.
2. **`--since` filters.** `test_since_filters_to_higher_sequence` (SQLite:
   seed 1..5, `--since 2` → `[3, 4, 5]`) and
   `test_since_filters_file_backend` (file backend: seed 1..3, `--since 1`
   → `[2, 3]`).
3. **`--tail` follows a new append, then interrupts cleanly.**
   `test_tail_follows_new_append_then_interrupts_cleanly`: monkeypatches
   the real stdlib `time.sleep` so the FIRST call appends a new event
   (simulating an external actor writing while tailing) and returns
   normally — the loop's second `_dump_since` call then genuinely emits
   that new line — and the SECOND call raises `KeyboardInterrupt`,
   exercising the `except KeyboardInterrupt: pass` path and the final
   `return 0`. Asserts `exit_code == 0`, exactly 2 fake-sleep calls
   happened, and the dumped sequences are `[1, 2]` with the tailed event's
   payload intact. `test_tail_with_no_new_events_still_interrupts_cleanly`
   covers the bare-interrupt-with-nothing-new path too. No thread, no
   wall-clock wait — deterministic and instant.
4. **Unknown/empty project.** `test_unknown_project_file_backend_emits_
   nothing` and `test_unknown_project_sqlite_backend_emits_nothing`: a
   project id string that was never registered and has no prior events —
   `cli.main(["events", "<never-seen>"])` prints nothing (`capsys` stdout
   is exactly `""`) and returns `0`. No exception, no traceback.

## Backward compatibility (pre-existing tests, out of scope.touch)

`tests/test_cli.py`'s `test_events_all` and `test_events_filtered_by_type`
(not touched — not in scope.touch) still pass unmodified: they call
`cli.main(["events", "demo"])` / `cli.main(["events", "demo", "--type",
"PAUSE_SET"])` against the `sample_project` fixture's registered "demo"
project and assert the expected JSON substrings appear/don't appear. Both
green in the same suite run above (part of the `1029 passed`).

## Deviations from scope.touch

None. Touched exactly: `nyxloom/src/nyxloom/cli.py` (module docstring +
`cmd_events` + the two `events_parser.add_argument` calls — no other
function/subparser edited), `nyxloom/tests/test_events_cmd.py` (new),
`nyxloom/docs/handoff/state-integrity/{SP04-LOG,SP04-REPORT}.md` (new,
this package's own handoff artifacts). Confirmed via `git diff --stat
main...HEAD` and `git show --stat 096c467` — no other file appears.
`storage.py`/`storage_sqlite.py`/`types.py` were read but not edited
(grep-confirmed no diff hunks against any of them); only the public
`storage.iter_events` API was used, per the handoff's hard rule.

## No BLOCKED conditions

Every oracle was satisfiable within `cli.py` (extended) +
`test_events_cmd.py` (new). No escalation needed.

## Not merged

Per instructions, this branch was NOT merged to `main` and the running
daemon was not touched. Ready for review at `feat/state-sp04-events-bridge`
(`096c467`).
