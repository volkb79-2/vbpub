# Claude Opus 5.5 high review — contract-2 release wave

- Session: `b4dd4947-ac2e-4e2f-a20f-8eaec30a61ac` (retained for follow-up)
- Review route: Claude Opus 5.5, high
- Review exit: 0
- Reviewed worktree: `.worktrees/cgprofile-cp20-contract2-20261012`
- HEAD: `336336d81b9af9fd7d7a9ad3a4a272ca01c66071`
- Tracked diff SHA-256: `8b894b6ddee69075a925d8c2e77e38d39f35d730b0d791dafe56e8ae6773eb00`
- Snapshot check: the current `git status --short` exactly matches `status.before`; HEAD and tracked diff also match the review snapshot.
- Review limit: Claude's Bash tool was denied, so it read source files directly and relied on the snapshot records. The caller independently verified HEAD, status, and tracked diff after completion.

---

# Release-readiness review: contract-2 wave (Assay 10, Run-Gate RG-55, cgprofile 1.0.0, CMRU KI-72)

**Decision: HOLD.** The core design is sound and most findings from earlier passes are now fixed in source. What remains: the candidate is uncommitted, the CMRU KI-72 gate refuses in this worktree today, the release order has a dependency cycle, and there is no host acceptance evidence yet.

**Limits of this review.** Bash was denied this session, so I could not run `git diff`, `git status` or fingerprints. I read files directly and bound the review to the caller's records:
- HEAD before is `336336d8`. It is an amend of `3f3012fc`, and the subject is now `chore: integrate contract-2 release wave`.
- The file list comes from `/tmp/assay-rg55-final-review-2026-10-13/status.before`.

Nothing was edited, tested, gated, installed or deployed, and no messages were sent.

## 1. Is the mutation verdict separated from resource measurement?

Not in the way the request describes. In the code, the observer is a precondition for native R2, not optional:
- `mutation.py:4212-4213` probes before the first candidate.
- `mutation.py:2923-2935` turns an unavailable observation into R2 `NO_MEASUREMENT/CGROUP_OBSERVATION_UNAVAILABLE`.
- R0/R1 are kept, and independent R3 continues (`runner.py:5341-5347`).
- Without observation there is no R2 claim at all. The docs say the same thing (`assay/docs/CONSUMERS.md:296-449`).

Resource observation does affect kill counting, but only in one direction. A candidate-attributable event turns killed/survived into `crashed` (`mutation.py:3075-3081`, `:4288`), and any `crashed` makes R2 ERROR. Observation can never produce a kill or a survivor. So it cannot cause a false green, only a false red or ERROR. That is the right asymmetry. Making correctness-only R2 work without an observer would re-open B145, where a refused fork gets scored as a kill. I recommend keeping the current contract and correcting the operator-facing description.

**Every ambiguous or unknown state, and what it produces:**

| Condition | Result |
|---|---|
| Hierarchy not visible before candidates start | R2 `NO_MEASUREMENT`; Run-Gate `ERROR` only if every other claim passed (`run-gate.py:316-391`) |
| Observer lost, hierarchy/limit/mount changed, or ancestor-local event with a quiet leaf, during a candidate | `ERROR/EXEC_FAILED`, lane stops, resumable (`mutation.py:2953-2963`, `:4421-4443`; `resource_limits.py:346-352`) |
| Event at the candidate leaf, including a reclaim-only `memory.events:max` | candidate `crashed` → R2 ERROR |
| Ancestor-local event and leaf event together | blamed on the candidate (conservative) |
| Hierarchical-only ancestor change (a sibling hit its own limit) | ignored — correct |
| Leaked descendant that was cleaned up | `crashed`, campaign continues |
| Cleanup could not be proven | lane-fatal; the process is poisoned for later windows (`process_tree.py:312`) |

## 2. Findings, most severe first

### Blockers

**B1. The candidate cannot be gated as an exact tree.**
- Required inputs are untracked: `assay/src/assay/process_tree.py`, its tests, `assay/tools/resource-observer-gid.sh`, `cmru/tools/check_assay_source.py`, and the mdt `.sysusers` and observer `.conf` files.
- The self-hosted gate builds from an exact-OID clone of HEAD (`assay/run-gate.toml:3-8`), so today it would test a tree without process-tree isolation.
- **Fix:** commit per package, then gate that exact tip.

**B2. CMRU's pre-install check refuses ordinary ignored files (confirmed in this worktree).**
- `check_assay_source.py` runs first in CMRU's coverage, mutation and canary lanes (`cmru/run-gate.toml:57`, `:110`, `:146`).
- It calls `_assay_install_tree_status` (`mutation_campaign.py:647-663`), which lists every ignored file under `assay/`.
- The only ignored files it accepts are egg-info, `__pycache__/*.pyc`, `.pytest_cache` and `.coverage*` (`:589-607`).
- `assay/.hypothesis/constants/*` and `unicode_data/*` exist here now. `assay/.gitignore` also ignores `coverage.json`, `build/`, `dist/` and `htmlcov/`, none of which are accepted.
- So the KI-72 lanes refuse in this worktree with "Assay source must be committed".
- **Plausible, not confirmed:** CMRU's own coverage lane runs Hypothesis in `cmru/`. Hypothesis may write a `constants/` cache even with `database=None`, which would then refuse the mutation step that follows it.
- No test builds this state: `test_mutation_campaign.py:188-213` covers only `.assay`, `.run-gate` and `__pycache__`.
- KI-72's text (`cmru/KNOWN_ISSUES_TODO_BACKLOG.md:1828`, `:1830`) promises a narrower refusal than the code enforces. This is the "superset refusal" pattern from AGENTS.md.
- **Fix (preferred):** install Assay non-editably from a `git archive <assay_source_commit>` export, so ignored files cannot take part at all.
- **Fix (alternative):** restrict the ignored-file refusal to what setuptools reads or executes: package roots, `pyproject.toml`, `setup.py`, `setup.cfg`, `MANIFEST.in`, and `*.pth` files.
- Either way, add a test with `.hypothesis/constants/x` and `coverage.json` present.

**B3. The release order has a dependency cycle.**
- Building cgprofile 1.0.0 requires the KI-69 release identity that only the new CMRU passes (`scripts/cgroup-profiler/build-push.py:163`).
- CMRU 6.2's release gate includes the mutation lane (`run_release_gate.py:572-595`), which requires a contract-2 observer: the lane opts into the observer (`cmru/run-gate.toml:102`) and the campaign refuses without counters (`mutation_campaign.py:1071-1095`).
- Assay's release gate also requires the observer (`assay/tools/tester-unified-gate.sh:1471`).
- KI-71's scope-aware bump (`version.py:292-327`) protects only a release that the new CMRU executes. The installed CMRU is still scope-blind.
- **Version-calculation risks:**
  - HEAD's subject is now `chore:`. Unless the final commit carries `feat(assay)!:` or `--set-version 10.0.0` is passed, the release will not be 10.0.0.
  - cgprofile has no previous tag, so `messages=[]` and the version takes the patch path (`version.py:643-655`). It needs `--set-version 1.0.0`, the same trap as the earlier nyxloom v1.0.0 incident.
- **Fix:** follow the sequence in §5. Bootstrap the candidate CMRU from the exact commit and use it to drive every release.

**B4. No host acceptance evidence.**
- Missing: the mdt observer install, the daemon redeploy, `version.resource_observer = {contract: 2, path, listening: true}`, and a live probe from an observer lane.
- The daemon only logs a failed observer bind (`serve.py:2417-2426`). Its socket only exists once host setup has created the directory and the daemon is restarted after that.

### High

**H1. Reclaim-only `memory.events:max` events can make long R2 runs unqualifiable.** This is a hypothesis, unmeasured.
- The kernel counts a `max` event on every charge that reaches the limit, including ones that succeed by reclaiming clean page cache.
- Every lane uses an unmeasured 2 GiB cap. Per-candidate snapshots and pytest file reads can fill that cap with cache, after which every later candidate becomes `crashed` and R2 ERROR.
- CMRU goes further: one such event stops the whole campaign (`mutation_campaign.py:1339-1351`). A deterministic resource-limited mutant would block it on every resume.
- Partial counter-evidence: the superseded contract-1 cgprofile segment saved 581 records and reported no crashes (`4-backlog.md:12459-12462`).
- **Acceptance:** the exact-tip R2 runs and pilots must report how many candidates had `memory_events_max>0` and `oom=0`. If that count is not zero, record a decision before qualification (more headroom, or a confirmation rerun in a fresh container).

### Medium

- **M1. Run-Gate's SPEC inverts a security property.** `run-gate-project/SPEC.md:2469-2471` reads "It does mount `ctl.sock`, grant the Docker socket…"; the code and every other document say it must not. Fix the wording.
- **M2. The kernel requirement is undocumented.**
  - `_effective_local_events` refuses when `pids.events.local` is missing and the mount has no `pids_localevents` option (`resource_limits.py:765-770`).
  - I believe that file only exists on roughly 6.13+ kernels. Please verify, because stock Debian 13 (6.12) may lack it.
  - Assay 9 worked with hierarchical-only counters, so Assay 10 silently drops native R2 to `NO_MEASUREMENT` on older kernels.
  - **Fix:** document the floor in README and CONSUMERS.
- **M3. One stalled client can fail other lanes.**
  - The observer serves one connection at a time on a single accept thread and waits up to 5 s for each request (`serve.py:3014-3024`, `:3050`).
  - The client gives each attempt 5 s and retries only `ENOENT`/`ECONNREFUSED` (`resource_limits.py:1066-1077`).
  - So one stalled client can make concurrent observer lanes fail mid-campaign. That includes mutated test code, because `assay.toml:58`, `97`, `208`, `317` and `365` pass the socket into test environments.
  - **Fix:** serve connections from a small thread pool or use a shorter server read deadline, and retry timeouts and connection resets within the 30 s window.
  - **Deferral is acceptable** while only one observer lane runs at a time, which is already the host load rule.
- **M4. Shared `run-gate.toml` edits widen the release scope.** ciu, cli-extended, nyxloom, pwmcp and mdt each become at least a patch release (`version.py:616-657`), and their gates now need the observer. Decide this explicitly with a `cmru status` preview, rather than discovering it during the release.
- **M5. Contract-2-only is a synchronized cutover.** Once cgprofile 1.0.0 is deployed, any Assay 9 observer user, including the cockpit's installed Assay, gets refused. Install Assay 10 in the cockpit in the same step as the daemon deploy.

### Low

- The preflight join timeout (6 s) is shorter than the restart-retry window (30 s), so a daemon restart reads as `NO_MEASUREMENT` at preflight (`resource_limits.py:49-51`, `:1327-1337`).
- `_copy_frozen_fixture` is defined twice (`mutation_campaign.py:714`, `:758`).
- The exact Hypothesis 6.169.0 and pytest-cov 7.1.0 pins (`assay/pyproject.toml:42`) are a principled exception, because the cold-witness allow-list admits exact digests. But they freeze the whole tester image and need the reason stated next to the pin, per the version policy.

## 3. Verified as fixed since the last pass

- **Mid-campaign `NO_MEASUREMENT` (H-A, B163):** now `ERROR`, and Run-Gate's mapping preserves other claims. The tests at `test_run_gate.py:1351-1419` are real behavioural oracles.
- **Docker GID (M-A):** derived from the host `/run/cgprofile` directory instead of the container's group table (`run-gate.py:7111-7158`, `resource-observer-gid.sh:56-80`).
- **Scope-blind bumps (B2 from pass 4):** fixed in CMRU as KI-71.
- **Impossible process-tree evidence (B164):** refused (`process_tree.py:101-115`).
- **Earlier KI-72 findings:**
  - Run-Gate state and coverage outputs are excluded from the campaign check (`:308-330`).
  - Fixtures are frozen and copied from tracked files only (`:1044-1061`, `project_fixture.py:95-141`).
  - Candidates are checked before and after each run (`:1181-1198`, `:1303-1319`).
  - Bytecode goes to private caches (`:146-148`, `:939-947`).
  - The source check now runs before the import (`:972` vs `:988`).
  - Resume is validated before the baseline (`:1028`).
  - Symlinked root components are refused (`:543-553`).
  - The version field is renamed `assay_distribution_version`.

## 4. Design choices that are settled

- Kernel-proven process-tree cleanup in-process: a subreaper, waiting only on known direct children, the ECHILD proof, never `waitpid(-1)`, and an inherited ignored `SIGCHLD` refused.
- Candidate-scope events plus ancestor-local attribution. Siblings are never blamed, and an unattributable event is withheld rather than guessed.
- Serial R2 (`jobs=1`), with parallel work deferred to B165/CP-23.
- A cleaned leak is `crashed` and resumable; an unproven cleanup is lane-fatal.
- A read-only, peer-bound observer on its own socket and dedicated group. A directory bind survives daemon restarts. `ctl.sock` is never mounted, and the Docker GID is excluded.
- The host setup is non-destructive:
  - It adds one sysusers group and one tmpfiles rule.
  - It refuses symlinks and a GID equal to Docker's.
  - It leaves Docker membership and `/run/cgprofile` unchanged.
  - It verifies the result (`install.sh:101-160`).
- The pilots are measurement-only. Under one worker, B110's GO thresholds cannot be met (about 3,760 candidates at about 560 s each). A NO-GO plus the per-candidate cost and event incidence is an honest outcome.

## 5. Shortest defensible sequence to qualify the release

1. Fix B2 (archive-based install, with a test), the SPEC wording (M1) and the kernel-floor docs (M2). M3 is optional.
2. Commit per package with scoped subjects: `feat(assay)!:`, Run-Gate, cgprofile, `fix(cmru):`, and the consumer `run-gate.toml` changes. Bootstrap the candidate CMRU from that commit and use `cmru status` to confirm:
   - Assay 10.0.0, Run-Gate 23.11.x, cgprofile 1.0.0 (`--set-version`), CMRU 6.2.0;
   - the explicit list of any other projects that become patch releases.
3. Rebuild tester-unified from the exact commit. Run the gates that do not need the observer: cgprofile `r0-r1` and `r3`, Run-Gate `selftest`, and CMRU `installed-wheel`, `assay` and `coverage`.
4. **Operator, on the host:** run `install.sh --observer-only` and verify the directory shows `0 <gid> 751` with a GID different from Docker's.
5. Release cgprofile 1.0.0 with the bootstrapped CMRU, update the CIU pin, and deploy. Verify that `version` reports contract 2 and `listening: true`. Run a positive probe (`assay lanes --json` shows available) and negative probes (a contract-1 request is refused; `ctl.sock` is not mounted).
6. Run the exact-tip observer gates one at a time: Assay `tester-unified` and `self-qualification-preflight`, Run-Gate `assay-r2`, and CMRU `mutation`, which produces the KI-70/72 evidence. Either run cgprofile's contract-2 R2 or record an explicit deferral. Report the H1 event incidence.
7. Release CMRU 6.2 → Run-Gate 23.11 → Assay 10.0.0. Install them in the cockpit in the same step as the daemon cutover, then notify dstdns.
8. Afterwards, run the B105 and analysis pilots one at a time, as measurements only.
