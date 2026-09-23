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
- lane schema remains 2; verdict schema is not changed until the required
  operator decision about v12 and B079.

P3 is carved but deliberately not started pending the prompt's mandatory
operator question: whether B079's discarded split joins the same verdict v12
cut as the dirty override marker and (possibly) `snapshot_history`. The P3
carve is `dirty_ignore` as a project-level declared glob list with a
pre/post dirty-set check, while the lane file itself is never suppressible;
liveness side files will use the chosen WARN/cleanup policy and retain any
evidence needed by verdict or rejudge. A mechanical BLOCKED trigger is: no
operator answer to the v12/B079 question, or a third consecutive gate/review
run showing the same external-state failure; neither trigger is asserted yet.

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
| verdict schema / B079 question | operator decision required before P3 |

The first gate attempt on `5bf832a4` ran the full self-hosted suite successfully
but correctly rejected `config.py`'s unaliased `dataclasses.field` import as a
pyflakes shadowing finding. The alias-only correction is `4ed15f31`; the
authoritative rerun built wheel `assay-6.5.1.dev243+g4ed15f31` and ended with
`GATE_EXIT=0`.

## History-walk audit

On 2026-09-23, `git ls-files '*assay.toml'` found 15 vbpub lane files and
`rg` found no declared argv invoking `git log`, `rev-list`, `show`, `archive`,
`merge-base`, tag-derived versioning, setuptools-scm, or hatch-vcs. The same
audit over dstdns's tracked `assay.toml` found no history-walking argv. No
lane needs a `snapshot_history = "full"` override for the current estate.

## Files in the P2/P4 package

`assay/src/assay/{config.py,isolation.py,runner.py,cli.py}`, the focused
config/isolation tests, `assay/README.md`, `assay/docs/{DESIGN-GUIDE.md,
CONSUMERS.md,INTERNAL-CONSUMERS.md}`, `assay/CHANGES.md`,
`assay/nyxloom-trove/decisions.md`, this log, and B101/B082-B084 backlog
status/evidence. P3 files and its verdict generations are intentionally not
in this package.
