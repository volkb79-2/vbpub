# B114 implementation log

## 2026-10-07 — resume and re-scope

- Worktree: `assay-b114-cold-witness`, CIU-managed, attached, clean at start.
- Assay base: `4670f53a67038a8a19b27ffe33e8308ec6f93fde` (Assay 8.0.0 / verdict v14).
- Current `main` advanced to `840a9791c` during this work, but the intervening commits change only `libraries/cli-extended/`; there are no intervening changes under `assay/`, `run-gate-project/`, or `tester-unified/`. Keep the B114 branch based on the last Assay-specific main and reconcile against current main before merge.
- Sol xhigh implementation plan saved at `assay-B114-PLAN-2026-10-07.md`.
- Independent read-only Sol xhigh review of P10a saved at `assay-B110-P10a-SOL-XHIGH-REVIEW-2026-10-07.md`. The review exited 0, recorded HEAD before/after as `4670f53a67038a8a19b27ffe33e8308ec6f93fde`, and left the worktree unchanged. Verdict: **REVISE**. It found the B145 ledger evidence conflict, unchecked ambient identity equality, stale review-scope/review-reference risks, incomplete receipt binding, and unresolved deadline policy. No P10a probes have committed evidence.
- P3a already defines a `BLOCKED-PARTIAL` path for the unsettled ledger anchor. Use that only for the reserved v15 wire: do not implement P10b, do not claim ledger audit support, and keep the B105 checker refusing non-null ledger use until the design is reviewed and ratified.
- B114's original v14 hard cut is stale because v14 shipped in 8.0.0. Implement a **v15 hard cut** and advance judge identity `/7` to `/8`; keep mutation-state schema version 1.
- P3b's fallback currently overclaims: a positive exit alone does not prove that a test call failed. Amend the contract so a cold or declared attempt counts as killed only with cause-sensitive, verified call-phase failure evidence. Collection/setup/teardown/auxiliary failures, signals, termination, resource events, missing or invalid receipts, and other uncertain results never become kills.
- Wave A audit: W6/B113 (`75ceb9e9`) and W8/B111 (`5a695516`) are ancestors of `assay-v7.2.0`; `reports/wave-a/CONTROLLER-HANDOFF.md` records `tester-unified` and `self-qualification-preflight` PASS on the merged revision. W6's final log documents the CD3 watchdog drop. W1/W4 completed or superseded B112's path/argv/dataclass scope; the remaining slow-tier decision has no current measured artifact and is not a prerequisite.
- B117/P6 is a direct B114 prerequisite for persisted deadlines and process-group termination. B115/P4 is a direct P3b prerequisite in the reviewed brief because it owns the executor loop P3b extends. B111/P0 and B113/P2 shipped in Wave A / Assay 7.2.0. Wave A W1/W4 superseded or completed B112's argv, dataclass, and test-scope changes; its remaining slow-tier redecision needs current timing evidence and is not a B114 prerequisite. W6 explicitly dropped the watchdog after the two named real-child tests disappeared.
- Keep B116/P5 out unless measured snapshot costs justify it. Keep P10b out until P10a is repaired and its pending operator choices are ratified. B118/P7 is the planned bounded pilot step after B114 and before the B131 analysis qualification.
- Parallel B087 session is active in `assay-b087-js-canary` at `cac4a92f`. It touches `assay/README.md`, `assay/docs/{CONSUMERS,DESIGN-GUIDE}.md`, and `assay/src/assay/cli.py`, which overlap this wave. I asked it to finish isolated acceptance/review but hold merge/release until coordination; do not edit its worktree or stop its job.
- No Assay tests, gates, mutation campaigns, or containers were started by this session during this review/re-scope.

## Intended sequence

1. Amend the B110/B114 contract and release-facing version references for v15, cause-sensitive kills, the P10a partial path, and the corrected dependency order.
2. Implement B117 campaign deadline and process-group termination, then B115 bounded work queue.
3. Implement B114 P3a–P3d: v15 schema/model/raw verifier, no-coverage command and baselines, cold witness, liveness disclosure, and source-bound B105 report checks.
4. Sync README, DESIGN-GUIDE, CONSUMERS, CHANGES, schemas, fixtures, backlog and evidence reports; focused tests, Sol xhigh review, then registered gates on the reviewed commit.
5. After B114 is integrated, implement B118's bounded pilot and continue with B131 on the updated Assay main.

## Evidence log

Append commands, captured exit statuses, review findings, gates, and merge/release outcomes here as they occur. Do not record a gate or probe as passing until its own exit status and receipt are captured.

## Sol xhigh final-review follow-up — 2026-10-07

- The review of `70bf4493e05b6e9a28b28f21e425a049c6f5be72` found two remaining gaps: the generated liveness plugin was trusted only by module/path, so a candidate `pytest_configure` could replace its report hook while preserving the visible fingerprint; and the B105 checker still read its plan, report, and tester receipt without regular-file, symlink, or size bounds.
- The witness plugin now pins the active liveness plugin's four HookImpl/function/code/globals registrations before candidate conftests load and checks them at collection and on later hook calls. The inactive-liveness path remains supported. Real child-pytest cases exercise forged failures and suppressed real failures in cold and declared attempts while asserting the fingerprint remains equal and neither kill, survivor, nor declared-failure proof is produced.
- B105 now reads report, plan, and tester receipt through the existing bounded no-follow regular-file reader, with limits of 64 MiB, 16 MiB, and 4 KiB. Tests cover valid files plus FIFOs, final-component symlinks, and over-limit files in receipt-only/full modes as applicable. A full 3,760-candidate report, plan, and deadline acceptance case covers the declared B105 inventory shape.
- The first local broad run exposed a test-helper import issue: clearing `PYTHONPATH` let a monorepo namespace directory shadow the package; a change using the fake repository path also broke five hermetic fixtures. The helper now binds its CLI import to this checkout's source roots while retaining the fixture repository for config and Git reads. The local test environment used the source-built wheel installed from its wheel file, preserving PEP 610 provenance for the synthetic run tests.
- Focused verification from `assay/`: `tests/core/test_mutation_witness_unit.py`, `tests/core/test_b114_cold_witness_real_runs.py` (excluding the cockpit-incompatible candidate campaign), `gate/tests/test_b105_report_check.py`, `gate/tests/test_b105_report_check_real_plan.py`, and `tests/core/test_docs_examples_and_vocabulary.py` — **267 passed, 1 deselected in 62.63s**. The deselection is `test_assay_run_cold_witness_covers_early_kill_survivor_and_one_fallback`, which requires visible host cgroup ancestors and belongs in `tester-unified`.
- `git diff --check` and Python compilation of the changed source/tests passed. No registered gate or B105 full R2 campaign was started. Next: update the agent log, commit this fix set, request the exact-commit Sol xhigh review, then coordinate registered-gate timing with B087.

## Final-review P2 closure — 2026-10-07

- Reworked the positive 3,760-candidate checker fixture to build 3,760 distinct native outcome records with derived candidate identities, set the matching candidate and attempt counts and ceiling, and require `verify_document(document) == []` before it binds the matching plan and deadline. The focused acceptance test passed (**1 passed in 0.95s**).
- Added `test_assay_run_liveness_hook_replacement_falls_back_to_declared_command`: with liveness enabled, a candidate conftest forges a failure for the survivor and suppresses a real failure for the killed mutant while the visible hook fingerprint stays unchanged. The end-to-end `assay run` assertion requires both attacks to fall back to the declared command and preserves the real killed/survived outcomes. Collection passed. Candidate execution is reserved for tester-unified because the cockpit cannot see the required cgroup ancestors.
- Updated B114 acceptance text to bind the review oracle to the real CLI candidate path and to require verifier-valid outcome buckets in the 3,760-candidate fixture. Final focused suite: **267 passed, 2 cgroup-dependent candidate campaigns deselected in 66.23s**. The 3,760-report acceptance test passed, Ruff passed for both changed test modules, and `git diff --check` passed.
- No registered gate or B105 full R2 campaign has run. Next: commit, obtain the fresh Sol xhigh review, then coordinate tester-unified with B087.


## 2026-10-07 — final B114 proof closure and local verification

- Closed the three findings from the Sol xhigh review of `d3d3e458`: a cold-policy full kill now requires declared-command evidence plus a failed-call witness; B105 binds that witness node to the R2 manifest; and a declared retry accepts a failed test anywhere in its verified started prefix, including when later tests continue.
- Added model and raw-verifier positive/refusal cases, B105 full-kill evidence and manifest tamper cases, and active-liveness candidate assertions that bind IDs to killed/survived buckets and require all three tests to complete on the declared fallback.
- Estate-venv affected suite: **420 passed, 2 deselected in 63.11s**. The deselections are the two real candidate-run cases that require visible cgroup ancestors; those belong in tester-unified. The documentation oracle passed separately (**54 passed**).
- An initial run under `/usr/local/bin/python` had three fixture failures because its editable Assay import pointed into the stale `.worktrees/rg55-p1-r2-isolated/...` checkout. Rerunning the four affected parametrized/fixture cases with `/home/vscode/.venv/bin/python` passed (**4 passed**); the estate-venv full suite then passed.
- Python compilation and `git diff --check` passed. Ruff reported no diagnostics on added Python lines (100 whole-file diagnostics remain outside the changed lines).
- No Assay registered gate or B105 full R2 campaign has started. The unrelated RG-89 tester-unified selftest container exited; the latest container check showed no assay/run-gate/tester container. Exact-tip Sol xhigh review and B114 registered gates remain pending.

## Review follow-up and source-backed verification — 2026-10-07

- The clean-tip Sol xhigh review of `e1e6b5d6ccbdd29dfa77bc7fad1bdd6c741ceb7f` found a P2 in the post-command snapshot check: `LANE_TIMEOUT` was absorbed as `dirt=None`, allowing a candidate to be classified and persisted without completing its HEAD/index/worktree integrity proof. The fix now propagates that timeout through the executor's unclassified `budget_exceeded` path. A regression test mutates the snapshot then simulates termination during the integrity check; it asserts a budget-exceeded result and no candidate state or progress event.
- Updated the P6 brief, B117 acceptance, and B114 plan to make post-command integrity part of the classification boundary. Earlier P6 text said to absorb that timeout; that statement is superseded by A-473's rule that deadline/termination on any attempt exit path is unclassified.
- The active-liveness hook regression had required a per-report attack log. On the current pytest path, replacing the pinned HookImpl invalidates cold-witness proof before that hook can produce accepted evidence. The test now records the actual replacement after installation and verifies all candidate results use the declared command with the true killed/survived outcomes.
- Replaced the B115 executor test's minimal fake `Future` with `concurrent.futures.Future`; Python 3.14's executor path reads the real Future's private condition while the old fake did not provide it.
- Source-backed regression command from `assay/` (`PYTHONPATH=src:analysis/src python -m pytest -q` over B105 boundaries, campaign deadlines, B114/B148, mutation resource/witness/reuse, CLI, verdict, docs, and dataclass tests): **942 passed, 1 skipped in 84.23s**. The final focused liveness replacement oracle passed separately (**1 passed in 7.98s**). `git diff --check` passed.
- No registered gate or B105 full R2 campaign has started. The current tester-unified and self-qualification-preflight runs remain scheduled for the final reviewed merge commit, per the B114 plan. The latest `docker ps` showed only persistent estate containers and no assay/run-gate/tester gate.

## Sol xhigh receipt and deadline repair — 2026-10-07

- Replaced production cold-witness receipt files with one per-attempt framed
  pipe for coverage baseline, no-coverage baseline, and mutation attempts.
  Both the standard and liveness process runners pass the descriptor. The
  parent drains concurrently, retains at most the receipt bound, and accepts
  one complete frame only; extra, trailing, incomplete, or oversized bytes
  leave no witness. The plugin closes its writer at session finish.
- Changed the retained file-reader utility to Assay's bounded no-follow
  regular-file reader. The ordered manifest now uses that same reader with a
  64 MiB ceiling and must still match the pipe receipt's collection digest.
- Added a final `deadline.remaining()` sample immediately after the snapshot
  integrity helper returns, inside artifact-reservation cleanup. Expiry at
  that classification boundary closes reservations and leaves the candidate
  absent from state and candidate-progress events.
- Added adversarial coverage for post-session receipt writes, duplicate and
  oversized frames, FIFO and symlink receipt files, descriptor passing through
  both process runners, and expiry between the final integrity sample and its
  return. The six focused regressions passed. The affected local suite passed
  **337 tests, with four B106 process tests deselected** because this cockpit
  autoloads unreviewed Hypothesis/Schemathesis hooks that correctly invalidate
  their expected replay witness; the tester image's pinned plugin set remains
  the authoritative oracle for those cases.
- Python compileall, selected Ruff import/error checks, and `git diff --check`
  passed. An initial broad run's two remaining failures were stale P23
  expectations that classified a candidate after its integrity deadline; the
  assertions now follow B114/B117's unclassified boundary.
- No registered gate or B105 campaign has been started by this repair. B087
  coordination remains unavailable through this thread's agent address; hold
  the shared registered gate and serial merge until that slot is coordinated.

## Sol xhigh receipt-drain P2 closure — 2026-10-07

- The exact-tip Sol xhigh review of `6c89ea61` found that a stalled receipt
  drain thread could outlive `finish()` and read a later capture after the
  parent reused its read descriptor.
- Gave the drain thread a duplicated read descriptor, checked the stop event
  after polling and inside the inner read loop, and moved the final bounded
  drain to the parent after the reader exits. The final drain requires EOF;
  `EAGAIN` means a descendant may still write and leaves the receipt
  untrusted. If the join times out, the parent closes only its descriptor,
  while the thread retains its distinct descriptor until exit.
- Added a regression that stalls the old reader during `os.read`, forces the
  next capture to reuse the parent's descriptor number, and verifies that the
  second capture still receives its own complete frame. Receipt-focused tests
  passed (**3 passed**). The affected local suite passed **231 tests, 2
  deselected in 53.58s**; the deselections are candidate campaigns requiring
  cgroup ancestors unavailable in the cockpit.
- Python compilation, Ruff import/error checks, and `git diff --check` passed.
  No registered gate or B105 campaign has run. Next: commit and request a
  fresh exact-tip Sol xhigh review, then coordinate the registered gates with
  B087.

## Sol xhigh follow-up P3 closure — 2026-10-07

- The review of `dee6f9a3d` found two P3s: pipe descriptors leaked if the
  reader descriptor duplication failed during construction, and the race
  regression allowed the later reader to consume the frame before the stalled
  reader resumed.
- `ReceiptCapture` now closes every acquired pipe descriptor if duplication
  or nonblocking setup raises. The regression holds the later reader inside
  `os.read` until the stalled reader resumes, making a stale read from the
  reused parent descriptor reliably consume the frame before the later reader
  can do so.
- Receipt-focused tests passed (**4 passed**). The affected local suite passed
  **232 tests, 2 deselected in 50.70s**; the deselections require cgroup
  ancestors unavailable in the cockpit. Ruff import/error checks, compileall,
  and `git diff --check` passed.
- No registered gate or B105 campaign has run. Next: commit and request a
  fresh exact-tip Sol xhigh review; coordinate gate timing with B087 before
  starting the registered lanes.

## Sol xhigh regression-timeout P3 closure — 2026-10-07

- The review of `c43c97764` found that the fd-reuse regression left its
  10-millisecond join timeout active for the later capture, so scheduler delay
  could turn a valid frame into a flaky test failure. It found no other new
  P0–P3 issue and confirmed both prior P3 fixes.
- The test now restores the original join timeout before finishing the later
  capture. Receipt-focused tests passed (**4 passed**); the affected suite
  passed **232 tests, 2 deselected in 50.45s**. Ruff import/error checks,
  compileall, and `git diff --check` passed.
- No registered gate or B105 campaign has run. Commit and request a fresh
  exact-tip Sol xhigh review; continue holding shared gate execution for B087
  coordination.

## Final exact-tip Sol xhigh review — 2026-10-07

- Review of `6078d159b1018bfc334114f71ecb3e687536a817` exited 0 with **no
  findings**. It confirmed the test restores the normal join timeout before
  finishing the later capture and found no new P0–P3 issue in the commit.
- The before/after HEAD matched exactly and `git status --short` was empty at
  both points. Review route: `gpt-6-sol`, xhigh, read-only.
- B114 is ready for registered `tester-unified` and
  `self-qualification-preflight`; no gate or B105 campaign has run. Hold the
  shared gate slot until B087 coordination is available, then merge serially.

## Tester-unified failure diagnosis and focused recheck — 2026-10-07

- The first registered `./run-gate.py tester-unified` run on
  `5d1ff0583ecb5a41d5dc1f4aba599052de34e54a` exited 1. Its self-hosted R0
  pytest phase reported **7,796 passed, 11 skipped, 17 failed in 480.46s**;
  the complete registered lane took about 510 seconds. The failures were
  stale v15/schema oracles, moved B105 exclusion lines, test fixtures that
  treated lane-deadline timeouts as per-candidate timeouts, and four B106
  witness/replay tests.
- Reproduced the four B106 failures in the tester image: all returned no
  trusted witness because the test child auto-loaded the image's unrelated
  pytest plugins. The same tests passed locally. B106 fixtures now set
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`; Assay still explicitly injects its
  liveness plugin, and the custom-hook test still exercises the unsupported
  project hook and full-suite fallback. The five focused B106 real-child tests
  passed in tester-unified (**5 passed in 10.69s**).
- Updated the exact B105 exclusion-line map, complete v15 CLI judgement shape,
  reason-code oracle, `/8` identity oracle, frozen v15 schema digest, cleanup
  helper's `command_plan` fixture, and the v15 proof-source diagnostic
  expectation. Candidate-timeout fixtures now declare a per-candidate bound;
  the auto-budget test gives the derived duration room inside the lane budget
  and asserts both child calls receive the derived bound.
- The focused set covering the 17 gate failures plus the resource-limited B106
  control passed in the estate venv (**27 passed in 14.54s**) and in the
  tester-unified image (**27 passed in 14.11s**). These are diagnostics, not a
  replacement for the full registered lane.
- No full gate was rerun after these repairs, and no B105 mutation campaign
  ran. All diagnostic containers exited and were removed; `run-gate status`
  reports no inflight gate. B087's `assay-b087-js-canary` tree is clean at
  `3776fe09`; it has no recorded registered-gate history. The supplied
  session ID is not addressable by this thread's collaboration tool, so I
  could not deliver a direct coordination message. Keep the serial merge and
  release coordinated because B087 overlaps the user-facing docs and `cli.py`.
- Next: commit these repairs, obtain the required fresh Sol xhigh review, then
  run `tester-unified` and `self-qualification-preflight` on the reviewed tip.
  B118 remains the prerequisite before B131.

## Clean-tip review follow-up — 2026-10-07

- Sol xhigh review of `37470c55f81ce095af60d5059daeffdd2cb65939` found two
  oracle gaps, with no observed production defect: the reserved
  `equivalence_ledger` had no valid positive verdict through schema, model,
  and raw verification; the auto-budget test's fake runner raised its timeout
  without consulting the supplied duration.
- Added the hand-written `r2_pass_equivalence_ledger.json` control and
  independent checks for schema/model/raw acceptance, malformed ledger facts,
  and ledger-plus-artifact refusal. The test comment preserves the boundary:
  wire verification is covered; B105 ledger production remains disabled.
- Added a real sleeping candidate child under a short test-derived automatic
  bound. The test verifies the derived duration reaches both candidates, the
  slow candidate is classified `budget_exceeded`, and the child does not
  survive process-group cleanup. The existing formula test remains the oracle
  for the shipped derivation.
- Focused suite in the estate venv: **108 passed in 55.23s** across verdict
  artifacts, candidate budgets, raw B105 verification, and B114 real runs.
  This is diagnostic evidence, not a registered gate. No container, full
  registered gate, or B105 R2 campaign ran in this step.
- Next: commit the review closure, obtain a fresh exact-tip Sol xhigh review,
  then run the registered `tester-unified` and
  `self-qualification-preflight` lanes. B118 remains a B131 prerequisite.

## Registered tester-unified failure and correction — 2026-10-07

- `./run-gate.py tester-unified` on `6fe26462692dc3d77e672d2c45ce2bc645065f55`
  exited 1 after 475.77s: **7,819 passed, 11 skipped, 1 failed**. The failure
  was `test_the_transcribed_manifest_agrees_with_its_sibling_transcription`:
  `test_errors.py` already transcribed B114's
  `NO_MEASUREMENT/CGROUP_OBSERVATION_UNAVAILABLE`, while the independent
  conformance vocabulary and fixture set had not.
- Added the missing pair to the conformance transcription, added a complete
  refusal artifact to the fixture set, and made the B148 live pre-R0 refusal
  test round-trip its emitted document through `assay verify`.
- The corrected conformance, reason-code, and B148 tests pass: **207 passed in
  2.68s** in the estate venv. This does not replace a fresh registered gate.
- The fresh Sol xhigh review attempt of `6fe26462` stopped with Codex's usage
  limit before returning findings; its exact before/after HEAD and clean status
  match. Retry after the stated reset using the small follow-up diff since
  `37470c55`, not the already-reviewed full branch.
- Next: commit this correction, rerun `tester-unified`, obtain the fresh exact-tip
  Sol xhigh review when the route is available, then run
  `self-qualification-preflight`. No B105 R2 campaign ran.

## Analysis schema-oracle correction — 2026-10-07

- Registered `./run-gate.py tester-unified` on `40f93c0d9c141968b49d5506dadd5e8f21ff22d7`
  passed the self-hosted Assay R0 lane, then failed the analysis R0 lane:
  **514 passed, 1 failed**. The failure was the schema-refusal case in
  `analysis/tests/test_analysis.py`; its fixture is v15 but the test still
  attempted to replace v14, leaving the valid artifact unchanged.
- Changed the oracle to derive the fixture's current schema from
  `VERDICT_SCHEMA_VERSION` and mutate it to the previous unsupported version.
  The focused schema case passed; the full `test_analysis.py` file passed
  (**162 passed in 6.56s**), and `git diff --check` passed.
- A fresh registered gate is still required on the reviewed repair commit,
  followed by `self-qualification-preflight`; no B105 R2 campaign ran.
- Strengthened the schema mutation oracle to assert the fixture actually carries
  the imported current schema before replacing it. This makes fixture/schema
  drift fail loudly instead of leaving an unchanged valid verdict. The analysis
  test file passed again (**162 passed in 6.65s**); `git diff --check` passed.
- A cockpit-only diagnostic of `python -m pytest analysis/tests -q
  --cov=analysis/src/assay_analysis` reported **513 passed, 2 failed**: two real
  R2 probes were refused because this devcontainer cannot see the complete
  cgroup ancestor hierarchy. This is an environment limitation, not a gate
  result; the dedicated `tester-unified` container remains authoritative. No
  product change was made for this cockpit refusal.
- Increased the real-child timeout regression's bounded marker wait to 1.5s for
  the child's 1.0s delay, polling until the marker appears or the bound expires
  instead of checking at the edge of the delay. The auto-budget tests passed
  (**3 passed, 50 deselected in 2.51s**); `git diff --check` passed.
- After reading the canonical `AUTHORING.md` §3b oracle rules, replaced that
  elapsed-time marker check with a synchronized process-wait boundary. The real
  child signals readiness; the controlled boundary asserts the exact derived
  timeout before injecting expiration, and the default runner must reap it via
  SIGKILL. Its 60s waits are hang failsafes only. Auto-budget tests passed
  (**3 passed, 50 deselected in 0.87s**); `git diff --check` passed.
- Ruff `E4,E7,E9,F` over the five touched test modules and
  `git diff --check 37470c55..HEAD` both passed.
- Sol xhigh follow-up review found two P3 gaps in the timeout oracle: it
  injected `TimeoutExpired` above the real `Popen.communicate(timeout=...)`
  boundary, and checked only the candidate leader after cleanup. Reworked the
  test to synchronize on child output, call the real `communicate` with the
  derived timeout, and verify a same-group descendant has exited. The focused
  test passed (**1 passed in 0.75s**); `git diff --check` passed.
- The full `test_mutation_progress_budget_plan.py` module passed (**53 passed in
  7.78s**); Ruff `E4,E7,E9,F` and `git diff --check` passed after cleanup of the
  now-unused module-level runner import.
- Sol xhigh's second follow-up review confirmed the real `communicate` and
  process-group checks, then found short five-second guards that could compete
  with a slow test and an early assertion outside descendant cleanup. Raised
  the child alarm and cleanup guards to 60s, the lane deadline to 120s, report
  guard expiry as `HANG`, and moved descendant cleanup around the full test
  path. The focused oracle passed (**1 passed in 0.75s**); the full module
  passed (**53 passed in 7.67s**); Ruff `E4,E7,E9,F` and `git diff --check`
  passed. A fresh exact-tip review remains pending.
- Sol xhigh's third follow-up review found that the descendant's natural
  60-second lifetime matched the cleanup guard and could mask leader-only
  termination. Extended the descendant sleep to 600s while retaining the
  60s cleanup guard; the test's outer `finally` still kills it on every
  failure path. The focused oracle passed (**1 passed in 0.84s**); the full
  module passed (**53 passed in 7.88s**); Ruff `E4,E7,E9,F` and
  `git diff --check` passed. A fresh exact-tip review remains pending.

## 2026-10-08 — resume and B105 admission/cleanup repairs

- Resumed from the provisionally integrated Assay tip `6086d4c9`. The B114
  feature commits are already ancestors of `main`; this worktree contains the
  remaining uncommitted acceptance repairs. CIU inspection confirms the
  worktree is registered, attached to `assay-b114-cold-witness`, and based on
  the recorded fork point. Its ignored `.assay/` state was preserved.
- Closed the current B105 launcher review items: derive the Docker-host bind
  source from `findmnt`, query the host systemd unit before placing a probe in
  the requested gates slice, reject runtime-generated units, fail on missing
  or unreadable cgroup controls, and verify finite point-in-time RAM headroom
  before launching the capped qualification container. The wrapper holds a
  Git-common-directory lock across B105 callers, treats the Docker gate scan
  as a non-atomic preflight requiring serial coordination, and force-removes
  only a container whose ID, name, and ownership token reconcile.
- Added wrapper/cgroup regression cases for failed Docker probes and reads,
  uninstalled/runtime-generated slices, RAM admission, shared-host refusal,
  cross-`/tmp` B105 serialization, and forced cleanup after a failed stop.
- B087 coordination: its second lane remains held while B114 uses the shared
  registered-gate slot; its runner and support containers were left untouched.
- The plan's actual bounded pilot sequence needs B118 tooling plus B108 phase
  1 campaign analysis before the pilot run; B119/P9 remains downstream of
  B108, and P10b/P11 remain conditional as their briefs specify.
- `bash -n` passed for the three changed shell entry points and
  `git diff --check` passed. No test or registered gate has run on this
  uncommitted repair set; the earlier receipts are not evidence for its tip.

## 2026-10-08 — exact-tip review repairs and B135

- The Sol xhigh read-only review of `a50d1e1b` found a linked-worktree mount
  failure, a test diagnostic mismatch, unverified bootstrap placement, the
  non-atomic cross-project gate scan, and a missing changelog entry.
- The B105 launcher now mounts the whole host workspace root at both its
  physical path and `/workspaces/vbpub`; the linked worktree and its shared Git
  directory remain addressable inside the child. Its tests assert this mount
  pair. The cgroup helper checks the running container's inspected
  `CgroupParent` against `CGROUP_PARENT_DEV_INTERACTIVE`, then queries the host
  system bus for both that parent and the gates slice, checking unit IDs,
  installed fragments and runtime-generated paths before using the gates
  slice. The test diagnostic now matches the refusal wording.
- The one-time Docker scan still cannot provide an atomic cross-project lease.
  This wave relies on the estate's serial registered-gate policy: B087's lane
  remains held, and no other registered gate may start during a B105 run. The
  docs and wrapper keep this limitation explicit; the coordinator must verify
  the gate slot immediately before and after each lane.
- Folded B135 into the cold-witness admission fix. pytest 9's `pytest.toml` and
  `.pytest.toml` are now inspected ahead of the older config formats; native
  `[tool.pytest]` `pyproject.toml` addopts are checked too. README,
  DESIGN-GUIDE, CONSUMERS and CHANGES describe when xdist config disables the
  cold-witness path.
- Static `bash -n` and `git diff --check` pass on this repair set. No tests or
  registered gates have run on it yet. The next steps are the exact-tip Sol
  xhigh review, serial provisional merge, then fresh registered
  `tester-unified` and `self-qualification-preflight` receipts.

## Sol xhigh repair follow-up — 2026-10-08

- The exact-tip Sol xhigh review of `0720efc56ce69e55668b5805f3e028c2b7feebf1`
  found three P2s: hostname lookup could inspect a different container with the
  same hostname; the first host-manager probe could launch before proving its
  interactive slice was installed; and pytest admission missed lexical
  ancestors of symlinked test paths. The review recorded the same HEAD and
  status before and after and ran no tests or gates.
- B105 now compares the current process's mount and PID namespace identities
  with the `docker exec` target before trusting its inspected parent. A live
  acceptance probe returned identical namespace IDs for the cockpit.
- The MDT template's host-side `initializeCommand` now derives its three tier
  names from `containerEnv`, requires the interactive name to match the active
  `runArgs`, and checks exact `Id`, `LoadState=loaded`, and an existing
  non-runtime `FragmentPath` before Docker creates the cockpit. Missing,
  malformed, unqueryable, and timed-out checks refuse startup. README,
  DESIGN-GUIDE, CONSUMERS, lifecycle, template, and host-setup docs remove the
  prior safe-no-op claim; a docs test parses the consumer JSON example and pins
  its cross-links.
- Cold-witness pytest config admission now checks lexical and resolved path
  ancestors, absolute paths, unresolved path selectors, and `--pyargs`. Tests
  cover child config paths and a symlink selector with xdist configured on its
  lexical parent.
- Final focused local checks: Assay cgroup/self-qualification/mutation-witness
  tests **149 passed**; MDT devcontainer/template tests **33 passed**;
  `git diff --check`, shell syntax, and Python compilation passed. No registered
  gate or R2 campaign has run. The B087 agent reports its registered gate slot
  idle and is holding it until the B114 run completes.

## 2026-10-08 — follow-up review repairs

- The next exact-diff review found that MDT's line scan could certify a
  `--cgroup-parent` string outside the effective JSONC `runArgs`, and that
  pytest admission could treat option values as path selectors while allowing
  a missing bare selector. MDT now parses JSONC structurally, preserves quoted
  strings/comments, rejects duplicate keys, and matches the actual runArgs to
  the interactive environment value. Assay separates recognized pytest
  option values from selectors, rejects unknown option arity and root
  selection overrides, and refuses every unresolved positional selector.
- Added regressions for a misleading nested cgroup-parent decoy, duplicate
  JSON keys, two-token runArgs, `-k` and `--ignore` values, unresolved bare and
  post-`--` selectors, unknown option arity, xdist transport, and root overrides.
  Folded B135 into B114 with its registered-gate completion condition.
- Current focused checks: Assay cgroup/self-qualification/mutation-witness
  tests **151 passed**; MDT template tests **35 passed**; Ruff
  `E4,E7,E9,F`, Python compilation, and `git diff --check` passed. The
  installed pytest long-option list was compared with Assay's recognized
  option grammar; no installed option remains unclassified.
- B087 confirmed no registered gate or R2/R3 campaign is running and is holding
  its slot. No registered gate or R2 campaign has run on this repair diff; the
  next step is a fresh exact-tip Sol xhigh review, followed by the serial
  registered gates.

## 2026-10-08 — Sol xhigh follow-up repairs

- The Sol xhigh review of `03f3c592` found: MDT could count a
  `--cgroup-parent` token consumed as an earlier Docker option's value; Assay
  could miss pytest's active config when `cwd` was a symlink and argv had no
  path selector; and the declared gate set did not exercise MDT's initializer
  acceptance item.
- MDT now parses Docker `runArgs` option/value boundaries (including short
  forms), records only effective cgroup-parent options, and refuses unknown or
  positional syntax it cannot classify. Added decoy-label regressions for
  `--label` and `-l`, a positive case where a decoy label value precedes the
  actual cgroup-parent, and a refusal case for an unknown Docker option. The
  parser's long-option set was compared with this host's `docker run --help`;
  every listed option is classified.
- Assay config admission now searches both lexical and resolved cwd paths,
  including the no-selector case. Added a symlinked cwd regression with a
  resolved `pytest.toml` that enables xdist. README, design, and consumer docs
  now state this behavior. B114 acceptance now requires the MDT `smoke` lane
  alongside Assay `tester-unified` and `self-qualification-preflight`.
- Verification: Assay mutation-witness plus docs tests **168 passed**; MDT
  template tests **39 passed**; Ruff `E4,E7,E9,F`, changed-file Python
  compilation, and `git diff --check` passed. No registered gate or R2
  campaign was started. A fresh exact-tip Sol xhigh review is required before
  registered acceptance gates.

## 2026-10-08 — second Sol xhigh follow-up repairs

- The exact-tip Sol xhigh review of `ea9de716` confirmed the acceptance gates
  still had to run and found two additional defects: the short-option scanner
  missed bundled xdist `-qn2`; and the host-unit probe accepted complete
  property lines without the closing frame marker, while trusting attached
  `docker run` transport status.
- Assay now parses short-option bundles for `-n`/`-f`, rejects bundled `-o`
  overrides, and applies those checks to pytest config `addopts`. The
  symlinked-cwd regression now covers both a command with no path selector and
  one with `tests` selected, against `pytest.toml` `addopts = ["-qn2"]`.
- The systemd query now starts a uniquely named detached container, records
  its ID, reads its own exit status through `docker wait`, captures output via
  `docker logs`, removes the container, and requires exactly two ordered,
  complete frames before accepting any unit properties. Added regressions for
  a nonzero container exit delivered over a successful wait transport and for
  truncated frames with container exit 0.
- Verification: Assay mutation-witness plus docs tests **174 passed**;
  `gate/tests/test_cgroup_parent.py` **21 passed**; Ruff `E4,E7,E9,F`, Python
  compilation, `bash -n`, `shellcheck -e SC2016`, and `git diff --check`
  passed. Plain ShellCheck reports SC2016 for intentionally single-quoted
  in-container scripts (one pre-existing and one added). No registered gate
  or R2 campaign ran on this diff. The next exact-tip Sol xhigh review must be
  followed by Assay `tester-unified`, Assay
  `self-qualification-preflight`, and MDT `smoke` on the final merged commit.
- The unrelated RG-89 `tester-unified` gate ended FAIL at commit
  `1266057b31dc04300a513d100980cbade8929897` after 559.796 seconds: its R0
  reported three failures in that worktree's `test_cgroup_parent.py` and
  recorded exit 1. Its run-gate history is outside this branch and is not
  B114 evidence. The registered-gate slot is now idle. B087 may run its
  non-registered uncovered-line canary; its registered gate and merge remain
  held until B114 acceptance.

## 2026-10-08 — third Sol xhigh follow-up repairs

- The exact-diff review of `6086d4c9..e97156e3` found two P2s: a failed Docker
  probe launch could make the EXIT trap force-remove a pre-existing container
  with the generated name, and `PYTEST_ADDOPTS="-o addopts=-n2"` could enable
  xdist while sequential replay admission still accepted the command.
- The systemd probe now carries a per-invocation ownership label. Before any
  removal, cleanup reconciles its Docker ID, exact generated name, and owner
  label, then removes by ID. Ambiguous or foreign containers are left alone.
  Fake-Docker regressions cover a name collision and a container created before
  the launch command reports failure.
- Sequential replay now declines any nonempty `PYTEST_ADDOPTS`; the cold-witness
  refusal still reports the environment-specific reason. README,
  DESIGN-GUIDE, and CONSUMERS describe the restriction. Added replay tests for
  direct `-n`, `-o addopts=-n2`, and `--override-ini=addopts=-n2` environment
  values.
- Focused results: mutation-witness and B106 reuse tests **177 passed**;
  B114 coverage-verifier tests **40 passed**; cgroup-parent tests **23 passed**;
  docs examples/vocabulary **55 passed**.
  Ruff `E4,E7,E9,F`, `bash -n`, `shellcheck -e SC2016`, and `git diff --check`
  passed. No registered gate or R2 campaign ran on this repair diff. The next
  step is provisional merge, followed by all three registered B114 acceptance
  lanes on the merged commit.

- The fresh Sol xhigh review of the full B114 diff through `976b5c41` passed
  with no actionable findings. HEAD and clean worktree status matched before
  and after; the review ran no tests, gates, or containers. A full-base
  `git diff --check` then identified a trailing blank line in
  `test_verify_b114_coverage.py`, removed as a whitespace-only follow-up.

## 2026-10-09 — Sol xhigh review round 2 deadline and report-check repairs

- A read-only GPT-6-Sol xhigh review ran from the preferred `.codex` home on
  the uncommitted diff at `cbd6054450610c2c57d51ea35d1e914a391c5177`.
  Before/after HEAD and status matched. It ran no tests, gates, or containers;
  the findings are retained in `reports/assay-B114-REVIEW-2026-10-09-round2.md`.
- The review found two P1s: deadline/termination could arrive during state
  serialization or replacement, and final bucketing could return a completed
  mutation after expiry when no state store was in use.
- State persistence now checks the deadline after serializing/fsyncing the
  temporary record and after atomic replacement/directory sync. If the latter
  check refuses, it removes and syncs the candidate record before propagating
  the timeout. Main-thread classification is checked before and after, and a
  complete campaign is checked around final bucketing and before return;
  incomplete budget-exceeded results retain their existing behavior.
- B105 and B110 report checkers invoke `assay verify` on the same bounded
  parsed document they accept and require the six terminal buckets to cover
  the full ordered plan exactly once. Regression tests cover changed report
  bytes, duplicate/partial inventories, deadline during serialization and
  post-replacement cleanup, and expiry/termination at candidate and final
  classification.
- Current focused results: the eight deadline/classification boundary cases
  passed; the complete B105/B110 checker and mutation-executor selection
  passed **183 tests in 61.55 seconds**. `git diff --check` passed. No
  registered gate or R2 campaign has run on this repair diff.
- Next: a fresh exact-tip independent review; after acceptance, commit and
  provisionally merge, then run the registered B114 acceptance gates on the
  merged tree.

## 2026-10-09 — Sol xhigh review round 3 repair pass

- The exact-tip review found four remaining gaps: rollback after directory
  sync failure, deadline expiry during resumed-result aggregation, preserving
  an older valid state record on aborted replacement, and missing user-facing
  documentation for same-snapshot verification/full bucket accounting. The
  read-only report is retained as
  `reports/assay-B114-REVIEW-2026-10-09-round3.md`.
- State writes now preserve an existing record until the replacement has
  passed its directory sync and commit guard; a failed new-record write removes
  its publication, while an aborted replacement restores the prior record.
  Rollback also covers directory-sync errors and runs for guarded and ordinary
  state writes. Completed campaigns check the deadline after resume merging,
  metadata/progress finalization, and immediately before return. Partial
  `budget_exceeded` campaigns preserve their incomplete-result behavior.
- README, DESIGN-GUIDE, CONSUMERS, and CHANGES now state that B105/B110
  checkers validate the same bounded parsed report snapshot used for provenance
  checks and require the six terminal buckets to account for the full plan
  exactly once. B114 backlog scope now records the current judge identity `/9`.
- Targeted state/deadline regressions: **6 passed**. Full B105/B110 checker and
  mutation-executor focused suite: **188 passed in 64.38s**. Documentation
  examples/vocabulary: **55 passed**. `git diff --check` passed before the
  final log/changelog edits; it will be rerun before review. No registered gate
  or mutation campaign has run on this repair diff.
- Next: run the exact-tip read-only Sol xhigh review from `.codex` (fall back
  to `.codex2` only on route failure); then commit, provisionally merge, and
  run the registered B114 acceptance gates on the merged tree.

## 2026-10-09 — Sol xhigh review round 4 refusal-order finding

- The exact-tip review reported one P3: it preferred running
  `verify_document` before B105/B110 source and plan comparisons. The review
  report is retained at `reports/assay-B114-REVIEW-2026-10-09-round4.md`.
- I tested that ordering and the focused suite reported 33 failures: existing
  tests assert B105's documented structural/plan/report refusal order and
  B110's specific R0/R1, inventory, and bucket refusals. The earlier checks
  all fail closed; when they pass, each checker still calls
  `verify_document` on the same bounded parsed object before accepting. Thus
  moving the verifier earlier changes refusal precedence, not the accepted
  report set or the same-snapshot guarantee. I restored the established order
  and removed the temporary precedence tests/docs.
- A fresh exact-tip review will explicitly assess the acceptance set and
  check for any path that could accept without verifying the parsed snapshot;
  refusal precedence remains the existing documented contract.

## 2026-10-09 — Sol xhigh review round 5 deadline-completion repair

- The review found that `run_mutation` inferred campaign completeness from
  whether `Mutation.budget_exceeded` was empty. That bucket also contains valid
  terminal per-candidate timeouts, so deadline checks could be skipped after
  those outcomes. The same review noted DESIGN-GUIDE's B105 verifier-order
  sentence did not match the established refusal precedence.
- `_execute_mutation_jobs` now returns its actual campaign-completion flag
  separately from the verdict payload. The flag comes from its
  lane-deadline/unsubmitted-candidate mask; `run_mutation` preserves it across
  resume merging and final progress writes. The guide now documents that
  source bindings are checked before the verifier validates the same parsed
  snapshot, with both required before acceptance.
- Extended `test_deadline_expiring_during_resume_merge_does_not_return_completed_result`
  to seed a recorded per-candidate timeout, expire the lane deadline during
  merge, and require `LANE_TIMEOUT`. Direct executor boundary tests assert the
  returned completion flag. The focused B105/B110 checker, mutation executor,
  mutation boundary, and documentation suite passed: **312 tests in 63.76s**.
  `git diff --check` passed. No registered gate or mutation campaign has run
  on this repair diff.
- Next: exact-tip Sol xhigh review from `.codex`; on acceptance, commit and
  provisionally merge, then run registered acceptance gates on the merged
  tree.

## 2026-10-09 — final Sol xhigh review

- The read-only GPT-6-Sol xhigh review from the preferred `.codex` home found
  no actionable issues in the current diff. The report is retained at
  `reports/assay-B114-REVIEW-2026-10-09-round6.md`; exit was 0, and HEAD plus
  worktree-status snapshots matched before and after.
- The reviewed tree passed the focused B105/B110 checker, mutation-executor,
  mutation-boundary, and docs-contract suite (**312 passed in 63.76s**) and
  `git diff --check`. No registered gate or mutation campaign has run yet.
- Next: commit and provisionally merge this package; run the registered B114
  acceptance gates on the merged commit.

## 2026-10-09 — tester-unified integration follow-up

- The first registered `./run-gate.py tester-unified` run on merged Assay
  commit `7ea7b5817d19e484335a87dfc5ba96c24858e467` exited 1 after 9m19s:
  **8,260 passed, 85 failed, 11 skipped**. The run log is
  `/tmp/run-gate/lanes/tester-unified/edda88996c5086fc74b80b21737517e3.log`.
- Diagnosed integration failures: B114 added tracked source
  `src/assay/_mutation_inventory.py` without adding it to both B105 target
  inventories; the B105 exclusion map retained pre-edit line numbers for
  `mutation.py`; and pyflakes found an unused caught exception name plus two
  local guard definitions that reused their initialized variable names.
- The repair adds the helper to both B105 target lists, updates the reviewed
  exclusion lines, removes the unused exception binding, and gives the nested
  progress guard functions distinct names before assigning them to the optional
  callbacks. Added R0/R1 tests covering every success and refusal path of the
  complete-inventory helper.
- Focused verification on this branch: **73 passed** across the B105 exclusion
  control, mutation-inventory tests, self-lane tests, and shipped-tree pyflakes
  check. A dedicated branch-coverage run for the helper reported **100%** (41
  statements, 30 branches); `git diff --check` passed.
- The failed gate container is gone, `./run-gate.py status --json` reports no
  inflight work, and the CIU-managed worktree remains attached to its recorded
  branch. Next: exact-tip independent Sol xhigh review, serial merge, and a new
  registered `tester-unified` run on the repaired merged tree.

## 2026-10-09 — Sol xhigh integration review round 7

- The read-only GPT-6-Sol xhigh review from the preferred `.codex` route
  returned **REVISE** with one P3: tests did not prove that each of the six
  terminal outcome buckets is required and counted. The report is retained at
  `reports/assay-B114-REVIEW-2026-10-09-round7.md`; before/after HEAD and status
  snapshots matched at `30fd138b95c3fbc50e9361eea4869dad5b8ebcb9` with a clean
  worktree. The review ran no tests or gates.
- Expanded the success fixture to put one planned candidate in each bucket
  and added a refusal case for each missing bucket. Focused verification then
  passed **78 tests**; helper statement and branch coverage remained **100%**
  (41 statements, 30 branches), and `git diff --check` passed.
- Next: exact-tip Sol xhigh review of the strengthened test, then proceed to
  the serial merge if accepted.

## 2026-10-09 — Sol xhigh integration review round 8

- The read-only GPT-6-Sol xhigh review from `.codex` returned **REVISE** with
  one P3: tests needed to distinguish a missing bucket from a present
  non-array bucket value. The report is retained at
  `reports/assay-B114-REVIEW-2026-10-09-round8.md`; before/after HEAD and status
  snapshots matched at `046f4831cde74fe587f29a14443320809734cdd9` with a clean
  worktree. The reviewer ran no tests or gates.
- Added a tuple-valued outcome case for each of the six buckets. Focused
  verification passed **84 tests**, and the helper remained at **100%** branch
  coverage (41 statements, 30 branches). `git diff --check` passed.
- Next: exact-tip Sol xhigh review of the new non-array refusal cases.

## 2026-10-09 — Sol xhigh integration review round 9

- The read-only GPT-6-Sol xhigh review from `.codex` returned **ACCEPT**, with
  no actionable P0–P3 findings. The report is retained at
  `reports/assay-B114-REVIEW-2026-10-09-round9.md`; before/after HEAD and status
  snapshots matched at `6133f78d66cd9f2499f52df5c6fed7855f39a01c` with a clean
  worktree. The reviewer ran no tests or gates.
- The reviewer confirmed the six-bucket success case, missing-key and
  non-array refusal cases for all buckets, exact source inventories, current
  exclusion lines, and behavior-preserving source edits. The controller's
  focused suite passed **84 tests** with **100%** helper branch coverage.
- Next: merge serially and rerun registered `tester-unified` on the repaired
  main tip.

## 2026-10-10 — integrated acceptance checkpoint

- The full since-8.0.0 Assay Sol xhigh review found no actionable code defect;
  its only P1 required a passing registered gate on the exact reviewed tip.
  The repair review of the subsequent test-oracle fix also accepted with no
  findings. Both reviews recorded unchanged HEAD/status snapshots.
- Registered `./run-gate.py tester-unified` passed on
  `b8713d5672c92ff323a832ae8de65d567493cf2e`; it emitted
  `ASSAY_REGISTERED_GATE_COMPLETE=1` and `ASSAY_GATE_CONTAINER_EXIT=0`.
  Run log: `/tmp/run-gate/lanes/tester-unified/201576387bf0a04109bb2c0e9faacb89.log`.
  The gate covered the self-hosted Assay lane, analysis R0/R1, self-hosting,
  pyflakes, SQL qualification, and B145 process-limit probes.
- The same-tip `./run-gate.py self-qualification-preflight` refused before
  launching its child: `dev-gates.slice` had 1,280,720,896 bytes of headroom,
  below the required 2,147,483,648 bytes. Log:
  `/tmp/run-gate/lanes/self-qualification-preflight/009eb0b000c7cfce23427c30281c6f6d.log`.
  No preflight container or R2 pilot was launched.
- The B114 MDT `./run-gate.py smoke` lane passed: **119 passed, 6 skipped,
  6 subtests**, exit 0. Log:
  `/tmp/run-gate/lanes/smoke/a47b9fab1586f093ad1756eb5b4b6dae.log`.
- Local `main` provisionally merged this reviewed/tested branch as
  `2cad53d926861a38f5185b00bcbf10625446d81`. Retry preflight and both bounded
  pilots after the host gates slice has the required capacity; do not claim
  B105 or B131 full R2 qualification from these checks.
