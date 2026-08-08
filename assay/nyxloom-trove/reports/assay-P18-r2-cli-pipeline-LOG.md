# P18 — Python R2 CLI pipeline — LOG

**Status:** DONE (merged)
**Branch:** `feat/assay-P18-r2-cli-pipeline`
**Worktree:** `/workspaces/vbpub/.worktrees/assay-P18-r2-cli-pipeline/assay`
**Base:** `c26acd00` (`docs(assay): record P17 gate counts measured inside tester-unified`)
**Handoff `input_revision`:** `48771e48c7b2ed7ed937cbe07e193718c6f242bb` — unchanged
between that commit and `c26acd00`; nothing in `src/assay/` moved in between.

Implemented and self-reviewed by an implementer session; reviewed, repaired and
merged by the controller. Four defects and two oracle gaps were found in review,
recorded as A-145–A-148 — see "Controller review", below.

**A note on P17.** There is still no `assay-P17-r1-cli-pipeline-LOG.md`, and
this LOG does not invent one: reconstructing a package's own record after the
fact would be exactly the kind of plausible-but-unverified artifact this
project exists to refuse. P17's information lives in its four commit bodies,
in decisions A-139–A-144, and in the "Carried in from P17" sections of the P18
and P19 handoffs. That is a real gap in the trove, recorded here and in
`STATE.md` rather than papered over.

## What was built

- `src/assay/config.py`: `judge.mutation` stops being an opaque passthrough and
  becomes a closed, validated `MutationConfig` — a required positive
  (non-boolean) integer `jobs`, and a required non-empty, duplicate-free,
  ORDER-preserving `operators` list cross-checked at LOAD time against
  `assay.mutation.MUTATION_OPERATORS`. That cross-check needs a DEFERRED import
  inside the loader function: `assay.mutation` imports `Lane` from `config` at
  its own module level, so a module-level import here would be a genuine cycle
  (the same reasoning `mutation.py` already gives for resolving
  `execute_command` from a function body). `jobs` is never derived from the
  running machine — this loader is the only place a value for it can come from
  at all (A-082/A-122).
- `src/assay/mutation.py`: `run_mutation`'s own internal baseline is GONE.
  `baseline` is now a mandatory, caller-supplied `CommandResult` — the exact R0
  result `run_lane` already produced — so the lane's command runs at most once
  per `assay run` invocation (sol finding 11). This also turns `assay verify`'s
  R2 baseline PROXY into an identity (A-137). New `resolve_mutation_targets`
  builds R2's per-file candidate list from the same resolved `AddedLines` R1
  measures against, under the identical source-root / excluded-directory /
  source-glob / test-path gates `evaluate.py`'s own private `_is_considered`
  applies for R1 — deliberately a second, independently written copy rather
  than an import, because `evaluate.py` is outside this package's `scope.touch`
  and two copies that must agree is itself a check. New `_filter_by_operators`
  retains only the declared operators before anything is submitted; an
  `UNSUPPORTED` adapter, a valid file with no eligible site, and a filter that
  matches nothing all collapse into the same honest `total == 0` →
  `INCONCLUSIVE`/`NO_MUTANTS`. `jobs` is validated before the executor
  boundary. (Controller: `project_prefix`/`_within_project` and a required
  `repo_top` — A-145.)
- `src/assay/runner.py`: `evaluate_r1` gains `on_added_resolved`, the same
  additive, default-`None` callback mechanism `on_base_resolved` already uses
  and for the same frozen-signature reason (`assay.canary` calls `evaluate_r1`
  directly and expects a bare `Claim` back). `run_lane` gains its R2 block: a
  non-PASS R0 short-circuits straight to the baseline's own
  `(outcome, reason_code)` — R2's baseline gate is strictly stricter than R1's,
  so R2's own prerequisite chain is never consulted; a PASS R0 either reuses
  R1's already-resolved diff or, when R1 was not declared (or its own
  coverage-specific guards tripped first), runs the identical
  `check_dirty_tree`/`check_base_is_head` guards itself. Mutation runs inside a
  `tempfile.TemporaryDirectory` this function owns end to end — outside the
  repository, so A-140's "the declared artifact must be git-ignored" rule
  does not extend to it. `judgment.r2` is populated alongside every claim that
  carries a mutation payload.
- `src/assay/cli.py`: `"R2"` added to `_built_in_registry`'s ONE existing
  `PythonAdapter` entry — the whole registry change, exactly as A-139
  predicted. `_resolve_declared_adapters` resolves the adapter for whichever of
  `_ADAPTER_BEARING_LEVELS = ("R1", "R2")` the lane declares; the refusal loop
  above it was already total over `lane.rigor`, so an R3 lane is still refused
  before anything runs, as a complete artifact. (Controller: the `--help`
  capability sentence — A-146.)

## Gate output (real `tester-unified` Docker container)

Run by parsing `nyxloom-trove/nyxloom.toml`'s own `[gates.tester-unified]`
`argv` and executing it verbatim, twice: once on the branch after the repairs,
once again directly against `main` after the merge.

```
tester-unified: PASS (exit 0)
  commit: 9750b54e3dce631fb978ea10e96a99dd9096edd9
  argv: python -m pytest tests -q --ignore=tests/test_self_hosting.py --override-ini=pythonpath=
7 passed in 12.29s
```

Counts measured INSIDE the gate image (the gate's own argv reports only an exit
code, and the cockpit venv carries different pins):

**1781 passed, 1 skipped, 100% statement AND branch coverage** (2941
statements / 1188 branches, zero missed in either). Up from P17's 1719
passed / 2839 statements / 1138 branches.

`tests/test_standalone.py::test_a_real_pass_matches_the_documented_r0_pass_shape`
fails in the DEVCONTAINER and only there: `setuptools_scm` is importable here,
so the wheel versions as `0.1.0` instead of the container's documented `0.0.0`
(A-069/A-124). It passes in the gate image. Worth knowing before reading a bare
`pytest` run as a regression.

## Per-oracle evidence

Every mutation below was applied to a COPY of `src/` and run against that copy
via `--override-ini=pythonpath=<copy>/src`; the real tree was never modified
(the same isolation discipline A-120/A-131 already use). Note that
`tests/test_standalone.py` builds its wheel from the REAL `PROJECT_ROOT` and is
therefore immune to this harness — the counts below exclude it, and the wheel
tests are cited separately by name.

### O1 — targets come only from considered changed lines in the same resolved diff, filtered to the declared operators

`tests/test_mutation_resolve_targets.py` (seven cases: a considered file
becomes one target carrying its own text and lines; a file outside every source
root, inside an excluded directory, not matching `source_globs`, or matching
`is_test_path` contributes nothing and is never even READ; path ordering; the
empty diff). `tests/test_mutation_operator_filter.py` proves the filter
DISCRIMINATES rather than being a no-op: a two-site fixture with two different
operators, a lane declaring one, and a process-boundary call count showing the
undeclared operator's mutant is never SUBMITTED.

- **A-067-1** (drop the `is_test_path` gate): **1 failed** —
  `test_a_test_path_is_excluded`.
- **A-067-2** (drop the source-root containment gate): **1 failed** —
  `test_a_file_outside_every_source_root_is_excluded`.
- **A-067-3** (keep every job regardless of the declared operators): **3
  failed** — the two `_filter_by_operators` cases plus
  `test_run_mutation_never_submits_an_undeclared_operators_mutant`.
- **A-067-12** (resolve R2's own diff even when R1 already resolved one): **1
  failed** — `test_r1_and_r2_together_reuse_the_same_resolved_diff_not_a_second_one`,
  which counts real `git diff` invocations through a patched `git.run`.
- **A-067-13** (leave Python at R1 only in the registry): **2 failed** —
  `test_run_evaluates_a_real_r2_pass_end_to_end` and
  `test_the_run_help_declares_exactly_the_levels_the_registry_reaches`.
- **A-067-14** (accept any operator name at load time): **1 failed** —
  `test_an_unknown_operator_is_rejected`.

### O2 — `jobs` is a required positive integer, observed at the executor-construction boundary

`tests/test_mutation_executor_bound.py`. The bound is observed by injecting an
`executor_factory` that RECORDS what it was called with — never by elapsed time
or by counting concurrent work.

- **A-067-4** (drop the positive-integer refusal): **2 failed** —
  `test_jobs_zero_is_rejected_before_the_executor_boundary`,
  `test_jobs_validated_even_when_the_baseline_never_passed`.
- **A-067-5** (construct the executor with the mutant count instead of `jobs`):
  **2 failed** — `test_the_executor_factory_receives_exactly_jobs_not_mutant_count`,
  `test_a_different_jobs_value_is_reflected_exactly`.

### O3 — R0's own result IS the mutation baseline; the command is not rerun

- **A-067-6** (re-run the lane's command inside `run_lane` instead of passing
  R0's own result): **2 failed** —
  `test_the_lanes_command_runs_exactly_once_against_the_unmodified_tree`
  (which counts invocations by real `cwd`) and
  `test_ended_covers_r2s_own_completion_not_only_r0s` (whose exact clock
  sequence changes the moment an extra execution appears).
- **A-067-7** (write the mutant into the shared tree instead of the copy): **4
  failed** in `test_mutation_isolation.py`, including
  `test_the_shared_source_tree_is_byte_identical_after_all_four_terminal_cases`.
- **A-067-8** (leave the non-killed buckets in submission order): **1 failed** —
  `test_the_recorded_buckets_are_ordered_by_identity_not_by_submission`.
  Dropping all three sorts together gives the same single failure.
- **A-067-8c** (await futures in completion order rather than submission
  order): **2 failed** — the synchronous-executor attribution test and the
  `jobs=1` vs `jobs=3` real-executor comparison.
- **A-067-9** (fold `BUDGET_EXCEEDED` into `killed` — A-122's own confirmed
  trap in `nyxloom`'s `mutation_gate`, deliberately not ported): **1 failed** —
  `test_run_mutation_reaches_all_four_buckets_and_total_accounts_for_every_one`.

### O4 — an installed-wheel R2 fixture produces exact artifacts while shared source bytes are unchanged

Five complete hand-written documents in `tests/test_standalone.py`, each
compared with a whole-document `==` (only `assay_version`/`started`/`ended`
excluded — the three values a real run cannot hand-inject):

| shape | test | artifact |
|---|---|---|
| killed + survived | `..._kills_one_mutant_and_lets_another_survive_through_the_wheel` | `FAIL`/`MUTANTS_SURVIVED`, `total=2 killed=1`, the survivor's own identity |
| no-mutants | `..._with_no_declared_operator_site_is_inconclusive` | `INCONCLUSIVE`/`NO_MUTANTS`, `total=0` |
| baseline-adverse | `..._propagates_an_adverse_baseline_verbatim` | `FAIL`/`COMMAND_FAILED`, no payload, no `judgment` |
| budget-exceeded | `..._mutant_that_outlives_the_lane_budget_is_its_own_bucket` | `BUDGET_EXCEEDED`/`LANE_TIMEOUT`, one survivor and one timed-out identity |
| resolved base | `..._diffs_the_resolved_merge_base_not_the_declared_ref` | two mutants, not one |

Every lane declares `env = { PATH = "/usr/bin:/bin" }` with
`env_passthrough = []`, so `env_effective` is fully determined and the complete
comparisons stay honest. The killed/survived and budget cases hash every
tracked file before and after the run and assert the digests are unchanged.

### A-145 — the project root is not always the repository top

- **A-067-10** (assume `project_root == repo_top`): **4 failed** — the two
  `project_prefix` unit cases and the two subdirectory-project pipeline cases,
  one of which asserts the SURVIVOR's recorded path is the repo-relative
  spelling.

### A-143's shape, applied to `judgment.r2`

- **A-067-11** (record a fixed `jobs` instead of the declared one): **1
  failed** — `test_judgment_r2_records_the_lanes_own_declared_policy_verbatim`.
- **A-067-11b** (canonicalise the declared operator list by sorting it): **1
  failed** — the same test. It declares `jobs = 3` and two operators in an
  order that is neither alphabetical nor `MUTATION_OPERATORS`'s own, of which
  the fixture exercises exactly one.

## What could not be honored as written

**O4's CRASHED mutant is not producible through a real installed lane.** That
bucket means the mutant's process could not be STARTED (`ERROR`/`EXEC_FAILED`,
A-073). `argv` is byte-identical for the baseline and every mutant (A-118,
proven in `test_mutation_argv_fidelity.py`), the scratch tree is a faithful
`copytree` of the project root, and exactly one source file's TEXT differs — so
any argv that launches for the baseline launches for every mutant, and any argv
that fails to launch fails the baseline first and short-circuits before a
mutant is ever submitted. It is reachable only through an injected process
boundary, where `test_mutation_isolation.py` and `test_mutation_judge.py`
already drive it alongside the other three buckets. This is recorded in the
suite itself, at the point where its absence would otherwise read as an
omission, rather than only here (A-072). **P23's own O3 names the same shape
for Go and there it IS reachable** — a mutant that fails to COMPILE is a
different thing from one that fails to launch.

**`judgment.r2` is populated but tied to nothing.** Closed as A-148 and handed
to P19 with a widened `scope.touch` and an explicit work item, rather than
improvised here: `verdict.py` is forbidden in this package and `verify.py` is
not in its `scope.touch`.

## Controller review — four defects and two oracle gaps

1. **A-145 — `assay run` crashed, with no artifact, for a project in a
   subdirectory of its repository.** Reproduced against the real CLI on
   assay's own layout inside `vbpub`. Diff paths are repo-top-relative; the
   per-mutant copy is a copy of the project root. Invisible to every existing
   oracle because every fixture in the suite makes the two the same directory.
   The failure arrives AFTER the lane's command has run, as a bare traceback —
   A-139's own shape, one package later.
2. **A-146 — `assay run --help` under-declared the build's own capability.**
3. **A-147 — work item 7 and O4 were skipped entirely**, without being declared
   skipped. Third consecutive package in which "a complete artifact" was
   satisfied by a handful of field assertions. Also confirmed the carve gap
   P18's implementer flagged for P23 (`cli.py` missing from its `scope.touch`)
   and fixed it — second instance of A-144's shape.
4. **A-148 — a deferral with no executor**, found by reading P19–P25's own
   scopes rather than this package's diff.
5. **Work item 8's mutation set had never been run.** Running it found two
   properties with no discriminating oracle: `judgment.r2`'s recorded policy
   (every fixture declared `jobs = 1`, so a hardcoded `1` passed everything),
   and `run_mutation`'s three bucket sorts (the only generating adapter in the
   suite already sorts its own output, so removing them changed nothing
   observable — but `adapters/base.py` promises no order, which is what those
   sorts are for and what P23's Go adapter will exercise). Both now have
   oracles; both mutations now fail.

## Housekeeping

- `tests/fixtures/verdicts/r2_pass_with_judgment.json` — the conformance
  matrix now carries the artifact shape P18's producer genuinely emits
  (A-141's rule applied to what this package made real), with a level-aware
  negative proving it is the only fixture covering `judgment.r2`.
- `tests/conftest.py` gains `make_r2_judge` and an optional `mutation`
  argument on `make_r1_judge`.
- `judge.canary` keeps `_as_opaque_table`, now its only caller; a test for
  that path was added when `mutation` stopped using it.
