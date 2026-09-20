# vbpub testing estate checklist

This is the canonical inventory of testing rigor, evidence, and test-design
review for the projects in this repository. It answers two different
questions:

1. **Did the declared lane run and produce the promised evidence?**
2. **Do the tests challenge the product behaviour that matters?**

Coverage and a green test command answer only the first part. The second part
requires behavioral oracles, boundary cases, integration evidence, properties
where invariants exist, and independent review.

The canonical testing method is
[`nyxloom/reference/TESTING-METHODOLOGY.md`](nyxloom/reference/TESTING-METHODOLOGY.md).
This document applies that method to the vbpub estate and records where the
method has not yet been checked against a project's complete test surface.

## Snapshot and evidence rules

The full-estate inventory was checked on **2026-09-20 UTC** against audit
baseline commit `0795ebb9a2471b714cb0f9aee9ae07e7e044c639` after the run-gate
and estate CLI version merges. Earlier row evidence is retained with its own
judged commit and is marked stale when the version surface changed.

This release-wave update is also being recorded against integration commit
`46744b07` (CIU instance `80e9c8`, created 2026-09-19 UTC). The released
first-party wheels `cmru 5.4.1`, `ciu 7.15.0`, `assay 6.5.0`, `nyxloom 0.8.0`,
and `topos 0.4.0` were checksum-verified and installed into
`/home/vscode/.venv`. The final estate transaction is still pending; this
paragraph records installation evidence and must not be read as a green final
gate.
The author-side review is by Codex. Plato performed the independent
read-only consistency review on the same date; live gate evidence is recorded
only after each job's own verdict has completed.

The baseline commit is the commit whose configuration and stored evidence were
examined. A later documentation commit must not be silently treated as a new
testing audit. Table 1 therefore records a **row audit basis** for every
project. A product-only change updates that product's row and evidence fields;
it does not require touching unrelated rows. A change to shared judging or
orchestration—Assay, `run-gate`, `tester-unified`, the root gate config, cgroup
policy, or this methodology—invalidates the affected rows and may require a
new full-estate audit.

Stored evidence is current only when all of these facts hold:

- the verdict names the exact expected commit;
- the judged tree was clean, or the lane explicitly permits a dirty tree for a
  declared reason;
- the job's own exit status and the structured verdict agree;
- the evidence is eligible for history and is not merely a stale local file;
- every artifact named by the lane exists and is bound to the same commit.

An old report that says “100%” is useful historical context but does not certify
the current tree.

## Status legend

| Mark | Meaning |
|---|---|
| ✅ | Declared and mechanically enforced, or current eligible evidence exists |
| 🟡 | Partly present, command-only, stale, or not enforced at the claimed strength |
| ❌ | Missing or no current evidence |
| N/A | Not applicable or structurally unavailable, with the reason recorded |
| ? | Not yet checked against the complete project surface |

Rigor levels have these meanings:

- **R0** — the declared command ran and produced a result.
- **R1** — the judged executable code was exercised by coverage evidence.
- **R2** — mutation testing shows that the assertions reject nearby wrong code.
- **R3** — a canary proves that the complete gate rejects a deliberately broken
  change for the intended reason.

For Python and JavaScript coverage formats that carry branch arcs, a lane that
claims full branch coverage must set `require_branch = true` and enforce its
declared floor. Go's `go-cover` format is statement-only; Go branch coverage is
therefore N/A until the upstream toolchain supplies branch data.

## Table 1 — high-level lane and adoption matrix

The **row audit basis** records the commit and date used for that row. The
**last stored run** column records the most recent local run found during this
audit, even when that run is ineligible.

| Project / lane surface | Row audit basis (commit/date) | Declared R0 | Declared R1 | Declared R2 | Declared R3 | Branch policy | Assay adoption and environment | Last stored run | Methodology status | Property / API testing status | Remaining checklist work |
|---|---:|---:|---:|---:|---|---|---|---|---|---|---|
| [`assay/`](assay/assay.toml) | `0795ebb9 / 2026-09-19` | ✅ | N/A by design | N/A by design | N/A by design | N/A for the self-hosting lane | Native self-hosting in `tester-unified` | Prior to version merge: PASS at `7f1cea69`; final release gate pending | Central method is present; self-gate is intentionally R0-only | Real Hypothesis property tests exist | Retain the deliberate self-hosting exemption; final gate must cover the version surface |
| [`ciu/`](ciu/assay.toml) | `0795ebb9 / 2026-09-19` | ✅ | ✅ | ❌ | ❌ | R1 requires branches and a 100% floor | Assay lane in `tester-unified`, `dev-gates.slice` | Prior to version merge: PASS at `6c687916`; final release gate pending | Current prior gate exercised the complete pytest-cov line+branch command | Hypothesis property tests cover path, size, and argument grammars; Schemathesis N/A for this CLI surface | Decide whether R2/R3 are required; final gate must cover the version surface |
| [`cmru/`](cmru/assay.toml) | `46744b07 / 2026-09-19` | ✅ Assay R0 | ✅ Separate command lane | ✅ Separate mutation command | ✅ Separate canary command | Command lanes enforce 100% line+branch; Assay declaration remains R0 | Assay plus raw `run-gate.toml` command lanes in `tester-unified`, `dev-gates.slice` | Released `cmru-v5.4.1`; the retained transaction reached its durable backup push after `1835 passed, 10 skipped`; r11/r12 credential IPC failed, r14 exposed a helper-shape error, r15 exposed a Docker API group mismatch, and r16 is pending with derived socket GID, direct askpass, and the managed BuildKit relay | Prior evidence includes behavioral, coverage, mutation, and canary results; the retained resume is the current authoritative run | No project-owned Hypothesis tests; no OpenAPI surface for Schemathesis | Bind the final transaction verdict, promotion SHA, and artifacts to the completed resume |
| [`modern-debian-tools-python-debug/`](modern-debian-tools-python-debug/assay.toml) | `46744b07 / 2026-09-19` | ✅ | 🟡 | ✅ | ✅ | Branch data is collected, but `require_branch` is unset and the floor is 60% | Assay full lane plus host/VM command lanes | Assay receipt at `9653f0f7` ended `BUDGET_EXCEEDED`: 203 candidates, 107 killed, 91 survived, 5 budget-exceeded; the five-line source-import correction is carried into `46744b07` while R2 remains open | Project docs exist; the mutation receipt is a real non-green result, not a release certification | No project-owned Hypothesis tests; no HTTP API for Schemathesis | Decide the branch floor and close or explicitly accept the mutation gap; keep VM evidence separate |
| [`nyxloom/`](nyxloom/assay.toml) tester lane | `0795ebb9 / 2026-09-19` | ✅ | ✅ | ❌ | ❌ | Broad R1 requires whole-source line and branch coverage; `session-extract` separately carries R2/R3 | Assay in `tester-unified`, `dev-gates.slice` | Prior to version merge: PASS at `d4e0b44d`; final release gate pending | Canonical method and project deltas are present; CLI lane has branch enforcement | Hypothesis tests exist; Schemathesis N/A for CLI-only scope | Keep the broad lane and session-extract scope distinct; rerun for the version surface |
| `nyxloom/session-extract` | `7f1cea69 / 2026-09-19` | ✅ | ✅ | ✅ | ✅ | R1 requires branches | Assay in `tester-unified` | Separate from the broad CLI release gate | Same methodology gap as above | Hypothesis exists; no actual Schemathesis suite | Run R2/R3 when this narrower lane is in the release scope |
| [`run-gate-project/`](run-gate-project/assay.toml) | `0795ebb9 / 2026-09-19` | ✅ | ✅ | ✅ | 🟡 | R1 requires branches; R2 does not | Assay R1/R2 plus raw R3 canary in the declared `bare-host` exception | Prior to version merge: PASS at `e72b666a`, 1224 passed/3 skipped; final release gate pending | The bare-host exception is documented because mountinfo/Docker self-tests fail inside an extra container | No actual Hypothesis tests; Schemathesis not applicable | Keep `selftest` as the bounded release gate; record final version-surface evidence and the separately budgeted R2 decision |
| [`topos/`](topos/run-gate.toml) | `0795ebb9 / 2026-09-19` | 🟡 Raw command | 🟡 Branch-aware raw command | ❌ | ❌ | Branch data and a coverage gate exist outside Assay | Raw command in `tester-unified`; no `assay.toml` | Prior run was dirty/exit 1 and predates version merge; final release gate pending | No complete methodology inventory | No project-owned Hypothesis or Schemathesis suite found | Add Assay R0/R1 evidence; decide on mutation and canary lanes |
| [`pwmcp/`](pwmcp/run-gate.toml) | `0795ebb9 / 2026-09-19` | 🟡 Raw pytest | ❌ | ❌ | ❌ | None | Raw command in `tester-unified`; no `assay.toml` | No eligible run after version merge; final release gate pending | No complete methodology inventory | No project-owned Hypothesis tests; Schemathesis is not presently justified | Add Assay R0 and behavioral boundary/integration evidence |
| [`shared-ramdisk-depot-manager/`](shared-ramdisk-depot-manager/run-gate.toml) | `0795ebb9 / 2026-09-19` | 🟡 Raw unit lane | 🟡 Raw coverage lane | ❌ | 🟡 Separate canary/e2e behavior | Go branch coverage is structurally unavailable | Raw unit/coverage/e2e commands; no `assay.toml` | Unit PASS through the declared `tester-unified-go`/SRDM gate container: `go test ./...`, `gofmt`, and `go vet` exit 0; coverage lane is now running with the same host-path and `dev-gates.slice` admission; e2e remains pending | No complete methodology inventory | No property framework found; consider Go fuzzing | Finish coverage/e2e or record their exact limitation before merging |
| `scripts/cgroup-profiler/` | `0795ebb9 / 2026-09-19` | ✅ Raw | 🟡 Raw branch-aware command | ✅ Assay | ✅ Raw canary | Branch data is collected but not uniformly enforced by Assay | Assay R2; other tiers are raw commands in `tester-unified` | No eligible run after version merge; final release gate pending | No complete checklist audit | No project-owned Hypothesis suite found | Normalize R0/R1/R3 evidence and define the branch floor |
| `scripts/damon-analysis/` | `fb9995a2 / 2026-09-19` | ✅ Raw | 🟡 Raw branch-aware command | ✅ Assay | ✅ Raw canary | Branches are collected without a uniform full-floor declaration | Assay R2; other tiers are raw commands in `tester-unified` | No local history record | No complete checklist audit | No project-owned Hypothesis suite found | Normalize evidence and add properties only where invariants exist |
| `scripts/debian-install-v2/` | `61111578 / 2026-09-19` | ✅ Raw | 🟡 Raw branch-aware command plus VM lane | 🟡 Assay R2 has a known mutation gap and is excluded from aggregate | ✅ Raw canary | Assay R2 plus host and QEMU/VM command lanes | Container gate PASS: 317 passed, 11 skipped, 95% line/branch report; R3 PASS: 3 rejected, 0 survived; QEMU VM lane PASS: 68 passed, verified real loop/swap tests, exit 0 | VM/testing docs exist; the container gate and VM lane are separate evidence surfaces | No actual Hypothesis suite; VM acceptance is the important additional surface | Bind the VM receipt and close or explicitly accept the R2 mutation gap |
| `scripts/gstammtisch-guide/` | `fb9995a2 / 2026-09-19` | ✅ Raw | 🟡 Raw branch-aware command | ✅ Assay | ✅ Raw canary | Branch data is collected without a uniform Assay floor | Assay R2; other tiers are raw commands in `tester-unified` | No local history record | No complete checklist audit | No project-owned Hypothesis suite; no API for Schemathesis | Normalize R0/R1/R3 evidence and add selective properties |
| [`scripts/netcup/`](scripts/netcup/run-gate.toml) | `61111578 / 2026-09-19` | ✅ Raw pytest | ❌ | ❌ | ❌ | None | `tester-unified`, `dev-gates.slice`; no `assay.toml` | Current gate PASS: 136 passed in 1.22s | No checklist audit | No property/API testing found | Add Assay R0 and decide whether its API client needs contract tests |
| [`scripts/telegram/`](scripts/telegram/run-gate.toml) | `fb9995a2 / 2026-09-19` | ✅ Raw pytest | ❌ | ❌ | ❌ | None | Raw command; no `assay.toml` | No local history record | No checklist audit | No property/API testing found | Add Assay R0 and message/transport behavioral tests |
| [`plesk-mailbox-create/`](plesk-mailbox-create/run-gate.toml) | `fb9995a2 / 2026-09-19` | 🟡 Smoke only | ❌ | ❌ | ❌ | None | Raw smoke command; no test file inventory | No local history record | No checklist audit | No property/API testing found | Add real behavioral tests and an Assay R0 lane |
| Wings Go patch stack (`wings-cgroups/v1-legacy/patchstack/assay/`) | `fb9995a2 / 2026-09-19` | ✅ | ✅ line coverage | N/A | N/A | Go branch data unavailable in `go-cover` | Separate patchstack Assay config, no root run-gate | No current vbpub gate record | Project-specific reviews exist outside this matrix | Use Go fuzzing where useful | Record Go R2/R3/branch limitations explicitly |
| Release-only assets (`tls-edge`, Empyrion, devcontainer images) | `fb9995a2 / 2026-09-19` | ? | ? | ? | ? | ? | No project-local Assay/run-gate declaration found | No comparable run record | Out of scope for this test-lane snapshot | Must be decided per asset | Add an explicit `testing_scope` entry rather than silently omitting them |

The `ciu/docs/v8-dstdns-demo/assay.toml` file is a demonstration/fixture
configuration and is not counted as a separate vbpub product lane.

## Table 2 — reviewer and author checklist for each lane

This is the detailed checklist that must be completed for every release-facing
lane. A row is not complete merely because a test file contains the words
“behavioral” or “property.” Each checked item needs a test path, report, or
review note that another person can inspect.

| Column | Required question | Mechanical evidence | Human evidence |
|---|---|---|---|
| Project/lane | Which product and declared lane are being checked? | Root and lane resolve from the explicit estate manifest/config | Owner confirms the lane's intended shipped boundary |
| Audit identity | Which exact tree was checked, and when? | Full commit, tree hash, UTC timestamp, clean-tree result | Reviewer confirms the commit is the one under review |
| Environment | Where did the lane run? | `tester-unified`, bare host, or VM identity and image digest | Reviewer confirms the environment matches the behavior being claimed |
| R0 command | Did the declared command complete successfully? | Job exit, structured verdict, captured output | Reviewer verifies status belongs to the job, not a wrapper or pipe |
| R1 line reach | Did the tests execute the judged executable code? | Coverage artifact, source roots, floor, changed/whole target scope | Reviewer checks that source roots represent shipped code |
| R1 branch reach | Were branch arcs measured and complete where supported? | `require_branch`, branch totals, missing arcs, floor | Reviewer classifies unavoidable platform-only paths |
| R2 assertion strength | Would nearby wrong implementations fail? | Mutation result, candidate count, killed/survived/budget buckets | Reviewer classifies survivors and adds behavioral tests for real gaps |
| R3 gate rejection | Does the complete gate reject a known-bad change? | Canary/parity receipt and expected failure reason | Reviewer confirms the canary attacks the shipped boundary |
| Behavioral oracle | Does each important behavior have an assertion about its result, not just execution? | Named test IDs or report references | Reviewer checks the asserted result against the product contract |
| Boundary values | Are zero, one, empty, negative, sentinel, malformed, and limit cases covered where legal? | Optional test inventory or explicit case labels | Reviewer identifies missing legal and failure boundaries |
| Property/model testing | Is there a crisp invariant, round-trip, idempotence rule, ordering rule, or state machine? | Hypothesis, Go fuzzing, fast-check, or equivalent test invocation | Reviewer decides whether the invariant is the right one |
| API/schema contract | Does the project own an OpenAPI/JSON Schema HTTP surface? | Explicit `api_surface` and contract-test command | Reviewer chooses Schemathesis or records a justified N/A |
| Integration seam | Do tests exercise at least one real collaborator/fixture at important boundaries? | Integration lane and fixture identity | Reviewer checks mocks have not replaced the shipped interface |
| Regression proof | Does a bug fix have fail-before/pass-after evidence? | Linked test and recorded failing baseline where practical | Reviewer checks the test reproduces the actual defect |
| Determinism | Does the same commit produce the same answer? | Seeds/profile, network policy, order/repetition result | Reviewer checks timing and environment assumptions |
| Evidence retention | Can another agent inspect the complete verdict and inputs? | Verdict, progress, receipt, artifacts, hashes | Reviewer checks no stale or missing artifact is being treated as green |
| Methodology review | Was the canonical methodology checked against the complete suite? | Checklist file names every applicable row and its status | Independent reviewer signs the checklist and records date/commit |
| Findings | What remains open? | Issue/backlog ID, severity, owner, due wave | Reviewer confirms the disposition is honest |

## Can this become a mechanical estate-wide check?

Yes, but only if the check distinguishes facts from judgments. A mechanical
checker can prove that a claim is declared, configured, and evidenced. It
cannot infer that a test asserts the correct product requirement from the test
name alone.

The recommended design is an explicit root manifest, for example
`testing-estate.toml`, rather than guessing project roots from directory names.
That manifest would list every release product, tool, support project, and
explicitly excluded asset. Each project entry would carry its repository root,
`assay.toml` path or an explicit no-Assay reason, `run-gate.toml` path, testing
scope, methodology-check path, API-surface classification, property-testing
tool, and branch-coverage policy. This is a proposed future schema, not a
shipped configuration file; it must be added to a loader and schema before
examples are treated as executable configuration.

The checker would then:

1. Refuse an undeclared project root or an untracked `assay.toml`/
   `run-gate.toml` pair.
2. Parse every declared lane and verify that its command, environment, source
   roots, coverage artifact, mutation configuration, and canary configuration
   agree across the two files.
3. Check capability rules: `require_branch` where branch data is supported,
   explicit N/A reasons for Go or host-only lanes, and no claimed R2/R3 where
   the adapter cannot provide it.
4. Locate the verdict, progress, receipt, and referenced artifacts, then require
   exact commit identity, eligible cleanliness, matching exit status, and a
   passing structured result.
5. Check that every lane has a methodology checklist and that every checklist
   row contains a real path or an explicit N/A reason.
6. Verify links, closed vocabulary values, config schema versions, and that the
   recorded audit date/commit are present.
7. Report semantic review items separately instead of converting their absence
   into a false green result.

The result should have two statuses:

- **Mechanical status** — pass, fail, stale, missing, or unsupported.
- **Review status** — accepted, open, N/A with rationale, or awaiting
  independent review.

The checker must never mark a lane green merely because `pytest` exited zero,
because a file named `coverage.json` exists, or because a test name contains
“property.” Those are exactly the distinctions this document is intended to
preserve.

## Assay adoption policy

The target state is **Assay or an explicit equivalent receipt for every
release-facing lane**, not necessarily an `assay.toml` file for every command.

- Use Assay R0 for any command-shaped lane where a structured verdict is useful,
  including shell, Go, VM-controller, and host orchestration commands.
- Use Assay R1/R2/R3 only when the relevant adapter and evidence exist.
- Keep host and VM acceptance operations in their correct environment; Assay
  should judge or record their result rather than pretending a container
  snapshot reproduces host-kernel behavior.
- Make R2 lanes pass `--resume` and `--progress` consistently. This gives real
  value for mutation campaigns. R0/R1 reruns are still command-level reruns,
  and R3 canaries generally do not have resumable candidate state.
- Let `run-gate` own placement, cgroups, containers, VMs, and orchestration;
  let Assay own judgment and verdict production. Do not make `tester-unified`
  itself responsible for product semantics.

## Recommended policy and value-first plan

The five recommendations are deliberately staged. They are a target operating
model, not a reason to block the next useful release on a repository-wide test
rewrite.

1. **Give every release-facing lane an Assay R0 verdict or an explicit
   equivalent receipt.** This is the first common evidence contract. It is
   cheap for command-shaped lanes and makes missing or stale results visible.
2. **Require branch coverage for Python and JavaScript lanes when the artifact
   can report branches.** Do not claim full branch coverage when
   `require_branch` is absent. Record Go and host-only limitations as explicit
   N/A decisions.
3. **Add R2 and R3 according to risk and adapter support.** Use mutation and
   canaries first on release orchestration, configuration, security, and
   recovery code. Do not force an unsupported adapter to manufacture a green
   result.
4. **Keep one methodology checklist per project.** The checklist names the
   behavioral oracles, boundaries, properties, integration seams, regression
   proof, determinism, and evidence artifacts. It is reviewed for the changed
   project rather than copied into every unrelated project update.
5. **Use one evidence shape everywhere.** `run-gate` owns execution and
   placement; Assay or an explicit adapter emits the verdict; progress,
   receipts, and artifacts identify the exact commit. R2 lanes always receive
   `--resume` and `--progress`.

The practical order is:

### Now: release CMRU first

CMRU is the highest-value release blocker because the released wheel is needed
to release and install the next version of `run-gate`. For this release cycle:

- freeze the CMRU change at one clean commit and update only CMRU's row audit
  basis and evidence fields;
- run CMRU's existing full pytest, branch-coverage, mutation, canary, and
  release-enrollment checks using the declared environment;
- require exact-commit, clean-tree, job-status, and artifact evidence before
  calling the wheel releasable;
- release and install CMRU before attempting to use it as the release driver
  for `run-gate`.

Do not make this release wait for Hypothesis adoption in every project or for a
new estate manifest. Those are valuable follow-ups, but they do not improve the
immediate CMRU release as much as obtaining valid current CMRU evidence.

### In parallel: improve `run-gate` in a worktree

The proposed parallel worktree is reasonable. Start it from the current main
baseline while CMRU's release cycle is running, then rebase it onto the exact
CMRU release commit before merging. Keep the worktree package focused on the
common evidence path:

- make the existing R0/R1/R2/R3 lanes produce commit-bound receipts and
  structured results consistently;
- close the dirty-tree and stale-evidence paths that currently make local gate
  history ineligible;
- decide whether the raw R3 canary should remain an explicit external receipt
  or become an Assay-registered capability;
- add the run-gate project's Table 2 checklist with concrete test paths;
- add Hypothesis only for a clear run-gate state or data invariant, rather than
  adding a dependency without a property to express.

This work can proceed without touching the other product rows. After CMRU is
released and installed, use that wheel to run the run-gate release cycle, then
merge the reviewed worktree serially.

### Next: build the mechanical estate check

Once the CMRU and run-gate evidence paths are stable, add the explicit estate
manifest and checker described above. Start with declaration consistency,
exact-commit evidence, artifact identity, and link/schema validation. Add the
semantic checklist references as required fields, but keep their acceptance
status separate from the mechanical result.

### Later: expand test techniques by value

Adopt Assay R0 for the remaining raw release lanes, then add R1 where coverage
is meaningful. Add Hypothesis, Go fuzzing, fast-check, or equivalent only where
the project has a crisp invariant. Add Schemathesis only for a project that
actually owns an OpenAPI or JSON Schema HTTP surface. Expand R2/R3 after the
shorter lanes are stable and their budgets are measured.

The current priority is **current CMRU evidence, the CIU/Nyxloom coverage
slices, the declared run-gate Assay lanes, and the final estate release**.
Universal R2/R3 adoption remains a later wave where a project has not declared
those lanes, and the run-gate bare-host exception remains explicit because its
tests inspect real mountinfo and Docker orchestration.

## Author and reviewer sign-off

Every update to this document should carry these fields in the commit or its
review report:

```text
Audit baseline commit: `0795ebb9a2471b714cb0f9aee9ae07e7e044c639`
Audit date (UTC): `2026-09-19`
Author/checker: Codex
Independent reviewer: Plato (read-only consistency review, 2026-09-19); estate
version diff reviewed by Codex after merge, 2026-09-19
Last full estate check: in progress; final gate verdicts are recorded in
`docs/plan-post-cmru-estate-release.md`
Changed project rows: CMRU, CIU, Assay, Nyxloom, Topos, MDT, PWMCP, SRDM,
debian-install-v2, run-gate-project, and cgroup-profiler
Evidence intentionally not rerun: product gates are deferred to the final
CMRU transaction; long R2 lanes remain outside this release critical path
Open findings: final estate gate verdicts, remote promotion, and artifact
verification after the estate release
```

Before declaring the estate merge-ready, the author checks every Table 2 row
for each changed lane, and an independent reviewer verifies the exact commit,
the evidence identity, the behavioral oracles, and every N/A decision.
