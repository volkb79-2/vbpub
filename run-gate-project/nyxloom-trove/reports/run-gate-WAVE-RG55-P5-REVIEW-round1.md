# RG-55 P5 final adversarial review — round 1

REJECT

## Initial blind review checkpoint (before repairs)

- Selected target: `P5`; isolated branch `rg55-p5-transport-port-20261010`.
- Base: `efaf112945233e3a8f6d53f2fef136439d18d089`; initial HEAD: `27b51ad427bf7291f0c52c6117a48a2983bcc79b`.
- Initial worktree status was clean. The 73-path integrated diff and its name/status list were captured at `/tmp/rg55-p5-initial-diff-name-status.txt` before editing. This includes Run-Gate, Assay, and cgroup-profiler; P5 gates cannot certify the latter packages for release.
- Caller route evidence reference: the outer programmatic invocation, visible in the process table at review start as PID 796441, used `codex exec --json -m gpt-6-sol -c 'model_reasoning_effort="xhigh"'` with this worktree and the literal P5 packet prompt. The caller retains authoritative invocation/session metadata; this is a reference to that record, not a reviewer self-attestation.
- Initial host memory PSI `full avg10=0.00`; no mutation/gate container was running. The managed main daemon was up. Contract mirrors had identical SHA-256 `409bafc7e5e572443d2d1075bebc8b491927ffc4f30fdb396d998e129583ecf4`.

## Initial blockers

| ID | Severity and location | Observable failure, reproduction, prescription |
|---|---|---|
| B1 | High, `run-gate-project/run-gate.py:10694` | `await_container` polls `LogStreamWatch` or `fallback_watch` even with a healthy daemon watch. A command or Assay lane can be called `STALLED` from local silence while the daemon has paused or withheld a verdict under host pressure. An existing test named `test_live_daemon_watch_does_not_trigger_fallback` does not install a local fallback, so it cannot detect this. Reproduce with an active fake daemon watch, a silent local watch, and a still-running fake lane. Poll the local watch only after daemon-watch failure/fallback and assert the lane remains running while daemon watch is healthy. |
| B2 | High, `run-gate-project/run-gate.py:3046` | `profile_start_policy` selects `--on-stall kill` for every declared `stall_timeout`, including shared-scope exec/bare-host lanes with no placement request. Contract §8.9(2) requires `report` in that case; daemon §8.3 refuses unsafe shared-scope kill as `bad-policy`. A valid lane therefore loses daemon profiling and falls back to local judgment. Reproduce with `scope=container-shared`, `stall_timeout`, no memory request/manifest. Derive policy from scope and verified placement plan; retain the declared local watchdog as verdict authority when kill is unavailable. |
| B3 | High, `run-gate-project/run-gate.py:12097` and `:11802` | The bare-host and exec paths call `ctl start` before spawning the token-bearing lane child. The daemon's placement start cannot resolve that child and can return `place-refused:no-target-pids`; the body then runs without applied placement. P5 handoff C6 requires the blocked wrapper to be terminated before the body runs on refusal, while current SPEC R-43l instead says to warn and run unplaced. This is an unruled behavioral contradiction and lacks a real Run-Gate placement probe. Repair with a ready/continue launch handshake plus explicit placement-result check, or obtain a numbered product ruling and synchronize the contract, SPEC, README, DESIGN-GUIDE, CONSUMERS, and LANE-AUTHORING before acceptance. |
| B4 | Medium, `run-gate-project/run-gate.py:10687` and `:10771` | A daemon kill can end a container before its watch thread queues the terminal `verdict`. The wait loop consumes once, closes the reader, and ignores the final `stop` summary's `watch`; the result can be ordinary exit 137 instead of the required R-40 stall path. Reproduce with a kill-summary response arriving after the log-follow process exits. Reconcile the terminal watch from `stop` before assigning the lane outcome, without converting a mere profiling failure into a verdict. |

`LANE-AUTHORING.md:385` also labels the P5 seam `RG-72`, although the handoff and backlog assign RG-72 to the separate failed-Assay-evidence issue. This is a required documentation repair. No code or tests had been changed when this initial record was written.

## Review continuation

This file is the initial REJECT record. Repair/test/gate evidence and the final verdict for this same review session will be appended below. No merge or release is authorized by this record.

## Final round-1 disposition

**REJECT.** B1, B2, and B4 were repaired in this session; B3 remains a
release-blocking P5 defect. The reviewed code commit is
`34497084a1ef6d69bfe4902a31846dd86a9d2e32` (tree
`6cbe91a145610044bf6812c7faa708f628fa21a6`). The final review-artifact
commit is the enclosing HEAD of this file. The initial diff was captured before
all edits. No merge, release, install, daemon replacement, or external checkout
mutation occurred.

### Requirement-to-oracle traceability

| Requirement | Code path | Observable and negative case | Evidence / disposition |
|---|---|---|---|
| D-30 / contract §8.1 carrier parity | `ProfilerClient._wire_args`, `_socket_ctl`, `_exec_ctl` | Socket and exec transport carry one verb shape; unavailable/malformed response is a disclosed profile failure | Golden/socket unit fixtures exist; candidate-image live socket/exec pair is recorded in the P5 REPORT at `465f67d0`. No new carrier probe was run on this code-repair tree; prior evidence is historical. |
| D-27 / §8.2, §8.9 single daemon watch | `await_container`, `run_exec_lane`, `run_bare_host_lane`, `consume_profiler_watch` | Healthy daemon watch owns liveness; only after failure can local silence judge. A late terminal kill still maps to R-40; `reported` does not | B1/B4 fixed in `34497084`; focused tests cover healthy daemon + silent local timer, killed vs reported summary. |
| §8.3 / §8.9 shared-scope kill safety | `profile_start_policy`, `start_lane_profiling`, `start_bare_host_profiling` | Unplaced shared scope asks `report`; placed shared scope or exact container scope may ask `kill` under declared stall policy | B2 fixed in `34497084`; behavioral policy fixture covers all three combinations. |
| D-20/D-31 / P5 C6 placement | `run_exec_lane`, `run_bare_host_lane`, daemon `SessionServer.start` → `LanePlacement.apply` | Token-bearing PID exists before placement, read-back proves exact leaf; refusal has a settled lane-body outcome | **B3 OPEN:** the child does not exist at `ctl start`; daemon `apply([])` returns `place-refused:no-target-pids`. Candidate-daemon direct probes do not exercise Run-Gate's order. |
| R-45 headroom and RG-56 separation | `prepare_profile_start`, `profile_admission` | `would-wait` is read-only on dry run; bounded wait proceeds with warning and no functional verdict | Existing source tests and historical gate receipts only; no new R-45 finding. RG-56 remains OPEN. |
| R-36h / D-15 / CP-19 | profiling finish path; `LanePlacement` origin inventory | Failed profiling leaves test verdict untouched; last-member move refuses, anchored move restores; no guessed parent | Candidate-image direct probes and CP-19 tests in P5 REPORT are historical. CP-19's documented check/move race is a disclosed S1 below, not a proven atomic lifetime lease. |
| Native-R2 observer / exact-tree evidence | Assay `resource_limits.py`; cgprofile `resource_observer.py`; source-backed Run-Gate lanes | Hidden ancestors alone trigger observer fallback; unattributable shared ancestor aborts before candidate bucket | Integrated diff inspected. Assay's own registered gate and final-source R2 remain separate release holds. No complete exact-tree mutation evidence exists for this reviewed commit. |

### Pairwise and combined-axis attack fixtures

| Axes | Constructed or required negative observation |
|---|---|
| Daemon healthy × local command/Assay silence | New focused test supplies an active daemon watch and a local watch that raises if polled. The lane finishes without local judgment. This would have failed before B1's guard. |
| Daemon kill × stream loses terminal event × container exits | New test has an empty stream and a stopped summary with `method=daemon`, `watch.verdict=killed`; the runner raises the R-40 stall path. The paired `reported` summary leaves raw exit 137 intact. |
| Shared scope × explicit `stall_timeout` × no placement request | Policy fixture requires `on_stall=report`; the same lane with a placement request and an exact container lane require `kill`. |
| Exec or bare-host × requested placement × token child not yet spawned | `run_exec_lane` calls `start_lane_profiling` before `subprocess.Popen`; `run_bare_host_lane` calls `start_bare_host_profiling` before `subprocess.Popen`. `SessionServer.start` samples current token PIDs immediately and `LanePlacement.apply([])` returns `place-refused:no-target-pids` (`scripts/cgroup-profiler/tests/test_serve_placement.py:1949`). There is no subsequent placement retry. |
| Origin scope last member × requested move | CP-19's candidate live probe refused `origin-scope-would-empty` with no leaf/PID move; anchored probe restored to the exact source unit. Another source process can exit between inventory and move; the design explicitly leaves that case non-atomic. |
| Missing gates slice × `--on-stall kill` | Contract §8.3 requires `bad-policy` before a session exists for unsafe kill. The current main daemon reports gates slice present; this negative host state was not live-probed on the repair tree. |
| Host contention × watch/admission/mutation | R-45's wait is scheduling only. The repaired container wait path cannot let a local silence timer outrun the daemon's pressure-aware watch. Exact-tree R2/full gate has not classified any candidate; budget or pressure interruption would be infrastructure/inconclusive. |

### Open blocker and follow-ups

**B3 — High, unresolved.** `run-gate-project/run-gate.py:11787,11904`
and `:12138,12224` show profiling starts before the exec/bare-host
token-bearing child;
`scripts/cgroup-profiler/lib/serve.py:1018-1045` resolves current PIDs before
placement, and `lib/placement.py:1207-1209` refuses an empty set. This means
the user-facing placement request can be emitted but never applied on these
two paths. The P5 handoff C6 calls for a blocked wrapper terminated before
the lane body on refusal; the current SPEC R-43l and RG-55/P5 backlog instead
say to warn and run unplaced. That contradiction needs a numbered controller
decision. Regardless of the refusal policy selected, the child must exist
and remain blocked until the daemon has either read back the exact leaf or
returned an explicit refusal; a Run-Gate-driven live probe must verify the
body's start/stop behavior and the placement/read-back/restore record. The
candidate-image direct `ctl start` probes do not close this gap. A fake
Docker argv cannot prove it.

**S1 — known CP-19 residual.** The origin-subtree inventory is an observation,
not a source-unit lease. If the remaining unrelated process exits between
inventory and transfer, restore can lose its exact unit. The implementation
preserves evidence and refuses a guessed parent; the design/CP-19 backlog
disclose this edge. No additional repair was attempted in this P5 review.

**S2 — separate release holds.** This branch integrates Assay and cgprofile
observer changes. The cgroup-profiler unit/coverage/canary receipts on
`465f67d0` and `db6d5b73`, the Run-Gate selftest receipt on `db6d5b73`, and
the candidate-image carrier/CP-19/DAMON probes are historical evidence for
those exact trees. They are not final-HEAD evidence after `34497084`. Assay's
own registered tester-unified gate, cgprofile final-source R2, Run-Gate
`assay-r2`/`gate-full`, and all final short gates remain to be run after B3 is
repaired and the judged tree is frozen. The older P1/P6 mutation verdicts
are outside this selected P5 review and cannot be copied into it.

### Repairs, documents, commands, and evidence limits

- Commit `34497084a1ef6d69bfe4902a31846dd86a9d2e32` repairs B1/B2/B4,
  bumps the copy-drift marker to rev 62, adds behavioral regression fixtures,
  synchronizes README, DESIGN-GUIDE, CONSUMERS, SPEC, LANE-AUTHORING and
  CHANGES, corrects LANE-AUTHORING's stale RG-72/P5 identity, and corrects
  the backlog's obsolete optional gates-parent wording. No cgprofile or
  Assay source was changed by the reviewer.
- In the cockpit, with memory PSI `full avg10=0.00` and
  `nice -n 19 ionice -c 3`, focused command
  `/home/vscode/.venv/bin/python -m pytest tests/test_run_gate.py -q -k
  'TestAwaitContainerProfilingWiring or TestExecLaneProfilingWiring or
  TestBareHostProfilingWiring or TestProfilerClient or
  test_unreleased_changelog_matches_revision_and_recovery_contract or
  test_user_facing_run_gate_config_examples_load_and_are_versioned or
  test_closed_result_and_runner_mode_vocabulary_is_documented or
  test_cross_document_anchors_resolve'` returned `PYTEST_EXIT=0`,
  **138 passed, 1,551 deselected** in 9.18 seconds on the repair code.
  The narrower killed/reported and healthy-watch fixture run returned six
  passes. These are bounded local checks, not the declared tester-unified
  gate or a changed-line coverage receipt.
- `git diff --check` exited 0. The two checked-in contract mirrors still
  have the identical SHA-256 stated above. `git ls-files --error-unmatch
  run-gate-project/run-gate.footprint.json` exited 0, proving the footprint
  manifest is tracked; its embedded source/revision is historical and it was
  not regenerated by this review.
- Live read-only daemon check after the repair commit: managed
  `cgprofile-host-daemon` is up (`cgprofile.slice`, host PID namespace,
  private cgroup namespace); `cgprofile ctl version --json` returned
  `ok=true`, contract 1, exec and listening socket transports.
  `cgprofile ctl status --json` returned `ok=true`, zero sessions, and
  `gates_slice.present=true`. The daemon is the restored main-managed
  singleton, not the candidate-image acceptance probe.
- No gate container was launched in this review session, so there is no
  exact-container CPU-cap receipt to claim. The P5 REPORT names older exact
  containers and 3-CPU caps for its earlier trees only. No registered
  `selftest`, `assay-r1`, `assay-r3`, cgroup-profiler unit/coverage/canary,
  Nyxloom lint, or `doctor` ran on this repair commit. Changed-line coverage
  and mutation candidate accounting on this tree are **unknown**, not 100%
  or PASS. One `budget_exceeded`, crashed, or unjudged candidate would keep a
  later R2 result incomplete. No final live Run-Gate placement probe ran.

The review artifact itself is the only remaining untracked path after the
code commit; it is committed separately below. The controller must not merge
or release from this REJECT. The code and review record are confined to the
selected isolated worktree.
