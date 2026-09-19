# Post-CMRU estate release plan

Status: execution in progress, updated 2026-09-19 UTC. CMRU v5.3.1 is
released and installed; the estate lanes and final release remain in progress.

## Operating rules

- Keep `/workspaces/vbpub` clean. All implementation, review, and test changes
  use isolated `.worktrees/<branch>` worktrees and are merged serially.
- The devcontainer is a cockpit for invoking and inspecting gates. It is not a
  test environment and is not a ship signal.
- Python and container acceptance runs execute in `tester-unified`; system and
  installer acceptance runs execute in the declared VM harness. A command run
  directly in the cockpit is only a local diagnostic and cannot certify a
  release.
- Do not inspect a running gate more often than once per 20 minutes. Every
  long-running invocation writes its own exit marker or structured verdict.
- Retain release logs and declared artifacts by default. Discard them only with
  an explicit `--discard-logs-on-release` or `--discard-artifacts-on-release`.

## Phase 0 — finish and install CMRU

Completed 2026-09-19 UTC from promoted commit
`20fa7730cb0f5e46b47b45037ffc88f78dfd17e6`:

- the governed CMRU gate passed with **1796 passed, 10 skipped**, 100% line
  and branch coverage, and a complete mutation campaign;
- tag `cmru-v5.3.1`, the published wheel, retained logs, and retained release
  artifacts were verified; and
- the wheel was force-reinstalled into the devcontainer user environment and
  verified outside the editable checkout as CMRU 5.3.1.

The remaining steps below use this installed CMRU. The final estate release
still has to verify the new first-party artifacts after promotion.

1. Let the retained CMRU candidate complete its `run-gate gate` conjunction.
2. If it passes, verify the exact promoted commit, `cmru-v5.3.1` tag, published
   release, wheel, checksum, and retained evidence before removing the durable
   candidate worktree.
3. Install the published CMRU wheel into this devcontainer's user environment.
   Verify the installed version and source identity from a shell outside the
   editable checkout, then run a read-only CLI diagnostic from the installed
   wheel.
4. If the gate fails, repair in the retained candidate worktree, push that
   candidate branch, and resume the same transaction. Do not create a second
   release candidate for the same attempt.

## Phase 1 — review the outstanding CLI commits

Use a dedicated worktree based on the post-CMRU `main`.

### `c9c34431d35cfa10bb856cb55eb84872da11604e`

Compare the commit and its parents with the already landed universal parser
diagnostic work. Preserve only behavior that is still unique and consistent
with the current headline scope. Carry its tests and documentation with any
ported behavior, then run the affected tool gates in their declared containers.

Review result (2026-09-19 UTC): discard the merge commit. Its functional and
test changes are already represented by `aa0e69fa` and subsequent main commits;
the only two-document delta against that implementation is already present in
current Nyxloom documentation. A wholesale merge would also reintroduce older
CMRU, checklist, and release-document states.

### `9e46dc36dd2c3ef6e7c9c31815cbb0e8b6e31e33`

Classify the commit before changing history:

| classification | action |
|---|---|
| already represented by current `main` | discard and record the superseding commit(s) |
| unique and compatible | port the behavior with new tests/docs, then review |
| unique but conflicting | preserve the current contract unless a new decision is recorded |
| obsolete or unsafe | discard and record the concrete reason |

The result must be a reviewed worktree commit, rather than a blind merge of an
old branch tip.

Review result (2026-09-19 UTC): discard the branch tip as a merge candidate.
The attribution correction was valid for the historical snapshot, but later
main commits replaced that architecture with the managed BuildKit remote and
remote-owned cache contract. The current MDT and PWMCP docs describe the
superseding design, and the VM acceptance plan retains the unresolved large
transfer investigation. No unique source or test change remains to port from
this three-file documentation commit.

## Phase 2 — strengthen the highest-value test lanes

Work in separate, non-overlapping worktrees. Use the current CMRU gate as the
reference shape, while keeping product tests in each product.

| priority | lane | required result |
|---:|---|---|
| 1 | CMRU | Retain the new behavioral coverage tests and make the full gate's assay, coverage, mutation, canary, and enrollment evidence eligible. |
| 2 | CIU | Establish a full branch-aware coverage lane, add Hypothesis where config/state invariants are crisp, and run it in `tester-unified`. |
| 3 | assay | Keep the self-hosting exception explicit; ensure its own progress/resume and mutation behavior is tested in its declared lane. |
| 4 | run-gate-project | Make the full gate, container placement, identity, mount, and failure-status behavior measurable; use Assay for resumable R0/R1/R2 evidence. |
| 5 | nyxloom CLI | Cover the CLI surface only as scoped, with deterministic Hypothesis profiles where useful; keep daemon/long-running tests in their own declared lanes. |

For each lane, check the canonical estate checklist against the complete test
surface. Add Schemathesis only where the project owns an HTTP/OpenAPI contract;
otherwise record a reasoned N/A. Do not claim full branch coverage merely from
line coverage or from a green pytest command.

### Baseline audit

Static audit completed 2026-09-19 UTC against `main` commit `7f1cea69`.
This records declarations only; no cockpit result is release evidence.

| project | current evidence and environment | assay/resume state | property testing | highest-value first slice |
|---|---|---|---|---|
| CIU | `tester-unified`; declared 100% line+branch | R0+R1; run-gate injects resume/progress | no Hypothesis; Schemathesis N/A for CLI scope | property tests for parser, path, worktree, and governance invariants; correct stale gate-slice docs |
| CMRU | coverage lane requests 100% line+branch; coverage/mutation/canary in `tester-unified`, enrollment/conjunction bare-host | custom mutation runner lacks durable resume/progress | no Hypothesis; Schemathesis N/A | make mutation evidence durable or move it to declared Assay R2/R3 semantics |
| Assay | self-hosted tester-unified from a bare-host driver; deliberately R0-only | direct tester script passes resume/progress | Hypothesis properties currently skip when dependency is absent | define self-qualification coverage policy and make the registered property dependency explicit |
| Nyxloom CLI | tester-unified; main lane is 100% line-only, `session-extract` has branch/R2/R3 evidence | both lanes receive resume/progress | Hypothesis present; Schemathesis N/A for CLI-only scope | measure and raise the complete CLI lane to branch enforcement |
| run-gate-project | bare-host by design for Docker/mountinfo self-tests; no VM lane | R1/R2 resume/progress; canary is command lane | no Hypothesis; Schemathesis N/A | whole-project line+branch campaign and make release consume complete Assay evidence |

Source paths: each project's `run-gate.toml` and `assay.toml`, the shared
`run-gate-project/run-gate.py`, and the project-specific tests named by those
declarations. The audit is a starting point for the work slices below, not a
claim that any lane is currently green.

First implementation slice: CIU branch `feat/ciu-property-coverage`, commit
`17a71c10` (2026-09-19 UTC). It adds Hypothesis to the test closure and
property checks for path translation, size parsing, and shared-infrastructure
argument grammar, and corrects CIU's gate documentation to use
`$CGROUP_PARENT_DEV_GATES`. The final CIU-managed worktree gate passed at
commit `6c687916` through `tester-unified` under `dev-gates.slice`; its
`run-ciu-tests.py` command enforces the complete pytest-cov line and branch
floor.

Second implementation slice: Nyxloom branch `feat/nyxloom-cli-branch-coverage`,
commit `f5e806e4` (2026-09-19 UTC). It enables `--cov-branch` and
`require_branch = true` for the broad CLI lane and adds the required README,
design, and consumer documentation. The final rerun also corrects stale gate
comments so they describe the whole-source line and branch contract; its
tester-unified verdict is recorded after that rerun and before merge.

CMRU repair state: the first candidate gate reached 100% branch coverage but
failed its mutation lane with twelve survivors. Candidate commit `d9ff92f8`
adds behavioral witnesses for those paths, removes two equivalent selector
flags, and ensures the mutation subprocess imports the candidate's `src`
tree. The same retained candidate transaction is being resumed; no second
CMRU release transaction is allowed for this attempt.

## Phase 3 — run the estate matrix in the right environments

For every release-facing project, run its declared lanes at one clean commit
and retain:

- the exact commit and tree identity;
- tester-unified image or VM identity;
- the job's own exit status;
- coverage with branch policy and source roots;
- Assay verdict, resume state, and progress stream;
- mutation candidate and kill evidence where declared;
- canary/rejection evidence where declared; and
- all artifacts named by the lane.

The cockpit may invoke `run-gate.py`, `cmru tester-gate`, or the VM controller,
but no product test is accepted from the cockpit's own Python environment.
Docker-sensitive tests use their declared nested-Docker lane. Debian install,
host setup, and other system-boundary tests use the `debian-install-v2` VM lane
or the project-specific VM harness.

## Phase 4 — full estate release

After the reviewed worktrees are merged and the estate matrix is green:

1. Confirm `main` is clean and aligned with `origin/main`.
2. Run the native `cmru release all` dry-run and inspect the ordered project
   plan, changed-project selection, dependency order, and artifact inventory.
3. Run the full `cmru release all` transaction with the declared gates value
   `CGROUP_PARENT_DEV_GATES=dev-gates.slice`.
4. Observe long-running gates at no more than 20-minute intervals.
5. Verify every promoted tag, release, wheel/image/bundle artifact, checksum,
   and retained evidence. Install newly released first-party wheels only after
   their release artifacts have been verified.

## Diagnostic and tooling disposition

The CMRU coverage-gap tests belong in CMRU because they assert CMRU-specific
behavior. Assay should continue to own the generic resume, progress, mutation,
and evidence protocol. Temporary coverage JSON files, timer commands, and
one-off shell probes are disposable.

A later assay enhancement may produce a mechanical report grouping uncovered
branches by project and linking them to a resumable checklist. It should report
gaps and evidence; it should not generate hollow tests or silently turn
uncovered code into a pass. That enhancement is lower priority than getting the
five release-facing lanes green in their proper environments.

## Definition of done

- CMRU is released, its published wheel is installed and verified in the
  devcontainer, and the candidate worktree is retired only after evidence is
  retained.
- Both outstanding commits have an explicit merge/port/discard decision.
- CIU, CMRU, assay, run-gate, and the nyxloom CLI lane have current evidence at
  their declared rigor, with open limitations recorded honestly.
- No ship claim depends on a cockpit-local test run.
- The final `cmru release all` completes from a clean, reviewed `main` and all
  released artifacts are verified.
