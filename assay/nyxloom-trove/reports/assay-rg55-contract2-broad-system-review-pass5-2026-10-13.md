# Release review, pass 5: contract-2 wave (Assay, Run-Gate, CMRU, cgprofile)

**Recommendation: still don't ship.** B2, H-A, M-A and B164 are fixed in source, but three new High findings block the release:
- a plain `cmru release` would publish Assay's schema hard cut as 9.1.0, not 10.0.0;
- Run-Gate's new ERROR mapping can hide a real R1/R3 failure;
- the end-to-end tests that the B163 backlog entry describes don't exist.

The B110 and B131 pilots can't run yet: the host observer directory is missing and the 2 GiB headroom check has kept refusing them.

**Bound to:** HEAD `336336d81b9af9fd7d7a9ad3a4a272ca01c66071` (`chore: integrate contract-2 release wave`), tracked diff sha256 `e7df978b908ddb45aa11504bc3905eb1cb224e47dcd0781207cd7ef4b58c123d` (126 files). There are also 10 untracked files, including `assay/src/assay/process_tree.py`, its tests and `mdt-cgprofile.sysusers`.

I only read git history and files. I also ran read-only `ls`/`stat` on `/run/cgprofile` and `/var/run/docker.sock` from the devcontainer. Nothing was edited, run, gated or committed.

## Checked and fixed [C]

| Item | Evidence |
|---|---|
| **B2.** No unscoped breaking subject remains | There is no `type!:` or `BREAKING` subject between the merge-base `aa41988e8` and HEAD. CMRU KI-71 is now filed (`cmru/KNOWN_ISSUES_TODO_BACKLOG.md:1814-1822`). |
| **H-A.** Only the pre-candidate check emits `NO_MEASUREMENT/CGROUP_OBSERVATION_UNAVAILABLE` | The only place it is produced is `mutation.py:2933-2934`, called once before candidates start (`mutation.py:4212-4213`). |
| **H-A.** Mid-campaign observer loss is `ERROR/EXEC_FAILED` | Each candidate's before and after counter reads go through the during-campaign wrapper (`mutation.py:2953-2963`, `4367-4369`, `4394-4399`). So does the zero-duration sample (`2966-2969`, `5097-5101`). |
| **H-A.** Ambiguous counter comparisons are `ERROR/EXEC_FAILED` | `mutation.py:2938-2950` |
| **H-A.** An after-counter failure can't replace a process-tree cleanup failure | The after-read failure is held back, the cause chain is checked for `ProcessTreeError` first (`mutation.py:4404-4417`), and only then is the observer error raised (`4419-4423`). |
| **H-A.** R3 continues only after the pre-candidate reason | `runner.py:5342-5346`. A mid-campaign `ERROR` refuses R3 too, which is consistent. |
| **Run-Gate maps only that reason to ERROR** | `run-gate.py:313-316`. Test: `test_run_gate.py:1351-1360`; `EMPTY_COVERAGE` still maps to FAIL. RG-92 is filed. |
| **M-A.** All three observer launch guards read the host Docker GID numerically, and none skips silently | Run-Gate's `resource_observer_docker_flags` takes the group ID from `os.lstat("/run/cgprofile")` and refuses (`fail_infra`) on error; the old `getgrnam` / `KeyError` skip is gone. `self-qualification-container.sh:60-68` and `tester-unified-gate.sh:58-66` use `stat` and `die` on failure. |
| **B164** | Model check at `process_tree.py:100-103`, verifier check at `verify.py:2304-2311`. Tests: `test_process_tree.py:58`, `test_verdict_conformance.py:1530`. Docs: README:529, DESIGN-GUIDE:951, CONSUMERS:3432. The runtime can't break the rule, because it records each PID as outliving before it signals it (`process_tree.py:366-375`). |
| **Docs** | Assay README:297-300, DESIGN-GUIDE:761-772 and CONSUMERS:436-447 now describe pre-candidate versus mid-campaign correctly. Run-Gate README:146, CONSUMERS:306-326, SPEC:223-226 and DESIGN-GUIDE:238-247 match the code (but see H2). KI-69 and KI-70 statuses are updated. |

The live devcontainer supports reading the GID from the directory. `/run/cgprofile` is `root:docker(994)`, while `/var/run/docker.sock` shows gid 0 and mode 777, so the socket would have been the wrong source.

## High findings

### H1 [C] A plain `cmru release` would version Assay's schema hard cut as a minor (9.1.0)
- `VERDICT_SCHEMA_VERSION = 16` (`assay/src/assay/verdict.py:402`) arrived in `90dc9078d feat: add contract-2 resource event attribution`. That subject is unscoped and not marked breaking.
- No Assay commit since `assay-v9.0.0` has a breaking subject.
- CMRU looks only at commit subjects (`cmru/src/cmru/version.py:72`, `--format=%s`; `274-286`).
- So `cmru release` computes **9.1.0** for a release where `assay verify` rejects v15 verdicts and every saved resume record becomes a cold start.
- Nothing in the backlog, decisions or release notes records `--major` or `--set-version 10.0.0`.
- **User impact:** consumers on a `>=9` floor, dstdns among them, would pick up a hard cut as a feature release.
- **Fix:** release Assay with `--set-version 10.0.0` and write that into the wave's release steps. Alternatively, commit the remaining Assay-only diff as a separate `feat(assay)!:` commit that touches only `assay/`; a mixed commit would hit KI-71.
- **Minor side effect:** the same unscoped `feat:` commit touched the `run-gate.toml` of ciu, cli-extended, nyxloom and others, so those projects would also bump minor if released in this wave.

### H2 [C] Run-Gate's ERROR mapping reads only the rolled-up result, so it can hide a real R1 or R3 FAIL
- **How Assay rolls up:** `NO_MEASUREMENT` outranks `FAIL` (`verdict.py:677-700`), and the top-level reason comes from the first claim with that status (`runner.py` around 2474-2478).
- **Why another claim can fail at the same time:**
  - R2 starts whenever R0 passed, whatever R1's result (`runner.py:4595`);
  - R3 keeps running after observation is unavailable (`runner.py:5342-5346`).
- **What Run-Gate does:** it maps using only the top-level `outcome` and `reason_code` (`run-gate.py:14413-14421`, `310-316`).
- **Example:** R1 coverage `FAIL` (or R3 canary `FAIL`) plus R2 observation unavailable rolls up to `NO_MEASUREMENT/CGROUP_OBSERVATION_UNAVAILABLE`, and Run-Gate reports **ERROR**. Before this change the same verdict was FAIL.
- **Affected lanes:** observer lanes that declare R1 and R2, or R1 through R3 (`pwmcp/assay.toml:44`, `assay/assay.toml:89`).
- **Why this matters now:** `/run/cgprofile/observer` does not exist on this host yet. Every such lane will report observation unavailable until the host is set up, so a real test failure shows up as infrastructure ERROR, which is retried rather than fixed.
- **Docs:** Run-Gate DESIGN-GUIDE:244-245 says the result "does not say that the tested code failed". That isn't true when another claim failed. The only test covers the top-level pair alone (`test_run_gate.py:1351`).
- **Fix:** when the reason is `CGROUP_OBSERVATION_UNAVAILABLE`, also check `claims[]` and `evidence[]`:
  - any other FAIL or INCONCLUSIVE → FAIL;
  - any BUDGET_EXCEEDED → BUDGET_EXCEEDED;
  - ERROR only when everything else passed;
  - a malformed `claims` field → ERROR.

  Add tests for R1 FAIL, R3 FAIL, and all-pass, each combined with R2 unavailable.

### H3 [C] The tests that B163's oracle describes don't exist
- The backlog's B163 entry says three cases are proven to produce infrastructure errors with no candidate bucket:
  - observer failure inside a candidate window;
  - an unattributable parent event;
  - an after-read failure combined with a process-tree cleanup failure.
- The only tests are helper-level (`test_mutation_resource_limits.py:1179-1232`).
- Nothing drives `run_mutation` through either call site. Grep finds no test for "could not prove an empty", `resource_observation_error`, or the precedence order.
- Two regressions would therefore pass every test:
  - changing `mutation.py:4368` or `4396` back to `_read_candidate_resource_counters()`;
  - moving the raise at `4422-4423` above the process-tree check.

  Either would bring back the exact pass-4 defect.
- **Fix:** add three `run_mutation`-level tests: an observer read sequence that fails at candidate 2; a parent-event `between()` refusal; and a runner that raises `ProcessTreeError` while the after-read also fails, asserting the process-tree message wins and no bucket is recorded.

## Medium findings

- **Shell GID guards are only checked by text matching.** `test_distribution_gate.py:151-157` asserts strings in the scripts. No test proves that equal GIDs cause `die`. Run-Gate does have behavioral tests (`test_run_gate.py:8170`, `8258`).
- **The Docker GID check relies on how `/run/cgprofile` is set up.** It proves "observer GID ≠ the group of `/run/cgprofile`", which equals the docker group only while tmpfiles keeps that directory `root:docker`. If its group drifted (for example to `root:root`), the check would compare against the wrong value without any error. A cheap guard would be to also require that GID to be non-zero and the directory mode to be `0770`.
- **M-D is still open.** The library precondition (the calling process must have no children, and becomes a process-wide subreaper) and the runner-selection API change appear nowhere in README, DESIGN-GUIDE or CONSUMERS.
- **Carried over, not re-reviewed:** H-B (unmeasured 2g caps versus the memory `max` → crashed rule), M-B (fixed 30 s reconnect window), M-C (a leak during the baseline destroys R0).
- **Low:** if both an execution error and a comparison failure occur, the comparison error is raised without `from execution_error` (`mutation.py:4434`), so the original error is lost from the diagnostics.

## B110 and B131 bounded pilots
Both are blocked on host prerequisites:
- `/run/cgprofile/observer` is absent right now, so observer lanes will refuse, safely.
- Every retry so far has been refused at the 2 GiB `dev-gates.slice` headroom check, with 1.0–1.3 GiB available (B131 entry, `4-backlog.md:11872`). I couldn't re-measure the headroom from the devcontainer.

**B110.** The plan is consistently amended for one worker (§7 amendment, §8.1, §9.3, P7 steps 3 and 6).
- As a measurement it is feasible: about 70 candidates run one at a time.
- The GO result is arithmetically out of reach: 3,760 candidates × at least 9.4 s ≈ 9.8 h, against the 5 h p90 threshold. The pilot can only record NO-GO and send the decision to the operator.
- One stale leftover: the P7 report template's shard-skew section still says "N=3".

**B131.**
- It is still marked "included in the current release wave" (`4-backlog.md:11874`), but it is non-qualifying.
- Take it out of the release criteria and run it after release, once the host is set up and headroom exists.
- Serially, about 317 candidates should fit within about an hour [H].

## Remaining release blockers, in order
1. Fix H2 and H3, and decide the Assay version route for H1.
2. Commit the 126 tracked files plus the untracked `process_tree.py`, its tests and the sysusers file.
3. Run the gates at the exact tip.
4. Operator-authorized mdt host-setup install, then redeploy cgprofile 1.0.0 and confirm the observer path is advertised.
5. Release Run-Gate, then Assay 10.0.0 via `--set-version`, then CMRU.
6. Run the pilots after the release as measurements.
