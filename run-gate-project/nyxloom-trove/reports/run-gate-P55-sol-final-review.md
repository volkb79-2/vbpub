---
schema_version: 1
id: run-gate-P55-sol-final-review
project: run-gate
component: release-review
title: "RG-55 final adversarial review and repair packet"
tier: frontier-review
input_revision: "3ccb752800d5a0c8de7d756b86882e3f1edc64fe"
depends_on: []
session: fresh
source:
  kind: review
  ref: run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-CONTROLLER-LOG.md#RW-551
scope:
  touch:
    - run-gate-project/run-gate.py
    - run-gate-project/tests/
    - run-gate-project/README.md
    - run-gate-project/docs/DESIGN-GUIDE.md
    - run-gate-project/CONSUMERS.md
    - run-gate-project/LANE-AUTHORING.md
    - run-gate-project/SPEC.md
    - run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md
    - run-gate-project/CHANGES.md
    - run-gate-project/nyxloom-trove/DESIGN-2026-09-12-liveness-placement-admission.md
    - run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md
    - scripts/cgroup-profiler/
    - cmru/
    - run-gate-project/nyxloom-trove/reports/
  forbid:
    - /workspaces/dstdns
    - .devcontainer/
    - modern-debian-tools-python-debug/
    - nyxloom/
    - TODO.md
    - scripts/netcup/
    - topos/
    - ciu/docs/CIU-V8-*ROUND4*
oracles:
  - id: O1
    observable: "Each selected candidate has a blind diff review, live probes, a written verdict, and no unclassified blocker."
    negative: "A crash, refusal, race, wrong namespace, false green, or missing proof is accepted as harmless or as a mere test detail."
    gate: selftest
  - id: O2
    observable: "Changed executable lines and branches meet the package's declared 100% coverage requirement in the real gate."
    negative: "A hollow, order-dependent, timing-dependent, or implementation-detail test makes a missing branch appear covered."
    gate: assay-r1
  - id: O3
    observable: "A complete mutation verdict accounts for every candidate, with survivors killed by behavioral oracles or proved equivalent."
    negative: "budget_exceeded, crashed, stale-tree, partial, or wrapper-success evidence is called a passing mutation result."
    gate: assay-r2
  - id: O4
    observable: "The independent canary and live acceptance probes exercise the shipped interface on the exact reviewed tree."
    negative: "A fake Docker argv, cockpit-only test, or daemon-absent result is presented as live acceptance."
    gate: assay-r3
  - id: O5
    observable: "The final package and release path preserve R-36h, D-15 safety, namespace provenance, and the contention-agnostic verdict contract."
    negative: "Host load, scheduler contention, a timeout, or a missing carrier changes a functional verdict without an explicit infrastructure/inconclusive state."
    gate: gate-full
gates: [selftest, assay-r1, assay-r2, assay-r3, gate-full]
escalate_if:
  - "the caller has not selected exactly one supported REVIEW_TARGET"
  - "a named RG-55 contract, settled decision, or package gate cannot be met as specified"
  - "scope requires a forbidden file or an unapproved external checkout"
  - "a live gate cannot produce complete evidence within its declared safety budget"
review_focus:
  - "host contention is planned and allowed; it must be irrelevant to functional verdicts, not eliminated"
  - "defaults and absence must fail closed rather than silently inventing facts"
  - "every status must distinguish healthy, absent, malformed, inaccessible, partial, and refused states"
  - "assay identity is per judged TREE; any commit invalidates mutation records for that tree"
---

# Manual Sol xhigh prompt

## Invocation contract

The operator may hand this packet to the reviewer by path instead of pasting
its body. The first message to the fresh Sol xhigh session must name exactly
one target and the absolute packet path inside that target's selected
worktree. The P5 example below is current for this candidate; for another
target, substitute both the target and that worktree's packet path:

```
REVIEW_TARGET=P5
Read /workspaces/vbpub/.worktrees/rg55-p5-transport-port-20261010/run-gate-project/nyxloom-trove/reports/run-gate-P55-sol-final-review.md in full before acting.
Follow that packet as the review contract. Review exactly the selected target, make scoped repairs when needed, and return the required verdict and artifact. Do not merge or release.
```

`REVIEW_TARGET=P5` is an explicit task parameter, not a shell variable that
the reviewer is expected to discover. If the line is absent or names anything
outside `P1`, `P4`, `P5`, `P6`, or `CMRU`, return `BLOCKED` before repository work.

Before assigning this packet, the caller selects the actual GPT-6-Sol route at
xhigh and retains the invocation/session metadata as route evidence. Put one
literal target line at the top of the prompt. For P5, use:

```
REVIEW_TARGET=P5
```

For the other release-blocking packages, replace `P5` with exactly `P1`, `P4`,
`P6`, or `CMRU`. Do not leave the variable unset, and do not use a shell-style
placeholder such as `$REVIEW_TARGET`; the reviewer must see the selected
target in its input. `RG56` is an optional related review of future admission
work and is not a release approval for RG-55.

The caller owns route selection and verification: launch this task on the Sol
xhigh route and retain the invocation/session metadata in the controller's
records. The reviewer must not be asked to establish or self-attest its own
model or effort. Missing route metadata available to the reviewer is not a
reason to stop the technical review or return `BLOCKED`.

Copy the remainder of this document into a **genuine fresh Sol xhigh** session,
once per review target. Do not reuse one Sol session as the “fresh” reviewer
for two packages. The controller will merge or release only after a real Sol
ACCEPT for that package and all post-review gates have passed.

## Role and authority

You are the final independent adversarial reviewer and, when necessary, the
repair implementer for `REVIEW_TARGET`. The caller is responsible for
launching this fresh session on the requested GPT-6-Sol xhigh route and
preserving authoritative invocation/session metadata. Do not attempt to verify
or attest your own model or reasoning effort: your self-report is not route
evidence, and lack of runtime introspection is not a reason to return
`BLOCKED`. The caller must resolve a known invocation mismatch before
assigning repository work; if the caller explicitly tells you the route was
wrong, stop before repository work and report that mismatch.

The operator explicitly authorizes you to make fixes and improvements yourself
within the scope below. You may edit production code, tests, user-facing docs,
reports, changelogs, and the named backlog rows when that is necessary to make
the behavior correct and evidenced. You may commit those changes in the
isolated target worktree. Preserve the review's independence: first capture the
initial tree, read the diff blind, and record every blocker before editing it;
then verify your own repair in the same live session. If your session ends,
the next reviewer must be fresh and must receive your prior round files.

You do **not** have authority to merge, tag, publish, push, install a release,
start the final main daemon, or alter the operator's unrelated dirty files.
Return the exact commits and a verdict to the controller. A reviewer ACCEPT is
not a merge/release command.

## Non-negotiable repository and host rules

Work only in the selected isolated worktree. Never touch
`/workspaces/dstdns`, even though the dstdns adoption brief is part of the
eventual close-out. Preserve these operator-owned paths exactly:

```
.devcontainer/*
modern-debian-tools-python-debug/*
nyxloom/*
TODO.md
scripts/netcup/*
topos/*
ciu/docs/CIU-V8-*ROUND4*
```

The shared main checkout was clean when this packet was refreshed, but its
status is live and may change. Always inspect it before any merge and preserve
all unrelated or operator-owned changes; never stage, reset, or clean them.
Do not use a broad Docker removal or prune command. Never use
`--cgroupns=host` or `--net=host`. Host PID namespace is prohibited for
workload, gate, helper, and test containers. The sole explicit exception is
the CIU-managed cgprofile host daemon's `pid: host`, authorized by RW-467 so
DAMON sysfs `pid_target` resolves host PIDs; deploy that daemon only through
its declared CIU singleton workflow, never an ad-hoc Docker command.

Host contention is an allowed operating condition, not a defect to eliminate.
The requirement is that functional outcomes and mutation classifications are
deterministic and contention-agnostic. A pressure-affected, incomplete, or
budget-truncated run is infrastructure/inconclusive evidence and must be
retried or reported as such; it is neither a product PASS nor a product FAIL.
Budgets are safety/resume controls, never correctness oracles.

Before launching any container or mutation lane, read
`/proc/pressure/memory`; do not launch while memory `full avg10 > 5`. RW-319
permits up to **three** mutation lanes estate-wide under the verified
five-CPU `dev-gates.slice` quota. This is a bounded concurrent allowance, not
a promise of non-contended CPU: scheduler interference is expected and must
not affect functional results or mutation classifications. Each lane needs a
unique exact container name, the declared and verified gates-slice parent,
and immediate verified `docker update --cpus=3 <exact-container>`. Bare pytest
may run beside mutation lanes only when PSI-gated and with
`nice -n 19 ionice -c 3`. Detached jobs must append an explicit job exit marker
and the marker must be read separately from the wrapper's status. Do not poll
a long gate every minute; leave it detached with a watcher and inspect it at a
meaningful completion or wakeup point. Budgets are safety/resume controls;
an interrupted, timed-out, pressure-affected, or unjudged candidate is
incomplete infrastructure evidence, never a product PASS/FAIL.

Assay identity is per TREE. A commit in a judged worktree voids all
mutation records for that worktree. Keep the HEAD quiet while judging. Resume
only from the exact judged tree with `git switch --detach <tree>`, then switch
back. Every assay mutation invocation must use `--resume --progress` as the
project's recipe requires. One `budget_exceeded` candidate means the lane is
not complete; do not manufacture a result by copying or renaming records.

## Read these files before making a judgment

Read the controller materials in this order:

1. `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-CONTROLLER-HANDOFF-2026-09-12.md`.
2. `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-CONTROLLER-LOG.md`,
   through the latest ruling (RW-551 at this checkpoint; refresh from the
   selected tree before review). The log, current git state, assay JSON,
   process state, and Docker state outrank prose in an old brief.
3. `run-gate-project/nyxloom-trove/WAVE-PLAN-2026-09-12-rg55-profiling.md`,
   all settled D-1..D-16 decisions.
4. `run-gate-project/nyxloom-trove/DESIGN-2026-09-12-liveness-placement-admission.md`,
   all settled D-17..D-30 decisions.
5. `run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md` §§1–8,
   especially §4.3a and §8; verify its bytes against
   `scripts/cgroup-profiler/docs/RG55-INTERFACE-CONTRACT.md`.
6. The selected target's handoff, brief, LOG, REPORT, prior review rounds, and
   current branch diff. Also read `run-gate-project/SPEC.md`,
   `run-gate-project/CONSUMERS.md`, `run-gate-project/LANE-AUTHORING.md`,
   the relevant package `README`, `docs/DESIGN-GUIDE.md`, and
   `docs/CONSUMERS.md`. For P5 also read
   `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P5-REPORT.md`,
   `scripts/cgroup-profiler/nyxloom-trove/nyxloom.toml`, and
   `scripts/cgroup-profiler/tests/test_serve_placement.py`.

The settled decisions are not product questions to reopen. Private helper
names and equivalent decomposition remain free; public names, serialized
shapes, refusal meanings, provenance, bounds, and carrier behavior do not.

## Target packets and current state

The P5 state below was refreshed for this candidate. The P1/P4/P6/CMRU
sections that follow are planning snapshots, not current branch, gate, or
process truth; refresh them before selecting any of those targets.

### `P5`: run-gate v1.1 client, revision 61

Worktree: `.worktrees/rg55-p5-transport-port-20261010`, branch
`rg55-p5-transport-port-20261010`; the controller supplies the exact final
HEAD when dispatching review. Read:

```
run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P5-HANDOFF.md
run-gate-project/nyxloom-trove/reports/run-gate-P55-sol-final-review.md
run-gate-project/run-gate.py
run-gate-project/tests/test_run_gate.py
run-gate-project/README.md
run-gate-project/SPEC.md
run-gate-project/CONSUMERS.md
run-gate-project/LANE-AUTHORING.md
run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md
run-gate-project/CHANGES.md
```

This target implements the RG-55/P5 v1.1 socket/exec transport seam,
one-reader daemon watch with ended/failed retry and fallback, policy authoring,
gates-slice parent and placement, R-45's bounded observational headroom wait,
schema-2 nullable watch/placement history, and synchronized adopter-facing
documentation. It does not implement RG-56's atomic registry reservation;
RG-56 remains OPEN under RW-283. RG-72 is a separate, already-fixed failed-
Assay-evidence issue and is not P5's backlog identifier.

The current candidate is an **integrated diff, not a P5-only branch**. Its
code checkpoint contains 67 changed paths across Run-Gate, Assay, and
cgroup-profiler relative to main
`efaf112945233e3a8f6d53f2fef136439d18d089`; merge commit
`83c7d4a21a30ba1180a16c50e3092c326a91e4f7` brings in the RG-89 observer
candidate, and `ae0ff0979` restores the P5 branch on that integration. Review
the complete diff that a provisional merge would land, including the
Assay/cgroup-profiler observer path; do not certify those package changes from
P5 gates alone. Record any distinct package/release holds separately. The
controller's exact gate, host-probe, and unresolved-evidence record is
`run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P5-REPORT.md`.
The integrated cgroup-profiler helper refactor has a prior fresh Sol ACCEPT
for provisional integration under RW-437, not for release; its final-source
R2, the DAMON-start fix validation, and live DAMON evidence remain open.

The current P3 delta adds CP-19 origin-unit lifetime protection: inventory the
full source cgroup subtree before initial and later moves; refuse a known
last-member move as `place-refused:origin-scope-would-empty`; refuse unknown
membership as `place-refused:identity-unavailable`; never guess a restoration
parent. Its tests, interface mirrors, README, DESIGN-GUIDE, CONSUMERS, and
program design rationale are in the diff. The design explicitly discloses
that this observation is not an atomic lease against an unrelated source
process exiting between check and move. Review whether that residual race is
acceptable for the settled exact-restore contract. Also account for the
previous live `--on-stall kill` probe's `place-refused:write-failed` cleanup
warning; it is not a clean-stop receipt until reproduced or explained.

The candidate is Run-Gate revision 61. CMRU currently resolves its next
release to `23.11.0`; the original `23.9.0`/rev-43 handoff is historical.
The report's current unit/coverage/canary receipts are interim runs on a dirty
worktree. The controller supplies the exact final HEAD; require final
Run-Gate `selftest`, `assay-r1`, `assay-r3`, and `doctor`, plus
cgroup-profiler `unit`, whole-package `coverage`, `canary`, and Nyxloom config
lint on that committed tree. The P5 footprint manifest must remain tracked.
Before review, require candidate-daemon live acceptance on the exact code:
socket/exec parity, the CP-19 pre-move refusal, anchored placement/restore,
and on-stall kill cleanup. The controller will use CIU to replace the one
host daemon only while idle, then restore it from main; do not launch a sibling
or use raw Docker to bypass the singleton.

The operator authorizes provisional merge only after those checks and a fresh
Sol ACCEPT. Run exact-tree `assay-r2` and `gate-full` asynchronously in
attached CIU worktrees after that merge, without mutating the judged tree.
Those long-run results remain release blockers. Any repair changes the judged
tree and requires fresh exact-tree short gates/review plus resumed or
restarted long-run evidence before release. P1/P6 mutation jobs may be running
in their separate worktrees; inspect process/container state and do not edit,
switch, or invalidate them. Check both contract mirrors. Do not merge,
release, install, start the final daemon, or touch dstdns.

### `P1`: cgprofile 1.0.0 daemon

Worktree: `.worktrees/rg55-p1-private-ns`, branch `rg55-p1-private-ns`.
The controller will supply the exact current `main` base and committed
candidate tip at dispatch; do not use historical SHAs below as current review
identity. Read:

```
scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P1-DAEMON-HANDOFF.md
scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P1-DAEMON-BRIEF-7.md
scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P1-DAEMON-REVIEW-HANDOFF.md
```

The former detached campaign and stale `5917d362`/`9b70a46e` gate receipts are
not evidence for the private-namespace candidate. The registered R2 on exact
tree `51198f2e4759acbd69dfd770b843cdf20b1d6ed0` completed at
`2026-09-24T09:19:52.297677Z`: 81/81 candidates executed, 71 killed, 10
survived, and zero equivalent, budget-exceeded, crashed, or hung. Its
mechanical result is `FAIL/MUTANTS_SURVIVED`; all ten are oracle gaps and the
P1 report/LOG map each to new behavioral tests. That candidate's prior short
gates are stale for the repair commit. The controller will commit the repair
tests and records first, run final short gates and a fresh registered R2 on
the same new quiet tip, then dispatch this final review. At review dispatch,
verify the exact tree's own R2 verdict, completion marker, every candidate
disposition, and short-gate history. Do not infer from any prior-tree count.

Current status supersedes the preceding snapshot: P1 was reconciled to current
`main` `e5e9b95c5ac8be3452c93f1066f9436347f862fd` by merge commit
`d108ebb2a014ac204d6c50a3b82d65471e8ada7d`. On that clean exact tree,
R0/R1 passed 1,377 tests with 5,021/5,021 statements and 1,732/1,732
branches; R3 rejected all seven canaries. Both history records are PASS/exit
0 with loaded `dev-gates.slice` and 3-CPU caps. The daemon was down, so R0/R1
used coarse rusage. Verify the latest exact tree and receipts at dispatch.

The operator authorizes provisional merge after fresh review and green short
gates while replacement R2/full-gate judging proceeds asynchronously in a
separate attached CIU worktree. This review does not certify mutation
completion, and provisional merge is not release/shipment. Release remains
blocked until the replacement exact-tree campaign accounts for every
candidate without budget/hung/incomplete results, the full gate passes, and
any fixes are backported and rejudged. Never edit or switch the judged tree.

Review the daemon's serve/ctl protocol, socket carrier, watch and placement
behavior, liveness/finalization, safety on daemon absence, host/container
namespace translations, and all four known-equivalent mutants from the brief.
Use real probes where possible. The cockpit has not been rebuilt after P8, so
`/run/cgprofile/ctl.sock` and `dev-gates.slice` may be absent inside it; a
docker-exec carrier result `place-refused:no-gates-slice` is then a disclosed
environment fact, not permission to invent socket success.

### `P4`: run-gate 23.8.0, revision 42

Worktree: `.worktrees/rg55-followups-run-gate`, branch
`rg55-followups-run-gate`, clean tip
`c8f1654cc371c09e78faa6bf66feaf2aaf2e1a22`. Read:

```
run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P4-HANDOFF.md
run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P4-REVIEW-HANDOFF.md
run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P4-REVIEW-round1.md
run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P4-REVIEW-round2.md
```

The fresh R2 on that exact tree was separately read as PASS: 59/59 killed,
zero survived/equivalent/budget-exceeded/crashed. Selftest was 1161 passed,
3 skipped; r3 rejected both canaries; doctor exited 0 with warnings/skips.
The launch observed high host PSI later in the run; it is disclosed
infrastructure context, not a product result. Re-run any gate required after
your edits. Check the actual `run-gate.footprint.json`, `__revision__ = 42`,
RG-57..RG-61 behavior, `R-36h`, `R-39`, lock-dir isolation, bare-host rusage
§4.3a, `meta.expected.source`, README/DESIGN-GUIDE/CONSUMERS synchronization,
and the real live probes. Round 2 was ACCEPT-conditional; your round is the
required final adversarial review.

### `P6`: cgprofile 1.1.0 follow-ups

Worktree: `.worktrees/rg55-followups-cgprofile-final`, branch
`rg55-followups-cgprofile-final`. The candidate includes the P1 daemon and P6
follow-ups, including private-PID placement and stop-time survivor
restoration. Round 5 rejected blockers B1–B6; repairs are recorded from
`da066287`, and `f4872603` passed registered `r0-r1`/`r3` with 100% line and
branch coverage. This packet and the P6 review handoff are being reconciled to
RW-379..RW-381, so rerun both short lanes on the final corrected packet tip
before dispatch. The required live probes must also pass. The next review is
round 6; round 7 is the series cap. Read the P6 LOG/REPORT, BRIEF-13, review
rounds 1–5, and the P6 review handoff. The old R2 at exact tree
`6540f87761a66ff933c8bb45f81d8ac9117f407b` ended
`BUDGET_EXCEEDED/CANDIDATE_HUNG`; it is not evidence for the updated tree.

Under RW-381, ACCEPT permits provisional integration only. Run replacement
R2 and the registered full gate asynchronously in a separate quiet CIU
worktree at the merged candidate; release, tag, install, and `ciu up` remain
blocked until both pass and all wave close-out conditions are met. Caller
selects and verifies the Sol xhigh route; never ask the reviewer to self-attest.

The latest prior P6 R2 is exact tree
`6540f87761a66ff933c8bb45f81d8ac9117f407b`, terminal
`BUDGET_EXCEEDED/CANDIDATE_HUNG`; it is not evidence for the updated tree.
Read the P6 LOG/REPORT, BRIEF-12, review rounds 1–4, and the P6 review handoff.
The P6 review's new three-round series is rounds 5–7; route selection and
verification belong to the caller, not reviewer self-attestation.

### `CMRU`: release-recovery repair encountered during this wave

Worktree: `.worktrees/cmru-buildkit-eof-20260914`, branch
`investigate/cmru-buildkit-eof-20260914`, clean tip `5d4b79e0`. This is a
related release-safety candidate, not a cgprofile package. It repairs the
BuildKit cache-export/EOF promotion path with transaction-scoped branch,
origin/main, prepared-tip, and completion-marker evidence, and proves the
post-push marker-failure revert. Read its diff and its CMRU review artifacts,
README/SPEC/DESIGN-GUIDE/CONSUMERS/backlog changes, and `cmru.release.log`
diagnostics. Its focused 37 tests, full local 1783 tests/3 skips, and
tester-unified r1/r3 are not a substitute for this Sol review or the future
Docker-backed release mutation gate. If you repair it, run its declared gates
again and report the exact CMRU tree. Do not merge or release it here.

### `RG56`: optional future admission implementation

This is not a release approval for RG-55. The design candidate is
`.worktrees/rg56-admission` at `92a2c097`; the isolated implementation is
`.worktrees/rg56-admission-implementation` at `84341e75`. The implementation
has pure admission arithmetic, checked overflow, dynamic capacity telemetry,
strict registry/manifest provenance, and an order-independent estate pairing
guard; its full local run-gate suite is 1101 passed/3 skipped. Review it only
if time remains after the selected release target. Keep the current RG-55
three-mutation-lane cap authorized by RW-319; `additional_slots` must be
derived capacity telemetry, never an authorization beyond three lanes. Any
RG56 repair must remain isolated and must not be merged as part of this final
review.

## Review and repair procedure

1. Treat the caller-provided Sol xhigh invocation/session metadata as the
   route evidence; do not self-verify or attest the route. Verify the selected
   target worktree path, branch, HEAD, status, merge base, and current
   processes/containers. Save the initial status and diff before editing. If
   the selected P1/P6 mutation job is alive, stop at inspection of evidence
   and wait for its mechanical completion marker; do not minute-poll or change
   its tree.
2. Read the named contract and settled decisions blind. Build a requirement →
   code path → observable → negative case → gate table. Enumerate every status
   the code can report and the real-world conditions that collapse into it.
   Attack missing-vs-empty, inaccessible-vs-absent, name-vs-object, and
   type-vs-behavior checks; translated host/container paths; stale receipts;
   defaults; cleanup; retries; crash windows; and concurrent callers.
3. Inspect the whole target diff, not only the implementer's report. Check
   production code, tests, docs, changelog, backlog, and generated/tracked
   footprint artifacts. Test both sides of every refusal and every success.
   A fake Docker argv proves construction only; perform one live acceptance
   probe for every new argv shape.
4. Run targeted tests first. If a defect is found, write the initial `REJECT`
   or `ACCEPT-CONDITIONAL` round record with file/line, severity, behavioral
   consequence, reproduction, and prescription before editing. Then make the
   smallest correct repair yourself, add a contract-level regression oracle,
   update all three user-facing documents when the capability/config/vocabulary
   changed, and commit in the isolated worktree.
5. After any commit, treat prior mutation evidence for that tree as void.
   Before provisional merge, require the affected package's selftest, assay-r1,
   assay-r3, changed-line line+branch coverage, and required live probes on the
   exact quiet tree. The controller may then run exact-tree assay-r2 and
   gate-full asynchronously in attached CIU worktrees while integration
   continues; their results are mandatory before release. Use the real
   `tester-unified` gate where the package declares it; run run-gate-project's
   own bare-host lanes exactly as declared. Read each verdict separately and
   record exit markers, tree hashes, container names, PSI, and CPU caps.
6. Review the final changed tree again. Return `ACCEPT` only when the reviewed
   code's 100% line+branch changed-line coverage and short package gates,
   docs, live probes, R-36h, and D-15 safety all support the claim. Use
   `ACCEPT-CONDITIONAL` only for named non-blocking follow-up rows that cannot
   alter shipped behavior. Use `REJECT` for any blocker. Use `BLOCKED` for a
   mechanical inability to meet the contract or scope, never to avoid a
   product decision.

## Test-oracle rules (paste-required authoring contract)

Because this packet asks for tests, every new or changed oracle must obey all
of the following:

**A. Nothing may make the verdict depend on machine speed.** Do not use a
`monotonic()+N` assertion, sleep-then-assert, elapsed-time assertion, or
iteration count as correctness. Wait on a real synchronization point, or
extract and call a pure step. A timeout is only a generous suite failsafe
(60 seconds, not a product decision).

**B. Nothing may depend on test order, worker assignment, or a sibling test.**
Restore process-global state, use fresh `tmp_path`, and assert cleanup. Do not
patch lazy-proxy synthesized attributes; patch the owning namespace. If a
full parallel suite differs, look for leaked state before declaring a race.

**C. No hollow tests.** Do not use `pass`, “nothing raised”, call counts,
private attributes, or log strings as the contract. Assert the observable
behavior and, where a guard prevents a crash, prove the unguarded crash too.

**D. No coverage evasion.** Do not add `no-cover` pragmas (including in
comments), exclude changed branches, or assume covering an `except` body covers
the clause. Restructure genuinely unreachable code.

**E. Network, clock, and filesystem are inputs.** Do not use real network,
registries, model endpoints, or uncontrolled current time in unit tests. Inject
or mock those boundaries and keep the offline path deterministic.

For every test, ask whether a slower machine, another worker, or another order
could flip its verdict. If yes, the oracle is not ready.

## Required output and files

Write the review artifact before returning. Use the existing naming convention
in the selected package's report directory, for example
`cgprofile-P1-DAEMON-REVIEW-round3.md`,
`cgprofile-P6-FOLLOWUPS-REVIEW-round5.md`, or the CMRU review's next round.
The first non-heading line must be exactly one of:

```
ACCEPT
ACCEPT-CONDITIONAL
REJECT
BLOCKED
```

The artifact must include:

- the controller-provided Sol/xhigh invocation evidence reference (not a
  reviewer self-attestation), target, base, final HEAD, worktree status, and
  whether the initial diff was captured before edits;
- a requirement-to-oracle traceability table and pairwise/combined-axis attack
  fixtures;
- every blocker as `B<n>` with severity, file:line, observable failure,
  reproduction, and exact prescription; non-blocking items as `S<n>`;
- exact commands, separately read exit codes/verdicts, coverage line+branch
  totals, mutation candidate accounting, tree identity, PSI, and container CPU
  caps;
- live probe results, including daemon/carrier availability and any honest
  `place-refused:no-gates-slice` disclosure;
- all commits made by you, targeted tests after each repair, and which old
  evidence was invalidated;
- docs/backlog changes and any unresolved open question, without silently
  inventing a product decision.

If you make a repair, leave the isolated worktree clean at the final reviewed
commit. Do not put review artifacts into the shared main index. Do not merge,
release, install, start the final daemon, or modify dstdns; the controller will
perform those operations after your ACCEPT and the required post-review gates.

## Open questions to resolve or explicitly disposition

1. What is the terminal verdict for the current P1 and P6 exact-tree R2? Are
   all candidates executed, or do any placeholders remain? If incomplete,
   identify whether the cause is a candidate hang, lane safety ceiling, or
   infrastructure/pressure condition and prescribe the correct resume; never
   classify it as a product result.
2. Are every P1/P6 survivor and known-equivalent mutant closed by a behavioral
   oracle or a proof from the shipped contract? A reviewer must not accept a
   count copied from an older tree.
3. Does P4's final tree retain the full bare-host rusage and lock isolation
   contract while remaining verdict-neutral when profiling fails? Is its
   footprint manifest real and tracked rather than producer-authored prose?
4. Does the CMRU repair cover both sides of the push/marker-write crash window,
   transaction/project isolation, dirty-main recovery, and actual remote
   promotion behavior? Are README, DESIGN-GUIDE, CONSUMERS, SPEC, CHANGES, and
   backlog consistent?
5. Does RG56 derive any future additional capacity from verified host facts,
   with no shadowing or empty-default path? RW-319 already authorizes up to
   three current RG-55 mutation lanes under the verified five-CPU
   `dev-gates.slice`; that operational ruling does not approve RG56 changes.
6. Do not rely on the historical release sequence or version/revision numbers
   printed in older P1/P6/P5 reports. The controller owns release order and
   must derive it from current CMRU state plus the operator's current release
   authorization. Do not release, tag, publish, or install from this review.
7. Can the final P3 probes use both the docker-exec and host socket carriers
   against the real daemon after it is installed from main? If the cockpit is
   not rebuilt, record the socket absence and `place-refused:no-gates-slice`
   honestly instead of treating it as successful socket evidence.
8. Is `run-gate.footprint.json` present, tracked, and regenerated from a real
   accepted live probe on the release history? If not, leave the exact
   controller action required.
9. The operator's dirty backlog addendum observed a 714.67-second dstdns UI
   run against a 600-second budget. It is evidence for a future measurement /
   infrastructure follow-up only. Do not touch dstdns or rewrite RG-55's
   contention-agnostic principle from that observation.
10. In-repository consumers must use the candidate worktree's source-backed
    Assay, not a cockpit-installed wheel or a historical version pin. Verify
    the actual gate invocation and its preflight on this exact tree. If a gate
    refuses, classify the concrete dependency/configuration mismatch; do not
    silently fall back to an older released Assay or call an infrastructure
    refusal a P5 product failure.

## Mechanical BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden
file, STOP — write `BLOCKED: <reason>` to the LOG, commit the review artifact
and any safe evidence, and exit. Do not improvise a workaround. If the gap is a
product choice rather than a mechanical blocker, record a new `D-<NNN>` with
the dependency instead and continue around it.
