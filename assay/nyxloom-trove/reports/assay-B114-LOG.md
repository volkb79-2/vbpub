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
