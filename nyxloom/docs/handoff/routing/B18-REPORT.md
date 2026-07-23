# B18 — model capability catalog (D-R13) — report

## What was built

- `src/nyxloom/capability_map.py` (new, 279 lines) — `CapabilityRecord`,
  `CapabilityMapConfig` (`default`/`from_dict`/`load`/`thresholds`),
  `assemble_catalog`, the managed-block writer
  (`write_capability_catalog` + private helpers), and `refresh_catalog`.
- `tests/test_capability_map.py` (new, 22 tests) — in-memory
  `BenchmarkRecord` inputs, `tmp_path` file I/O, no live network (source
  fetch is mocked), no live routes.toml mutation.
- `docs/handoff/routing/B18-LOG.md` / this file — the two allowed doc
  additions beyond `scope.touch`.

`scope.touch` honored exactly: no file outside these four was touched.
`config.py`, `benchmark_sources.py`, `band_thresholds.py`,
`free_models.py`, `routes.host.toml` are all untouched (`git status`
confirms only the two `src`/`tests` files as new, plus the two doc files).

## Oracle → test mapping

| Oracle | Test(s) |
|---|---|
| O1 (deterministic banding, coding-axis drives implement gating) | `TestBanding::test_coding_score_above_upper_cutoff_bands_top`, `::test_coding_score_exactly_on_cutoff_bands_higher_inclusive`, `::test_axis_missing_from_scores_bands_zero_unrated` |
| O2 (auto vs operator role_gating, both directions) | `TestRoleGating::test_auto_mode_gates_on_agentic_band_thresholds`, `::test_operator_mode_grants_override_band_in_both_directions` |
| O3 (hard filters exclude) | `TestHardFilters::test_below_min_context_is_excluded`, `::test_missing_required_flag_is_excluded`, `::test_excluded_records_never_appear_review_or_carve_eligible` |
| O4 (writer idempotence) | `TestWriterIdempotence::test_writing_twice_is_byte_identical`, `::test_write_adds_trailing_newline_when_source_lacks_one` |
| O5 (writer non-clobber) | `TestWriterNonClobber::test_preserves_free_models_block_and_hand_authored_tier` |
| O6 (round-trip parse) | `TestRoundTrip::test_written_catalog_round_trips_through_tomllib_and_routes_load` |
| O7 (empty catalog) | `TestEmptyCatalog::test_empty_catalog_writes_valid_idempotent_block` |

Additional tests beyond the seven oracles (config loader default/from_dict/
load coverage, `thresholds()`, optional-field omission when `None`,
`refresh_catalog` composition + its `cfg=None` default-load path via the
`tmp_state` conftest fixture) exist purely to close 100%-diff-coverage
gaps the first draft's oracle tests alone didn't reach — see LOG for the
exact branches.

## Verification performed (honest accounting)

1. **Syntax**: `python -m py_compile src/nyxloom/capability_map.py
   tests/test_capability_map.py` — clean, no errors.
2. **Scoped smoke test** (this cockpit *does* have nyxloom's deps
   installed, so this ran for real, not just import-checked):
   ```
   python -m pytest tests/test_capability_map.py -q
   ```
   Result: **22 passed**, 0 failed, 0 errors.
3. **Local coverage sanity check** (not the required verification step,
   done anyway since the deps were available):
   ```
   python -m pytest tests/test_capability_map.py -q --cov=nyxloom.capability_map --cov-branch --cov-report=term-missing
   ```
   Result: **100% line coverage, 100% branch coverage** (136 statements,
   36 branches, 0 missing) on `src/nyxloom/capability_map.py`.
4. **NOT run**: the full repo test suite / the authoritative
   `tester-unified` Docker gate. Per the handoff's gate-discipline
   section, that is the controller's job, not mine — I did not
   background it, did not wait on it, and am not claiming it passed. (I
   did start a full `pytest tests/` run in this cockpit purely as
   personal due-diligence that I touched nothing else; it exceeded the
   tool's foreground timeout and was moved to background by the harness.
   I am NOT using its outcome as evidence here, per the "don't end your
   turn waiting for a gate" instruction — it is irrelevant to this
   report's claims, which rest solely on items 1-3 above, run
   synchronously and observed directly.)

## Bug found and fixed pre-commit

Initial `write_capability_catalog` accumulated one extra blank line per
write/rewrite cycle (the blank separator line survived `_strip_managed_
block` and a fresh one was added on top each time), which failed the O4
byte-identical-idempotence requirement on the *second* write. Caught by
my own O4/O7 tests failing on the first `pytest` run; fixed by trimming
trailing blank lines after stripping the prior managed block. Full
before/after detail in `B18-LOG.md`.

## Commit

Commit hash: filled in after `git commit` below (see final message to
controller for the actual hash — this file is written just before that
commit, per the LOG/REPORT convention of writing REPORT after
implementation completes but the commit itself follows).
