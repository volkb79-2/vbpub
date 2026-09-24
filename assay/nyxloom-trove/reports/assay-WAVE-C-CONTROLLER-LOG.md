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

Independent adversarial review completed with no remaining findings. An
earlier P0 review pass found the missing B104 release detail and conflated
CIU/CMRU mutation outcomes, then hit its usage limit before formal closeout.
The completed fresh review found B079's incomplete-status boundary and the
B104 Docker-test/checklist contradiction. Those repairs were rechecked; a
further pass corrected the B104 witness-regeneration recipe to derive identity
values from the disposable repository rather than the artifact being checked.
Final reviewer result: P0 closed, no blockers, no gates/tests run by the
reviewer. The initially selected `gpt-6-astra` reviewer hit its usage limit;
the completed fresh review used `gpt-6-sol` at xhigh effort.

Local record checks before gate:

| check | result |
|---|---|
| `git diff --check` | PASS |
| backlog frontmatter vs shipped schema | PASS, 105 unique indexed IDs including B105 |
| new cross-file report links | PASS |
| shared `/workspaces/vbpub` checkout | clean at kickoff SHA |

The detached tester-unified gate passed on the P0 branch tip
`26ec6b3905ffe0b8c3a4eebdbdc11e81b4b0efaa`. The exact captured output is
[`assay-WAVE-C-P0-gate-2026-09-24.log`](assay-WAVE-C-P0-gate-2026-09-24.log),
SHA-256 `db36280ce66ea5c36dfc8cbb61c87c41e747de643b043c805bb52c7027194b85`.

- Container: `run-gate-assay-selfhosted-1477047-21288-1790210014`; inspected
  after `docker update --cpus=3`: `cpus=3000000000`, parent
  `dev-gates.slice`.
- Started at `2026-09-24T00:33:34.207416Z`; the detached log completed at
  `00:48:43Z` (about 15m 9s). At the required 90-second check, wheel creation
  had completed and six phase markers were present. The historical estimate
  was 19–21m; this run finished earlier, and the next read found its terminal
  markers.
- Exit evidence: `ASSAY_GATE_CONTAINER_EXIT=0`,
  `ASSAY_REGISTERED_GATE_COMPLETE=1`, all 12 `ASSAY_GATE_PHASE=` markers, and
  `GATE_EXIT=0`. No container remained afterward.
- Run-gate warned that the optional `cgprofile-host-daemon` was absent and
  used coarse rusage sampling; this did not change the registered gate result.

P0 was merged after that branch-tip gate passed; the later report commit adds
the durable log and result summary.

## P1 — B080 JavaScript default-argument coverage

### Implementation and review

The P1 branch is `assay-b080-js-default-arg`, based on the P0-updated main
tip. It contains the B080 carve/fixture (`b13707ad`), the narrowed
signature-line implementation and user docs (`86c16f85`), the current-main
merge (`40bd4906`), and the independent-review regression plus summary-row
correction (`e29c1fb0`). The branch was clean at the review boundary.

The operator ruling is A-459: shape C recovers a statement-less default-arg
node only when an arm of that same branch maps to the signature line under
the existing `_arm_line` rule. The broader enclosing-function rule remains
documented as an alternative; it covers additional multiline layouts but
changes prior-PASS counts when a function's execution count is used for an
unmatched branch.

Fresh independent review round 1 found no implementation or public-document
defect. Its nonblocking P3 record finding (B080 was still OPEN in the Wave C
glance row) was corrected in `e29c1fb0`. The reviewer added a combined-axis
regression for same-line aggregation, fallback attribution, an uncalled
qualifying function, unmatched default metadata, nested metadata, and reversed
branch order: 4 parameter cases passed. The bounded 12-module suite passed
343 tests in 5.08s. Additional differential probes checked 1,152
baseline/current artifact shapes; 2,832 prior-PASS evaluations were
unchanged. Malformed later arms refused before function-map access, and
overlapping nested headers refused as ambiguous. Controller re-ran the
focused signature module (71 passed) and pyflakes on the changed Python
source/tests (exit 0). The docs example/vocabulary/cross-link suite passed 45
tests after widening its anchor oracle to all three public docs, including a
must-fail control; this also guards the README's new B080 consumer link.

### Gate

P1 reviewer result: READY. Authoritative tester-unified gate: pending. The
gate will run against this branch tip before merge; its exact container,
host-capacity checks, phases, exit status, and log hash will be recorded here.
