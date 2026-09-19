# Post-CMRU estate release plan

Status: execution in progress, updated 2026-09-19 UTC. CMRU v5.3.1 is
released and installed; the reviewed run-gate and estate CLI changes are
merged, and the final estate release remains in progress.

Independent read-only review by Plato on 2026-09-19 found and drove fixes for
stale checklist rows, the README's obsolete Nyxloom exclusion and broken
devcontainer link, stale Nyxloom gate comments, and the run-gate contract's
bare-host wording. The version branch was independently reviewed in this
working session; the remaining operational steps are to record the final
release-facing evidence, promote the clean local `main` tip to `origin/main`,
and verify every artifact after the final release.

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
- Retain release logs, declared artifacts, and declared gate evidence by
  default. Discard each category only with its explicit opt-out flag:
  `--discard-logs-on-release`, `--discard-artifacts-on-release`, or
  `--discard-evidence-on-release`.

## Phase 0 — finish and install CMRU

Completed 2026-09-19 UTC from promoted commit
`20fa7730cb0f5e46b47b45037ffc88f78dfd17e6`:

- the governed CMRU gate passed with **1796 passed, 10 skipped**, 100% line
  and branch coverage, and a complete mutation campaign;
- tag `cmru-v5.3.1`, the published wheel, retained logs, and retained release
  artifacts were verified; and
- the wheel was force-reinstalled into the devcontainer's canonical
  `/home/vscode/.venv` environment and verified outside the editable checkout
  as CMRU 5.3.1.

The remaining steps below use this installed CMRU. The CMRU candidate is
closed; its merged worktree was retired after its evidence was recorded. The
final estate release still has to verify the new first-party artifacts after
promotion.

## Phase 1 — review the outstanding CLI commits

Use a dedicated worktree based on the post-CMRU `main`.

### `c9c34431d35cfa10bb856cb55eb84872da11604e`

Compare the commit and its parents with the already landed universal parser
diagnostic work. Preserve only behavior that is still unique and consistent
with the current headline scope. Carry its tests and documentation with any
ported behavior, then run the affected tool gates in their declared containers.

Review result (2026-09-19 UTC): discard the merge commit. `c9c34431` merges
parents `8c56f6b3` and `2000236f`; compared with the clean implementation
`aa0e69fa`, its only remaining delta is six documentation lines in
`nyxloom/README.md` and `nyxloom/docs/CONSUMERS.md`, and those lines are already
present in current Nyxloom documentation. A wholesale merge would reintroduce
the old merge snapshot's stale CMRU, checklist, and release-document states.

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

### Estate CLI version compatibility

The reviewed worktree `feat/estate-cli-version-20260919` produced commit
`05f373a4055e6fa1d753a0dd43d9e86512b7a58c` and merged into `main` as
`0795ebb9a2471b714cb0f9aee9ae07e7e044c639`. It adds the top-level
`--version` probe and headline-first parser diagnostics to the documented
first-party entrypoints. Nyxloom's `extract --help` and missing
`SESSION_LOG` cases are explicit regression tests. The review retained the
existing bare `version` verbs where they already existed and documented the
intentional exclusions: DAMON has no authoritative product version metadata,
and internal helpers are not separate operator entrypoints.

Focused checks passed in the branch. Topos's parser assertions passed, but its
optional `zstandard` dependency was unavailable for the complete local pytest
run; SRDM Go tests and formatting could not run in the cockpit because Go is
not installed. These are environment limitations, not release evidence; the
declared estate release gates must supply the final verdict.

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

CMRU evidence-retention implementation completed in commit `1e149e49` on
2026-09-19. `[project.release].evidence_paths` now declares exact gate output
files/directories; successful transactions retain them under the project-local
`evidence/cmru-release/<immutable-id>/` coordinate with a source-commit and
SHA-256 manifest. Missing, overlapping, escaping, or symlinked declarations
refuse before any move, and rollback is covered. The initial declarations are
CMRU's `coverage.json`/`.assay` and run-gate-project's `coverage.json`; other
projects remain unclaimed until their gates name stable outputs.

The estate CLI identity contract is being applied in the same version worktree:
`tool --version` is a quiet, one-line identity probe (`tool <version>` on
stdout, exit 0, no stderr), while every parser usage, help, and argument or
configuration diagnostic at every verb depth starts with the tool's full
headline (`TOOL <version> — description`). Normal command output is not
prefixed. The Nyxloom nested-parser regression that exposed this distinction is
the acceptance example for the other first-party entrypoints.

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
design, and consumer documentation. The final CIU-managed rerun on branch
`nyxloom/gate-20260919`, commit `d4e0b44d`, corrected the stale gate comments
and passed through `tester-unified` under `dev-gates.slice`.

Third implementation slice: the clean run-gate integration branch merged as
`e887693e` after a tester-unified gate on judged commit `e72b666a`. It passed
`1224 passed, 3 skipped`; the warnings were the known unavailable wheel
toolchain and inactive host profiler. The integrated test additions cover
declared cgroup placement and parser/status paths without importing the
obsolete documentation branch.

CMRU repair state: the first candidate gate reached 100% branch coverage but
failed its mutation lane with twelve survivors. Candidate commit `d9ff92f8`
added behavioral witnesses for those paths, removed two equivalent selector
flags, and ensured the mutation subprocess imports the candidate's `src`
tree. The resumed transaction closed successfully as CMRU v5.3.1; no second
CMRU transaction was created for that attempt.

## Phase 3 — run the estate matrix in the right environments

Environment exception: `run-gate-project` and Assay's self-hosting driver use
their declared `bare-host` lanes because their tests start Docker and inspect
the host's real mountinfo. These are host harnesses invoked from the cockpit,
not tests run in the cockpit interpreter; the product tests execute in the
declared subprocess/venv and their release contract records the exception.
All ordinary Python and container lanes remain in `tester-unified` under
`dev-gates.slice`.

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

The final `cmru release all` scope is the nine-project orchestration order:
`ciu`, `cmru`, `assay`, `topos`, `nyxloom`,
`modern-debian-tools-python-debug`, `pwmcp`, `tls-edge`, and
`run-gate-project`. The five priority lanes above are the manually reviewed
test-enhancement slices; they do not reduce the final release scope. The
transaction's own per-project release gate supplies the remaining exact-commit
evidence, and the ledger must record each selected project's result before the
release is called complete.

The cockpit may invoke `run-gate.py`, `cmru tester-gate`, or the VM controller,
but no product test is accepted from the cockpit's own Python environment.
Docker-sensitive tests use their declared nested-Docker lane. Debian install,
host setup, and other system-boundary tests use the `debian-install-v2` VM lane
or the project-specific VM harness.

### Live lane verdict ledger

These entries name the exact judged commit and environment. A pending entry is
not release evidence.

| lane | judged commit | environment and command | verdict |
|---|---|---|---|
| CIU | `6c687916` | CIU-managed worktree; `ciu/run-gate.py ciu`; `tester-unified`, `dev-gates.slice` | PASS; complete pytest-cov line+branch command |
| Nyxloom CLI | `d4e0b44d` | CIU-managed worktree; `nyxloom/run-gate.py tester-unified`; `tester-unified`, `dev-gates.slice` | PASS; whole-source line+branch command |
| Assay | `7f1cea69` | CIU-managed worktree; `assay/run-gate.py tester-unified`; declared bare-host self-hosting driver | PASS; self-hosted tester-unified qualification, intentionally R0-only |
| run-gate-project | `e72b666a` | CIU-managed worktree; `run-gate.py gate-full`; declared bare-host exception | PASS; 1224 passed, 3 skipped; selftest, Assay R1/R3, and aggregate gate-full exited 0 |

## Phase 4 — full estate release

After the reviewed worktrees are merged and the estate matrix is green:

1. Confirm `main` is clean, then push the reviewed commit to `origin/main` so
   CMRU's stable-source contract can snapshot the exact tree.
2. Confirm the dry-run names the nine-project orchestration scope and inspect
   the ordered project
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

CMRU now retains declared gate evidence, separately from publishable artifacts,
and writes a source-commit/hash manifest. This closes the evidence-loss gap for
the CMRU and run-gate-project release contracts; declarations for additional
projects should be added only when their gate output paths are stable and
verified.

Assay already provides the useful pieces for retained evidence:
`assay analyze progress` summarizes a progress JSONL stream, and
`assay analyze verdict --format text` renders a compact, commit-bound verdict.
The missing operator surface is a bounded `assay analyze report` (or equivalent)
that combines a launcher/receipt, the latest progress phase, job exit status,
the last limited set of errors, and paths to the complete logs and artifacts.
It should refuse missing or mismatched commit evidence, never treat truncated
wrapper output as a verdict, and leave full detail available by explicit path.
That is the durable answer to long-gate output exceeding a terminal buffer; it
is lower priority than getting the priority lanes and the complete
nine-project release transaction green in their proper environments.

The Codex-side operator log has a separate, smaller improvement: `Ran ...` is
the UI rendering of an `exec_command` call, usually a shell command string, but
it is not a promise that every tool call is Bash or that the displayed line is
the complete command. A safe local wrapper would accept a human `--purpose`
and an argv after `--`, print a bounded `PLAN`/`EXEC`/`EXIT` record, preserve
the child's exit status, and write complex multi-step sequences to a reviewed
script with `set -o pipefail` and an explicit exit marker. It should execute an
argv array rather than `eval` a free-form string; the referenced Copilot wrapper
is useful as an operator-facing pattern but its `eval` and sourced plan file
are too permissive for a general estate helper.

This wrapper can make session logs easier to review, but it cannot discover the
model's true remaining context or token budget from Bash. A tool result may
expose an output-token count, and Nyxloom can index an available session log,
but those are different facts. A post-call hook may append timestamps, exit
status, output-log paths, and tool-result size; it must label any token value as
an approximation and never present it as the model context size.

## Definition of done

- CMRU is released, its published wheel is installed and verified in the
  devcontainer, and the candidate worktree is retired only after evidence is
  retained.
- Both outstanding commits have an explicit merge/port/discard decision.
- every project selected by the nine-project `cmru release all` has current
  exact-commit evidence at its declared rigor, with open limitations recorded
  honestly; the five priority lanes also have their enhanced review evidence.
- No ship claim depends on a cockpit-local test run.
- The final `cmru release all` completes from a clean, reviewed `main` and all
  released artifacts are verified.
