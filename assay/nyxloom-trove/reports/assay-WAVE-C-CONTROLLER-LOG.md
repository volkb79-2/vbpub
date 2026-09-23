# Assay Wave C controller log

**Status:** IN PROGRESS. The source handoff is
[`WAVE-PROMPT-2026-09-23-wave-c-refusals-js-liveness.md`](../WAVE-PROMPT-2026-09-23-wave-c-refusals-js-liveness.md).

## Checkout and baseline

- The handoff records main at `2f59fdd3`; the live main tip when this work
  started was `db29266f8a006b22a30609a74de7645d1e4c50b7`. The prompt's facts
  were checked against that live tip before editing.
- Work is isolated in CIU-managed worktree
  `/workspaces/vbpub/.worktrees/assay-wave-c-controller`, logical name and
  branch `assay-wave-c-controller`, created from the exact live-main SHA above.
- The primary `/workspaces/vbpub` checkout was clean at kickoff. The CIU
  instance is ready; no application container was started.
- Package reviews, gates, merges, push, conditional P4, release, deploy and
  dstdns notification are pending.

## P0 — v7.0.0 close-out reconciliation

### Changelog

Moved the orphaned hand-written `assay analyze` detail under 6.4.0, where its
implementation already shipped (`fde9527a` is present in the `assay-v6.4.0`
tag). Moved the B101/B102/B093/B079 feature and documentation notes, P22 base
resolution detail, P25 qualification error evidence, and history-cut tests
under 7.0.0. `## [Unreleased]` is present and empty; the release-history marker
and generated sections remain intact. Restored the omitted B104 W3 witness and
candidate-budget note under the 7.0.0 fixed details.

### Backlog

Updated the glance list to mark the B101 wave shipped in v7.0.0 and to list
Wave C's P1-P4 items in execution order. Updated B079's body and index to
record the A-454/v12 disposition, release evidence, and its two still-open
original acceptance items. Filed B105 as an open
finding (not an implementation package) with the evidence that the registered
release gate is R0-only and the v7.0.0 mutation attempts found no candidates.
The backlog's schema forbids a top-level status field; B105 was added only to
the existing `items` index shape.

### CMRU findings

Filed KI-30 for the recurring plain `## [Unreleased]` changelog section that
CMRU does not fold. The three affected assay releases were 6.4.0, 6.5.0 and
7.0.0; KI-23's versioned `- UNRELEASED` guard does not cover this spelling.

Filed KI-31 after reproducing the child project-config remap failure with CMRU
`5.4.2.dev262+ge434b293`:

- A fresh bounded dry-run reproduced it after the initial report was drafted.
  The default relative config and direct absolute `--config` both reached the
  doubled child path. Complete command/output/exit/cleanup transcripts are in
  [`assay-WAVE-C-CMRU-probes-2026-09-23.md`](assay-WAVE-C-CMRU-probes-2026-09-23.md).
- CMRU's `--abandon` continues into a fresh release attempt; the evidence
  report includes the failed first cleanup path and the exact-path cleanup.
  All three new probe branches/worktrees were removed. The shared `main`
  checkout remained clean at the kickoff SHA.

- From the CIU worktree, `cmru release assay --dry-run` exited 1 and attempted
  to load `.../.worktrees/cmru-release-20260923_213534-assay-0uror9/.worktrees/cmru-release-20260923_213534-assay-0uror9/assay/cmru.toml`.
- Passing `--config /workspaces/vbpub/cmru.orchestration.toml` directly also
  reproduced the doubled path (`cmru-release-20260923_213915-assay-kr97ci`).
- The prior B101 log reports that a temporary `CMRU_BIN` wrapper rewrote the
  child `--config` argv to the absolute central config and allowed the release
  to finish. That wrapper is gone; `/tmp/assay-release-wrapper-args.log`
  records the argv rewrite, but the direct CLI retry here did not verify it as
  a working remedy.
- All three probe transactions created by this investigation were abandoned
  by exact path through CMRU; no pre-existing retained release transaction was
  selected or removed. The cleanup invocation used an invalid `--ref` after
  `--abandon` so CMRU would stop before allocating another fresh transaction.
  These probes did not promote a candidate or move local `main`.

No CMRU product code was changed, as required by the handoff.

### P0 review and gate

Independent adversarial review completed with no remaining findings. The
first pass caught the missing B104 release detail, conflated CIU/CMRU mutation
outcomes, B079's incomplete-status boundary, and the B104 Docker-test
contradiction. Repairs were reviewed again; a further pass corrected the
B104 witness-regeneration recipe to derive identity values from the disposable
repository rather than the artifact being checked. Final reviewer result:
P0 closed, no blockers, no gates/tests run by the reviewer. The initially
selected `gpt-6-astra` reviewer hit its usage limit; the completed fresh review
used `gpt-6-sol` at xhigh effort.

Local record checks before gate:

| check | result |
|---|---|
| `git diff --check` | PASS |
| backlog frontmatter vs shipped schema | PASS, 105 unique indexed IDs including B105 |
| new cross-file report links | PASS |
| shared `/workspaces/vbpub` checkout | clean at kickoff SHA |

The detached tester-unified gate and all 12 phase markers remain pending. Add
its captured log and exit markers here before merging.
