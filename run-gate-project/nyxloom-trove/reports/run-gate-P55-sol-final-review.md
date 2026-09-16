---
schema_version: 1
id: run-gate-P55-sol-final-review
project: run-gate
component: release-review
title: "RG-55 final adversarial review and repair packet"
tier: frontier-review
input_revision: "8823dca820cf6ffc6520da57663f8b7424f1ce35"
depends_on: []
session: fresh
source:
  kind: review
  ref: run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-CONTROLLER-LOG.md#RW-243
scope:
  touch:
    - run-gate-project/run-gate.py
    - run-gate-project/tests/
    - run-gate-project/README.md
    - run-gate-project/CONSUMERS.md
    - run-gate-project/LANE-AUTHORING.md
    - run-gate-project/SPEC.md
    - run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md
    - run-gate-project/CHANGES.md
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
  - "the Sol xhigh runtime identity or effort cannot be verified"
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
its body. The first message to the fresh Sol xhigh session should therefore
contain these literal instructions (change only the target when appropriate):

```
REVIEW_TARGET=P5
Read /workspaces/vbpub/run-gate-project/nyxloom-trove/reports/run-gate-P55-sol-final-review.md in full before acting.
Follow that packet as the review contract. Review exactly the selected target, make scoped repairs when needed, and return the required verdict and artifact. Do not merge or release.
```

`REVIEW_TARGET=P5` is an explicit task parameter, not a shell variable that
the reviewer is expected to discover. If the line is absent or names anything
outside `P1`, `P4`, `P5`, `P6`, or `CMRU`, return `BLOCKED` before repository work.

Before pasting this packet, select the actual model route in the client and
put one literal target line at the top of your prompt. For the currently ready
P5 package, use:

```
REVIEW_TARGET=P5
```

For the other release-blocking packages, replace `P5` with exactly `P1`, `P4`,
`P6`, or `CMRU`. Do not leave the variable unset, and do not use a shell-style
placeholder such as `$REVIEW_TARGET`; the reviewer must see the selected
target in its input. `RG56` is an optional related review of future admission
work and is not a release approval for RG-55.

Copy the remainder of this document into a **genuine fresh Sol xhigh** session,
once per review target. Do not reuse one Sol session as the “fresh” reviewer
for two packages. The controller will merge or release only after a real Sol
ACCEPT for that package and all post-review gates have passed.

## Role and authority

You are the final independent adversarial reviewer and, when necessary, the
repair implementer for `REVIEW_TARGET`. The client must be configured to the
actual Sol route (model id `gpt-5.6-sol`, effort `xhigh`, or the platform's
exact equivalent). A normal Codex/GPT-5 route is not a Sol review. State the
runtime identity and route metadata actually exposed to you; do not infer or
claim Sol from this prompt. If the client cannot establish that it launched
the Sol xhigh route, return `BLOCKED` before touching the repository.

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

The shared main checkout currently has two intentional operator changes:
`run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md` and
`modern-debian-tools-python-debug/host-setup/Preventing another misplaced
BuildKit.md`. Do not stage, reset, clean, or commit either. Do not use a broad
Docker removal or prune command. Never use `--cgroupns=host`, `--pid=host`, or
`--net=host`.

Host contention is an allowed operating condition, not a defect to eliminate.
The requirement is that functional outcomes and mutation classifications are
deterministic and contention-agnostic. A pressure-affected, incomplete, or
budget-truncated run is infrastructure/inconclusive evidence and must be
retried or reported as such; it is neither a product PASS nor a product FAIL.
Budgets are safety/resume controls, never correctness oracles.

Before launching any container or mutation lane, read
`/proc/pressure/memory`; do not launch while memory `full avg10 > 5`. Keep no
more than two mutation lanes estate-wide. Use the declared cgroup environment,
not a hard-coded slice, and apply `docker update --cpus=3 <exact-container>`
immediately after each launch, then verify it. Bare pytest may run beside a
mutation lane only when PSI-gated and with `nice -n 19 ionice -c 3`. Detached
jobs must append an explicit job exit marker and the marker must be read
separately from the wrapper's status. Do not poll a long gate every minute;
leave it detached with a watcher and inspect it at a meaningful completion or
wakeup point.

Assay 6.1.1/6.2 identity is per TREE. A commit in a judged worktree voids all
mutation records for that worktree. Keep the HEAD quiet while judging. Resume
only from the exact judged tree with `git switch --detach <tree>`, then switch
back. Every assay mutation invocation must use `--resume --progress` as the
project's recipe requires. One `budget_exceeded` candidate means the lane is
not complete; do not manufacture a result by copying or renaming records.

## Read these files before making a judgment

Read the controller materials in this order:

1. `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-CONTROLLER-HANDOFF-2026-09-12.md`.
2. `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-CONTROLLER-LOG.md`,
   through the latest ruling (currently RW-245). The log, current git state, assay JSON, process state, and
   Docker state outrank prose in an old brief.
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
   `docs/CONSUMERS.md`.

The settled decisions are not product questions to reopen. Private helper
names and equivalent decomposition remain free; public names, serialized
shapes, refusal meanings, provenance, bounds, and carrier behavior do not.

## Target packets and current state

### `P5`: run-gate v1.1 client, revision 43

Worktree: `.worktrees/rg55-client-v11`, branch `rg55-client-v11`, product
implementation tip `8823dca820cf6ffc6520da57663f8b7424f1ce35` (the packet is a
tracked report in the same worktree). Read:

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

This target implements C1–C9 of the P5 handoff: the v1.1 socket/exec
transport seam, one-reader daemon watch with ended/failed retry and fallback,
policy authoring, gates-slice parent and placement, wait-then-proceed RG-56
admission, schema-2 nullable watch/placement history, and synchronized
adopter-facing documentation. The ended-reader repair and its behavioral
regressions are committed through `8823dca8` (including the bare-host path and
the live/fallback branch matrix). The full selftest from that quiet tree passed
1,287 tests with 3 skips, with 642/642 changed executable lines and 276/276
changed branches covered. This is valid P5 evidence, but the packet remains
provisional until the operator's assay 6.3 source-backed integration is
reconciled into the final review tree; that reconciliation must rerun any
affected gates. The same quiet tree's R1 rerun with `--base main` passed, R3
passed with both canaries rejected and zero survivors, and doctor exited 0
with zero failures. R2 mutation evidence is not yet available for this tip.
Review the full diff and run the real `selftest`, `assay-r1`, `assay-r2`,
`assay-r3`, and `gate-full` gates as appropriate. P1/P6 mutation jobs may be
running in their separate worktrees; do not edit, switch, or invalidate them.
The P5 branch itself must be quiet while its mutation evidence is collected.
Check the new request shape against both contract mirrors, run live acceptance
probes for socket and Docker-exec carriers where the installed daemon permits,
and disclose the cockpit's known `place-refused:no-gates-slice` condition if
the unrebuilt devcontainer cannot expose the host gates slice. Do not merge,
release, install, start the final daemon, or touch dstdns.

### `P1`: cgprofile 1.0.0 daemon

Worktree: `.worktrees/rg55-profiler-daemon`, branch `rg55-profiler-daemon`,
current branch tip `5917d362`. Read:

```
scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P1-DAEMON-HANDOFF.md
scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P1-DAEMON-BRIEF-7.md
scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P1-DAEMON-REVIEW-HANDOFF.md
```

At packet creation, a detached R2 was still running as PID `3301167` in exact
container `run-gate-vbpub-r2-3301167-1789437789`. Do not edit this worktree,
switch its HEAD, or triage an old verdict while that process/container is
alive. On completion, read its explicit verdict and completion marker
separately, verify the judged tree, and reconcile every candidate. Historical
P1 evidence included seven survivor dispositions and earlier budget
placeholders, but those counts are not current evidence for `5917d362`.

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

Worktree: `.worktrees/rg55-followups-cgprofile`, branch
`rg55-followups-cgprofile`, current tip `8076246c`. Read:

```
scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P6-FOLLOWUPS-HANDOFF.md
scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P6-FOLLOWUPS-BRIEF-10.md
scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P6-FOLLOWUPS-REVIEW-HANDOFF.md
```

At packet creation, the fresh R2 was still running as PID `3403254` in exact
container `run-gate-vbpub-r2-3403254-1789439415`, after the timeout-oracle
repair commit `8076246c`. Do not edit this worktree while it runs. The repair's
targeted `tests/test_summary.py tests/test_serve.py` suite was 143/143. The
older aggregate had five budget placeholders caused by summary/status tests;
that evidence is stale for this repaired tree. On completion, read the new
verdict and progress separately, then triage all survivors and any incomplete
candidate. Verify CP-2..CP-12, socket peer credentials, watch role, placement
guard, `cgprofile.slice`, host-PSI seam, kill-finalizes behavior, and both
carriers. No release follows from an incomplete mutation lane.

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
two-mutation-lane cap; `additional_slots` must be derived capacity telemetry,
never a literal authorization to launch a third lane. Any RG56 repair must
remain isolated and must not be merged as part of this final review.

## Review and repair procedure

1. Verify the actual Sol xhigh runtime, worktree path, branch, HEAD, status,
   merge base, and current processes/containers. Save the initial status and
   diff before editing. If the selected P1/P6 mutation job is alive, stop at
   inspection of evidence and wait for its mechanical completion marker; do
   not minute-poll or change its tree.
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
5. After any commit, treat prior mutation evidence for that tree as void. Run
   the affected mutation lane again from the new exact tree with resume state
   that belongs to that tree, plus the package's selftest, assay-r1, assay-r3,
   and required live probes. Use the real `tester-unified` gate where the
   package declares it; run run-gate-project's own bare-host selftest/r1/r2/r3
   lanes exactly as declared. Read each verdict separately and record exit
   markers, tree hashes, container names, PSI, and CPU caps.
6. Review the final changed tree again. Return `ACCEPT` only when the complete
   mutation result, 100% line+branch changed-line coverage, package gates,
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
`cgprofile-P6-FOLLOWUPS-REVIEW-round3.md`, or the CMRU review's next round.
The first non-heading line must be exactly one of:

```
ACCEPT
ACCEPT-CONDITIONAL
REJECT
BLOCKED
```

The artifact must include:

- actual Sol runtime identity, target, base, final HEAD, worktree status, and
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
5. Does RG56 preserve a hard current cap of two mutation lanes and derive any
   future additional capacity from verified host facts, with no shadowing or
   empty-default path? This is a future-wave review, not authorization to add a
   third live lane now.
6. Which exact post-review release order is still valid: run-gate 23.8.0,
   cgprofile 1.0.0, cgprofile 1.1.0, then run-gate 23.9.0 after P4/P6? Identify
   any dependency that makes a sequence unsafe, but do not release it yourself.
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
10. Assay 6.3 is being prepared in the operator-owned
    `.worktrees/assay-source-backed-20260916` worktree to remove internal
    hard pins before release. Do not edit that worktree or create another
    re-vendoring cycle. If a P5 gate is refused solely because this checkout's
    assay pin is stale, report the exact dependency and let the controller
    rerun it after the source-backed 6.3 release; do not misclassify that
    sequencing issue as a P5 product failure.

## Mechanical BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden
file, STOP — write `BLOCKED: <reason>` to the LOG, commit the review artifact
and any safe evidence, and exit. Do not improvise a workaround. If the gap is a
product choice rather than a mechanical blocker, record a new `D-<NNN>` with
the dependency instead and continue around it.
