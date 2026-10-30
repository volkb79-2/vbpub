<!-- Persisted from Claude Opus 5.5 high session 28227ce4-cbdc-471a-8b69-a430af63fb67. Session retained for follow-up. -->

# Release review, pass 4: Assay process isolation, observer contract 2, RG-55

**Recommendation: don't ship yet.** The design from pass 3 is now implemented and mostly correct. Four things still block the release:
- The version-bump commit is unchanged.
- The tree is uncommitted and has no gate run at its exact tip.
- The host has to be set up and the cgprofile daemon redeployed (with proof) before any observer lane can run.
- New defect H-A: when cgroup observation fails mid-campaign, the result is reported as "not measured", and Run-Gate turns that into a product **FAIL**.

H-B (memory caps) is mainly an availability risk, but needs measured evidence on this wave's own lanes before release.

**Bound to:** HEAD `3f3012fcd2cdf1474b7a50d916fe38960e205507`, tracked diff sha256 `ed201a05c9614ea70bacd0fe1de1139fd3c855cb01e7844bda76b6f98b1caf2c` (125 files). Untracked: `assay/src/assay/process_tree.py`, `assay/tests/core/test_process_tree.py`, `modern-debian-tools-python-debug/host-setup/units/mdt-cgprofile.sysusers`, CP-23, and five review reports. I re-read the pass-1–3 reports. I used only `git` and file reads; nothing was edited, tested or run.

I couldn't save this review to the plan file: the Write tool is disabled in this session.

**Labels:** **[C]** = confirmed in source, **[H]** = hypothesis.

## Fixed since pass 3 (checked in source)

- **Observer socket now lives in a directory, and the whole directory is mounted** [C]:
  - **cgprofile:** the default path is `/run/cgprofile/observer/resource-observer.sock` (`serve.py:72`). The daemon no longer creates the directory itself, and it checks the directory's owner, group and mode before binding (`serve.py:2426-2535`).
  - **Host setup:**
    - `d /run/cgprofile/observer 0751 root cgprofile-observer` (`mdt-cgprofile.conf`);
    - a dedicated group via `mdt-cgprofile.sysusers`;
    - `install.sh:624-628` creates the group before the directory.
  - **Run-Gate:** mounts the directory read-only (`run-gate.py:372-380`, `7026-7071`).
  - **Assay launcher:** does the same (`self-qualification-container.sh`).
  - **Assay client:** retries connecting to the observer for up to 30 s (`resource_limits.py:1010-1080`).
- **Container limits** [C]:
  - Run-Gate has a new `pids` resource key. Observer lanes must declare `resources.memory` and `resources.pids`, and `RUN_GATE_EXTRA_MOUNTS` is refused on them (`run-gate.py:917-921`, `1045-1050`, `1768-1784`, `7029-7031`, `11869-11871`).
  - Assay's launcher adds `--pids-limit=8192`, and its post-launch container check now verifies it.
- **G1:** `process_tree.py` is now in both Assay target lists (`assay/assay.toml:159`, `:274`) [C].
- **G3:** an unexpected `OSError` from either runner now stops the lane (`runner.py` `except OSError`; `liveness.py` converts it to `ProcessTreeError`) [C].
- **KI-30:** both hand-written `[Unreleased]` changelog bodies are emptied [C].
- **Process-tree tests:** `test_process_tree.py` now has 30 tests. They cover a blind procfs, an existing child at window entry, a child reaped by another waiter, cleanup failures, and failure to restore the subreaper flag [C].
- **A-494 is implemented:** R0/R1 are kept and R3 still runs when R2 can't be observed [C].

---

## Blockers

### B2 [C] One breaking commit subject bumps unrelated projects to a major
- HEAD is still `feat(assay)!:` and no remote branch contains it.
- CMRU decides bumps from commit subjects filtered by path, and ignores the scope (`cmru/src/cmru/version.py:72`, `:274-283`). So Run-Gate, CMRU, ciu, cli-extended and nyxloom would all get major versions.
- No CMRU known-issue has been filed for scope-blind breaking detection.
- **Fix:** split or reword the commit before pushing, so only Assay paths carry `!`; or release every project with `--set-version`. File the CMRU known-issue.

### R2 [C] Nothing in the wave is committed or gated
The new module and its tests, the sysusers file and CP-23 are untracked. B162, KI-70 and RG-84 all say their registered gates are pending. CMRU's resume check compares Assay's tracked source, so it can't see changes to the untracked `process_tree.py`.

### R3 [C] The host and the daemon must change before any observer lane can run
- The new path doesn't exist until the operator runs mdt `install.sh` on the production host. That creates the `cgprofile-observer` group and the directory.
- Without it the daemon won't bind the observer (`serve.py:2428-2455`; it advertises `listening: false`), and Run-Gate refuses the lane (`run-gate.py:7037-7061`, path comparison).
- An old daemon still advertises the old path, which Run-Gate also refuses. Both fail safe, but none of this evidence is in the checkout.
- **Acceptance evidence needed:**
  - `stat /run/cgprofile/observer` shows `0751 root:cgprofile-observer`;
  - `version.resource_observer` is `{2, "/run/cgprofile/observer/resource-observer.sock", true}`;
  - the socket's group is the observer GID;
  - the running image's OCI revision label equals the commit of the `cgprofile-v1.0.0` tag;
  - `id -G` inside a lane container doesn't include the docker GID.

## High

### H-A [C] Failures after candidates start are reported as "observation unavailable", and Run-Gate calls that a product FAIL
- `_read_candidate_resource_counters` and `_candidate_resource_limit_evidence` (`mutation.py`, around `:2922-2952`) now raise **NO_MEASUREMENT/CGROUP_OBSERVATION_UNAVAILABLE**. They run before and after **every** candidate, not only at the pre-candidate check.
- `ResourceLimitEvidence.between` raises for:
  - a change of cgroup path or limits (`resource_limits.py:222-226`);
  - **a local event at a parent cgroup that can't be attributed** (`:345`).

  Those are real events that were observed, not missing observation.
- After such a failure:
  - R3 still runs (`runner.py:5344-5346`);
  - Run-Gate maps `NO_MEASUREMENT → "FAIL"` (`run-gate.py:305-311`).
- **This contradicts the project's own text:**
  - A-494 says "before any native R2 candidate starts";
  - `verify.py:3112-3113` says "unavailable after R0/R1 and before native candidates start";
  - `DESIGN-GUIDE.md:527`;
  - `CONSUMERS.md:402-409` says a quiet candidate with a parent event is `ERROR/EXEC_FAILED`.
- **It also hides cleanup failures.** The after-read sits in a `finally` before the process-tree check (`mutation.py`, after `execute_plan`). If that read raises, it replaces a `ProcessTreeError`.
- **User impact:** a sibling pushing `dev-gates.slice` to its limit, or a daemon redeploy longer than 30 s, shows up as a **product FAIL**. That breaks the estate's contention rule: infrastructure conditions must never be reported as product verdicts.
- **Fix:**
  - only the pre-candidate check (`if job_list: _read_candidate_resource_counters()`) reports NO_MEASUREMENT;
  - every per-candidate read or compare failure stays ERROR/EXEC_FAILED;
  - move the after-read inside the `try` and check for a process-tree failure first;
  - Run-Gate maps NO_MEASUREMENT with reason `CGROUP_OBSERVATION_UNAVAILABLE` to `ERROR`.
- **Tests:** a mid-campaign observer loss, an injected parent event, an after-read failure combined with `ProcessTreeError`, and Run-Gate's mapping.

### H-B [C config, H frequency] Mandatory, unmeasured 2 GiB caps meet the rule that any memory-`max` event makes a candidate "crashed"
- Run-Gate now requires a memory cap on observer lanes. Seven consumer lanes were given a uniform `memory = "2g"`, among them Run-Gate `assay-r2`, cgprofile `r2`, pwmcp `r2` and nyxloom `session-extract`.
- No measured footprint backs the value: `run-gate.footprint.json` has no R2 or observer lane entries.
- `--memory` without `--memory-swap` lets the container swap as much again. CMRU's lane uses 2g RAM with 16g swap, and Run-Gate's `CONSUMERS.md:210` still recommends "tight RAM + ample swap absorbs bursts".
- Assay counts `memory_events_max` as a limit hit (`resource_limits.py:206-216`; deliberately, `DESIGN-GUIDE.md:669-673`).
- The kernel counts a `max` event whenever a charge reaches `memory.max`, before reclaim, and page-cache charges count too.
- **Effect:**
  - a lane that sits at its cap because of swap use or retained page cache marks *every* candidate crashed, so R2 is ERROR;
  - CMRU's campaign aborts on the first event (`mutation_campaign.py:806-818`).

  This is availability, not a false pass. How often it happens is unmeasured.
- **Fix:**
  1. Add a baseline guard in Assay: wrap the R0/R1 baseline and the r2-baseline in the same counter samples. An event in the candidate's own cgroup during the baseline refuses R2 up front, with a message like "the lane's ceiling is reached by the unmutated suite". CMRU already does this.
  2. Size caps from measured `memory.peak` and `pids.peak`, with headroom above working set plus cache.
  3. State in Run-Gate's CONSUMERS docs that observer lanes must not rely on swap bursts.
- **Probe:** for each of the wave's observer lanes, run the baseline plus about 20 candidates and record each candidate's `memory.events max` delta and `memory.stat` file/anon split. If max-only events turn out to be routine, record a decision; don't quietly weaken the rule.

## Medium

- **M-A [C] The docker-group refusal checks the wrong group table and can silently skip.**
  - `grp.getgrnam("docker")` reads the devcontainer's `/etc/group`. On `KeyError` the check is skipped (`run-gate.py` near `:7057-7063`; the launcher uses `getent`/`DOCKER_GID` the same way).
  - The devcontainer adds the host's docker GID by number (`templates/devcontainer.json:199-200`), so the name may map to a different GID or not exist at all.
  - **Fix:** take the host docker GID from a path in the same view, such as the group of `/run/cgprofile` (`root:docker` per tmpfiles) or of the mounted Docker socket. Refuse if they're equal or if neither can be read.
- **M-B [C] The 30 s retry is a fixed literal unrelated to how long a redeploy takes.**
  - It isn't tied to how long `ciu up` takes to pull and recreate the daemon.
  - A connection that is accepted and then closed during shutdown fails as "not exactly one JSON line" and isn't retried.
  - **Fix:** add an operational rule against redeploying cgprofile while observer lanes run (or admission control), and also retry on reset or empty replies.
- **M-C [C] Leftover processes after the baseline destroy R0.** Any command run through `ProcessTreeProcessRunner` that leaves descendants becomes ERROR (`runner.py`, the `outliving_descendants > 0` branch), including the R0 baseline of R2 lanes. The same command passes as an R0-only lane, which goes against A-494's "keep lower rigor" principle.
  - **Fix:** keep the R0 claim with detail text and make only R2 unavailable; record a decision either way.
- **M-D [C] Two 10.0.0 changes are undocumented.**
  - The runner API change: custom runners must supply process-tree evidence, and selection depends on `is default_process_runner`.
  - The embedding/thread limitation: the window owns all of the caller's child processes.

  Neither appears in README, DESIGN-GUIDE or CONSUMERS.
- **M-E [C] Statuses are stale.**
  - KI-69 says "open" though `b310458e9` implements it (`cmru/KNOWN_ISSUES_TODO_BACKLOG.md:1796`).
  - B114 and B118 statuses predate 9.0.0.
  - B110 D10 (`assay-B110-PLAN-2026-09-28.md:125`) and `P7-pilot-tooling.md:79-80` still allow 1–8 pilot jobs.
  - RG-86 says "awaiting review" while the RG-NARROW code is present [H].
- **M-F [C] B110's sequencing rests on a premise that no longer holds.**
  - D3's bounded concurrent work queue (P4/B115) is void under A-491.
  - At one worker, 3,760 candidates × 9.4–13.4 s ≈ 9.8–14 h, against a 5 h p90 GO threshold, so the pilot can only measure a NO-GO.
  - The realistic paths to GO:
    - **P9 shards in separate containers.** Each has its own cgroup, so B157's attribution still holds, though the host's one-gate-at-a-time rule rules this out locally.
    - **P11** isolation units.
    - **CP-23** per-candidate cgroups.
  - Record a D-decision.
- **M-G [C] B131 is claimed in-wave without the capacity to run it.** The backlog says the pilot is "included in the current release wave" (`4-backlog.md:11874`), but it has repeatedly been refused at the 2 GiB headroom check (`:11872`). It now also needs R3. Take it out of the release.
- **H-C [C] The pilot's memory GO criterion can fail on page cache alone.** Criterion §8.1.2 (`memory.peak ≤ 1.6 GiB`) counts reclaimable page cache. Use anon/RSS measures plus memory-full stall instead.

## Low

- `descendants_signalled ≤ outliving_descendants` is never checked, so a tampered record is accepted (`process_tree.py:77-98`, `verify.py:2299-2303`).
- `clean` can only be `True`.
- A failed observer bind is only logged (`serve.py:2418-2424`); `version` doesn't say why.
- Nothing enforces Assay ≥ 10 for external consumers' observer lanes [H].

## Missing adversarial and acceptance tests
- H-A's mapping cases.
- The baseline resource guard.
- A **real** leader killed by SIGSEGV going through `assay verify` (the current coverage is synthetic, `test_mutation_classification.py`).
- Docker-GID namespace cases.
- Live:
  - a daemon restart during a lane, both shorter and longer than 30 s;
  - per-lane headroom;
  - a fork storm with a PID ceiling, where the candidate is recorded as crashed and the lane continues;
  - a concurrent sibling lane;
  - SIGTERM during the baseline and during a candidate without liveness.

## Release order
1. Fix B2, H-A and M-A. Commit per package, including the untracked files. File the CMRU known-issue and the Run-Gate mapping known-issue.
2. Rebuild tester-unified. Run the exact-tip gates:
   - cgprofile `r0-r1` and `r3`;
   - Assay `tester-unified` plus the PostgreSQL hazard set;
   - Run-Gate selftest;
   - mdt host-setup render tests.
3. **Operator-authorized host change:** run mdt `install.sh` (sysusers, then tmpfiles) and verify the directory.
4. Release cgprofile 1.0.0 with `--set-version`, pin it, run `ciu up`, and collect the R3 evidence.
5. Run the live probes, including the H-B headroom probe on Run-Gate `assay-r2`, cgprofile `r2`, CMRU `mutation` and Assay self-qualification.
6. Merge. Release Run-Gate 23.11, then Assay 10.0.0. Run the CMRU gate, then release CMRU 6.2. Install in the cockpit venv and notify dstdns.

**Defer explicitly:** the B110 and B131 pilots (measurements after release, B110 recorded as a lower-bound NO-GO); H4, H5 and M6; CP-18, CP-19 and CP-23; a CMRU SIGTERM handler.

**Open hypotheses:** how often max-only memory events occur under the 2 GiB caps; whether forkserver helpers outlive pytest; whether systemd `DefaultTasksMax` applies to Docker scopes; RG-86's real status.
