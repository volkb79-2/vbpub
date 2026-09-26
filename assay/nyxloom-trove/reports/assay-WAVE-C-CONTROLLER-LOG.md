# Assay Wave C controller log

**Status:** COMPLETE. Wave C shipped as Assay 7.1.0 on 2026-09-25; B105 is
the agreed next package before M7. The source handoff is
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


## P3 — B095 monitor cost and B076 baseline ruling

P2 merged serially to `main` at `130ba5ba0b639dcdafbf1c2a18d0f09c9c4a5c4a`; this package's CIU worktree was created from that exact main tip on branch `assay-wave-c-p3-b095-b076`.

B095 now keeps the CPU samples needed for the trailing 30-second comparison in a deque, dropping an old sample only when its successor also meets the window boundary. A deterministic 20,000-tick virtual run compares every resulting baseline and CPU-growth classification against the former reverse-list algorithm, includes `/proc`-style missing samples, and asserts a maximum of 121 entries at a 250ms minimum interval. `_EventProgressReader` tracks each candidate file's appended bytes, pending partial line, record count and finish-pid state; it resets on missing/replaced/truncated files. Differential tests compare it with `_read_events_progress` through split records, malformed tails, all original `str.splitlines()` boundaries (including Unicode separators), mixed stamped/unstamped finish records, atomic file replacement and in-place truncation. A reviewer also ran 25,289 differential event polls across 640 deterministic append sequences. Process-tree accounting and `/proc` failure behavior remain as before, with the existing regression tests still exercising them.

After the line-boundary compatibility fix, a refreshed probe used a valid 60,000-record NDJSON fixture (8,377,780 bytes) under Python 3.14.7 on Linux 7.1.8 / x86_64. It warmed the full-file reference twice, then measured seven calls: median 209.15ms (201.18–542.46ms). Seven independent incremental readers parsed the initial file at a median 164.48ms (152.84–192.38ms); reading one subsequently appended record took 0.0562ms; seven unchanged polls had a 0.0213ms median (0.0192–0.1742ms). Results matched at `(60000, False)` and `(60001, False)`. The unchanged-poll median is about 9,800 times lower than a full re-read in this probe. Times are descriptive and do not gate behavior.

The process-tree sampling probe separately called `tree_cpu_seconds` 1,000 times on a one-process tree: 0.0471ms median, 0.0774ms p95, 0.9978ms maximum. Its code remains unchanged because the measured sampling cost is small beside the former full event rescan, and preserving its process-tree and `/proc` failure semantics is more valuable than speculative changes.

B076 is ruled in A-457 as option (a): the native R2 baseline continues to receive `timeout=None`; caller-side stall detection owns it, with `command_running` as the signal when `--progress` is enabled. The docs name the rejected `budget_per_baseline` and overloaded-`budget` alternatives. No reason-code or lane/verdict schema changed, and `assay verify` remains untouched.

Focused checks after the line-boundary and replacement regressions landed:
`tests/test_liveness_runner_monitor.py`, `tests/test_liveness_proc_helpers.py`,
`tests/test_config_unbounded_budget.py`, and
`tests/test_docs_examples_and_vocabulary.py` passed (163 tests, 1.62s).
Ruff's `E4,E7,E9,F` selection passed on changed Python modules; `git diff
--check` passed. Fresh independent review against main `130ba5ba` returned
READY, with no blockers; the reviewer verified a 25,289-poll differential
probe, the 20,000-tick CPU history oracle, docs anchors, and the replacement
and separator regressions.

### Authoritative gate

The registered `./run-gate.py tester-unified` gate passed on exact code tip
`09d1f38d0c95118b0a98724bd541a5c16cf109cc`. Container
`run-gate-assay-selfhosted-3989906-21494-1790318054` ran under
`dev-gates.slice`; inspection found it initially had no CPU cap, so
`docker update --cpus=3` was applied within about 39 seconds and verified at
3 CPUs for the rest of the run. At the required 90-second check, all six setup
and compatibility phases had passed and the self-hosted lane was progressing;
the estimate was 10–15 minutes based on P2. It completed in about 17 minutes
from launch. Run-gate removed the container on completion.

All 12 `ASSAY_GATE_PHASE=` markers were present, along with
`ASSAY_GATE_CONTAINER_EXIT=0`, `ASSAY_REGISTERED_GATE_COMPLETE=1`, the
registered lane exit 0, and the outer `GATE_EXIT=0` marker. The Topos
qualification, CMRU B006A qualification, seven independent self-hosting tests,
and pyflakes phase passed. The optional cgprofile daemon was absent; run-gate
used coarse rusage sampling and reported no effect on the gate result.

The raw gate output is preserved at
[`assay-WAVE-C-P3-gate-2026-09-25-09d1f38d.log`](assay-WAVE-C-P3-gate-2026-09-25-09d1f38d.log),
SHA-256 `fa478e0c3c6e15e1b3e7c336a6299cde2bd3405a4e80e49336ff9b8626b46a4c`.
It is 6,348 bytes; no compression was needed.

The gate log and this result entry are report-only additions after the passing
code-tip gate. The tested source tip remains `09d1f38d`.


## P4 — B100 `assay analyze report` (review READY; gate PASS)

P4 is in CIU worktree `/workspaces/vbpub/.worktrees/assay-wave-c-p4-b100-report`,
branch `assay-wave-c-p4-b100-report`, CIU id `9fcfcc`, created from the
P3-merged base `8911e636ad09071868c813347bf7a4d0a00049bb`.

The implementation adds the read-only `assay analyze report` snapshot under
A-460, a packaged JSON schema v1, and CLI examples in README, DESIGN-GUIDE,
CONSUMERS, and INTERNAL-CONSUMERS. Status is derived only from a verifier-valid
commit-bound verdict or fresh nonterminal progress. The progress reader uses
the latest run and tolerates one malformed unterminated final record; the log
reader hashes the complete file and scans only its final 64 KiB for bounded
diagnostics. The command writes no report or progress artifact.

Focused verification after the first adversarial review found boundary cases:

- `tests/test_analysis.py` and `tests/test_docs_examples_and_vocabulary.py`:
  **165 passed**. Added regressions for malformed oversized torn progress,
  oversized complete progress records both newline-terminated and valid but
  unterminated, freshness observed after an earlier lane's log snapshot, a
  complete diagnostic exactly at the retained-window boundary, ignored-file
  writes, and parser coverage for each B100 example.
- Focused Ruff selections (`E4,E7,E9,F` and `I001`), pyflakes on the changed
  Python files, and `git diff --check`: **PASS** (estate venv).
- Fresh independent adversarial review against the P4 base: **READY** after
  round 3. The reviewer confirmed the earlier findings were fixed and
  differential-checked the oversized-record framer against 5,000 generated
  or mutated JSON documents and 18 explicit syntax cases; no remaining
  blockers.
- Registered detached `./run-gate.py tester-unified` passed on exact code tip
  `7859057f802640e905497f0132cc802212e07c41`. It started at approximately
  2026-09-25 10:37:54 UTC and completed at approximately 10:53 UTC. At the
  required 90-second check, six of twelve phase markers had passed and the
  gate was progressing; the estimate was 18–25 minutes. All twelve phase
  markers later appeared, including the Topos and CMRU qualifications, and
  the self-hosted lane passed. Exit evidence: `ASSAY_GATE_CONTAINER_EXIT=0`,
  `ASSAY_REGISTERED_GATE_COMPLETE=1`, lane exit 0, and `GATE_EXIT=0`.
- Container `run-gate-assay-selfhosted-4171085-30356-1790332674` ran under
  `dev-gates.slice`. The nested gate container was capped at 3 CPUs with
  `docker update --cpus=3`; the limit was verified in Docker's `NanoCpus`
  field. The container was removed after completion. The optional
  `cgprofile-host-daemon` was unavailable, so run-gate reported coarse rusage
  sampling; this did not affect the gate result.
- The raw output is preserved at
  [`assay-WAVE-C-P4-gate-2026-09-25-7859057f.log`](assay-WAVE-C-P4-gate-2026-09-25-7859057f.log),
  SHA-256 `a12909d52a86e5e17d334b1584daba4bb61bb1f744ca633b2dfab8754edf11fe`.
  The log and this result entry are report-only additions after the passing
  code-tip gate; the tested source tip remains `7859057f`.


## P5 — B106 selective mutation reuse

P5 is implemented in CIU worktree
`/workspaces/vbpub/.worktrees/assay-wave-c-p5-b106-selective-reuse`, branch
`assay-wave-c-p5-b106-selective-reuse`, based on main tip `3f117b27`. The
implementation adds schema v13 candidate identity, exhaustive current-scope
inventory, bounded pytest failure-witness capture, current witness-prefix
replay with full-suite fallback, preview classification, and README/design/
consumer documentation. V12 is cold-start only. A-461 records the schema-only
change; lane schema and reason codes are unchanged.

GPT-6-Luna xhigh recommended v13 verdict fields only. Its initial adversarial
review's five findings were repaired. The final follow-up found a P2 module
prefix spoof; the hook allowlist now checks exact generated plugin paths and
pytest's installed package path. A deceptive dynamically registered lookalike
plugin regression verifies that a passing test cannot be replay-certified as
a kill. A fresh final xhigh review found no actionable P1/P2 findings.

Local evidence: the combined focused suite had 315 passes; after fixing the
intentional empty-`PYTHONPATH` ShellCheck annotation, ShellCheck and the gate
marker/order test passed. After the final P2 repair, the B106 suite passed 25
tests, including the lookalike-hook and real liveness-plugin integration
tests. The first registered gate attempt at `d43167c6` exited 1 in the
historical-v6-v10 hard-cut check: its inline expectation still said the
current verifier was v12 after P5 cut it to v13. The installed v13 verifier
correctly refused a v6 template; this was stale gate expectation, not a
product failure. The raw log is preserved at
[`assay-WAVE-C-P5-gate-failed-2026-09-25-d43167c6.log`](assay-WAVE-C-P5-gate-failed-2026-09-25-d43167c6.log),
SHA-256 `deb8164774864c80134b7f36244128515490ea2073aff1a4d0a15900f186fb18`.
The gate now derives its refusal from `VERDICT_SCHEMA_VERSION` and pins the
P5 cut to 13; its source test covers that assertion. P5 details are in
[`assay-WAVE-C-P5-B106-REPORT.md`](assay-WAVE-C-P5-B106-REPORT.md).

The next attempt started on `c28fbbb9` and passed the focused Wave C/P5
phases, but was intentionally stopped with exit 143 when the 90-second check
confirmed that shared `main` had advanced to `23214ef5` beyond P5's
`3f117b27` base. Its exact container had a verified 3-CPU limit under
`dev-gates.slice`. The raw log is preserved at
[`assay-WAVE-C-P5-gate-cancelled-2026-09-25-c28fbbb9.log.gz`](assay-WAVE-C-P5-gate-cancelled-2026-09-25-c28fbbb9.log.gz).
Current `main` was merged cleanly into the P5 branch as `34580468`; only the
gate on that merged tree can count as final acceptance.

The final registered `./run-gate.py tester-unified` gate passed on source tip
`a0e2fe23d4a47ddd1c04564a901f5ef647acc670`, with current `main`
`23214ef58d6ba91bca6e01ee6cef0553929e440a` included. It completed in 18m27s
after all 13 phase markers passed, including the full self-hosted lane, Topos,
CMRU B006(a), independent witness, and pyflakes. Exit evidence:
`ASSAY_GATE_CONTAINER_EXIT=0`, `ASSAY_REGISTERED_GATE_COMPLETE=1`, lane exit 0,
`OUTER_GATE_EXIT=0`, and `GATE_EXIT=0`. Container
`run-gate-assay-selfhosted-520812-20390-1790349632` was confirmed at 3 CPUs
under `dev-gates.slice` and removed after completion. The optional profiling
daemon was unavailable, so run-gate used coarse rusage sampling; this did not
affect the result. The raw 6,955-byte log is preserved at
[`assay-WAVE-C-P5-gate-2026-09-25-a0e2fe23.log.gz`](assay-WAVE-C-P5-gate-2026-09-25-a0e2fe23.log.gz),
SHA-256 `f930192e75eca593d82fce196eb5a53a0323ef80621fc95a0bf7ac250570a056`.
The tested source tip remains `a0e2fe23`; only its gate log and report were
added after the pass.

### Agreed continuation after P5

After P5 is gated, merged, and included in the single Wave C release, B105 is
the next package and must complete before M7. The Wave C pre-release lane stays
R0-only. B105 will add an explicitly invocable whole-source R0–R3
self-qualification run: R1 whole-target branch coverage with a full floor,
native R2 mutation over the full source, R3 canary qualification, and a
verifier-accepted report produced from the final B105 tree. The report, not a
review, is the required evidence. See the P5 report for the recommended
separate named gate and acceptance details.

## Release and final closeout — 2026-09-25

The single Wave C release is complete. The release used `origin/main` at P5
source tip `e5e9b95c5ac8be3452c93f1066f9436347f862fd` for its dry run and
transaction; the shared primary checkout had concurrent RG-55 commits and was
not used as the release base. CMRU selected the minor release `assay-v7.1.0`.
Its immutable candidate/tag commit is `f8999975bdc54c0ac8e0a8cb2a6ff9e3e5c1ae4f`,
whose parent is that exact P5 source tip.

The release's registered `tester-unified` gate passed on the tagged candidate
with all 13 expected `ASSAY_GATE_PHASE` markers,
`ASSAY_GATE_CONTAINER_EXIT=0`, and `ASSAY_REGISTERED_GATE_COMPLETE=1`. It ran
for 1,428.8 seconds in container
`run-gate-assay-selfhosted-996702-27557-1790368267`, capped at 3 CPUs under
`dev-gates.slice`. The captured 6,723-byte gate output is preserved at
[`assay-WAVE-C-release-gate-2026-09-25-f8999975.log`](assay-WAVE-C-release-gate-2026-09-25-f8999975.log),
SHA-256 `93b7ba458db216fe5c758ff4b37128711ca6128f4bb0032e805be9ad5228d04a`.
The outer CMRU transcript is preserved byte-for-byte in
[`assay-WAVE-C-release-transaction-2026-09-25-f8999975.log.gz`](assay-WAVE-C-release-transaction-2026-09-25-f8999975.log.gz):
its 5,911-byte raw SHA-256 is
`67aa265637a5655c596ecf9fb8a7837129c0f0b5c52c2057f2d3cfaf01afaf12`, and the
gzip artifact SHA-256 is `2aa0fcc6e572b461da9817fac7126b8332847b803b246be02664ccbae935bab9`;
the publication step output is at
[`assay-WAVE-C-release-publish-2026-09-25-f8999975.log`](assay-WAVE-C-release-publish-2026-09-25-f8999975.log),
SHA-256 `1b6d9fac7d1c70a812842fa162d435a89368d078149f4f840e63cedaf4133912`.

CMRU's outer transaction reported `CMRU_RELEASE_EXIT=1` because a concurrent
push advanced `origin/main` before its final promotion; the candidate was
retained, not rebased. Tagging and publication had already succeeded. Recovery
merge `ecaa74b7772e3ee69a621a71157ab13cf4d55120` has parents
`af1aca25d6a384790736ade6114cec290a7d0525` and the exact tagged candidate
`f8999975...`; `f8999975` is an ancestor of `origin/main`. This keeps the
published, gated source tree intact while including it on current main.

GitHub release [assay-v7.1.0](https://github.com/volkb79-2/vbpub/releases/tag/assay-v7.1.0)
contains the wheel, wheel sidecar, pyz, pyz sidecar, release manifest, and
manifest sidecar. The three sidecars verified successfully. SHA-256 values:

- `assay-7.1.0.pyz`: `08ec434abc7495db7153abd7801ff2083af825d704f1800e2018c90546d3ab3c`
- `assay-7.1.0-py3-none-any.whl`: `4a1cf23a970919bbf0c916ca91170a22d48d47976438e227c965100973191493`
- `release-manifest.json`: `23a949101d83eacb1e0de7651343e115a421ebd4c6cdae2cf3ee26e63f69dca7`

The released wheel is installed in `/home/vscode/.venv`; `pip show assay`
reports version `7.1.0`, and the installed CLI reports `assay 7.1.0`.

The dstdns inbox notification was written according to its then-current
`.assay-inbox/CONTRACT.md`, with the pyz sidecar digest and the landed
B080/B089 items; its notes also cover B106 selective reuse. Its SHA-256 is
`4c46ef6ce096d75e8b16f019b56350c915481e6095d5301cc444b06b22e0b46e`; no
dstdns tracked source was changed. RG-55 received the release/version update
in thread `01a0984e-414f-7b32-80ee-932767086470`; its response confirmed the
release does not alter the active source-backed P1 campaign. No RG-55 worktree
or branch was changed.

The operator-approved notice about the unrelated stale CMRU candidate at
`0950fcea` could not be delivered to thread
`01a0d02b-2c7a-7683-8355-1340ec6a9aab`: messaging refused because that thread
already had an active writer. Its worktree was left untouched. That older
candidate's mutation campaign ended with three survivors; the controller's
later update identifies a separate current run based on `ecaa74b7`.

After the release, the hand-written notes that CMRU leaves under `[Unreleased]`
were moved into the generated `7.1.0` section of [`CHANGES.md`](../../CHANGES.md),
and `[Unreleased]` is empty. This final commit contains the documentation plus
the captured tester-unified gate, CMRU transaction, and publication logs. It
does not change the released artifact or its tagged source. No new test suite
or gate was run for this docs-only closeout.

B105 remains the agreed next package after Wave C and before M7. The ordinary
Wave C release lane is R0-only; B105 must independently qualify all production
`src/assay` source at R0–R3 and retain an `assay verify`-accepted report for
its exact final tree. Reviews do not replace that measurement.
