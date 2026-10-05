# W8c report: R2 survivor batches

Branch `cli-ext-w8c-survivors`. All new tests are in `tests/test_w8c.py`; no source changes in batch 1.
Mutants were planted by hand on scratch copies of `src/` (outside the repo) and the file run
with PYTHONPATH pointing at the copy. I did not run assay itself.

## Batch 1

| # | Survivor | Test | Planted mutant | Result |
|---|----------|------|----------------|--------|
| 1 | `_positive_int` `number < 1` -> `<= 1` (the brief's `<=` read as the 1-boundary flip) | `test_positive_int_accepts_one_and_refuses_zero`, `test_max_candidates_zero_is_an_argument_error_but_one_is_parsed` | `<= 1` | killed (both) |
| 2a | `plain` `include_json` False -> True | `test_surface_verbs_refuse_json_and_progress[...--json]` | True | killed (5 of 5 verb cases) |
| 2b | `plain` `include_progress` False -> True | same test, `--progress quiet` cases | True | killed (5 of 5) |
| 3a | `CliConfig` `frozen=True` -> False | `test_listed_dataclasses_are_frozen_and_reject_assignment[config-CliConfig]` | False | killed |
| 3b | `ProjectConfig` `frozen=True` -> False | same, `ProjectConfig` | False | killed |

Notes:

- **Item 1.** The existing test used `--max-candidates 1` expecting exit 2, but that comes from the later
  candidate limit, not argument parsing, so it did not pin the boundary. The new test calls
  `_positive_int` directly (1 accepted, 0 and -1 refused with the message) and checks, through the
  CLI, that `0` gives the argparse message while `1` does not.
- **Item 2: why every verb.** All five `surface` verbs (`sync`, `check`, `template`, `pack`, `report`)
  are registered with the one `plain` dict, so one verb would kill both flips. I test all five
  against both flags (10 cheap cases) so a verb later moved off `plain` is also caught. `--progress`
  takes a mode, so the test passes `--progress quiet`: a bare `--progress` fails with exit 2
  whether or not the option exists, and my first version of the test let the True mutant survive
  for exactly that reason. The assertion is the exact `unrecognized arguments: ...` text.
- **Item 3: reflective coverage.** `test_every_dataclass_in_the_package_is_listed_here` imports every
  module of `cli_extended` and requires the set of dataclasses defined there to equal the
  `FROZEN` table plus an explicit `MUTABLE` set (`CliRuntime`, `RegisteredCli`, both plain
  `@dataclass`). A new dataclass therefore fails that test until it is added to one of them.
  Discovery cannot be "find the frozen ones" alone, since a `frozen=True` -> False mutant would
  drop out of such a discovery and pass. Every class in `FROZEN` (22 classes) is then asserted
  `frozen` and has each field's assignment and one deletion rejected with `FrozenInstanceError`
  (on an `object.__new__` instance, so no constructor arguments are needed). `MUTABLE` classes are
  asserted not frozen. 22 frozen classes were covered, not only the two named; I only planted
  mutants for the two named ones.

## Gate

`run-gate: lane 'r0-r1' verdict PASS; exit_code 0` (batch 1, after the last edit).
