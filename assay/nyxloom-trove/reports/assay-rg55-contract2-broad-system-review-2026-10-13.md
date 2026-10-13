# Read-only release-wave review: Assay contract-2 + RG-55 / cgprofile contract-2

**Bound to:** branch `cgprofile-cp20-contract2-20261012`, HEAD `3f3012fcd2cdf1474b7a50d916fe38960e205507`, tracked-diff sha256 `118a723ee62e8a9216489a6b37607e339ef9b5254e4c6057420cc4356f07bd51`. That hash is the one you gave me: I ran no commands, so I could not recompute it. Everything below comes from reading files as they are now, including the untracked `assay/src/assay/process_tree.py` and `assay/tests/core/test_process_tree.py`. No tests, gates or commands were run, and none of this assumes the running suite will pass.

**Verdict: not releasable yet.** The process-tree window closes the leak class H1 and H2 described, but it introduced a regression in how Assay stops when terminated. It also creates a mismatch where Assay can write a verdict that its own verifier rejects. Separately, the observer socket's deployment lifecycle and the lack of per-container limits make attribution fragile in a shared slice. Two release-mechanics blockers from earlier reviews are still open.

Labels: **[C]** = confirmed by reading the source. **[H]** = hypothesis that needs a probe.

---

## Blockers

### B1 [C] Signals stop the run only when the liveness runner is in use
- `ProcessTreeProcessRunner` (`process_tree.py:437-500`) never calls `liveness.register_live_group`. The plain runner it replaces did (`runner.py:417`), and so does `LivenessRunner` (`liveness.py:1718`).
- The SIGTERM handler (`cli.py:552-574`) only kills registered process groups and never raises, so `proc.wait(timeout=…)` in `process_tree.py:481` keeps blocking.
- **Affected:** the coverage baseline, the r2-baseline, candidates without liveness, and the canary, in every native R2 lane (`runner.py:3996-3998`, `4190`, `4906`, `4980`, `5409`). This includes the ~20-minute baseline of lanes that do use liveness.
- **Effect:** the B117 guarantee ("terminate, kill children, write an incomplete verdict") is lost on those paths.
  - Under a Run-Gate cancel or `docker stop`, the container is SIGKILLed after the grace period and no verdict is written.
  - With `budget = "unbounded"`, a SIGTERM is ignored until the command finishes.
- **Why the tests miss it:** the only real-process termination test uses `liveness = true` (`test_campaign_termination_real.py:106`).
- **Fix:**
  - register the leader's group right after `spawn` and unregister it after `finish()`;
  - make the wait loop check `termination_requested()`;
  - add termination tests for a `liveness = false` candidate and for SIGTERM during the R0 baseline.

### B2 [C] One commit marked breaking bumps unrelated projects to a major
The `feat(assay)!:` commit `3f3012fcd` touches run-gate-project, cmru, cgroup-profiler and nine `run-gate.toml` files. CMRU decides bumps from commit subjects only, filtered by path (`cmru/src/cmru/version.py:72`, `:274-283`). Result: Run-Gate 24.0.0, CMRU 7.0.0, and majors for ciu, cli-extended and nyxloom.

Fix: reword or split the commit while it is still unpushed, or pass `--set-version` on every release. Also file a CMRU known-issue: breaking-change detection ignores the commit scope.

### B3 [C] KI-30 will refuse two releases
`run-gate-project/CHANGES.md:9` and `scripts/cgroup-profiler/CHANGES.md:9` still have hand-written `[Unreleased]` bodies, which `cmru/src/cmru/changelog.py:341-348` refuses.

### B4 [C] The new module is untracked, and nothing has run at the exact tip
- `process_tree.py` and its tests are untracked, but `runner.py:152`, `mutation.py:145`, `liveness.py:96`, `verify.py:97` and CMRU's `mutation_campaign.py:128` all import it. A commit that leaves it out breaks every import.
- B162 and KI-70 both say "exact-tip gate pending".
- Fix: commit per package, including the three review reports and CP-23, then gate the exact tip.

---

## High

### H1 [C] Assay can write a "killed" result that its own verifier rejects
- A leader killed by a signal with no timeout and no cgroup event (SIGSEGV, SIGABRT, a test that runs `os.killpg(0, SIGTERM)`) returns a negative exit code.
- `_execute_plan_inner` turns that into FAIL (`runner.py:1477-1531`), and the live classifier (`mutation.py:4244-4269`) maps FAIL to `killed`. Neither checks `leader_status`.
- But the resume validator (`mutation.py:2295-2300`) and `verify.py:2318-2325` both require `leader_status == "exited"` for killed, survived or equivalent.
- **Effect** (lanes without cold witness):
  - the verdict is written but `assay verify` fails;
  - the state record is never reused, so the same candidate re-runs on every resume.
- CMRU is consistent here (it maps a negative exit code to `crashed`).
- **Fix:** choose one rule and enforce it in all three places. Recommended: any leader that didn't exit normally is `crashed`. Add a test that combines a signalled leader with `assay verify`.

### H2 [C config, H frequency] Without per-container limits, a limit hit can't be pinned on the candidate, and one bad mutant can abort neighbouring lanes
- Observer containers get no `--pids-limit`: neither Run-Gate's container command (`run-gate.py:11796ff`) nor Assay's own launcher (`assay/tools/self-qualification-container.sh:528-541`) sets one. Only acceptance probes do (`tester-unified-gate.sh:1061`, `:1138`).
- So PID exhaustion is enforced at a shared parent slice. That raises the parent's local `pids.events max` while the candidate's own counter stays zero, which B155 treats as ambiguous and aborts the lane (`resource_limits.py:343-349`). The same applies to memory for lanes with no memory limit.
- **The check is narrower than the claim:** the B145 low-PID probe proves "a candidate exhausting PIDs is recorded as crashed" only for a container with its own PID limit, which production containers lack.
- **Blast radius:** one fork-storm mutant hits `dev-gates.slice`'s limit, and *every* concurrent observer lane in that slice sees the same unattributable parent event and aborts.
- **Fix:**
  - require a per-container PID limit and memory limit on every `resource_observer` lane: Run-Gate refuses such a lane without them;
  - add `--pids-limit` to Assay's launcher;
  - document that attribution relies on the container's own limits.

### H3 [C] Restarting the cgprofile daemon permanently breaks running observer lanes
- The daemon deletes and recreates the socket file on every start (`serve.py:2459-2462`) and deletes it on shutdown (`serve.py:2618-2620`).
- Run-Gate (`run-gate.py:7028-7030`) and Assay's launcher (`self-qualification-container.sh:480`) bind-mount the socket **file**, so running containers keep pointing at the old, dead socket.
- **Effect:** after any `ciu up`, image update or daemon crash, every running observer lane fails its next counter read with ECONNREFUSED. Multi-hour R2 lanes have to be restarted.
- **Fix:** keep the observer socket in its own directory and bind-mount that directory (the control socket stays unmounted); or have the listening socket survive restarts via systemd socket activation.
  - Operational rule meanwhile: don't redeploy cgprofile while observer lanes are running.
  - Run-Gate's start-of-lane check (`run-gate.py:7037-7061`) can't catch this.

### H4 [C] The process-tree window is unsafe for embedded or multi-threaded callers
- The window is process-wide. It proves "no children" with `waitid(P_ALL)` (`process_tree.py:165-174`), then SIGKILLs and reaps *every* direct child of the process that procfs reports (`:344-371`). The lock only serialises windows against each other, not against other `subprocess` calls.
- If another thread spawns a child during a window:
  - that child is killed and counted as a candidate leak, so the candidate becomes `crashed` (wrong attribution);
  - its exit status is reaped by the window, so the other thread's `Popen.wait` gets ECHILD. In my understanding of CPython, `Popen` then reports `returncode = 0` — I didn't check the stdlib source — which would make the embedding application see success for a process that was killed.
- A child that already exists before entry makes the window refuse to start, which is the safe outcome.
- **Today's CLI and CMRU are safe.** In Assay, no thread spawns during a window: the heartbeat, witness drain and observer-preflight threads only do I/O, and git/integrity checks run outside windows. CMRU's campaign is single-threaded.
- **But `run_mutation` is a documented library entry point** ("Direct library callers…", `mutation.py:3461-3467`).
- **Fix:** see design alternative A (an isolation helper process). At minimum, document that callers must not spawn or reap children concurrently, and that the subreaper flag stays on after a cleanup failure (`process_tree.py:408`).

### H5 [H] Events at a shared parent cgroup still abort the whole lane
This is carried forward and the logic is unchanged. Resume now limits the damage, but each abort re-runs the baseline. How often the `max` limit on `dev.slice`/`dev-gates.slice` fires because of cockpit or sibling load is unmeasured.

Fix: retry the ambiguous candidate a bounded number of times inside the lane, then fail. Run a probe with a sibling filling the slice's memory limit.

---

## Medium

- **M1 [C] Leftover processes now fail the baseline (R0) of R2 lanes, and the diagnostic is generic.**
  - `runner.py:1440-1463` returns ERROR/EXEC_FAILED for *any* baseline that leaves a descendant, including an orphan that had already exited and was only reaped (`process_tree.py:349`, `:371`).
  - The same command still passes as an R0-only lane.
  - The claim carries no text saying "left N descendants".
  - [H] On Python 3.14, the tester image's version, the default start method is `forkserver`. Its helper processes (forkserver, resource tracker) can outlive pytest briefly, so any consumer suite that uses multiprocessing could be refused at the baseline.
  - Fix: add specific claim detail, a migration note in the 10.0.0 CONSUMERS docs, separate "alive at sweep" from "already exited" in the evidence, and a probe.
- **M2 [C] The runner API changed silently.**
  - Native R2 now requires the runner to set the private attributes `process_tree_evidence` and `assay_*_dropped_bytes` on its result; otherwise it refuses (`mutation.py:4389-4395`).
  - Isolation is selected with `process_runner is default_process_runner` (`runner.py:3997`, `mutation.py:3466`), so wrapping the default runner (for logging, say) loses isolation and then refuses.
  - Fix: a typed result protocol, plus documentation of the break in the 10.0.0 notes.
- **M3 [C] The new tests are thin and timing-dependent.**
  - `test_process_tree.py` has two behavioural tests, and both run windows inside the pytest process.
  - The timeout test uses `timeout=2` against a child that may take up to 5 s to write its PID file (`:92-124`), so it can fail under host load (contention doctrine).
  - Missing tests:
    - refusal when children already exist at entry;
    - cleanup deadline failure;
    - procfs fallback;
    - signalled leader;
    - a double-fork chain;
    - a concurrent spawning thread;
    - termination;
    - an end-to-end lane without liveness.
- **M4 [C] The B110 claims are internally consistent but can't be met in this wave.**
  - A-490 and A-491 correctly make pilots non-qualifying, and P8 always reports `qualifying: false`.
  - But at one worker, about 3,760 candidates × 9.4–13.4 s of fixed cost each comes to more than 9.8 h, against a GO threshold of 5 h at p90. The pilot can only confirm NO-GO.
  - D10 (`assay-B110-PLAN-2026-09-28.md:125`) and `P7-pilot-tooling.md:80` still allow 1–8 pilot jobs.
  - Fix: record a D-decision that B110 qualification is out of scope for this release, and don't make the release wait for pilots.
- **M5 [C] The real failure cause can be hidden.** The parent-attribution error is computed before the stored `execution_error` is raised (`mutation.py:4375-4387`). A cleanup failure can therefore surface as an attribution error.
- **M6 [C] Candidates can reach the observer.** The whole container gets the observer's group (`--group-add`), so mutated test code can flood the shared daemon. There is no rate limit.

## Low
- `ProcessTreeEvidence.clean` can only ever be `True`.
- `process_tree.py:501-502` is a no-op `except: raise`.
- `listening` is reported once the socket binds (`serve.py:791`).
- The old `liveness._cleanup_process_group` looks like dead code.
- The plain runner still sends a kill to the process group after reaping the leader (non-native lanes only; low risk).

## Resolved since the last review [C]
- **Previous H1:** every native execution now goes through the window, as long as the default runner is used.
- **Previous H2:** a cleaned leak becomes a resumable `crashed` candidate, in Assay (`mutation.py:2224-2234`, `4244-4250`) and in CMRU (`mutation_campaign.py:224-226`).
- **Previous M1 and M2:** procfs gaps are tolerated, and the empty-tree proof comes from the kernel's ECHILD answer.
- **Previous M6:** CMRU's resume checks the process-tree evidence (`mutation_campaign.py:497-511`).
- **CMRU docs:** README, DESIGN-GUIDE, CONSUMERS and SPEC are in sync with schema 4.
- **PID reuse:** the window signals only children that haven't been reaped, and the process group only while the leader is alive.
- **The two questions you asked about directly:**
  - *Is correctness evidence separated from profiling?* Yes. Resource and process-tree evidence decide the outcome; liveness CPU and PSI are diagnostic only. They are coupled only operationally, because the observer lives in the profiler daemon.
  - *Do sibling events get mis-attributed?* No. A sibling hitting its own limit shows up only in the parent's hierarchical counters, which are correctly ignored.

---

## Design alternatives
- **A. An isolation helper process (recommended for H4 and B1).**
  - Assay runs `python -m assay._isolate -- argv`. The helper makes *itself* the subreaper, runs the window, forwards SIGTERM, and reports evidence as JSON on an inherited file descriptor.
  - Assay waits only on the helper's exact PID; if the helper dies abnormally, that is a cleanup failure.
  - This removes the global lock and global state, makes it safe for embedding applications, and gives one termination path.
- **B. Per-container limits now (H2).** Cheap and high-value: it turns PID or memory exhaustion into a recorded candidate result instead of a lane abort, and stops one bad lane affecting its neighbours.
- **C. Fix the observer socket lifecycle (H3)** with a dedicated mounted directory or socket activation, plus a drain rule for deploys.
- **D. Per-candidate cgroups (CP-23, next wave).** True attribution, `cgroup.kill`, and later parallel R2. This is what would make B110 reachable.
- **E. Retry ambiguous candidates** a bounded number of times before failing the lane (H5).

## Recommended release order
1. Fix B1–B4 and H1. Then fix H2 and H3, or record a D-decision for each; document H4 at minimum. Commit per package.
2. Rebuild tester-unified and record its image ID.
3. Run cgprofile `r0-r1` and `r3` (they don't need the observer). Release cgprofile 1.0.0 with `--set-version`, pin it, and run `ciu up`. Verify `version.resource_observer = {2, path, listening}` and the image's OCI revision label against the release tag.
4. Run the live acceptance probes:
   - restart the daemon while an observer lane is running;
   - run two concurrent observer lanes, one with a fork storm;
   - send SIGTERM during the baseline and during a candidate without liveness (an incomplete verdict should appear within seconds);
   - run a py3.14 multiprocessing suite through the baseline.
5. Run the Assay `tester-unified` gate plus the PostgreSQL hazard set, and the Run-Gate selftest (its `assay-r2` lane needs step 3). Merge the integration branch.
6. Release Run-Gate 23.11, then Assay 10.0.0 (BREAKING notes: v16 verdicts, judge identity `/12`, the R0 leak refusal, the runner API change). Each release uses an explicit version.
7. Run the CMRU `gate`: its mutation lane needs the deployed daemon and Assay's process-tree code from the same worktree. Release CMRU 6.2 with `--set-version`.
8. Install in the cockpit venv and notify dstdns.
9. B110 and B131 pilots afterwards, as measurement only.
EXIT=0
