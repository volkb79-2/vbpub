# RG-55 wave — controller handoff (session ended 2026-09-12 ~22:00Z)

Written by the controller session `session_01YBJzBA7KyG4ayu5ndNf9Hx` on the
operator's instruction: "let all agents reach a semantic boundary and
checkpoint to files for handoff to a new session; do not start new work;
long-running test runs may continue". This file is the entry point for the
NEXT controller session. Read, in order: memory
`rg55-lane-profiling-program.md` → this file → the controller log
`run-gate-WAVE-RG55-CONTROLLER-LOG.md` (rulings RW-52..RW-57 are this
session's last; the dispatch table's `status` column is per-track truth) →
the per-track BRIEF files named below. Plan of record, design, contract,
handoffs: see the log's header.

## 1. Operator asks still open (nobody answered)

1. May the controller discard the one-line uncommitted edit in the shared
   checkout's `scripts/cgroup-profiler/cgprofile.py` (`HERE`→`DEFAULT_OUT`;
   the P1 branch carries the same change) right before the P1 merge? git
   refuses the merge while the file is dirty. Until answered: P1 and P6
   cannot merge.
2. Park the nyxloom WIP in the shared checkout (27 M + 47 untracked, not on
   any branch; `nyxloom/core.1330418` core dump to delete) on a branch
   before another agent works on nyxloom. Commands were given in-session.
3. Host: install the merged mdt host-setup (P8, `a71c46b0`) BEFORE any
   devcontainer rebuild (`/run/cgprofile` bind mount refuses a missing
   source). Sequence in `run-gate-WAVE-RG55-P8-REPORT.md`.
4. dstdns `.devcontainer/devcontainer.json` was edited on request
   (uncommitted, the operator's to commit): `/run/cgprofile` mount +
   `CGROUP_PARENT_DEV_GATES`.

## 2. Processes that may still be running (they outlive this session)

Both mutation-lane processes are reparented to init in their own process
groups (verified `ppid=1`), so they survive the session. Their logs are in
this session's scratchpad
`/tmp/claude-1003/-workspaces-vbpub/5d55184a-d2df-482e-aa2b-541cae13c0ad/scratchpad/`
(survives until the host reboots; copy what you need). Check with
`docker ps --format '{{.Names}}\t{{.Status}}'` and `pgrep -af run-gate.py`.

| run | tree | pid | container | started | expected end | on death |
|---|---|---|---|---|---|---|
| P1 full r2 (208 candidates) | `.worktrees/rg55-profiler-daemon` @ `637b8c09` | 3677631 | `run-gate-vbpub-r2-3677631-1789244480` | 20:20Z | ~01:30Z 09-13 | relaunch `./run-gate.py r2` from `scripts/cgroup-profiler/` in that worktree on the UNCHANGED tree — assay resumes the judged records (identity is per tree, RW-41); if P1 committed a BRIEF after this (HEAD moved), detach at `637b8c09` for the relaunch |
| P6 r2 | `.worktrees/rg55-followups-cgprofile` @ `d4f51bbc` | 4136306 | `run-gate-vbpub-r2-4136306-1789246893` | 21:01Z | ~04:00Z 09-13 | same rule, tree `d4f51bbc` |
| P7 gate `tester-unified` | `.worktrees/assay-liveness` @ `6f3aefad` | 4180329 | anonymous (`vibrant_gagarin`, `--cpus=2`) | 21:08Z | FINISHED 21:36Z, `exit 0` | recorded in `assay/.run-gate/history.json` (`latest` = `6f3aefad`, pass) and RW-57 |

Verdict/progress files: `<worktree>/<project>/.assay/{verdict,progress}-r2.json[l]`
(P2: `.run-gate/…` state under the run-gate-project dir, 281 records).

BRIEF files written at the wind-down (each ends with a retention prompt;
each BRIEF commit moved its branch HEAD past the judged tree — for a resume,
`git switch --detach <judged tree>` in that worktree, relaunch, switch back):

| track | brief | branch tip after the brief | judged tree |
|---|---|---|---|
| P1 | `scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P1-DAEMON-BRIEF-7.md` | `a9bfc748` | `637b8c09` |
| P6 | `scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P6-FOLLOWUPS-BRIEF-10.md` (P6 counted 484 r2 candidates → ~15.7 h against the lane's 4 h budget: the first attempt ends `BUDGET_EXCEEDED` by design, resume until judged; its `-REVIEW-HANDOFF.md` is on `main`, not on the branch) | `99ec0572` | `d4f51bbc` |
| P4 | `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P4-BRIEF-5.md` | `2ca41a02` | (no run pending) |
| P2 | `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P2-BRIEF-<latest>.md` (see the branch) | see the branch | `186461de` |
| P7 | no brief — `-LOG.md`/`-REPORT.md` session 10 sections + the reviewer's round-2 file are the state | `6f3aefad` | `6f3aefad` (gate) |

Estate rules in force: memory PSI is the launch gate (`full avg10` > 5 →
launch nothing); ≤ 2 CONTAINER mutation lanes estate-wide, `docker update
--cpus=3` after launch; bare `nice -n 19 ionice -c 3` runs are PSI-gated
only (RW-52); never `docker rm` by filter (memory
`docker-remove-by-exact-name-only`); TaskStop a superseded subagent when
its successor is dispatched (memory `subagent-watchers-reinvoke-closed-sessions`).

## 3. Track state and the resume sequence

Agent ids below are this session's subagents: their transcripts persist
(`~/.claude/projects/-workspaces-vbpub/5d55184a-d2df-482e-aa2b-541cae13c0ad/subagents/agent-<id>.jsonl`)
but a new session cannot message them — dispatch FRESH successors seeded
with the BRIEF files (dispatch skill; Sonnet unless noted; checkpoint clause
on; reviewers FRESH Opus xhigh, never a fork, 3-round cap).

Order of work (each step's gate before the next; nothing else changes):

1. **P2 — run-gate 23.7.0 (critical path).** Branch `rg55-run-gate-client`
   (`647a2cc6` + close-out), worktree `.worktrees/rg55-run-gate-client`;
   review round 2 ACCEPT on `186461de`. Mutation evidence: run 2 judged
   281/283 (263 killed, 18 survived, 2 `budget_exceeded` PLACEHOLDERS never
   executed → lane outcome BUDGET_EXCEEDED under assay 6.1.1). The final
   2-candidate `--resume` pass failed twice at the R0 baseline (21:05Z and
   21:24Z). Root cause of the first (RW-56): order-dependent test defect —
   `tests/test_run_gate.py:4036 test_unusable_lock_path_is_infra_failure_not_traceback`
   plants a directory at the pid-scoped `/tmp/run-gate-exec-…` lock path
   without cleanup; pytest-randomly makes the three sibling
   `TestExecModeMutex` tests fail on a coin flip (RG-62 family; fixed on
   P4's branch by RW-46a). The second pass ran with
   `PYTEST_ADDOPTS="-p no:randomly"` and still failed, while the same
   baseline BY HAND with that variable is fully green (1087 passed) —
   so assay 6.1.1 evidently does not propagate it (confirm in the pyz's
   runner). RULE (RW-58): relaunch the pass on the tree detached at
   `186461de` up to three more times (≈6 min each, records intact); first
   PASS is the evidence; if all fail in the baseline, accept run 2 with a
   disclosure (281/283 judged, both placeholders named, RG-62/RW-56 cause)
   in the REPORT and CHANGES and release. Then: switch to
   the branch, survivor triage in the REPORT (18 survivors; candidate 105
   was SIGKILLed by the controller, RW-50 — state its classification;
   the `-p no:randomly` disclosure), final gates (`selftest`, `assay-r1`,
   `assay-r3`, `doctor`), merge --no-ff, `cmru release --project
   run-gate-project --set-version 23.7.0` (release gate = `selftest`,
   fast), `pip install --upgrade` into `/home/vscode/.venv`, verify
   `run-gate --help` rev 41, clear CHANGES `[Unreleased]`. Reviewer
   `a2e8002c5d05cedc3` gave ACCEPT; no round 3 needed unless the triage
   changes code.
2. **P7 — assay 6.2.0 (B091 liveness).** Branch `assay-liveness`, tip
   `6f3aefad`, worktree `.worktrees/assay-liveness`. Round 1 REJECT (B1–B5,
   RW-49) repaired: B1 `802f0855`, B2 `07e121d9`, B3 `5c1b9ef8`, B4+B5
   `4ef3985f`, S-items `ef247935`. Gate on `6f3aefad`: GREEN `exit 0`
   (1691.6 s). Review round 2 (`run-gate-WAVE-RG55-P7-REVIEW-round2.md`,
   committed `792fc1ba`): REJECT on ONE new blocker B6 — B1–B5 verified.
   B6 = false `hung` when the events file is written by several pytest
   processes (xdist): the `session_finish` disjunct at `liveness.py:1214-1217`
   arms the 30 s grace at the FIRST `session_finish` without consulting
   CPU. Rulings RW-57: merge-blocking; repair = the idle conjunct
   (`and idle_for >= _HUNG_SESSION_FINISH_GRACE_S`) + an xdist-shape
   regression test; pid-stamped events/per-pid parsing = a filed follow-up
   row; D3's trailing gap as implemented is accepted with that guard; the
   repair commit files the backlog rows for S1/S3/S5/S6, pid-stamping and
   N3 (`crashed` in `mutation_pct`). Next session: FRESH Opus repair
   successor seeded with the round-2 file + P7's LOG/REPORT session-10
   sections; ONE gate run on a quiet tip (no commits during it); round 3
   (the last) by a FRESH reviewer seeded with rounds 1–2. On ACCEPT: merge
   --no-ff, `cmru release --project assay --set-version 6.2.0` (the release
   mutation gate judges the whole since-last-release diff — long; PSI/slot
   rules apply), then drop `.assay-inbox/release.json` for dstdns (sha256;
   include the schema-11 disclosure sentence from CHANGES).
3. **P4 — run-gate 23.8.0 (RG-57..61).** Branch `rg55-followups-run-gate`,
   tip `1f8d9ca3` (B5 + S6–S11; selftest/r1/r3 GREEN; its 21:14Z r2 was
   terminated, RW-54/55). BRIEF: `run-gate-WAVE-RG55-P4-BRIEF-<latest>.md`.
   After 23.7.0 is on main: merge `main` --no-ff (expect conflicts on
   version/revision lines + CHANGES; P4 is rev 42 / 23.8.0), selftest +
   r1 + r3 on the merge tip, bare r2 on it (HEAD quiet), survivor triage,
   round 3 with a FRESH reviewer seeded with
   `run-gate-WAVE-RG55-P4-REVIEW-round{1,2}.md` (round 2 was
   ACCEPT-conditional on B5 only), merge, release 23.8.0, install, verify
   rev 42. RG-62 is P4's flaky-test row; P5's row is RG-63.
4. **P1 — cgprofile 1.0.0 (daemon).** Branch `rg55-profiler-daemon`, tip
   `637b8c09`; full r2 running (§2). BRIEF: `scripts/cgroup-profiler/
   nyxloom-trove/reports/cgprofile-P1-BRIEF-<latest>.md`. After the r2:
   survivor triage, r0-r1 (bare) + r3, FRESH Opus reviewer with
   `cgprofile-P1-DAEMON-REVIEW-HANDOFF.md` ("daemon down; you may `ciu up`
   it for probes and must `ciu down` after"), merge (blocked on ask §1.1),
   `cmru release --project cgroup-profiler --set-version 1.0.0`, `ciu up`
   the daemon from main.
5. **P6 — cgprofile 1.1.0 (CP-2..CP-12, carriers, placement).** Branch
   `rg55-followups-cgprofile`, tip `d4f51bbc` (contains P1 `637b8c09`); r2
   running (§2). BRIEF: `cgprofile-P6-BRIEF-<latest>.md`. After the r2:
   triage, r0-r1/r3, FRESH Opus reviewer with
   `cgprofile-P6-FOLLOWUPS-REVIEW-HANDOFF.md` (base = P1 tip), merge (after
   P1), release 1.1.0, `ciu up`.
6. **P5** — dispatch only after P4 + P6 are merged; handoff
   `run-gate-WAVE-RG55-P5-HANDOFF.md` (fix RG-62→RG-63 in its text first);
   release 23.9.0.
7. **P3 close-out** — live probes with the REAL daemon (both carriers),
   measured DAMON overhead, `run-gate-WAVE-RG55-REPORT.md`, fill the
   adoption brief's ⟨P3⟩ fields (NOT dispatched to dstdns), RG-55 FIXED /
   RG-56 / RG-57 verified, memory + MEMORY.md final, tear down the
   `rg55-*` and `assay-liveness` worktrees, final report of versions
   installed + anything left out.

SHIPPED before this handoff: P8 (mdt dev-gates.slice etc., `a71c46b0`);
contract v1.1 §8 + §4.3a; design D-17..D-30; run-gate 23.6.1 / cgprofile
0.x / assay 6.1.1 are the INSTALLED versions (nothing from this wave is
released yet).

## 4. Retention prompt for the next controller session (paste at start)

```
Role: controller of the RG-55 wave (vbpub). Read memory
rg55-lane-profiling-program.md, then
run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-CONTROLLER-HANDOFF-2026-09-12.md
(entry point: open operator asks, running processes, resume order), then the
controller log's RW-52..RW-57 and dispatch table. Do not touch
/workspaces/dstdns. Limit controller calls; long, decided instructions to
agents; Sonnet implementers seeded with the BRIEF files, Opus where design
judgment; FRESH Opus xhigh reviewers, never a fork, 3-round cap; merge
--no-ff + cmru release under the standing vbpub authorization; "shipped" =
merged + released + installed / daemon up. Memory PSI is the launch gate;
≤ 2 container mutation lanes; never docker rm by filter; TaskStop superseded
subagents; never `cd` in bash (use git -C / absolute paths); commit with
`git -C /workspaces/vbpub commit -F <msg> --only -- <paths>`; trailers
Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com> + Claude-Session.
Ask the operator only product questions; never stop on silence.
```
