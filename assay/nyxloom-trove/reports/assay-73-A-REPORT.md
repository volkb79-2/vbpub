# assay-73-A report (B142, B140, B141)

Branch `assay-73-A` from `origin/main` `b2c7522b6`. Written by the package-A implementer; a fresh reviewer verifies.

## Headline finding: the three items were already implemented on main

`main` already contains B140, B141 and B142: the v14 bundle `9ae442998`
("feat(assay)!: bundle dstdns backlog fixes for v14"), the exact-file tracked-root
check in `runner.py`, and the B142 review repairs (A-481/A-482, `redaction.py`,
`judge_sha256` identity `/3`+). Their `CHANGES.md [Unreleased]` entries (B140, B141,
B142) and docs (README, `docs/CONSUMERS.md` migration notes v13-to-v14,
`docs/DESIGN-GUIDE.md`) also already exist. Only the backlog `Status:` lines still said
OPEN. I therefore did not re-implement; I verified, closed the one gap I found, and
marked the statuses.

## What changed on this branch

1. `assay/nyxloom-trove/4-backlog.md`: B140, B141, B142 status OPEN -> IMPLEMENTED.
2. New `assay/tests/core/test_cli_passthrough_secret_e2e.py` (2 cases, exit 0 and 7).
   The task asked for "a planted secret never appears anywhere in the verdict file or
   other artifacts; grep the whole output dir". Existing tests proved each seam in
   memory but none grepped real artifacts. This runs the real CLI (`assay run`) in a
   real git repo with a real child that echoes `X_PASSWORD` and `SCHEMA_GATE_DSN` on
   stdout and stderr, then asserts the raw values are absent from every file in the
   output dir and the repository checkout, and from captured CLI stdout/stderr; it also
   checks the marker, the SHA-256 digests, `verify_document`, and the same-width mask.
3. This report. `CHANGES.md` untouched: its `[Unreleased]` already carries accurate
   B140/B141/B142 entries (a duplicate would feed cmru twice).

No `src/` file differs from main (`git diff -- assay/src` empty after the plants).

## Schema decision (already made on main; flagged for the controller)

B142 added `env_effective_passthrough_sha256` and changed `env_effective` values, and
B140 added `[defaults]`; main folds these into verdict **schema v14, a hard cut from
v13** (CONSUMERS "Migration notes (v13 to v14)"; `assay verify` refuses v13). The last
release, `assay-v7.2.0`, is v13. Releasing 7.3.0 from main therefore ships a breaking
v14 verdict schema and a `feat(...)!` BREAKING CHANGE commit, not a minor-compatible
tightening. The task text preferred a compatible change; that option is not what main
contains (the new key is required by the v14 schema and verifier). Controller decides
whether 7.3.0 is acceptable as-is, should be versioned as a major, or the v14 cut must
be reverted/deferred. I did not touch it.

## Verification actually run

Host PSI checked first (cpu some avg10 ~3%); all runs serial under
`flock ... nice -n 19 ionice -c 3`.

- New e2e test: 2 passed.
- Focused set (B140/B141/B142 suites: config env/accept/source_roots, runner plan env,
  verdict transparency/verify, measurability, resolve_targets, evaluate_language_free,
  cli_plan_jobs, redaction boundaries, go stmtpos helper): all pass except tests that
  need the host cgroup namespace (below).
- Full `python -m pytest tests -q` in `assay/`: **134 failed, 6962 passed, 1 skipped.**
  All 134 are environmental and pre-existing, since `src/` is identical to main:
  this devcontainer runs in a private cgroup namespace, so B145's native-R2 candidate
  cgroup-ancestor observation refuses with
  "the cgroup namespace hides the process's parent cgroups" (EXEC_FAILED), and some
  CLI tests report "no judge_provenance" because the installed editable `assay` points
  at another worktree. The registered gate runs these in a `--cgroupns=host` tester
  container; I did not use host cgroupns (memory rule). Treat these as
  infrastructure-inconclusive, not product PASS. In particular
  `test_mutation_state_crash_tails.py` (B142 crashed-candidate tails) could not run
  here: 5 cgroup failures, so that seam is NOT verified by me.
- Coverage: not measured; no `src/` change.
- Not run: `tester-unified`, self-qualification lanes (controller's registered gate).

## Plants (each killed, then reverted; `git diff -- assay/src` empty afterwards)

| Item | Plant | Result |
|---|---|---|
| B142 | `verdict.py`: skip `env_effective[name] = PASSTHROUGH_ENV_VALUE_MARKER` (values written verbatim) | 9 failed: both new e2e cases + 7 in `test_verdict_transparency.py` |
| B140 | `config.py` `effective_env_passthrough` = lane list if non-empty else defaults (the entry's wrong implementation family) | 2 failed: `test_a_project_default_is_recorded_in_the_run_verdict`, `test_explicit_project_defaults_and_lane_names_form_a_stable_effective_union` |
| B141 | `evaluate.py`: treat a file root as its parent directory | 2 failed: `test_a_changed_symlink_sibling_to_a_file_root_stays_outside_r1`, `test_a_symlink_file_root_does_not_admit_its_resolved_target_into_r1` |

Observation (not fixed): `test_a_file_source_root_excludes_changed_sibling_files_from_judgment`
passes under the B141 plant because its `REPO_TOP` paths do not exist on disk, so
`root.is_file()` is False; the real-file symlink tests are what kill the plant. A
reviewer may want a real-file variant of that plain-sibling oracle.

## Not claimed

I did not re-audit every line of the existing B140/B141/B142 implementation beyond
mapping each backlog oracle to a test and running the plants. The oracle literals
`X_PASSWORD=s3cr3t` / `postgresql://u:p@h/db` are not used verbatim by existing
tests (equivalent fake values are). Go helper refusal and probe-diagnostic masking are
covered by `test_adapters_go_stmtpos_invoker.py` and `test_b105_runner_boundaries.py`,
which passed.
