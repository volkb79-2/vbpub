# Release review, pass 6: contract-2 wave (Assay, Run-Gate, CMRU, cgprofile, MDT)

**Recommendation: don't ship yet.** Pass 5's two code blockers are fixed in source: Run-Gate's ERROR mapping (H2) and the missing whole-path tests (H3). But a new regression would make the registered Assay gate fail, and Assay's version route is still unrecorded. Nothing has been gated at this exact tree, and the host observer directory still doesn't exist.

## Target binding

| | |
|---|---|
| Checkout | `/workspaces/vbpub/.worktrees/cgprofile-cp20-contract2-20261012` |
| Branch | `cgprofile-cp20-contract2-20261012` |
| HEAD | `336336d81b9af9fd7d7a9ad3a4a272ca01c66071` (`chore: integrate contract-2 release wave`) |
| SHA-256 of `git diff --binary --no-ext-diff` | `9efe2f418a5186267c45746004ee891ca655cee5ae4a0c2a82ef7a93e485d6b6` (128 files, +5096/−2460). `/tmp/assay-rg55-contract2-current-tracked.diff` is byte-identical. |
| SHA-256 of `git status --porcelain=v1` | `2bcb3fc98eb4c4a2960297d3db2c42ddf7527007223505a8eef8288780eb70d4` |
| Untracked code | `assay/src/assay/process_tree.py`, `assay/tests/core/test_process_tree.py`, `assay/tools/resource-observer-gid.sh`, `modern-debian-tools-python-debug/host-setup/units/mdt-cgprofile.sysusers`, CP-23 backlog file, and 7 review reports |

**Method.** I re-verified the binding before reviewing. I used `git` reads, file reads, and one read-only `stat` of `/run/cgprofile` from the devcontainer. Nothing was edited, tested, gated, started or released. Pass 5 was bound to the same HEAD but a different diff (`e7df978b…`), so I re-checked its findings here and treated earlier gate or pilot evidence as not applying.

**Labels:** **[C]** means confirmed by reading the code at this target. **[H]** means a hypothesis that needs a live probe.

## Fixed since pass 5 (checked in this target)

| Item | Evidence |
|---|---|
| **H2:** an observer refusal no longer hides an R1 or R3 failure | `run-gate.py:316-321` sends `NO_MEASUREMENT/CGROUP_OBSERVATION_UNAVAILABLE` to `_observer_unavailable_rollup` (`324-389`). That function checks every claim and every evidence entry: any FAIL, INCONCLUSIVE or other NO_MEASUREMENT becomes FAIL; BUDGET_EXCEEDED becomes BUDGET_EXCEEDED; any other ERROR stays ERROR; malformed claims or evidence become ERROR. ERROR with the observer reason is returned only when everything else passed. This matches Assay, whose rollup also counts evidence statuses (`runner.py:2474-2475`, `verdict.py:5255-5256`). Claims and evidence are passed in at `run-gate.py:14493-14504`. Tests at `test_run_gate.py:1351-1440` cover R1 FAIL, R3 FAIL, R1 BUDGET_EXCEEDED, all-pass, a wrong rigor, and malformed claims and evidence. |
| **H3:** whole-path tests through `run_mutation` | `test_mutation_resource_limits.py`:<br>• `:1284`: observer lost at the after-read of candidate 2; one record saved, ERROR/EXEC_FAILED, the "during native R2" message.<br>• `:1343`: a parent-only event; no bucket and no saved record.<br>• `:1403`: cleanup failure wins over an after-read failure.<br>• `:1457`: the comparison error keeps the execution error as its `__cause__`.<br>These would catch both pass-5 regressions: reverting the read wrapper, or moving the observer-error raise ahead of the cleanup check. |
| **Low (pass 5):** comparison error lost the original cause | `mutation.py:4436-4439` now raises `from execution_error`. |
| **Shell GID guards were only checked by text matching** | All three launchers now share `assay/tools/resource-observer-gid.sh`. A behavioral matrix (`test_distribution_gate.py:160-218`) covers a GID of zero, mode 750, and the observer GID equalling the Docker GID; each refuses. |
| **The GID check depended on how `/run/cgprofile` was set up** | The helper now requires the control directory to be root-owned, have a non-zero group, and mode exactly `770` (`resource-observer-gid.sh:55-63`). It also requires the observer directory to be group-searchable and not writable by group or others. On this host, `/run/cgprofile` is `0 994 770`, which satisfies it. |
| **M-D:** library precondition undocumented | README:542, CONSUMERS:3448 and DESIGN-GUIDE:963-973 now document the process-wide subreaper state, the refusal when children already exist, and how `ProcessTreeProcessRunner` is selected. |
| **SQL-qualification container** | It no longer receives the observer mount or group (`tester-unified-gate.sh` around 1273-1342). The one container that has the Docker socket is now separated from the observer, and `qualify_sql.py` doesn't need it. |
| **B2 / KI-30 / cgprofile release route** | No `type!:` or `BREAKING` subject in `main..HEAD`. The `[Unreleased]` changelog bodies are empty in Assay, Run-Gate, cgprofile and CMRU. cgprofile 1.0.0 via `--set-version` is documented (`scripts/cgroup-profiler/docs/CONSUMERS.md:27-31`). CMRU's release step now runs the full `gate`, including R2 (`cmru/cmru.toml`). |
| **H-A** (mid-campaign observer loss is ERROR, not NO_MEASUREMENT) | Unchanged and still correct:<br>• the `CGROUP_OBSERVATION_UNAVAILABLE` reason comes only from the pre-candidate check (`mutation.py:2923-2935`);<br>• every in-window read goes through the during-campaign wrapper (`2953-2963`, `4367-4369`, `4394-4399`);<br>• the cleanup failure takes precedence (`4404-4417`). |

## Findings

### N1, High [C by reading; the exact-tip gate will confirm]: the registered Assay gate's launcher tests will fail after the observer-directory refactor
- **Where the tests run:** Assay's own lane runs `pytest tests gate/tests` (`assay/assay.toml:48`) inside the registered tester container. That container gets `ASSAY_RESOURCE_OBSERVER_SOCKET=/run/cgprofile-observer/resource-observer.sock` (`tester-unified-gate.sh:896`).
- **What the tests pass in:** the launcher tests set `ASSAY_GATE_RESOURCE_OBSERVER_SOURCE` from that variable (`test_self_qualification_container.py:792-798`, `854-860`). They then assert a successful launch (`:932`, `proc.returncode == 0`).
- **Two refusals in the launcher:**
  1. Its allow-list still names the **old** in-container path `/tmp/cgprofile-resource-observer.sock` and not the new `/run/cgprofile-observer/resource-observer.sock` (`self-qualification-container.sh:31-37`). It dies with "unsupported cgprofile observer source path".
  2. Even with the path allowed, the helper reads the host Docker group from `/run/cgprofile` (`:36`). That directory isn't mounted in the tester container (only the observer directory is, at `tester-unified-gate.sh:899`), so the helper refuses again.
- **Outside the container:** in the devcontainer, the default `/run/cgprofile/observer/...` doesn't exist (checked by `stat` just now), so the same tests fail there too.
- **Why it happened:** the old pair of `/tmp` target and `/tmp` allow-list was consistent. The refactor moved the target and left the allow-list behind. The helper's `/tmp` sticky-bit branch (`resource-observer-gid.sh:23-26`) is now dead code that still lets a guard accept sockets in a world-writable directory.
- **Fix:**
  1. In the launcher allow-list, replace the `/tmp` alternative with `/run/cgprofile-observer/resource-observer.sock`.
  2. For that nested path, take the host Docker GID from the outer launcher, which has already verified it. Pass it explicitly (for example `-e ASSAY_GATE_HOST_DOCKER_GID=…` from `tester-unified-gate.sh`), and refuse if it's missing. Never fall back to `/run/cgprofile`.
  3. Delete the `/tmp` branch from the helper.
  4. Add a launcher test that runs with the nested path.
- **Nit:** the mount line at `self-qualification-container.sh:457` is mis-indented.

### H1, High [C]: Assay's version route is still not recorded, so a plain `cmru release` would produce 9.1.0
- Assay's hard cut (verdict v16, judge identity `/12`, resume records rejected) arrived under the unscoped, non-breaking subject `90dc9078d feat: …`.
- CMRU looks only at commit subjects (`cmru/src/cmru/version.py:72`, `274-286`).
- No backlog entry, decision, CONSUMERS section or changelog mentions `--set-version 10.0.0` or `--major` (I searched for both).
- **Fix:** put `cmru release assay … --set-version 10.0.0` in the wave's release steps (B-entry or decision), the same way cgprofile's 1.0.0 route is written down. Without that, consumers on a `>=9` floor, dstdns among them, receive the hard cut as a minor release.

### R1, Release state [C]: someone tried a release mid-wave, and local `main` is ahead of origin
- `cmru.release.log` (git-ignored, written 08:42 today) shows a `cmru release` attempt. It was refused: "Local main is 5 commit(s) ahead of origin/main".
- Those 5 commits are the B155 scope work (`d8f8dae31`…`aa41988e8`). This wave builds on them: `main` is an ancestor of HEAD.
- CMRU's refusal was correct. But it shows a release was attempted before this tree was committed or gated.
- **Fix:** release only after the steps below, and push `main` as part of integration before any `cmru release`.

### Medium: still open, carried over and re-checked
- **H-B [C config, H frequency].** The uniform 2 GiB observer caps are still unmeasured, and swap is allowed, while any memory `max` event makes the candidate `crashed`.
  - Assay has no baseline guard: `runner.py` and `mutation.py` have no baseline counter window.
  - This is an availability risk, not a false pass. Measure it with a live headroom probe before release, or add the baseline guard (an event in the candidate's own cgroup during the unmutated baseline refuses R2 up front).
- **B131 status is stale.** `4-backlog.md:11874` still says the pilot is "included in the current release wave" but non-qualifying. Take it out of the release criteria.
- **M-C:** a process-tree leak during the baseline still destroys R0 (unchanged).
- **M-B:** the observer reconnect window is still a fixed 30 s, with no retry on a connection reset or empty reply (`resource_limits.py:51`, `1018`). Acceptable once documented; the restart probe below will show whether it's enough.

### Low
- Run-Gate has no Assay version floor for observer lanes. The new observer-refusal mapping assumes v16 reason codes, and an older judge would show up as a plain FAIL. Add it to the Run-Gate CONSUMERS compatibility table rather than writing code.
- CMRU's `run-gate.toml` and the other projects' observer-lane edits went in under the unscoped `feat:` subject, so those projects bump a minor version if released in this wave. This is expected, but say so in the release notes.

## Contract soundness in a shared systemd slice
The architecture is still sound:
- the in-process subreaper window;
- the five-counter, candidate-local attribution, with parent-only events refused as "shared ancestor" ERROR, and changes seen only in a parent's hierarchical counters ignored, so sibling contention doesn't become a false `crashed`;
- the separate observer socket using `SO_PEERCRED`, in a `0751 root:cgprofile-observer` directory mounted as a directory;
- A-494's narrow NO_MEASUREMENT, which Run-Gate now maps without hiding other claims.

The remaining exposure in a shared `dev-gates.slice` is availability, not certification:
- **[H]** whether a busy sibling's memory pressure produces local `max` events inside a candidate's own cgroup;
- **[H]** whether a `TasksMax`/`DefaultTasksMax` limit on the slice can fire above the container's own `--pids-limit`.

Both are covered by the live probes below.

## What the pilots can prove
- **B110:** only a lower-bound **NO-GO**. About 3,760 candidates × at least 9.4 s with one worker is about 9.8 h, against the 5 h p90 threshold.
- **B131:** about 317 candidates, which may fit serially [H].
- **Both are blocked:**
  - `/run/cgprofile/observer` is absent on the host (confirmed now);
  - all earlier attempts were refused at the 2 GiB `dev-gates.slice` headroom check.
- Run them after the release, as measurements.

## Smallest path to ship, in order
1. Fix **N1**, record **H1**'s `--set-version 10.0.0` route, and update B131's status.
2. Commit the 128 tracked files plus the untracked `process_tree.py`, its tests, `resource-observer-gid.sh` and the sysusers file. Use project-scoped subjects so you don't hit KI-71.
3. Run every touched project's registered gates at that exact tip (Assay, Run-Gate, cgprofile, CMRU full `gate` including R2).
4. Operator-authorized `mdt host-setup` install (sysusers, tmpfiles), then `stat` the observer directory and socket.
5. Release cgprofile 1.0.0 (`--set-version`), deploy it, and confirm `version.resource_observer` reports `{contract: 2, path, listening: true}`.
6. Live probes:
   - restart the daemon mid-campaign;
   - headroom on Run-Gate `assay-r2`, cgprofile `r2`, CMRU `mutation` and Assay self-qualification;
   - a fork storm against `--pids-limit`;
   - sibling memory pressure;
   - SIGTERM during a CMRU campaign.
7. Push `main`, then release Run-Gate 23.11, Assay 10.0.0, and CMRU 6.2.
8. Install in the cockpit venv and notify dstdns.
9. Run the pilots.

**Defer explicitly:**
- H4 (threads and embedding);
- H5 (retry after a parent event);
- M6 (observer rate limiting);
- CP-18, CP-19 and CP-23 (per-candidate cgroups, which is the path to running R2 safely in parallel);
- a CMRU SIGTERM handler;
- the B110 GO criterion, which needs shards, isolation units or CP-23.

**Open hypotheses (each needs a live probe):**
- how often max-only memory events happen under the 2 GiB caps;
- whether forkserver helpers outlive pytest and get counted as leaks;
- whether `DefaultTasksMax` applies to Docker scopes;
- the real `MemoryMax` values in `/etc/mdt/host-setup.env`.

CLAUDE_REVIEW_EXIT=0

---

**Review session for follow-up:** Claude Opus 5.5, high; retained session `28227ce4-cbdc-471a-8b69-a430af63fb67` (read-only fork). Review target and hashes are listed above.
