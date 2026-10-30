# Assay, Run-Gate, CMRU and cgprofile system review — 2026-10-13

- Reviewer: Claude Opus 5.5, high effort, read-only review
- Persistent Claude session: `28227ce4-cbdc-471a-8b69-a430af63fb67`
- Bound checkout: `/workspaces/vbpub/.worktrees/cgprofile-cp20-contract2-20261012`
- Bound HEAD before/after: `3f3012fcd2cdf1474b7a50d916fe38960e205507`
- Working diff SHA-256 before/after: `b2a6c5601bd1e2a537e919a8a01ceaa5a9cbefa392a3e49abd4a0caa01067113`
- Review transcript: `/home/vscode/.claude/projects/-workspaces-vbpub--worktrees-cgprofile-cp20-contract2-20261012/28227ce4-cbdc-471a-8b69-a430af63fb67.jsonl`
- Condensed plan: `/home/vscode/.claude/plans/you-are-performing-a-functional-fern.md`
- No tests, gates, or containers were run by this review.

---

This was a read-only review of HEAD `3f3012fcd` plus the uncommitted delta: 25 modified files, including the new subreaper code in Assay and CMRU, and 2 untracked files. HEAD exists only locally; no remote branch contains it. I changed no files except the plan file, and ran no tests, gates or containers. Every gate status below comes from backlog text, not from a run.

There are three release blockers. Two are cheap to fix before merge. The bigger design problems are in the new subreaper work.

## Blockers

**1. The `feat(assay)!:` commit makes CMRU release unrelated projects as major versions** (code defect plus a CMRU design gap; blocks release and merge).
- CMRU decides the version bump from commit subjects only, filtered by path, and ignores the scope (`cmru/src/cmru/version.py:72`, `:274-283`). Commit `3f3012fcd` touches run-gate-project, cmru, cgroup-profiler and nine `run-gate.toml` files, which are not excluded as release-control files.
- I confirmed it is the only breaking subject since each project's last tag.
- Result: Run-Gate would release as **24.0.0** instead of 23.11, CMRU as **7.0.0** instead of 6.2, and ciu, cli-extended and nyxloom would each get a major bump. cgprofile is unaffected because its first release uses `--set-version`.
- Fix: the commit is unpushed, so split or reword it before merge so that only Assay paths carry the `!`. Otherwise every release needs an explicit `--set-version`.
- File a CMRU known-issue: breaking-change detection ignores the commit scope.

**2. CMRU will refuse the Run-Gate and cgprofile releases** (code defect; blocks release). Both `run-gate-project/CHANGES.md` and `scripts/cgroup-profiler/CHANGES.md` still have hand-written `[Unreleased]` bullets. `cmru/src/cmru/changelog.py:341-348` refuses that (KI-30); both projects use the default `CHANGES.md`. Assay and CMRU already have empty bodies.

**3. Nothing is gated at the exact tip** (missing evidence; blocks merge and release).
- The subreaper work is uncommitted, and HEAD itself has no gate run.
- B162 and KI-70 both say "exact-tip gate pending".
- The observer lanes can't be tested at all until a contract-2 daemon is deployed.
- `CP-23` and the isolation review report are untracked and need to be committed.

## High

**H1. B162's isolation only covers one of the two candidate runners** (code defect, and docs claim more than the code does; should block the Assay release as documented).
- Only `LivenessRunner` uses the subreaper. `runner.py:4831-4852` sends every other candidate through `default_process_runner` (`runner.py:405-432`). That runner pipes output with `communicate()`, kills the process group only after the leader is reaped, and never subreaps.
- **Lanes affected:**
  - pwmcp (`liveness = false`, `pwmcp/assay.toml:86`) and mdt (`modern-debian-tools-python-debug/assay.toml:53`), both of which gained `resource_observer = "cgprofile"` in HEAD;
  - any lane whose command isn't pytest, or isn't Python;
  - **every coverage and R2 baseline**, including those of lanes with liveness enabled.
- **What can go wrong:** a process that detaches from a baseline is re-parented to docker-init, where Assay's "no existing children" check can't see it. It keeps running into candidate windows. Shared `/tmp` or ports can then make a later mutant's test fail, which is a false kill the cgroup counters won't catch.
- **Docs overclaim:** A-492 and B162 say "every native R2 candidate", and so do DESIGN-GUIDE and `docs/CONSUMERS.md:3416`.
- **Validation:** a real `setsid` test on a `liveness = false` lane, and a baseline that leaks a detached process.
- **Fix:** move the isolation into one wrapper that every native-R2 run uses, baseline included; or refuse native R2 without it.

**H2. A cleaned-up leftover process aborts the whole lane, and resume can't get past it** (design defect).
- Assay raises at `liveness.py:1898` and CMRU at `mutation_campaign.py:320` whenever a normal exit left descendants, even after cleanup succeeded. No result is recorded for that candidate, so `--resume` runs the same mutant again and aborts again.
- **Concrete case:** Assay's self-qualification lane (the B110 target) mutates `liveness.py` and `runner.py`. The new tests (`test_liveness_runner_monitor.py:1034`, `:1060`) start `sleep 300` with no `finally` cleanup. A surviving mutant in the cleanup code, such as `and`→`or` at `if not descendants and not direct_children`, leaves the sleeper behind, and the outer Assay run then aborts deterministically.
- **Also too strict:** an orphan that already exited and is waiting as a zombie still counts as a leak.
- **Fix:** once cleanup succeeds, the after-counters are already clean. Record a non-kill `crashed / descendant-leak` result, and abort only when cleanup itself fails. CMRU already contains that branch (`mutation_campaign.py:373-375`), but it is unreachable outside its unit test.
- **Validation:** a mutant fixture that leaks; the lane must complete with a non-kill result.

**H3. Carried forward: events at a shared parent cgroup abort the lane** (missing evidence; availability, not a false pass). `resource_limits.py:343-349` still raises when a parent cgroup shows a local event but the candidate's own delta is zero. The new test makes memory `max` conservatively `crashed`. How often a sibling gate pushing `dev-gates.slice` to its limit trips this is still unmeasured; it needs a probe.

## Medium

- **M1. Some Assay cleanup failures become a per-candidate result instead of a lane abort** (code defect).
  - `_candidate_descendant_pids` (`liveness.py:189`) and `liveness.py:233` call `_pid_children_via_task` without a guard, and it raises `FileNotFoundError` if one of Assay's threads disappears from `/proc/self/task` mid-scan.
  - A likely trigger is the witness drain thread (`mutation_witness.py:256`), which exits when the candidate tree closes its pipe — exactly when cleanup runs.
  - The `OSError` escapes, `runner.py:1406` turns it into a `crashed` candidate, and the subreaper setting stays on.
  - This contradicts A-492. Fix: tolerate vanished threads and wrap every cleanup error as an `AssayError`.
- **M2. CMRU's process scan can report "empty" when it simply can't see** (code defect). `_pid_children` (`mutation_campaign.py:148`, `:159`) treats a missing `children` file as no children. On a kernel without that file, the sweep reports a clean tree. Assay fails closed in the same situation. Fix: probe for the file at startup and refuse if it's absent.
- **M3. Sub-second timing tests will flake under host load** (gate gap). CMRU's `setsid` timeout test uses 0.2 s and needs two Python interpreters to start; Assay's equivalent uses 0.5 s. Under load the PID file may never be written.
  - CMRU's mutation lane runs this test file first (`cmru/run-gate.toml:110`), so a flake fails the release gate.
  - In Assay's self-qualification R2, a flaky test produces false kills.
  - Fix: have the child signal readiness before the timeout is armed.
- **M4. CMRU's docs are out of sync with the new behaviour.** `cmru/README.md:729`, `docs/DESIGN-GUIDE.md:559` and `docs/CONSUMERS.md:727` still describe process-group cleanup on timeout only. None mentions the subreaper, the "leftover process is an infrastructure error" rule, or schema-3 evidence. The estate's doc-sync rule requires all three in this change.
- **M5. Stale statuses and plan text:**
  - B114 (`4-backlog.md:11483`) and B118 (`:11652`) predate the 9.0.0 release.
  - KI-69 still says "open" although `b310458e9` implements it.
  - B110 plan D10 (`assay-B110-PLAN-2026-09-28.md:125`) and P7 §B (`P7-pilot-tooling.md:79-80, 276, 292, 448`) still allow 1–8 pilot jobs, but the CLI refuses anything except 1.
- **M6. CMRU resume doesn't check `descendants_killed == 0`** before reusing a recorded kill, although KI-70 says reuse requires a clean process-tree completion.

## Low

- Dead code in CMRU: the `baseline.descendants_killed` check at `mutation_campaign.py:754`, and the `descendant-leak` branch, whose unit test only exercises an unreachable path.
- `default_process_runner` still sends a kill to the process group after the leader is reaped (`runner.py:430`), so a reused group ID could be hit. This predates the wave.
- In unit tests, the sweep runs inside the pytest process and would kill any other child pytest has at that moment, so tests are coupled to each other's processes.
- The observer's `listening` flag only means the socket was bound.
- CMRU's candidate output file has no size limit on disk; only the returned tail is capped.

## Resolved since the last pass

- Run-Gate now checks the daemon's advertised contract 2, socket path and listening flag before an observer lane (`run-gate.py:7037-7061`, `:11775`).
- Run-Gate's observer check now requires an ephemeral environment rather than the name `tester-unified`, and also runs at config load (`:1730-1761`, with tests).
- CMRU has a dedicated mutation environment.
- The KI-70 false kills are fixed: timeouts and cgroup events are inconclusive.
- The v16 verdict cut is now marked breaking.
- The `assay plan --json` wording is fixed.
- P8's projection-jobs check now applies to all native lanes, and the fixed overhead no longer counts the first candidate twice (`campaign.py:1458-1464`, `1663-1667`).

**Future design, not this wave:** CP-23 (per-candidate cgroups, an observer contract 3, then parallel R2), and scope-aware bump detection in CMRU.

## What blocks what
- **Testing:** blocker 3 only, plus deploying the daemon for observer lanes.
- **Merge:** blocker 1 (reword while unpushed), and committing the delta.
- **Release:** blockers 1–3, H1 (or narrow the documented claim), and M4.
- **Release readiness:** H2 and M1–M3 should be fixed or recorded as explicit decisions before the CMRU and Assay gates are treated as authoritative.

## Release order

1. Fix blockers 1 and 2, plus H1/H2/M1–M4 or record decisions for them.
2. Commit per package, including the two untracked files.
3. Rebuild tester-unified and record its image ID.
4. Run the gates at the exact tip:
   - Assay `tester-unified`, plus the PostgreSQL hazard set;
   - Run-Gate selftest;
   - cgprofile `r0-r1` and `r3`;
   - CMRU `gate`.
5. Merge.
6. Release cgprofile 1.0.0 with `--set-version`, pin it, run `ciu up`, then verify `version.resource_observer` and the image's OCI revision label against the release tag.
7. Run the live observer probes (a command lane and an Assay lane).
8. Release Run-Gate, then Assay 10.0.0, then CMRU, each with an explicit version.
9. Install in the cockpit venv and notify dstdns.

The condensed version of this review is in `/home/vscode/.claude/plans/you-are-performing-a-functional-fern.md`.
