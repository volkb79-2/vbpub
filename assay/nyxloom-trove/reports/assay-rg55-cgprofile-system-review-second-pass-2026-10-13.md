# Release-wave review: Assay process-tree isolation and cgprofile observer contract 2

**Recommendation: do not ship yet.** Since the last review, the two Assay correctness blockers (signals ignored outside liveness lanes, and signal-killed leaders recorded as "killed") are fixed. What still blocks release:
- the release mechanics: the version-bump commit, the hand-written changelogs, and untracked files;
- a new gate gap: `process_tree.py` is outside Assay's own coverage and mutation gate;
- the observer socket's lifecycle and its group;
- observer lanes running without a memory ceiling.

All of these are small. With them done, the wave can ship with the limitations listed in section 6.

**Bound to:** HEAD `3f3012fcd2cdf1474b7a50d916fe38960e205507`. The tracked diff now hashes to sha256 `aa9e3c9b4944e27ccd0486fde088e34fc15a072fb271071e1cb3680e1e23bb11` (86 files), not the `118a723e…` from the last review, so the working tree has changed since then. I used only read-only `git` and file reads; nothing was edited, committed, tested or gated. Labels: **[C]** = confirmed in the checkout, **[H]** = hypothesis.

## What changed since the last review

| Item | Status | Evidence |
|---|---|---|
| B1: signals ignored outside liveness lanes | **Fixed [C]** | the runner registers and unregisters the leader's process group (`process_tree.py:482-509`); termination tests now cover a candidate without liveness and SIGTERM during the baseline (`test_campaign_termination_real.py`, diff) |
| H1: signal-killed leader recorded as "killed" | **Fixed [C]** | live classifier `mutation.py:4253-4263`; resume validator `:2224-2236`; synthetic tests in `test_mutation_classification.py` |
| M5: cleanup failure reported as an attribution error | Open [C] | `mutation.py:4375-4388`: the evidence comparison still runs before `raise execution_error` |
| H2 limits / H3 socket lifecycle | Open [C] | `run-gate.py`, `serve.py` and `self-qualification-container.sh` are unchanged |
| B2 version bump / B3 changelogs / B4 untracked files | Open [C] | HEAD unchanged; `run-gate-project/CHANGES.md` and the cgprofile `CHANGES.md` still have hand-written bullets; `process_tree.py` and its test are untracked |
| The remediation design from the last turn | **Not in the checkout** | the four saved reports end with the broad review |

---

## 1. Is the release design still right?

**Process-tree isolation: yes, keep the in-process window.**
- After the B1 fix it composes with the existing termination path: SIGTERM kills the registered group, `proc.wait` returns, `finish()` sweeps the detached descendants, and `execute_plan` turns it into a lane timeout (`runner.py:1331-1335`).
- The emptiness proof comes from the kernel (`waitid` reporting no children), not from procfs.
- Embedding and multi-threaded callers remain a documented limitation; per-candidate cgroups (CP-23) are the long-term fix, not a helper process.

**Parent and local counters (contract 2): keep the B155 rule.** Treat a local event at a parent cgroup as unattributable whenever the candidate's own delta is zero. Relaxing it would be a false-kill path:
- A parent's memory `max` can cause ENOMEM in the candidate without any OOM recorded under the candidate.
- A parent's `pids max` makes `fork()` fail with EAGAIN in the candidate.

In both cases the test fails because of a sibling, and that would be counted as a kill.

What needs changing is availability, not correctness:
- add local per-container ceilings, so candidate-caused events show up in the candidate's own cgroup (Q3);
- add a bounded retry for ambiguous parent events (deferred H5);
- events seen only in a parent's hierarchical counters are already, correctly, ignored.

**New [C]: profile placement can't move observer lanes.** Placement only applies when the scope isn't `"container"` (`run-gate.py:3641`; help text at `13146-13148`). So CP-19 (origin scope lost when placement moves its last process) is a profiling bug and can be deferred.

## 2. The observer socket is mounted as a file

**What happens today [C]:**
- Run-Gate mounts `source=/run/cgprofile/resource-observer.sock` with a file bind mount (`run-gate.py:7028-7030`); Assay's launcher does the same (`self-qualification-container.sh:480`). A file bind pins the inode the path pointed to when the container started.
- On restart the daemon unlinks the old socket and binds a new one at the same path (`serve.py:2459-2462`); on clean shutdown it unlinks it (`:2618-2620`).
- **Inside a running container**, the mount still points at the old, unlinked socket, which nothing listens on. Every later connect fails with ECONNREFUSED; there is no client retry (`resource_limits.py:1041`). The candidate becomes ERROR/EXEC_FAILED and the lane aborts. This fails closed and is resumable, but the multi-hour lane must restart, and it doesn't recover even after the daemon is back.
- **New containers** get the new socket. If the daemon is down, Run-Gate's version check and `lstat` refuse to start the lane.
- A failed observer bind is only logged (`serve.py:2418-2424`); `listening = false` is what makes Run-Gate refuse.

**Directory mounting is the right lifecycle contract.** A mounted directory is looked up at every `connect()`, so a rebind inside it is visible to running containers. The contract: *the daemon owns only the socket name; host setup owns the directory; the directory is never removed while gates run.*

**New security finding [C]: observer lanes run with the host's `docker` group.**
- The host creates `/run/cgprofile` as `0770 root docker` (`mdt-cgprofile.conf:35`).
- Run-Gate requires the socket's group to equal the parent directory's group and then passes `--group-add <that gid>` (`run-gate.py:7020-7032`). Assay's launcher does the same (`self-qualification-container.sh:536-538`).
- So every observer container gets the docker GID. Any reachable `docker.sock`, for example via `RUN_GATE_EXTRA_MOUNTS`, whose help text uses `docker.sock` as its example (`run-gate.py:13167-13168`), then gives the candidate full Docker access, which is root on the production host.

**Required changes:**

| Layer | Change |
|---|---|
| Host setup (`mdt-cgprofile.conf`) | add a dedicated `cgprofile-observer` group and `d /run/cgprofile/observer 0750 root cgprofile-observer -` |
| cgprofile `serve.py:72`, `:2426-2467` | default path `/run/cgprofile/observer/resource-observer.sock`. **Remove `os.makedirs`** for the observer directory: today it invents a directory with default ownership when it's missing (defaults doctrine #2). Require an existing directory: root-owned, not group- or world-writable, group-searchable. Socket `0660 root:<dir gid>`. Bind failure keeps `listening = false` and gives the reason in `version` |
| cgprofile docs and fixtures | `docs/PROTOCOL.md`, both RG55 interface contracts (cgprofile and Run-Gate copies), DESIGN-GUIDE, CONSUMERS deploy recipe, README, `tests/fixtures/rg55/socket/version-response.json`, `test_deployment_contract.py`, `test_serve.py` |
| Run-Gate `run-gate.py:372-374`, `6990-7061` | mount the directory read-only at a target such as `/run/cgprofile-observer`; set `ASSAY_RESOURCE_OBSERVER_SOCKET` to the socket inside it. Validate the directory (uid 0, `mode & 0o022 == 0`, group-searchable, gid == socket gid) and the socket (uid 0, group-connectable, no other-user bits). Refuse if the gid is the docker group's (`getgrnam("docker")`). The version check compares the advertised path. Tests in `TestResourceObserverLane`; docs README, CONSUMERS, SPEC, DESIGN-GUIDE |
| Assay launcher (`self-qualification-container.sh:19-60`, `480`, `600`) | same directory mount, same group validation, updated expected-mount entry; also `tester-unified-gate.sh` and the tests that reference the socket path (`test_resource_observer.py`, `test_b148_…`, `test_mutation_resource_limits.py`) |
| Assay client (`resource_limits.py` near `:1041`) | on ECONNREFUSED or ENOENT only, retry for a bounded ~30 s. This is safe: the counters live in the kernel, and `ResourceLimitEvidence.between` still refuses any change of cgroup path or mount identity |
| Acceptance (live) | restart the daemon mid-candidate → the next read succeeds and the counters only increase; daemon down longer than the retry window → ERROR with no record; the container's `id -G` doesn't include docker |

## 3. Should observer containers require local limits?

**Memory: yes, required.** Lanes with no memory ceiling today [C]:
- Run-Gate `assay-r2` (`run-gate-project/run-gate.toml:109-118`, only `cpus`);
- cgprofile `r2` (`scripts/cgroup-profiler/run-gate.toml:61-77`);
- pwmcp `r2` (`pwmcp/run-gate.toml:17-23`).

Without a ceiling, memory pressure surfaces first at `dev-gates.slice` (`MemoryMax`, `dev-gates.slice.in:27`), which always aborts the lane. Docker's per-scope backstop is "sized generously above any legitimate single container" (`docker-scope-default-limits.conf.in:15-25`), so it isn't a candidate-scale limit. Lanes that already set one: cmru, ciu, worktree, mdt.

**PIDs: yes, required, and it also protects the host.**
- The checked-in slices set no `TasksMax` (`grep` of `host-setup/units`) [C]. Whether systemd's `DefaultTasksMax` applies to Docker scopes on this host is [H].
- Correction to my earlier hypothesis: the trailing `none` in the launcher's identity check is `NetworkMode`, not a PID limit (`self-qualification-container.sh:584-588`) [C].

**Smallest sound change:**
1. Run-Gate: add `pids` (positive integer) to the allowed lane resource keys (`run-gate.py:1031-1033`) and emit `--pids-limit` next to `--memory` (`:11826-11831`).
2. Extend `_validate_effective_resource_observers` (`:1730`) to refuse an observer lane without `resources.memory` and `resources.pids`. No default is invented.
3. **Extra mounts:** refuse `RUN_GATE_EXTRA_MOUNTS` on observer lanes. A Docker socket lets candidates start work outside both the process tree and the counters, and with the docker group it amounts to host root. An allowlist can come later.
4. Consumers declare `pids` (and `memory` where missing), sized from `pids.peak` history (`run-gate.py:2852`).
5. Assay self-qualification containers already have `--memory=2g --memory-swap=8g --cpus=3` (`:534-537`). Add `--pids-limit=<N>` and `{{.HostConfig.PidsLimit}}` to the identity check at `:584-588`.
6. Assay: report the candidate cgroup's own `pids.max`/`memory.max` in the plan output and warn when either is `max`. Don't refuse: correctness doesn't depend on it.
7. Docs: Run-Gate CONSUMERS/SPEC; Assay DESIGN-GUIDE/CONSUMERS ("attribution requires limits on the candidate's own cgroup").

None of this is profiling. Container limits, the observer socket and the counters are correctness infrastructure; DAMON, watch and placement stay optional and never touch an observer lane's cgroup.

## 4. False-certification hazards and missing adversarial tests

**Confirmed gaps:**
- **G1 [C] (HIGH): `src/assay/process_tree.py` is outside Assay's own qualification.**
  - The self-qualification and preflight `targets` lists in `assay/assay.toml` (≈`:115-176` and ≈`:230-290`) enumerate modules explicitly and don't include it.
  - I found no test that checks the list against `src/assay/**/*.py`.
  - So the module that now decides whether a kill is valid escapes the 100% line-and-branch coverage gate and R2.
  - Fix: add it to both lists, plus an inventory-completeness test. This is cheap and blocks release.
- **G2 [C] (MEDIUM, M5):** an ambiguous parent event computed at `mutation.py:4376` can replace a `ProcessTreeError` cleanup failure (lane-fatal either way, wrong diagnosis). Fix: raise `execution_error` first when its cause chain contains `ProcessTreeError`.
- **G3 [C] (MEDIUM): an infrastructure fault in `LivenessRunner` becomes a reusable crashed candidate.**
  - When `_monitor` raises an `OSError` (for example a `/proc` read), `finish(force=True)` attaches clean evidence and the error is re-raised (`liveness.py:1730-1744`). `_execute_plan_inner`'s `except OSError` then returns ERROR (`runner.py:1418-1429`), which is recorded as `crashed` and **reused on resume**.
  - Not a false pass (crashed makes R2 ERROR), but an Assay fault is persisted as a candidate fact and requires `--rejudge-outcome crashed`.
  - Fix: only `ProcessTreeSpawnError` should map to a candidate ERROR; any other runner `OSError` should be lane-fatal.
- **G4 [C] (LOW): `ProcessTreeEvidence.clean` can never be `False`** (`process_tree.py:97-98`), so `verify.py:2314-2317` checks a field that always passes. The real guarantee is that evidence exists only after the kernel's empty-children proof. Document that.
- **G5 [C] (LOW): resume can't see changes to the untracked module.** CMRU's resume binds `git.head_rev(assay_source)` and a tracked-source diff (`mutation_campaign.py:565`, resume check), so edits to the untracked `process_tree.py` are invisible to it. Committing the file (B4) fixes this.

**Checked and found sound [C]:**
- Cleanup failure is lane-fatal with no record: `ProcessTreeError` → `AssayError` (`runner.py:1430-1435`, `liveness.py:1748-1753`).
- A native result without process-tree evidence is refused (`mutation.py:4402-4408`).
- `/12` state records without evidence are rejected (`mutation.py:2430-2433`).
- The live classifier, the resume validator and `verify.py` (`:2318-2348`) now agree on the leader-status rules.
- A failed timeout cleanup is retried once in `__exit__`, then still raises.

**Missing adversarial tests** (`test_process_tree.py` has only 2 behavioural tests):
1. Procfs blind (children files and the ppid scan both empty) while a live child exists → deadline → `ProcessTreeError`; lane ERROR, no state record.
2. A child already exists at window entry → refusal before spawn.
3. Cleanup deadline exceeded (injected `waitid`) → lane ERROR, no record, and no classification after the after-counter read.
4. A double-fork chain of depth ≥3, and a process that keeps forking and exiting.
5. A thread spawning a child during a window: documents the current misattribution (H4 limitation).
6. End-to-end leader killed by SIGSEGV or `os.abort()` → `crashed`, **`assay verify` passes**, resume reuses it without executing.
7. A crashed leak record reused on resume; `--rejudge-outcome crashed` re-executes it.
8. A tampered state record (killed with outliving descendants > 0, or leader `signaled`) is rejected.
9. `ProcessTreeError` during timeout cleanup → lane ERROR, not `budget_exceeded`.
10. G3: `_monitor` raising `OSError` → decided behaviour (lane-fatal).
11. CMRU: signal-killed leader → `crashed`; procfs blind → infrastructure error; resume with a tampered killed record.
12. **The timeout test has a race [C]:** `test_process_tree.py:92-124` sets the runner timeout to 2 s, but the parent waits up to 5 s for the descendant's PID file, and then `pid_file.is_file()` is asserted. Under host load this fails. Make it readiness-driven.
13. Python 3.14 multiprocessing (forkserver) in a baseline [H]: does it refuse the lane?

CMRU termination: there is no SIGTERM handler. Python's default action kills the campaign without running its `finally` blocks. Inside the registered container, `--init` and container teardown clean up, and the evidence written so far stays consistent and resumable. **Acceptable as a limitation** for the registered lane.

## 5. Release sequence, versions, acceptance, backlog, pilots

**Versions:**
- Assay 10.0.0: v16 verdict cut, judge identity `/12` (state records written by older versions won't be reused, so the first run is a cold start), signal-killed leader → crashed, baseline leak refusal, Linux subreaper requirement, runner API change.
- Run-Gate 23.11: `resource_observer` itself is unreleased, so this is a minor.
- cgprofile 1.0.0: first release, needs `--set-version`.
- CMRU 6.2.
- **Blocked by B2:** HEAD's `feat(assay)!:` subject makes CMRU bump every project the commit touches to a major. Reword or split it while it is still unpushed.

**Coupling:** CMRU's mutation lane imports `assay.process_tree` from the same worktree, so the CMRU gate needs the Assay change committed first. Assay 10's native R2 needs a deployed observer that serves contract 2.

**Deployment acceptance** (none of this evidence is in the checkout):
- `version.resource_observer = {2, <directory path>, listening}`;
- the image's OCI revision label equals the commit of the `cgprofile-v1.0.0` tag;
- daemon restart during a running lane;
- fork storm with a PID ceiling → `crashed` and the lane continues;
- a concurrent sibling is unaffected;
- SIGTERM during the baseline and during a candidate without liveness;
- the container's group list excludes docker.

**Backlog fit:**
- *In the wave:* B162, KI-70, CP-20, CP-21, RG-89, KI-69.
- *Defer:* CP-18 and CP-19 (profiling/placement, can't affect observer lanes per `run-gate.py:3641`), CP-23, the H5 retry, M6 rate limiting.
- *Stale:* B110 plan D10 (`assay-B110-PLAN-2026-09-28.md:125`) and `P7-pilot-tooling.md:80` still allow 1–8 pilot jobs; KI-69 status.

**Pilots:**
- The B131 backlog says the pilot "is included in the current release wave" (`4-backlog.md:11874`), and both pilots have repeatedly been refused admission for lack of 2 GiB headroom on `dev-gates.slice` (`:11872`). **Don't make the release depend on them.**
- **B110:** at one worker it can't produce a GO. Recording a NO-GO is honest only as a lower bound: the minimum measured per-candidate cost, excluding the mutation itself, × candidate count + fixed overhead > 5 h. That bound survives host contention, which only adds time. Record it as a D-decision.
- **B131:** the historical estimate is about 317 candidates (unmeasured). At ~10–15 s each, a **full** one-worker run (~1 h) may fit; that would be a real qualifying result instead of a pilot [H; needs `assay plan analysis-r2`].
- Both are measurements to run after the release.

## 6. Smallest coherent set to ship now

**Block release:**
1. B2: reword or split HEAD's subject, or release every project with an explicit `--set-version`.
2. B3: empty the Run-Gate and cgprofile `[Unreleased]` bodies.
3. B4: commit `process_tree.py`, its test, CP-23 and the reports.
4. G1: add `process_tree.py` to both Assay target lists, plus the inventory test.
5. H3 + group: directory mount with a dedicated observer group, no `makedirs`, Assay client reconnect. It must land **before** cgprofile 1.0.0 and Run-Gate 23.11 freeze the socket path.
6. H2 minimum:
   - Run-Gate requires `memory` and `pids` on observer lanes (adds the `pids` key);
   - Run-Gate refuses extra mounts on observer lanes;
   - Assay launcher adds `--pids-limit`;
   - consumers declare the values.
7. M5 and G3 (two small Assay changes), the readiness-driven timeout test, and adversarial tests 1–3, 6 and 9.
8. The 10.0.0 docs: runner API change, the embedding/threads limitation (H4), signal-killed leader semantics.

**Defer explicitly, documented as limitations:**
- H4: the in-process window needs exclusive control of child processes;
- H5: parent-cgroup events abort the lane; add a retry later, and run observer lanes one at a time meanwhile;
- M6: no rate limit on the observer;
- CMRU has no SIGTERM handler (container-scoped);
- CP-18, CP-19, CP-23;
- B110/B131 pilots run after the release, with B110 recorded as a lower-bound NO-GO.

**Order:**
1. The block-release items, committed per package.
2. Rebuild tester-unified.
3. cgprofile `r0-r1`/`r3`, release 1.0.0, deploy, run the acceptance probes.
4. Assay `tester-unified` plus the PostgreSQL hazard set, and the Run-Gate selftest; merge.
5. Release Run-Gate 23.11, then Assay 10.0.0.
6. CMRU gate, then CMRU 6.2.
7. Install in the cockpit venv and notify dstdns.

## Open hypotheses
- Whether systemd's `DefaultTasksMax` applies to Docker scopes on this host.
- The actual `MemoryMax` values for `dev-gates.slice`/`dev.slice` and the Docker backstop (they live in `/etc/mdt/host-setup.env`, not the checkout).
- How often parent-cgroup `max` events happen under cockpit and sibling load.
- Whether forkserver helpers outlive pytest and get counted as leaks.
- The B131 candidate count.
- Whether any current observer lane depends on `RUN_GATE_EXTRA_MOUNTS`.

## Session and snapshot record

- Claude Code session: `28227ce4-cbdc-471a-8b69-a430af63fb67` (resumable for follow-up questions).
- Saved response SHA-256: `dddcf02d34b0de4d8bfd30861ea5a6cdbd3ad83a62c8851a10cc0d4dbb5a2526`.
- Checkout verification before/after the read-only review: HEAD `3f3012fcd2cdf1474b7a50d916fe38960e205507`; 93 status paths; tracked diff SHA-256 `aa9e3c9b4944e27ccd0486fde088e34fc15a072fb271071e1cb3680e1e23bb11`.
