# SP04 — `nyxloom events` greppability bridge — LOG

Branch: `feat/state-sp04-events-bridge`
Worktree: `/workspaces/vbpub/.worktrees/state-sp04-events-bridge`
Plan: `nyxloom/docs/plan-state-integrity.md` Part A (A.3, phase SP04)

## Actions

1. Created worktree `feat/state-sp04-events-bridge` from `main` (HEAD
   `facd686` — RP01's merge commit, the tip named in the handoff).
2. Read context: `nyxloom/docs/plan-state-integrity.md` Part A.3 (SP04's own
   bullet), `nyxloom/src/nyxloom/storage.py` (the file backend + the
   `NYXLOOM_STATE_BACKEND` selector guard clauses on every public function),
   `nyxloom/src/nyxloom/storage_sqlite.py` (the SP01 SQLite backend —
   `iter_events` SELECTs `seq > ?  ORDER BY seq`, reconstructs the same
   `Event` shape), `nyxloom/src/nyxloom/types.py` (`Event`/`_Serde.to_dict()`
   — plain dict of `schema_version, sequence, timestamp(iso), project,
   actor{kind,id}, type, payload, task_id, attempt_id, wave_id,
   decision_id`), `nyxloom/src/nyxloom/paths.py` (`events_path`/
   `project_dir`/`ensure_layout` — confirmed no registry/config dependency:
   any project-id string works, registered or not).
3. **Found an existing `events` CLI verb already in `cli.py`** — added in
   the original P10 CLI package (`d7e553a`, the handoffctl→nyxloom rename;
   present since before SP04 was ever planned), with `--since`/`--type`
   already dumping `Event.to_dict()` JSONL via `storage.iter_events`. This
   is NOT an SP04 artifact — it predates this plan. `tests/test_cli.py`
   already has two tests for it (`test_events_all`,
   `test_events_filtered_by_type`) which are OUT of scope.touch (only
   `test_events_cmd.py` is listed) and were left untouched — confirmed both
   still pass unmodified against the new implementation (see REPORT).
4. Decision: **extend the existing verb in place, do not relocate it.**
   The handoff's "anchor immediately after `resync`" instruction was
   written to minimize conflict with SP02 (which anchors `migrate-store`
   after `render`) under the apparent assumption that `events` did not yet
   exist and would need a brand-new subparser + dispatch-branch insertion.
   Since the subparser registration and `elif args.cmd == "events":`
   dispatch branch already exist (near `digest`/`version`, nowhere near
   `render`), relocating them would touch MORE of the file (delete here,
   re-insert there) for zero conflict-avoidance benefit — the existing
   location already doesn't collide with SP02's `render`-adjacent edits.
   Left the subparser/dispatch position untouched; only extended
   `cmd_events`'s body and the `events_parser.add_argument(...)` calls
   in place. Documented as a deviation (see REPORT).
5. Grepped `src/nyxloom/*.py` and `docs/*.md` for any other reference to
   `cmd_events`/the `events` verb — only `docs/plan-state-integrity.md`'s
   own SP04 bullet; no other module calls it, so no other blast radius.
6. Rewrote `cmd_events` (docstring + body): kept `--since`/`--type`
   behavior byte-for-byte compatible; dropped the (unused, dead-result)
   `_cfg(args.project)` registry lookup so an unknown/never-written
   project id is not an error — `storage.iter_events` already yields
   nothing for a project with no `events.jsonl`/no `state.db` row on
   either backend, and `paths.ensure_layout`/`project_dir` don't consult
   the registry at all, so this "just works" without a special case.
   Added `--json` (explicit alias for the already-JSONL default — no other
   output mode exists, so it is a documented no-op) and `--tail` (poll
   loop: `time.sleep(1.0)` then re-`iter_events(project, since=last_seq)`,
   tracking `last_seq` across the whole call including filtered-out
   events so a later `--since` boundary is exact; `except
   KeyboardInterrupt: pass` then `return 0`).
7. Added the two new `argparse.add_argument` calls to `events_parser`.
8. Wrote `tests/test_events_cmd.py` (new, 11 tests) covering all 4 oracles
   — see REPORT for the oracle-by-oracle mapping. The `--tail` test
   monkeypatches the real stdlib `time.sleep` (not a module-local import)
   so the loop body runs deterministically and boundedly: first fake-sleep
   call appends a new event then returns (loop iterates once for real),
   second fake-sleep call raises `KeyboardInterrupt` (loop exits, `return
   0`) — no thread, no wall-clock wait, no hang risk.
9. Ran a local devcontainer venv pass (`PYTHONPATH=src python3 -m pytest
   tests -q`, Python 3.14.6) as a fast dev-loop check ONLY — per
   CLAUDE.md's cockpit-vs-gating-test-runner rule this is NOT the ship
   signal. Green (same pre-existing 1 xfail as SP01/RP01's reports note).
10. Committed (`096c467`) BEFORE the real gate run (avoiding the vacuous
    0/0 diff-coverage false-green the handoff warns about).
11. Ran the real gate in `tester-unified:local` per the handoff's exact
    command. First invocation hit the shell tool's own 2-minute default
    timeout (not a gate failure — the suite itself takes ~4 minutes, same
    order as SP01/RP01's reported ~227s); re-ran with an explicit longer
    timeout. **GATE_EXIT=0, diff-coverage OK: 19/19 changed executable
    lines covered (100.0%).** See REPORT.

## Design decisions

- **No new project-registry validation.** This is a deliberate behavior
  change from the pre-existing `cmd_events` (which called `_cfg()` but
  never used the result): oracle 4 requires an unknown project to be a
  clean no-op, and `storage.iter_events` already has that property
  natively on both backends. Removing the dead `_cfg()` call is the
  simplest way to satisfy the oracle without a special-cased `try/except`.
- **`--tail`'s poll interval is a literal `1.0`, not a module constant.**
  The handoff suggested "monkeypatch/inject the interval"; the simpler
  seam is monkeypatching `time.sleep` itself (patches the real stdlib
  module regardless of import style), which the test uses to both inject
  a side effect (append an event) and terminate the loop
  (`KeyboardInterrupt`) — no separate configurable constant needed, and
  no behavior to get wrong by exposing one.
- **`last_seq` advances on every event iterated, not just filtered-in
  ones.** So `--type` + `--tail` together still track the true log
  frontier; a filtered-out event's sequence isn't re-scanned forever.
- **`--json` is a genuine no-op**, documented as such in both the CLI
  docstring and a dedicated test
  (`test_json_flag_is_explicit_alias_for_default_output`) asserting
  identical output with/without the flag — there is no second output mode
  in this package, so making the flag "do something different" would be
  inventing a contract nobody asked for.

## Status

Implementation complete, gate green. See SP04-REPORT.md for full evidence.
