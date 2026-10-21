# RG-SMALL report (2026-10-07)

> **Integration note (INT-02, 2026-10-21).** Historical report, taken from tree `rg-small` (branch `rg-small`, `7a5c4461e`). Read against current main:
> - Section 3 (RG-62) is current; the test fix and the RG-62 backlog row/status were integrated (`44434c6aa` plus the RG-62 hunks of `72c3a0a58`).
> - Section 2 (RG-85): main already carries RG-85 as FIXED via a different change (RG-55 candidate); `72c3a0a58`'s RG-85 code and tests were deliberately not re-applied.
> - Section 4 (RG-88 / assay B148): superseded. On main, RG-88 is a different defect (nullable DAMON `hot_bytes`) and assay B148 ("native R2 discovers a private cgroup namespace only after R0") already exists, so the filings described here were not made. The cgroupns analysis stands as evidence for B148.
> - Section 1 (status reconciliation of RG-45/81/84/86) was not integrated; those rows on main were not re-verified here.
> - Section 5 (RG-46 decision ask A/B/C) is still an open operator decision.
> - Commit hashes below refer to the `rg-small` branch, not to main.

Worktree `rg-small`, base `main` at `4670f53a6`. Commits: `44434c6aa` (RG-62 tests), `72c3a0a58` (RG-85, backlog, assay B148), plus this report. `__revision__` NOT bumped (it is 55): the CHANGES test `test_unreleased_changelog_matches_revision_and_recovery_contract` pins `source rev {__revision__}` to the released 23.10.0 section, and RG-86 set the precedent that the release owner bumps. Release-note text for the controller is at the end.

## 1. Status reconciliation (every index row and body checked)

| Row | Was | Now | Evidence |
|---|---|---|---|
| RG-81 | IN PROGRESS (rev 54, package gate pending) | FIXED 2026-10-07 (rev 54, run-gate 23.10.0) | `CHANGES.md` 23.10.0 "Source-backed Assay identity and recovery (RG-81, rev 54 ...)"; release gate ran registered `selftest`; tag `run-gate-v23.10.0` exists. Row and body both changed. |
| RG-84 | IN PROGRESS (rev 55, selftest pending) | FIXED 2026-10-07 (rev 55, 23.10.0) | CHANGES 23.10.0 "Init and cgroup resource-event guard (RG-84 ... rev 55)"; `__revision__ = 55`; live acceptance report plus release-gate selftest. Row and body changed. |
| RG-86 | IN PROGRESS (awaiting review) | FIXED 2026-10-07 (23.10.0) | CHANGES 23.10.0 "Changed" entry; `run-gate-RG-NARROW-2026-10-REPORT.md` rounds 1-4, round-4 gate PASS 100% diff coverage. Row and body changed. |
| RG-45 | OPEN 2026-09-08 | CLOSED 2026-09-30 | Body already said "CLOSED ... resolved in Assay B078 ... released assay-v7.1.1"; only the row was stale. |
| RG-85 | OPEN | FIXED (this package) | section 2 |
| RG-62 | OPEN | FIXED (this package) | section 3 |
| RG-88 | (new) | OPEN | section 4 |

Rows checked and left unchanged because row and body agree and the code confirms: RG-18 (dstdns-side), RG-37 (no `ciu.resolved.toml` lookup in `run-gate.py`), RG-42 (`ciu8/run-gate.toml` still absent, the estate sweep skips ciu8), RG-46 (section 5), RG-56 (body OPEN), RG-73 and RG-75 (no implementation), RG-82 (carve in flight), RG-83 (FIXED by RG-87, body says so), RG-54/65/70 (closed/merged/absorbed). Every other row is FIXED and no body status line says otherwise (the only bodies with an explicit non-FIXED status were 56, 81, 82, 84, 85, 86). Many old FIXED entries have no body status line at all; that was not changed.

## 2. RG-85 (FIXED)

Observed/analysis: `run_container_lane` already `mkdir`s `<repo>/.run-gate` (the main checkout's durable state mount, RG-49), but `assay.toml` points TMPDIR and `GIT_CEILING_DIRECTORIES` at `/worktree/.run-gate`, i.e. the judged worktree's own `.run-gate`, which no code created. Fix: `ensure_worktree_scratch_root(worktree)` in `run-gate.py`, called in the dispatch for every non-dry-run `kind = "assay"` lane after the `config:` line and before admission (so container, exec and bare-host runners and sequence members are all covered). Owner: invoking user. Mode: default umask (0777 & ~umask, normally 0755); an existing directory is not touched; `OSError` (for example a file squatting there) is an infrastructure error exit 2 naming the path.

Tests (class `TestComparisonBasePassthrough`): `test_rg85_assay_lane_creates_the_scratch_root_before_launch` (absent before, present at the moment `run_container_lane` is entered, owner uid, owner rwx), `test_rg85_dry_run_never_creates_it`, `test_rg85_unusable_scratch_path_is_an_infrastructure_error`.
Plants (run, then reverted): call replaced by `pass` -> `test_rg85_assay_lane_creates...` and `..._unusable_scratch_path...` FAILED (2 failed, 1 passed); `and not args.dry_run` dropped -> `test_rg85_dry_run_never_creates_it` FAILED (1 failed, 2 passed). Restored: 3 passed.
Not done: the issue's literal oracle (fresh `git worktree add` then `assay-r1 --base main` reaches PASS) was not run; it needs the tester-unified assay lane and hours of runtime. The pre-launch ordering is proven at unit level only.

## 3. RG-62 (FIXED, both flakes reproduced and made deterministic)

The two flakes are named in `run-gate-WAVE-RG55-P4-LOG.md` (session 5); the backlog body did not name them.
1. `TestEstateBudgetTimeoutPairing::test_estate_pairing_sweep_is_alive`: order-dependent through the class-level `PAIRINGS_SEEN` list. Reproduced before the fix, deterministically: with `-p no:randomly`, aggregate first -> silent SKIP ("no troves scanned", a hollow guard); `[ciu]` then aggregate -> FAILED `estate-wide pairing collapsed to [('ciu', 'tester-unified')]`. Fix: pure `_pair(trove)` helper; the aggregate scans every trove itself; no shared state.
2. `TestHistoryEligibilityGuard::test_tree_state_is_sampled_before_the_lane_not_after`: `assert 4.001 == 4.0`, wall-clock delta between the `_started_monotonic` stamp and the finish call. Fix: the test freezes the `time` seen by `run_gate` (`monotonic` constant, everything else delegates to the real module). No sleep, retry or tolerance.
Evidence (serial, `nice -n 19 ionice -c 3`, scratch venv with pytest 9.1.1 + pytest-randomly 5.0.0): pairing class 20/20 pass; the history test 20/20 pass; reversed orders (aggregate first; aggregate after one sibling; history test before the aggregate) pass; 20 `--randomly-seed` runs of both together 20/20 pass. Plant: a clock 1 ms off reproduces `assert 4.001 == 4.0` (reverted). I did not reproduce flake 2 under real host load before the fix; it is explained by the arithmetic and by the planted 1 ms.
Caveat: other tests build `_started_monotonic = time.monotonic() - N` (several places); not audited for the same hazard.

## 4. New item RG-88 / assay B148 (filed; no run-gate code change)

Cause, verified in code and by one probe: the error is assay's. `assay/src/assay/mutation.py` `_read_candidate_resource_counters()` (first call at `mutation.py:3061`, the start of the mutation sweep, hence after R0/R1 work) -> `resource_limits.read_current_cgroup_counters()` raises at `resource_limits.py:512` when `/proc/self/cgroup` is `0::/`. That is the intended B145 fail-closed rule. The process hitting it is the LANE container: run-gate's ephemeral `docker run` passes no `--cgroupns`, and `docker run --rm tester-unified:local cat /proc/self/cgroup` prints `0::/` on this host (cgroup v2, systemd driver). So any native-R2 assay lane launched via an ephemeral environment fails here, regardless of where run-gate runs; `cmru tester-gate` containers see the hierarchy, which is why release gates pass.
Not a small run-gate change: run-gate cannot know the lane container's cgroupns before launching (would need a probe container or setting the flag itself, which is an operator decision and forbidden for me). Filed: assay `B148` in `assay/nyxloom-trove/4-backlog.md` (front-matter id, summary list, body; early pre-R0 named infrastructure verdict with remedy; oracles) and run-gate `RG-88` (OPEN, points to B148, workaround: judge via `cmru tester-gate`, classify the message as infrastructure-inconclusive). B148 is the next free number across all worktrees (max B147 everywhere).

## 5. RG-46 (left OPEN: needs a design decision)

Its own text says why: not a mechanical addition. Arming `ProgressWatch`/`LogStreamWatch` in `follow_container` is easy to write, but on a detected stall the follower would have to act on a container it does not own (RW-14 forbids a follower `terminate()`/`rm -f`), and the `LogStreamWatch.join()` docstring records a caller precondition a follower cannot satisfy by copying `await_container`. Decision ask for the operator/controller:
- A: follower only DISCLOSES (WARNING, evidence path, "owner appears stalled, run `--fresh` to take over"), never acts. Smallest, self-contained; no authority change.
- B: follower acts after the same owner-liveness re-check `promote_follower` uses: if the owner is dead, promote and apply the stall policy; if alive, disclose only.
- C: leave as is; document that a follower relies on its owner's `stall_timeout`.
Recommendation: A now (tests: follower on a silent fake container discloses within the configured silence, never calls `rm`/`kill`), B only if the operator wants stall takeover. I did not implement any of them.

## 6. Gate

Registered selftest (`cmru.toml` `[steps.run-tests]`: `cmru tester-gate --cwd . -- ./run-gate.py selftest`, `CMRU_TESTER_*` from `cmru.orchestration.toml`), from `run-gate-project/`, tree clean at `72c3a0a58`, under `flock gate.lock nice -n 19 ionice -c 3`, exit 0, log `rgsmall-selftest.log` in the scratchpad. A direct `./run-gate.py selftest` from the devcontainer cannot run (no `/opt/tester-venv`), hence tester-gate, as the release and the RG-NARROW report did. Verdict lines read in a separate step: `1702 passed, 2 skipped, 1 warning`; `diff-coverage OK: 9/9 changed executable lines covered (100.0%); branches 2/2 taken`; `lane 'selftest' verdict PASS; exit_code 0`. (The docker-identity profiling WARNING is the known no-socket-in-tester-unified notice.) The backlog and assay-backlog edits were committed before that run; this report was added after. Mutation (R2) not run. No independent review done; that is the controller's.

## Release-note text (CHANGES `[Unreleased]` must stay comment-only)
- fix(run-gate): RG-85 assay lanes get `<judged worktree>/.run-gate` created before launch (exit 2 with the path if it cannot be created).
- test(run-gate): RG-62 the pairing rot-guard no longer depends on sibling test order; the history duration test freezes its clock.
- docs: backlog status reconciliation (RG-45, RG-81, RG-84, RG-86); RG-88 filed, assay B148 filed.
