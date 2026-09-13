# Assay B096 implementation brief

## Assignment

Implement assay backlog B096 on branch `assay-b096`, based on the accepted
B092+B098 branch. Work only in `/workspaces/vbpub/.worktrees/assay-b096`.
Do not modify the running B092+B098 gate worktree, the detached P6 judged
tree, run-gate/cgroup-profiler, dstdns, or operator-owned dirty files.

## Contract

The `assay run --help` text for `--rejudge-outcome` must derive its accepted
canonical bucket list from the owner vocabulary `assay.verdict.MUTATION_BUCKETS`
(available through the existing `assay.mutation` import if that preserves the
current import graph). It must retain the CLI-only convenience alias `error`
for `crashed`, and the help must clearly distinguish that alias from the
canonical values. Do not duplicate a second hand-written canonical list.

The runtime parser/validator already uses the canonical vocabulary and accepts
the alias; preserve its behavior byte-for-byte. Preserve the existing
`--rejudge-outcome` help shape and examples except for replacing the manually
transcribed canonical names with derived text. If the canonical tuple changes
in a test or future vocabulary update, the help must change without a second
edit. Do not add `error` to `MUTATION_BUCKETS`.

## Required work

- Add an implementation-level helper or expression with a clear owner and no
  import cycle.
- Add a red-first regression test proving the current help contains every
  member of `MUTATION_BUCKETS` and the alias, and that the generated list is
  not a stale duplicate. Prefer a test that temporarily/monkeypatches the
  vocabulary or tests the helper against the owner tuple rather than merely
  asserting the current literal output.
- Update B096's row in `assay/nyxloom-trove/4-backlog.md`, `CHANGES.md`, and
  all three adopter-facing docs (README WHAT, DESIGN-GUIDE WHY, CONSUMERS
  HOW) if their public CLI vocabulary/help claim needs synchronization. Keep
  schema/version facts and cross-document anchors valid. Do not reopen B092,
  B098, or liveness decisions.
- Write a short checkpoint/report with exact tests and gate status. Do not
  merge or release.

## Verification

Run focused serial tests and the docs/example suite; PSI-gate all execution.
Do not launch any mutation campaign: P6 is the active mutation lane and the
B092+B098 assay gate is already running. Keep HEAD quiet for any recorded
assay evidence. Use `apply_patch`, commit with a precise message and
`Co-Authored-By: GPT-5.6 Luna <noreply@anthropic.com>`, and return the exact
commit, files, tests, and residuals. If context approaches the dispatch
checkpoint, write a new BRIEF plus retention prompt and commit at a coherent
boundary.
