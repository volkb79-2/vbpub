# B101 isolation wave controller log

Controller: Codex, worktree `assay-b101-wave`  
Started: 2026-09-23  
Base: `main` at `a02dcb326` (P1 already merged)

## Scope and carve

The wave prompt sequences P0 B104, P2 B101, P3 B102+B093, P4 B082-B084,
then gate/release. This worktree is the implementation boundary; `main` is
untouched until a reviewed serial `--no-ff` merge.

P2 contract:

- source repositories remain complete, non-shallow, non-promisor,
  alternates-free SHA-1 repositories;
- the default private seed contains exactly the judged commit and the
  pre-snapshot resolved base when present, with both OIDs in `.git/shallow`;
- `snapshot_history = "full"` is the only ancestry opt-in;
- project-level `[isolation.limits]` is the sole configuration surface,
  defaults are explicit, every value is a positive integer, and
  `max_total_tree_blob_bytes <= max_total_object_bytes`;
- `run` and `plan` construct the same effective `SnapshotSpec`, and every
  materialization writes/verifies the same boundary before closure checks;
- lane schema remains 2; the v12 verdict cut adds only the optional dirty
  provenance object and the ingested discarded-entry reason.

The operator answered yes to the mandatory v12/B079 question. A-452--A-455
record the resulting decisions: repo-top POSIX `dirty_ignore` globs with a
protected lane file, snapshot-only `--allow-dirty` with explicit verifier and
receipt policy, the closed `compile_error`/`runtime_error` split, and external
temporary liveness files with cleanup after `tests_completed`.

The implementation is complete in this worktree. The short deterministic
review gate is the focused suite, frozen W8 acceptance, docs/vocabulary checks,
compile/schema checks, and the full local suite with the known Docker-reaching
dstdns witness test explicitly deselected. Once those are clean, this branch
is provisionally merged to unblock work; the long mutation campaign and the
authoritative tester-unified gate are launched independently from a `ciu
worktree` pinned to the provisional merge. Any defect found there is
backported as a small follow-up commit rather than disturbing the running
campaign.

## P0 — B104

The pinned dstdns commit was reachable exactly in `/workspaces/dstdns`; the
failure was not checkout drift or a SQL regression. The real verdict carried
the shipped v11 stable `mutation.hung` and `judgment.r2.liveness` fields that
the frozen witness lacked, plus a run-derived candidate budget that cannot be
an exact cross-host witness. The comparator now strips only that volatile
field after validation, and the witness carries the stable v11 fields. The
targeted real Docker-reaching test and two normalizer tests pass. This is
committed as `c6a97f1e`; backlog B104 records the evidence and the test remains
outside a plain gate unless its existing dstdns/Docker fixtures are present.

## P2 implementation checkpoints

Implemented in the worktree:

- `SnapshotSpec.resolved_base`, `SnapshotLimits.max_total_tree_blob_bytes`,
  shallow/full inventory and seed transfer, exact shallow metadata in every
  materialization, walking seed re-verification, child closure limits;
- loader support for closed `snapshot_history` and project-level limits;
- both `runner.py` and `cli.py plan` `SnapshotSpec` sites receive the same
  resolved base, history policy, and limits;
- real shallow/full/base/merge and config oracles, with the existing P1
  history-cut suite retained;
- README, DESIGN-GUIDE, CONSUMERS, INTERNAL-CONSUMERS, and CHANGES updates;
- decision A-451 (A-449/A-450 are reserved elsewhere).

The key red-first risk is preserved in tests: a source shallow boundary still
refuses, default history does not expose ancestors, full history remains
limit-accounted, and replacing the shallow metadata must fail the walking
seed/materialization proof rather than be masked by `--no-walk`.

## Verification ledger

| checkpoint | result |
|---|---|
| B104 focused tests | PASS: 3 tests, real pinned dstdns capture included |
| config + isolation focused tests | PASS: 133 tests |
| P1 runner suites | PASS: 71 tests |
| tester-unified gate | PASS on `4ed15f31`: all required phases, self-hosted lane, Topos, cmru B006(a), independent self-hosting, and pyflakes |
| full local suite after commit | PASS: 4799 passed, 19 skipped, 95 deselected |
| v12 distribution/packaging follow-up | PASS: 41 tests; pyflakes gate test PASS |
| verdict schema / B079 question | PASS: operator answered yes; A-452--A-455 record the v12 decisions |

The first gate attempt on `5bf832a4` ran the full self-hosted suite successfully
but correctly rejected `config.py`'s unaliased `dataclasses.field` import as a
pyflakes shadowing finding. The alias-only correction is `4ed15f31`; the
authoritative rerun built wheel `assay-6.5.1.dev243+g4ed15f31` and ended with
`GATE_EXIT=0`.

## P3/P4 implementation and review

The v12 package includes the following tested surfaces:

- dirty-path normalization and `dirty_ignore` matching at repo-top scope;
  ignored dirt is recorded, unignored dirt refuses, `--allow-dirty` records an
  override, and a dirty loaded `assay.toml` always refuses;
- `assay run`/`assay plan` parity for the snapshot-only override, with R0 and
  in-place paths remaining strict;
- `assay verify` warning semantics and release-receipt refusal for overridden
  verdicts;
- external liveness temporary-directory cleanup and the absence of a checkout
  `.assay/liveness` tree after a higher-rigor run;
- v12's `discard_reason` reconstruction, raw verification, and W8's real
  40-entry high-discard fixture (`compile_error` for the actual CompileError
  records), including closed-vocabulary mutation tests;
- README, DESIGN-GUIDE, CONSUMERS, INTERNAL-CONSUMERS, CHANGES, backlog and
  decision-ledger updates.

The adversarial review of this package found and fixed one real issue: Git
reports an untracked directory as a trailing-slash path, which is not a valid
verdict wire path; the implementation now normalizes that marker while
retaining child paths for glob matching. The focused regression suite is green
after the correction. No unresolved review finding remains at this checkpoint.

The current deterministic evidence ledger is:

| checkpoint | result |
|---|---|
| W8 v12 frozen acceptance | PASS: 111 tests |
| ingested mutation/verifier payload suites | PASS: 152 tests |
| config, CLI, runner, and liveness suites | PASS: 150 tests |
| gate harness, W8, Python qualification | PASS: 138 passed, 9 skipped |
| authoritative tester-unified rerun | PASS on `c183a371`, all 12 phase markers, `ASSAY_REGISTERED_GATE_COMPLETE=1`, `GATE_EXIT=0` |

## Provisional post-merge execution

The provisional merge was made with `--no-ff` before the long-running work, so
the following jobs ran from the CIU-managed worktree
`assay-b101-long-20260923` rather than blocking the main line:

- the CIU lane finished in about 4m28s with R0/R1/R3 PASS and a truthful
  R2 `INCONCLUSIVE/NO_MUTANTS` result (`CIU_GATE_EXIT=5`); this lane does not
  declare mutation candidates;
- the requested CMRU mutation campaign started and was checked after kickoff,
  but stopped in about 10s because the declared source diff produced no
  mutation candidates (`CMRU_MUTATION_EXIT=1`), so this is not reported as a
  mutation pass;
- the authoritative assay tester-unified rerun was checked after kickoff and
  completed in about 19m with every registered phase passing. Its only defect
  was a stale W3 dstdns witness schema version, found by the first run and
  fixed in `f0339e3a`, then backported to main as `c183a371` before the rerun.

The gate's final evidence is `/tmp/assay-b101-provisional-tester-unified-rerun.log`;
the temporary CIU worktree can now be removed after this record is committed.

## History-walk audit

On 2026-09-23, `git ls-files '*assay.toml'` found 15 vbpub lane files and
`rg` found no declared argv invoking `git log`, `rev-list`, `show`, `archive`,
`merge-base`, tag-derived versioning, setuptools-scm, or hatch-vcs. The same
audit over dstdns's tracked `assay.toml` found no history-walking argv. No
lane needs a `snapshot_history = "full"` override for the current estate.

## Files in the provisional package

`assay/src/assay/{config.py,isolation.py,runner.py,cli.py}`, the focused
config/isolation tests, `assay/README.md`, `assay/docs/{DESIGN-GUIDE.md,
CONSUMERS.md,INTERNAL-CONSUMERS.md}`, `assay/CHANGES.md`,
`assay/nyxloom-trove/decisions.md`, this log, B101/B102/B093 backlog evidence,
and the W8 v12 verdict generations. P3 is no longer pending the operator
decision; the post-merge gate is green. The mutation attempt produced no
candidates and therefore remains an explicitly recorded non-pass outcome.
