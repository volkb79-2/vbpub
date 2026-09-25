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

P1 reviewer result: READY. The first registered gate attempt at
`5ba8e0a5` failed in the Topos qualification because
`test_stream_window_without_bounds_and_frame_response_without_sequence`
started its producer thread and immediately read `stream_window()`. That API
is nonblocking, so the test could observe empty history if the test thread ran
before the producer published its frame. This is a scheduling race in the
test; host load only changes how often the race is exposed and must not define
the assertion. The fix synchronizes through bounded `broker.current()` before
checking history, without changing `stream_window()`'s nonblocking behavior.
The failed raw log is preserved byte-for-byte, gzip-compressed, at
[`assay-WAVE-C-P1-gate-2026-09-24-5ba8e0a5.log.gz`](assay-WAVE-C-P1-gate-2026-09-24-5ba8e0a5.log.gz).
Its decompressed SHA-256 is
`7b7a28185fece0d394e072b6657637202ae7fc66c45fea693e50519b74369cd2`; the
compressed artifact SHA-256 is
`d768ba76026eeafcad2d03eebc5d7ec8817ec6646adcc7d8098b9dc3533c053d`.

The authoritative rerun passed at `8823bfeabffc4c1e22560753c1679b27efc28e9a`
with all 12 expected phase markers, `ASSAY_REGISTERED_GATE_COMPLETE=1`, and
`GATE_EXIT=0`. Container: `run-gate-assay-selfhosted-3675926-27679-1790305476`.
The raw log is preserved at
[`assay-WAVE-C-P1-gate-2026-09-25-8823bfea.log`](assay-WAVE-C-P1-gate-2026-09-25-8823bfea.log),
SHA-256 `36bcde21e86e2821aef8bc51d79d23df40d9b7c79978cb10e2f90da9daf3ccde`.
The optional cgprofile daemon was absent; run-gate used coarse rusage sampling
and the gate result was unaffected.

## P2 — B081, B094, B025 refusals

The independent review of the P2 implementation at `f7895bd2` returned READY
with no blockers. It verified B081's ownership-remedy diagnostic, B094's
whole-lane `BAD_LANE_CONFIG` refusal and `assay verify` acceptance across R2/R3,
and B025's attestation-timeout forward test. Its one nonblocking note was that
a valid rejudge ID assigned to another shard was described only as unknown or
stale. Commit `045dac4c` names operator/shard filtering in that diagnostic and
adds a verify-accepted CLI oracle for the shard case.

The earlier registered gate passed at `f7895bd255255b332f1ec5a04e6607f029e3b12c`
with exit 0 and all 12 markers; its raw log is preserved at
[`assay-WAVE-C-P2-gate-2026-09-25-f7895bd2.log`](assay-WAVE-C-P2-gate-2026-09-25-f7895bd2.log),
SHA-256 `0f98ba640f2f43b3b3008c226f511ed6e7893fa13021657d49a056ca89399b16`.
That gate predates the shard-message oracle and the later `main` tip, so it is
historical evidence only. The branch merged `main` at `8a4a9709`.

### Final review and repairs

A fresh independent review at `11ff984f` returned NEEDS-FIX for three findings:
the shared Topos helper passed progress flags to the pinned Assay 1.2.5 release
smoke (whose CLI rejects them); P0's already-released B101/B102/B093/B079 and
`assay analyze` changelog records had reappeared under `[Unreleased]`; and the
merge had introduced unrelated trailing-whitespace cleanup in
`libraries/cli-extended/BACKLOG.md`. The gate confirmed the first finding:
4,988 passed, 21 skipped, one release-smoke failure in 492.14s. Its raw log is
preserved byte-for-byte, gzip-compressed, at
[`assay-WAVE-C-P2-gate-2026-09-25-11ff984f.log.gz`](assay-WAVE-C-P2-gate-2026-09-25-11ff984f.log.gz).
Raw SHA-256: `fcbac36218e6d50728120dda35aa8884f05fcb0549a3d39389c00101a007d45b`;
compressed artifact SHA-256:
`a785a979951dad239920cd5927fa9c9920094f846d4b98d62bdd11a80a58dd61`.

Commit `7d45deaa` makes progress recording conditional for the historical
release owner while current judge invocations retain `--resume --progress`,
restores the main changelog and adds only P2 notes, and removes the unrelated
backlog diff. Its full gate then found one test-stub signature mismatch after
the helper gained a keyword-only progress option: 4,988 passed, 21 skipped,
one failed in 536.84s. The stub now accepts the keyword and asserts that the
current judge enables progress. The focused diagnostic test passed (`1 passed
in 0.13s`). The failed raw log is preserved at
[`assay-WAVE-C-P2-gate-2026-09-25-7d45deaa.log.gz`](assay-WAVE-C-P2-gate-2026-09-25-7d45deaa.log.gz),
raw SHA-256 `e05a193193d56593babd04572ee0d196618da4b56f30b8a681e6c9f87fbde3a8`;
compressed artifact SHA-256
`789af01c8b605a427a8f9e4fc235713e81fd9ea6f3c2494eca4120400c4797be`.

The final read-only review returned READY at `f21f96291e6bd36b3ceff836a9aa97cb52b530a9`
against `8a4a9709`, with no actionable findings. It confirmed the P2 behavior,
docs, corrected release-smoke argv, changelog, and test-stub update.

### Authoritative gate

The registered `./run-gate.py tester-unified` gate passed on exact code tip
`f21f96291e6bd36b3ceff836a9aa97cb52b530a9`. Container
`run-gate-assay-selfhosted-3892837-23513-1790314123` was inspected with a
3-CPU limit under `dev-gates.slice`. At the 90-second check, all six setup and
compatibility phases had passed; the `tester-unified` assay progress stream
reported the direct pytest command running at 60s, with no error. That main
lane then passed in 533.29s. The nested current Topos run used
`--resume --progress`, wrote its progress stream, and passed its primary
coverage phase in 125.46s; the pinned 1.2.5 smoke also passed with its original
CLI arguments. The CMRU B006A qualification, 7 independent self-hosting tests
(13.61s), and pyflakes passed. All 12 `ASSAY_GATE_PHASE=` markers were
present, as were `ASSAY_GATE_CONTAINER_EXIT=0`,
`ASSAY_REGISTERED_GATE_COMPLETE=1`, run-gate lane exit 0, and
`GATE_OUTER_EXIT=0`. The optional `cgprofile-host-daemon` was absent; run-gate
used coarse rusage sampling and reported no gate impact.

The passing raw output is preserved at
[`assay-WAVE-C-P2-gate-2026-09-25-f21f9629.log.gz`](assay-WAVE-C-P2-gate-2026-09-25-f21f9629.log.gz),
raw SHA-256 `8561f720e6c8251ed8183de169254093582b6ad8603bc564cbd1d9478da466f9`;
compressed artifact SHA-256
`efe5a25ec15783e617d14ca127c66b06e3699f7d81718e8b2b634475e463395a`.

The controller report and three preserved gate logs are report-only evidence
after the passing code-tip gate; the tested source tip remains `f21f9629`.
