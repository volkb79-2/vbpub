# Adversarial review — CIU/CMRU workspace instance

This is the review ledger for
[`PLAN-CIU-CMRU-WORKSPACE-INSTANCE.md`](PLAN-CIU-CMRU-WORKSPACE-INSTANCE.md).
It is deliberately kept beside the plan and is updated as implementation and
verification progress. A checked item requires both code and an executable
oracle; a passing cockpit command is not gate evidence.

## Initial review (2026-09-20)

| Area | Initial result | Required closure |
|---|---|---|
| CIU specification | Red | Make generated facts authoritative and document the exact migration. |
| CMRU specification | Red | Describe one transaction per Git family consistently. |
| Shared library specification | Red | Add API, record, locking, identity, and lifecycle contract. |
| Shared implementation | Amber | Remove duplicated generic lifecycle and identity code from CIU/CMRU. |
| Root discovery | Red | Validate marker contents and handle no-root/nested-root worktrees before allocation. |
| Visible identity | Red | Include the shared six-character identity in CMRU-visible names. |
| Documentation contracts | Red | Parse all three user-facing documents for both products and the library. |
| Test adaptation | Red | Adapt stale suites and add missing combined-axis acceptance tests. |
| Hypothesis | Red | Cover shared lifecycle and CMRU invariants, not only CIU properties. |
| R0–R3 | Red | Declare and execute the four rigor lanes through the real gate. |
| Full branch coverage | Red | Reach 100% line and branch coverage in the declared gate targets. |
| Full gate | Red | Run tester-unified gates and preserve exit/artifact evidence. |

## Closure checklist

- [x] The library has `SPEC.md`, `DESIGN-GUIDE.md`, and `CONSUMERS.md`.
- [x] Every public symbol exported by `worktree.__all__` is named in the
      library specification; the final cross-check reports `missing=[]`.
- [x] CIU and CMRU specs, README, DESIGN-GUIDE, and CONSUMERS agree.
- [x] Generated CIU facts are the sole internal generated-facts authority;
      `ciu.env` is export-only.
- [x] CIU discovers and validates committed markers before allocation and
      handles no-root and partial-preparation cases fail-closed.
- [x] Workspace/root identity is shared and visible where the plan requires it.
- [x] Generic Git lifecycle is implemented once in `libraries/worktree`;
      CIU's compatibility-facing root record retains its historical product
      fields, but create/adopt/ensure/remove and legacy teardown all use the
      neutral lifecycle record rather than a second Git-removal algorithm.
- [x] Every config example and closed public value is covered by documentation
      contract tests, including cross-document anchors.
- [x] Existing CIU, CMRU, and library tests are adapted and green.
- [x] Hypothesis covers CIU, CMRU, and the shared library.
- [~] R0, R1, R2, and R3 lanes are declared and resumable in both
      `assay.toml` files, with snapshot, coverage, mutation, and canary
      contracts. CIU now has a complete green tester-unified run on post-sync
      commit `33dbc009`; CMRU's liveness/fail-fast lane hardening is locally
      tested, but its final tester-unified run is still pending.
- [x] Full branch coverage is green in the local full-suite equivalents:
      CIU, CMRU, and the shared library each report 100% line and branch
      coverage.
- [~] A fresh adversarial review found and closed the mutation-oracle gap and
      the Assay merge-commit boundary. CIU's final post-sync gate is green;
      CMRU's final tester-unified gate remains outstanding.

## Adversarial review checkpoint (2026-09-20, before tester-unified execution)

| Area | Final result | Evidence and qualification |
|---|---|---|
| CIU specification and docs | Pass | `docs/SPEC.md` S1/S2/S3/S7.5a/S16 now make the generated facts tables authoritative and `ciu.env` export-only; README, DESIGN-GUIDE, CONSUMERS, CIU guide, and CONFIG guide were reconciled. |
| CMRU specification and docs | Pass | `docs/SPEC.md` S-CLI.5/S-CLI.5b/S3.5/S16, README, DESIGN-GUIDE, CONSUMERS, and RELEASE-TRANSACTIONS agree on Git-family scope, runtime `none`/`ciu`, the six-character identity, and the R0-R3 gate contract. Build-worktree examples now include `-<workspace-id>` and contract tests check the normative passages. |
| Shared worktree library | Pass | `libraries/worktree/SPEC.md`, DESIGN-GUIDE, and CONSUMERS describe the API, records, locking, identity, namespace boundary, cleanup, and legacy bridge. Both consumer wheels contain `worktree/__init__.py` and `worktree/core.py`. |
| DRY lifecycle | Pass with explicit compatibility qualifier | Generic Git discovery, identity, records, leases, create/adopt/resume/remove, and legacy teardown are in `libraries/worktree`. CIU's root record still mirrors historical S16 metadata and lease JSON for compatibility; its lease is now mirrored into the neutral record before shared cleanup can proceed. CMRU has no second Git-removal path. CIU's branch-hygiene removal at `worktree.py:2051` is a separate merged-branch survey, not workspace lifecycle. A literal zero-duplication claim is not made. |
| Test adaptation | Pass | Full local CIU and CMRU suites pass; stale CMRU naming fixtures and the post-refactor CIU shared-adapter fixture were corrected. Combined-axis adversarial cases cover stale ambient root plus sibling export, nested roots, family separation, visible identity, and legacy records. |
| Hypothesis | Pass | Property suites run in CIU, CMRU, and `libraries/worktree`; they cover path/identity/lifecycle invariants rather than only example cases. |
| Branch coverage | Pass locally | Post-closure full branch coverage is 100% for CIU (11,189 statements / 4,474 branches), CMRU (7,601 / 2,816), and the library (416 / 128). |
| R0-R3 qualification | Declared; execution unproven here | CIU and CMRU now both declare `R0,R1,R2,R3`, including the approved snapshot boundary, coverage artifact, native mutation policy, and import-break canary. The real tester-unified lanes still could not start in this cockpit, so no green R0-R3 claim is made. CMRU's separate `coverage`, `mutation`, and `canary` run-gate commands remain supplemental release evidence. |
| Real tester-unified gate | Not run at this checkpoint | Both project assay lanes refused before launch because `$CGROUP_PARENT_DEV_GATES` was unset. This was the state on 2026-09-20; later execution evidence is recorded below. |

At this checkpoint the answer to “are R0–R3 all working?” was
**configuration yes, execution unproven**. That historical assessment is
superseded by the 2026-09-22 evidence below.

## Continuation (2026-09-22)

- CIU tester-unified passed on `20236e437996f45d88c0be2e8331c16dc1ea370d`
  in container `run-gate-vbpub-ciu-1898737-1790053234`: R0–R3 all PASS,
  R1 797/797 changed executable lines and 120/120 branches, and R2 killed all
  65 candidates with no survivor, hang, crash, or budget overrun.
- After merging current `main` at `d05939b9`, feature merge commit
  `e2a4a68d0a8d60006d8beee00ed172d866542219` was tested in
  `run-gate-vbpub-ciu-1960924-1790066446`. R0 and R3 passed; R1 reported
  0/0; R2 was `INCONCLUSIVE/NO_MUTANTS` (exit 5). The verdict records
  `base_resolution = "first-parent"` and base `20236e43`, because Assay
  measures a merge commit's first-parent payload, which contains no changed
  CIU source here. This run is not accepted as a green for the branch.
- The `main` sync itself had no merge conflicts. A review-ledger follow-up
  commit (`33dbc009`) made `HEAD` non-merge so Assay could resolve the feature
  fork point. The resulting final CIU gate is green; CMRU's final run remains
  pending after explicit liveness/fail-fast protections were added.

## Evidence log

Commands and results are appended here after each implementation pass. Never
replace a previous result: a later green run must not erase an earlier red
run.

### 2026-09-22 continuation evidence

- CIU gate before latest-main synchronization:
  `CGROUP_PARENT_DEV_GATES=dev-gates.slice ./run-gate.py ciu` → container
  `run-gate-vbpub-ciu-1898737-1790053234`, exit `0`, verdict PASS on
  `20236e437996f45d88c0be2e8331c16dc1ea370d`. R0 PASS; R1 PASS at
  797/797 changed executable lines and 120/120 branches; R2 PASS (65 killed,
  0 survived, 0 hung, 0 crashed, 0 budget exceeded); R3 PASS. The verdict is
  `ciu/.assay/verdict-ciu.json` (overwritten by the later attempt).
- Latest-main synchronization: `git merge --no-ff main` created
  `e2a4a68d0a8d60006d8beee00ed172d866542219`, with first parent
  `20236e43` and second parent `d05939b9`. Merge was clean.
- CIU gate on that merge commit:
  `CGROUP_PARENT_DEV_GATES=dev-gates.slice ./run-gate.py ciu` → container
  `run-gate-vbpub-ciu-1960924-1790066446`, exit `5`,
  `INCONCLUSIVE/NO_MUTANTS`. The exact verdict has R0 PASS, R1 0/0 PASS,
  R2 INCONCLUSIVE (`NO_MUTANTS`, total 0), R3 PASS; resolved base is the
  first parent `20236e43`. This is a measurement-boundary failure, not a
  passing R2 result. Full container log:
  `/tmp/run-gate/run-gate-vbpub-ciu-1960924-1790066446.log`.
- CIU gate after the non-merge follow-up:
  `CGROUP_PARENT_DEV_GATES=dev-gates.slice ./run-gate.py ciu` → container
  `run-gate-vbpub-ciu-1997513-1790068325`, exit `0`, verdict PASS on
  `33dbc009f92a052b45fda6de53929062c74bdce7`. Assay resolves the intended
  merge base `d05939b9`. R0–R3 all PASS; R0 full-source coverage is 11,189 /
  11,189 statements and 4,474 / 4,474 branches; R1 is 799 / 799 changed
  executable lines and 122 / 122 branches; R2 killed all 64 candidates with
  zero survivors, hangs, crashes, or budget overruns; R3 import-break PASS.
  Verdict: `ciu/.assay/verdict-ciu.json`.
- CMRU's Assay lane previously relied on liveness `auto` inference and lacked
  an explicit first-failure limit. The lane now explicitly enables liveness
  and `--maxfail=1`; the supplemental coverage/mutation/canary test commands
  also fail fast. The CMRU config/doc/transaction contract slice is green
  (`111 passed`), as is the CIU documentation/spec contract slice (`89
  passed`). Final CMRU tester-unified execution is still pending.
- That run also emitted a cgroup-admission warning because the private
  devcontainer namespace did not expose the host slice's `memory.max`; the
  configured `dev-gates.slice` parent was passed to Docker, and the gate used
  shared-infrastructure admission. The profiler daemon was absent, so only
  in-lane sampling was available.

### Final evidence (2026-09-20)

- `ciu`: `PYTHONPATH=src:../libraries/worktree/src python run-ciu-tests.py`
  → exit `0` (also captured as `EXIT=0`), `3860 passed`, branch coverage
  `100.00%` (`/tmp/ciu-full-final-adversarial.log`; 11,189 statements / 4,474
  branches).
- `cmru`: full `pytest` with `--cov=src/cmru --cov-branch
  --cov-fail-under=100` → exit `0`, `1865 passed, 10 skipped`, branch
  coverage `100.00%`, `EXIT=0`
  (`/tmp/cmru-full-final-adversarial.log`; 7,601 statements / 2,816 branches).
- `libraries/worktree`: final full pytest with `--cov=src/worktree --cov-branch
  --cov-fail-under=100` → exit `0`, `23 passed`, branch coverage `100.00%`
  (`/tmp/worktree-adversarial-final.log`; 100% line and branch coverage).
- Documentation/spec slices: CIU `89 passed` (`test_ciu_documentation_contract.py`
  plus `test_spec_contracts.py`); CMRU `47 passed` (docs, standards, and
  worktree-naming contracts); the shared library full boundary/property slice
  `23 passed`.
- The final cockpit rerun after the shared-adopt, spec-wording, lease-sync,
  primary-adoption refusal, typed Git-error, and R0-R3 lane-contract closures
  is CIU `3860 passed`, CMRU `1865 passed, 10 skipped`, and library `23 passed`;
  all three full local commands exited `0` with their 100% line-and-branch
  floors. The captured final logs are `/tmp/ciu-full-final-adversarial.log`,
  `/tmp/cmru-full-final-adversarial.log`, and `/tmp/worktree-adversarial-final.log`.
- Wheel packaging: `pip wheel --no-deps --no-build-isolation` succeeded for
  both CIU and CMRU; inspecting each wheel found
  `worktree/__init__.py` and `worktree/core.py`.
- The two wheel copies of both shared modules were compared byte-for-byte and
  are identical. `assay lanes --json` validates both checked-in lane files
  with the worktree-local Assay source; both report declared `R0,R1,R2,R3`,
  reachable `R1,R2,R3`, the approved snapshot selection, and mutation/canary
  policies. This validates configuration shape, not an unexecuted campaign.
- Gate discovery: CIU lists `ciu assay tester-unified`; CMRU lists `assay`,
  `canary`, `coverage`, `mutation` on tester-unified plus bare-host lanes.
  CIU `./run-gate.py ciu --worktree ... --allow-dirty --dry-run` and CMRU
  `./run-gate.py assay --worktree ... --allow-dirty --dry-run` both exit `2`
  with the same explicit missing `$CGROUP_PARENT_DEV_GATES` refusal.
- `git diff --check` and Python compilation of the changed lifecycle modules
  pass.
- The current Assay inventory validates both lanes with declared
  `R0,R1,R2,R3`, reachable `R1,R2,R3`, the approved snapshot selection, a
  coverage artifact, serial mutation policy, and import-break canary. The
  inventory is a config oracle; it is not a substitute for executing the
  mutation/canary campaign.
- The final shared-library public-API cross-check reports `public_api=27` and
  `missing=[]`; the unused `WORKSPACE_RECORD_NAME` export was removed during
  the final adversarial pass. The package now rejects primary-checkout
  adoption and translates OS-level Git startup failures into the documented
  `git-error` refusal; its final full suite is `23 passed` with 100% line and
  branch coverage.
- The CIU `ciu up` lease path now mirrors its compatibility lease into the
  neutral shared record; the focused lease suite is `98 passed`, and the final
  CIU full run above is the post-fix proof.

### Iteration evidence retained

- The first liveness-enabled CIU gate after adding the workspace mutation lane
  was red on `245765e0493356c40e81b06dde2623bff2c21738`:
  container `run-gate-vbpub-ciu-1722982-1790055018`, exit `4`, with 63/67
  mutants killed, 2 surviving schema-version mutations, and 2 candidates
  classified hung. The follow-up added `--maxfail=1`, exact integer/version
  oracles for both generated-facts readers, and one shared schema validator;
  the next CIU R2 campaign killed all 65 candidates with no hangs or survivors.
- The first post-refactor CIU full run was `3855 passed, 1 failed`: its only
  failure was an adversarial fake that still exposed the removed raw-Git
  fallback. The fixture was adapted to `remove_unrecorded_workspace`.
- The first post-refactor CMRU full run was `1864 passed, 10 skipped` but
  failed its coverage threshold at `99.98%` because the new legacy error
  wrapper had no oracle. A focused adversarial assertion was added; the final
  run above is 100%.
- The follow-up review found that CIU's `adopt` path still wrote only the
  compatibility record. It now adopts the checkout through
  `libraries/worktree` too; the existing CIU lifecycle test asserts the
  neutral record, and an adversarial test covers the shared-adoption refusal
  wrapper. The post-closure full rerun is recorded above.
- The final spec wording audit corrected CIU S0/S2.7 so `ciu.env` is described
  as a shell export and “pre-set values win” is limited to ordinary machine
  facts; generated identity/root selection remains authoritative and
  fail-closed against ambient values. The earlier pre-final documentation/spec
  slice was CIU `88 passed` and CMRU `46 passed`; the final rerun above is CIU
  `89 passed`, CMRU `47 passed`, and the shared library `23 passed`.

## Adversarial follow-up (2026-09-22; aggregate gates pending)

This pass reviewed the three specifications against the shipped APIs, then
traced retained-worktree listing through CMRU's automatic-abandon, resume, and
build-discard call paths.

- **CIU specification:** S16 and the shared-inventory references remain in
  sync with the CIU adapter. The full local suite is the behavioral check for
  CIU; the latest cross-document contract slice is `10 passed`.
- **CMRU specification:** S-CLI.4 previously described Git's `prunable` bit as
  current-view visibility and encoded the false status in `visible`. A broken
  linked-worktree `.git` back-link can produce `prunable` while the checkout
  directory and a valid HEAD remain. The CLI now emits `prunable`, preserves
  `source_commit`, and withholds actions without claiming that the path is
  absent. README, DESIGN-GUIDE, CONSUMERS, and contract tests were updated in
  the same change. S-CLI.4/S-CLI.5b now require purpose/branch agreement for
  shared CMRU records and identify shared lifecycle context as the remover's
  authority.
- **Worktree-library specification:** the public API and ownership boundary
  match `worktree.__all__` and implementation. The specification now defines
  `GitWorktree.is_prunable` as Git registration metadata, not a path-existence
  or visibility proof; its consumer and design guides use that same meaning.
  The exact-record lookup shared by CIU and CMRU is now public
  `find_workspace()`, exported and specified as an exact absolute stored-path
  comparison that does not normalize, resolve, or probe the checkout. Consumer
  and design guides document that contract; tests cover exact match, absence,
  alternate spelling, relative input, and duplicate ownership.
- **Lifecycle adversarial finding:** CMRU's inventory-created
  `ReleaseWorkspace` omitted its existing shared record. Automatic abandon
  could then route a new CMRU workspace into `remove_unrecorded_workspace`,
  which is only for pre-library worktrees. Listing now carries the exact
  shared record context, and resume/discard refuse a shared record owned by a
  different product or whose purpose disagrees with its branch. Regression
  tests cover automatic removal, a foreign `ciu`-purpose `cmru-build-*` branch,
  broken Git back-links, and record/branch disagreement.
- **DRY review:** NUL-safe worktree inventory, family discovery, path identity,
  shared records, exact record lookup, leases, and generic
  create/adopt/resume/remove remain in `libraries/worktree`. CIU and CMRU both
  call the library's exact lookup instead of maintaining path-search loops;
  CMRU's handler and tester-gate common-directory lookup delegate to its one
  transaction wrapper. Product-specific CIU root preparation, CMRU release
  policy, and their different long-lived locks remain adapter-owned;
  consolidating those would mix policy, not remove duplicated mechanics. No
  second product porcelain parser or generic Git removal path remains. The
  final pass caught CMRU's path-to-record map masking duplicate ownership;
  uniqueness is now enforced by neutral `list_workspaces()` before any adapter
  can collapse records into a map.
- **Local evidence after these fixes:** CIU `3880 passed`, 100% line/branch
  coverage (11,176 statements / 4,452 branches); CMRU `1885 passed, 10
  skipped`, 100% line/branch coverage (7,633 statements / 2,814 branches); the
  library `58 passed`, 100% line/branch coverage (583 statements / 202
  branches). After the final code/docs edits, CIU cross-document contracts
  passed `10` tests and CMRU config-example/cross-document contracts passed
  `5`; the CMRU worktree/spec regression slice passed `32`. Hypothesis suites
  ran as part of all full suites.
- **External status:** Assay's self-hosted tester-unified gate passed at
  `b9cad87c31f4e55e70b31665e3a32ac4bef1c5f1`, container
  `run-gate-assay-selfhosted-2297444-13689-1790079347`, exit `0`. Its output
  includes an Assay B006(a) CMRU qualification receipt; that is not the final
  CMRU product `assay` lane.

## Product-gate R2 follow-up (2026-09-22)

- The first serialized CMRU aggregate on `84b7919d124684c0a53feb65f5cc8de181c249ff`
  stopped at its first `assay` sub-lane; later CMRU sub-lanes and CIU were not
  launched. Named container `run-gate-vbpub-assay-2475105-1790083264` exited
  `1` with `MUTANTS_SURVIVED`: R0 and R1 passed, R2 considered 71 candidates,
  with 69 killed and two survived; there were no crashes or hangs. The runner
  reported peak memory of 932 MiB and 8 seconds of memory-full stall; this was
  a mutation-oracle failure, not a resource/PID termination.
- The survivors exposed two missing behavioral oracles: CMRU CLI line 2333
  (`And -> Or`) could print a build-discard command for a prunable registration;
  `transaction.py` line 1286 (`Or -> And`) rejected a valid `cmru-legacy`
  record on a transaction branch. Added explicit regressions for withholding
  discard on prunable build inventory and accepting a correctly scoped legacy
  record. The focused regressions passed, followed by CMRU `1885 passed, 10
  skipped` at 100% line/branch coverage (7,633 statements / 2,814 branches).
- The R2 closures are in the next commit; the complete CMRU aggregate and CIU
  R0-R3 gate runs remain pending and will run serially. Local green status does
  not substitute for those external gate results.
- The serialized CIU aggregate on `0da8f1583d990a9eee2d2463c4192c0fd181c100`
  reached R0–R3 in container `run-gate-vbpub-ciu-2734759-1790091744`. R0,
  R1 (100%, 820/820 executable lines and 124/124 branches), and R3 passed;
  R2 killed 64/65 candidates and had one survivor at
  `ciu/src/ciu/worktree.py:4482` (`Or -> And`). The persisted verdict is
  `ciu/.assay/verdict-ciu.json`; the container has since been removed.
- The surviving boolean mutant exposed that the old malformed-root fixture
  made both refusal predicates true. The regression now covers a relative
  existing directory and an absolute missing directory independently, as
  well as a relative missing path. The focused test passes all three cases;
  the new commit and CIU aggregate rerun are pending. CMRU's aggregate passed
  on the immediately preceding code commit; both product gates will be
  serialized again on the final reviewed head before merge.
