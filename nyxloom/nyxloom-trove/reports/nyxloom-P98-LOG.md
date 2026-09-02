# nyxloom-P98 -- implementation LOG

One entry per commit, in order made. This is a fresh dispatch attempt against
the repaired, READY handoff (frozen `7aeb39ff`, matches this worktree's prior
HEAD `ec02107f` verified at start).

## Orientation (before any edit)

- Verified `git log --oneline -1` == `ec02107f` (matches the handoff's own
  frozen commit, `input_revision: "7aeb39ff"` in the frontmatter -- the file's
  own prior commit hash).
- Read `nyxloom-trove/decisions.md` (near-empty ledger, header line only --
  nothing dated after 2026-08-17 to skim).
- Read `nyxloom-trove/reports/nyxloom-P98-CARVE-REVIEW.md`,
  `nyxloom-P98-FIX-VERIFICATION.md`, `nyxloom-P98-FIX-VERIFICATION-2.md` in
  full (three adversarial review rounds; round 3 verdict: READY).
- Read the handoff's 13-item "Context to read first" list and the named
  source lines for each.
- Ran my own tabulated sweep (`git grep` for `coverage_gate`, `mutation_gate`,
  `gate_canary` across `src/`, `tests/`, `tools/`) BEFORE touching anything --
  see REPORT.md's "Independent sweep" section for the full table. Every hit
  was accounted for by a named Work item; no `escalate_if` #1/#2 trigger on
  the three-token sweep as scoped.

## Commits

1. **`18389a73`** -- fix(nyxloom): P98 -- extract mutants.py, delete
   coverage/mutation/canary toolkit (Work item 1). Created
   `src/nyxloom/mutants.py` (Mutant + generate_mutants, verbatim, plus their
   `_COMPARE_SWAP`/`_BOOLOP_SWAP` module-level dependencies the handoff's cited
   line range didn't separately call out but generate_mutants needs). Moved
   27 pure `generate_mutants` test cases to a new `tests/test_mutants.py`
   (import retargeted `nyxloom.mutants`); left
   `test_falsy_swap_motivating_survivor` with the deleted file since it also
   calls `mg._run_is_killed` (the deleted judgment half). Retargeted
   `tools/remote_mutation_audit.py`'s import. Deleted
   `src/nyxloom/coverage_gate.py`, `src/nyxloom/mutation_gate.py`,
   `src/nyxloom/gate_canary.py`, `tests/test_coverage_gate.py`,
   `tests/test_mutation_gate.py`, `tests/test_gate_canary.py`,
   `tests/test_gate_verify_cadence.py`.
2. **`74a3664f`** -- fix(nyxloom): P98 -- remove the GA1 gate-verify CLI
   surface (Work item 2). Deleted `cmd_gate_verify` and its only helper
   (`_asserts_mismatch_report`, unused elsewhere), the `gate_verify_parser`
   registration, the `args.gate_cmd == "verify"` dispatch branch, and the
   help-text entry. Reworded two more stale "run `nyxloom gate verify`"
   mentions in `cli.py` found during this same edit (the `--scaffold-gate`
   help string and its matching post-scaffold print).
3. **`c105767a`** -- fix(nyxloom): P98 -- remove the GA4 daemon cadence end
   to end (Work item 3). `effects_gates.py`: deleted `verify_gate`/
   `_run_verify_probe`/`drain_verify`, `verify_running`/`verify_results`, the
   `gate_canary` import, the `VerifyGate` HandlerSpec. `daemon.py`: dropped
   `gate_canary` from imports and `_days_since_gate_verify` + its call site.
   `reconcile.py`: deleted `VerifyGate` and item 16's docstring paragraph
   (numbering gap left, per the handoff's explicit instruction); edited item
   17's cross-reference; removed `TRACE_KINDS`'s `"gate-verify"` entry (a
   symbol-level finding my own sweep made: `rules_attention.gate_verify`'s
   `emit.note("gate-verify", ...)` is the only producer of that breadcrumb
   kind, and `test_planning.py`'s
   `test_the_breadcrumb_vocabulary_is_exactly_what_the_rules_can_record`
   checks `TRACE_KINDS` bidirectionally against what rules can actually
   record). Kept `ReconcileInput.days_since_gate_verify` and
   `Policy.gate_verify_interval_days` fully unedited. `rules_attention.py`:
   deleted `gate_verify` + the `VerifyGate` import. `planning.py`: deleted
   the `gate-verify` `RuleSpec`. `types.py`: deleted
   `EventType.GATE_VERIFY_RECORDED`.
4. **`9b85f970`** -- fix(nyxloom): P98 -- update tests for the removed
   GA1/GA4 surface (Work item 4). `test_cli.py`: deleted the whole gate-verify
   test block (~35 functions, 3 fixtures, 3 helpers) and the now-dead autouse
   `_healthy_transport_by_default` fixture; kept the two GA2 schema tests and
   `test_gate_no_subcommand_prints_help_and_exits_2`. `test_effects.py`,
   `effect_differential.py`: removed the `VerifyGate` sample-table entries.
   `test_invariants.py`, `test_snapshot_faults.py`: removed
   `GATE_VERIFY_RECORDED` from `KNOWN_IGNORED_EVENT_TYPES` and `IRREVERSIBLE`.
   `test_planning.py`: deleted `test_the_gate_verify_cadence_claims_nothing`.
   `corpus_profiles.py`: deleted the `"gate-verify-due"` profile tuple.
   Verified `test_daemon.py` (zero real VerifyGate content), `test_gap_audit.py`,
   `test_remote_mutation_audit_tools.py`, `planner_corpus.py`,
   `test_planner_differential.py` unchanged and collecting green.
5. **`17d21b2e`** -- fix(nyxloom): P98 -- fix stale onboarding/scaffold
   references and consumers (Work item 5). `onboarding_gate.py`: fixed all
   THREE "nyxloom gate verify" mentions (the module docstring is a third one
   beyond the two Work-item-named blocks -- O9's grep is whole-file). Dropped
   the `coverage_gate.py` mention. `gate_scaffold.py`: dropped the
   `coverage_gate` invocation line, `asserts` down to `["tests-pass"]`,
   reworded the ADJUST_MARKER comment and module docstring. Found while
   verifying O2/O9 would hold: `tests/test_onboarding_gate.py` (2 assertions)
   and `tests/test_cli.py::test_onboard_check_gate_offers_a_gate_when_none_declared`
   (1 assertion) and `tests/test_gate_scaffold.py` (1 assertion) all asserted
   the exact stale strings being changed -- fixed in the same commit.
6. **`479d7e71`** -- fix(nyxloom): P98 -- NL-4, add assay-verdict to the
   asserts enum (Work item 6). Schema enum + `config.py` docstring mirror;
   removed the stale `cli.cmd_gate_verify` docstring sentence.
7. **`05133b46`** -- docs(nyxloom): P98 -- STANDARD.md, retire the three
   nyxloom-gate-verify mentions (Work item 7). All four occurrences per the
   handoff's exact split.
8. **`e3535082`** -- docs(nyxloom): P98 -- assay.toml header, correct the
   mutation_gate.py claim (scope.touch item, no numbered Work item).
9. **`dd3c8c5c`** -- docs(nyxloom): P98 -- decisions.md, record the toolkit
   + GA1/GA4 retirement (Work item 8). 275-word entry, both required facts,
   the reorientation-report citation, and the field-exception rationale.
10. **`699e6a68`** -- docs(nyxloom): P98 -- archive the superseded P90
    extract-testing-library proposal (Work item 9).
11. **`1c455ca7`** -- fix(nyxloom): P98 -- fix two gate failures found only
    by actually running the gate. `test_planning.py`'s
    `test_every_rule_names_a_real_contract_item` (module-contract-item
    completeness check; item 16 is now a second deliberate exception, like
    item 8) and `tests/test_effect_differential.py`'s frozen fixture
    (re-recorded per its own documented workflow -- one-entry diff, exactly
    the expected `"verify-gate-dispatch": []` removal). Both files were
    already in scope.touch for this class of edit.
12. **`918c3a03`** -- fix(nyxloom): P98 -- close a real changed-line-coverage
    gap in mutants.py. Assay's R1 claim failed (99.47%, `mutants.py:98`
    uncovered) on the first real gate run. Added one test case
    (`test_falsy_swap_ignores_a_non_literal_return_value`) exercising
    `_falsy_swap_target`'s final fallback for a non-literal return value
    (`return x > 0`). Verified 100% coverage on `mutants.py` afterward.

## Gate runs

Two full `./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-p98
tester-unified` runs, both from inside this worktree's `nyxloom/` directory,
each immediately `docker update --cpus=3`'d on its container per the
shared-host load rule:

- **Run 1** (commit `699e6a68`, before commits 11-12): FAIL/COMMAND_FAILED.
  4 pytest failures: `test_core_characterization.py::test_inventory_paths_all_exist`,
  `::test_inventory_sizes_are_within_the_declared_tolerance`,
  `test_effect_differential.py::test_moved_effects_reproduce_the_pre_cr05_event_sequences`,
  `test_planning.py::TestThisRepo::test_every_rule_names_a_real_contract_item`.
  Also R1 (changed-line-coverage) FAIL at 99.47%
  (`src/nyxloom/mutants.py` line 98 uncovered).
- Commits 11-12 fixed 3 of the 4 pytest failures and the R1 coverage gap.
- **Run 2** (commit `918c3a03`, current HEAD): still FAIL/COMMAND_FAILED --
  exactly the same 2 `test_core_characterization.py` failures, nothing else.
  R1 now PASS at 100%.

See REPORT.md's BLOCKED section for the full diagnosis: both remaining
failures trace to a single stale row (`src/nyxloom/gate_canary.py`) in
`nyxloom-trove/reports/CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md`, a
non-test report file not in this package's `scope.touch` and not named
anywhere in the handoff, its Context list, or either prior adversarial
review round.

## Round 2 -- handoff repaired (input_revision `fca2d122`), Work item 10

The coordinator confirmed the BLOCKED finding was correct and reported it
repaired: the handoff grew a new Work item 10 (and `scope.touch`/O2 grew to
match), frozen at `fca2d122`, matching this worktree's HEAD (`bb46cbbb`,
"fix-verification round 4") before this round started. My 13 prior commits
stood unchanged. Re-read the full handoff (frontmatter + body) to confirm
Work item 10's exact wording before touching anything: THREE rows, not two
-- `gate_canary.py` (remove), `effects_gates.py` and `cli.py` (both already
over tolerance, re-measure for real with `wc -l`, don't hardcode), and
`rules_attention.py` (re-measure too even though still in tolerance; trim
its stale prose regardless).

13. **`1b76dfa9`** -- docs(nyxloom): P98 -- Work item 10, fix all three
    stale ownership-inventory rows. In
    `nyxloom-trove/reports/CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md`:
    removed the `src/nyxloom/gate_canary.py` row entirely. Re-measured with
    real `wc -l` on the current tree: `effects_gates.py` 473 -> 354,
    `cli.py` 2,469 -> 2,220, `rules_attention.py` 118 -> 83 (matches the
    coordinator's stated numbers exactly). Trimmed `effects_gates.py`'s
    "the gate-verify cadence and post-merge validation, including BOTH
    background-work registries" to "post-merge validation, including its
    background-work registry" (only one family remains); trimmed
    `rules_attention.py`'s "...and the gate-verify cadence that is
    deliberately outside the carve mutex" clause entirely (`cli.py`'s
    "Operator and recovery commands" text needed no wording change, only
    the count). Added one consolidated "Re-measured 2026-09-02
    (nyxloom-P98)" note following the document's own existing convention,
    covering all three rows and citing `decisions.md`. Verified locally:
    all 26 `tests/test_core_characterization.py` tests pass, including
    both named oracle tests.

## Gate runs, round 2

- **Run 3** (commit `1b76dfa9`): `FAIL/COMMAND_FAILED`. A single,
  unrelated failure: `tests/test_properties.py::test_sequence_integrity_under_concurrency`
  -- `sqlite3.OperationalError: database is locked` inside a forked worker,
  a real-SQLite multiprocess-concurrency test with no connection to this
  package's diff (nothing touched touches `storage.py`/`storage_sqlite.py`/
  `test_properties.py`). Host load was 8.93/10.27/8.20 (1/5/15-min) with 67
  containers running at the time -- consistent with a load-induced SQLite
  lock-wait timeout, not a regression. Waited for load to ease (settled to
  ~4.2 after several minutes) and re-ran rather than editing anything.
- **Run 4** (same commit `1b76dfa9`, unchanged tree): `PASS (exit 0)`. Both
  R0 and R1 assay claims PASS. Confirms Run 3's failure was the transient
  host-contention flake it looked like, not a real regression -- identical
  code, identical commit, clean pass once the host had room to run the
  concurrency test's four forked workers without starving each other.

All 9 oracles now verified PASS -- see REPORT.md's updated O2 section and
closing verdict.
