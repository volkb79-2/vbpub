# Release review: HEAD `1ad026d58` plus uncommitted CMRU snapshot and Run-Gate hooks fixes

**Recommendation: don't ship yet.** The committed tree fixes most earlier blockers, including the cross-project breaking commit, the NO_MEASUREMENT mapping and the docker-GID check. The two new fixes (CMRU gate snapshot, Run-Gate hooks overlay) are the right direction but are uncommitted and ungated. They also leave one high-severity gap: a lane can still get code executed on the host through git metadata other than hooks.

**What was reviewed:**
- HEAD `1ad026d58e148c81ca920824fe97173342568d93`: 10 commits on top of `3f3012fcd`.
- The dirty diff: 17 modified files plus untracked `cmru/src/cmru/gate_snapshot.py`.
- The earlier reports, latest first, from the CMRU Sol xhigh review (15:44) back to the assay pass-5 to pass-9 and final reviews.

| | Before | After |
|---|---|---|
| HEAD | `1ad026d58e14…` | `1ad026d58e14…` |
| Status entries | 18 | 18 |
| Status sha256 | (not hashed) | `38ce109c…f464` |
| `git diff` sha256 | `873c457d47cc21e17af2c73cdc823274ddcaa3fd4459b283ac673741fd358345` | same |
| `gate_snapshot.py` sha256 | | `ead57308…abbaed` |

I took only the first status hash after the review. The before-listing of the 18 entries matches the after-state. Nothing was edited, tested, gated or deployed. **[C]** = confirmed in source, **[H]** = hypothesis (not exploited or run).

## 1. Status of earlier findings

| Finding | Status | Evidence |
|---|---|---|
| B2: one `!` commit bumps every project to a major | **Fixed [C]** | `4104a79c1 feat(assay)!:` touches only `assay/`; the integration commit is `chore:`; KI-71 fixed in source |
| H-A: post-preflight failures reported as NO_MEASUREMENT → FAIL | **Fixed [C]** | `mutation.py:2923-2960` (only the preflight emits NO_MEASUREMENT; failures during candidates are ERROR/EXEC_FAILED); `run-gate.py:316-345` (observer refusal maps to ERROR unless another claim is adverse) |
| M-A: docker-group check read the container's group table | **Fixed [C]** | `run-gate.py:7140-7159` takes the host docker GID from `/run/cgprofile`'s group; MDT `install.sh:143` rejects an equal GID |
| S1: secret created after the gate's inventory is visible | **Fixed in the dirty diff, uncommitted [C]** | `gate_snapshot.py` plus `run_release_gate.py` |
| R-H: lanes can plant `.git/hooks` | **Partly fixed in the dirty diff [C]** | `run-gate.py` rev 67, `git_hook_info_mount_flags` (empty read-only hooks, read-only info); RG-93 |
| B110 plan allowing 1–8 pilot jobs | Fixed [C] | the "1 ≤ N ≤ 8" text is gone from P7 |
| R2: no gate evidence for the exact tip | **Open** | nothing in the checkout binds a registered gate result to `1ad026d58` (the Sol report reached the same conclusion) |
| R3: host setup and daemon redeploy | **Open (no live evidence)** | MDT `check.sh:436` now verifies the merged rules; no live record in the checkout |
| H-B: 2 GiB caps vs memory-`max` events | **Open** | example `DEV_GATES_MEMORY_MAX=1.5G` (`host-setup.env.example:197`) is below the 2g that observer lanes declare (e.g. `cmru/run-gate.toml:132`); Run-Gate `CONSUMERS.md` admission wording is now honest (dirty diff) |

## 2. Release blockers

### BL-1 [C mounts / H exploit] HIGH: lanes can still rewrite git metadata that later host git commands follow (gitfile, admin dir, `core.fsmonitor`)

**The hooks fix closes one route, not the class.** A lane can still make host git execute arbitrary code through the repository metadata it is allowed to write:
- **The judged worktree, including its `.git` gitfile, is mounted read-write.** `container_mount_flags` → `_dual(worktree)` (`run-gate.py:10418-10456`).
- **The worktree's own admin directory is read-write.** That covers `commondir`, `gitdir`, `HEAD`, `index` and `config.worktree` (`git_worktrees_mount_flags`, `:10361-10378`).

**How it executes on the host:** a lane can redirect the gitfile (or the admin directory's `commondir`) to a git directory it built itself. That directory can borrow the real objects through `objects/info/alternates` and set `core.fsmonitor=<program>` in its config. HEAD, branch and tree still match, so `_verify_snapshot` passes. The next host `git status` then runs that program as uid 1003, which can read the main checkout's `cmru.secret.toml`.

**Host `git status` calls that would follow the redirect:**

| Caller | Location | Hardening today |
|---|---|---|
| Run-Gate's clean-tree check before every nested lane | `check_clean_tree`, `run-gate.py:4235-4237` | none; `fsmonitor` appears nowhere in `run-gate.py` |
| CMRU gate snapshot | `gate_snapshot.py:420`, after repository discovery at `:415` that trusts the gitfile | `_host_git_policy` (`:60-83`) sets only `core.hooksPath`, `NOSYSTEM`/`GLOBAL` |
| An operator's next `git status` in any judged worktree | any Run-Gate consumer | none |

**The docs overclaim.** Run-Gate `CONSUMERS.md` (dirty diff) says a lane cannot install anything "that a later host Git command could execute". That is broader than what the change checks.

**Repair:**
1. **Run-Gate:** mount the judged worktree's `.git` gitfile and its admin directory's `commondir`, `gitdir` and `config.worktree` read-only (create an empty read-only overlay when absent).
2. **Run-Gate:** run its own host git with `-c core.fsmonitor=false -c core.hooksPath=/dev/null` (or the `GIT_CONFIG_COUNT` equivalent).
3. **CMRU:** add `core.fsmonitor=false` to `_host_git_policy`. Run lifecycle git with `GIT_DIR` and `GIT_WORK_TREE` taken from the trusted workspace record, not discovered through the gitfile.
4. **CMRU:** before any host git call on the sidecar, verify that the gitfile bytes and the admin `gitdir`/`commondir` match what was recorded.
5. Extend RG-93, or file a new RG entry, and correct the CONSUMERS wording.

**Acceptance evidence** (a real container, extending the existing live test at `test_run_gate.py:29667-29716`):
- the lane tries to rewrite `{worktree}/.git`, `$common/worktrees/<own>/commondir` and `config.worktree`, and each write fails;
- a host `git status` afterwards does not run a planted `fsmonitor` program;
- a negative control on a rev-66 mount set shows the write succeeding.

### BL-2 [C] HIGH: the fixes are uncommitted and nothing has been gated at the exact tip
- **Uncommitted:** 17 files plus the untracked `gate_snapshot.py`.
- **Pending registered gates:** KI-69 through KI-74, RG-92, RG-93, B162, KI-70 and KI-72 all record only focused tests, with the registered gate still to run.
- **Repair:** commit per package and rebuild tester-unified. Then gate the exact tip, retaining the tester image ID and its build revision (Sol finding 4):
  - Assay `tester-unified` plus the PostgreSQL hazard set;
  - Run-Gate selftest, including the real-container test from BL-1;
  - cgprofile `r0-r1` and `r3`;
  - MDT host-setup tests;
  - the full CMRU `gate`, including mutation.

### BL-3 [C code / H trigger] MEDIUM-HIGH: SIGTERM leaves the gate snapshot leased for 18 h and evidence uncopied
- **Cause:** the old masker's SIGTERM/SIGHUP handler was removed with it. `run_release_gate.py` now has no handler, so its `finally` blocks don't run on SIGTERM (`:212-229`).
- **What is left behind:**
  - the lease, which has an 18 h TTL (`gate_snapshot.py:36`, `:259`). `acquire_lease` refuses an unexpired lease without checking whether its holder is alive (`libraries/worktree/src/worktree/core.py:1214-1220`), so a retry of the same HEAD is blocked for up to 18 h;
  - the copy-back in `finish_lane`, which never runs;
  - the canary file in the source root;
  - a manifest still saying `"running"`.
- **Who sends SIGTERM:** for example, Run-Gate's 14 h budget on the `gate` lane.
- **Repair:** reinstate the SIGTERM/SIGHUP → `SystemExit` handler. Treat a lease whose holder PID is dead (on the same host) as stale, or document an explicit release verb.
- **Acceptance evidence:** SIGTERM during a real `mutation` lane leads to:
  - the partial `mutation-cmru.json` present in the source;
  - the lease released;
  - the canary removed;
  - an immediate re-run for the same HEAD that resumes.

## 3. Other findings (actionable)

| ID | Severity | Finding | Evidence | Repair / acceptance |
|---|---|---|---|---|
| CM-1 | **Medium** | **Gate failure reports read stale files.** `_invoke_lane` calls `_report_lane_failure(repo_root/"cmru")` on the source *before* `finish_lane` copies the lane's results back, so a failing lane is reported without its failing tests. The unit tests call it directly and don't catch this. | `run_release_gate.py:163-180`, `:249-256` [C] | Report from the snapshot, or report after the copy-back. Test it through `_invoke_lane` with a snapshot. |
| CM-2 | **Medium** | **Old outputs are seeded into each lane.** Every lane gets the whole evidence set seeded from the source (verdict, coverage, junit, canary results), not just the resume inputs. If a lane dies before writing a file, the seeded old copy is copied back with a fresh mtime, so the stale file looks current. | `gate_snapshot.py:370-373`, `:451-466` [C] | Seed only `mutation-cmru.json`, `progress-mutation-cmru.jsonl` and the manifest. Record each copied-back file's sha256 and the lane exit codes in the manifest. |
| CM-3 | **Medium** | **Sidecar snapshots pile up.** The identity includes HEAD (`:219-223`), so every gated HEAD leaves a full monorepo checkout, a `cmru-gate-*` branch and a separate Assay state directory under the main `.run-gate/assay-state/` (keyed per `run-gate.py:9471-9496`). They are removed only together with a *release* candidate (`transaction.py:752-761`). A dev worktree that is removed some other way orphans its sidecars, and `remove_gate_snapshots_for_source` cannot reach them because it requires the source path to exist (`:553`). | [C] | Teach `cmru cleanup` to remove `cmru-gate` records whose source is missing or whose HEAD isn't current, together with their state directories. Document it in CONSUMERS. Acceptance: two HEADs gated, then cleanup leaves one sidecar. |
| CM-4 | **Medium** | **Evidence can be bound to the wrong commit.** Nothing re-checks the source HEAD before the host builds the mutation facts (`run_release_gate.py:268-274`) or at the end, so a commit made in the source mid-gate gets this run's evidence. | [C] | Assert source HEAD == `snapshot.commit` before building facts and at the end; otherwise fail. |
| CM-5 | **Low-Med** | **Tripwire blind spots.** In `validate_lane_outputs`, the generated-cache allowlist is checked *before* the secret-name check, so a `cmru.secret.toml` under `.venv/`, `build/`, `__pycache__` or the like is accepted. The secret-name branch is effectively dead code. `.run-gate/` is skipped entirely. | `gate_snapshot.py:430-442` [C] | Check secret names first, and include `.run-gate/`. |
| CM-6 | **Low-Med** | **The canary check can't fail.** It runs only in `installed-wheel` and returns early when the manifest is missing or not `"running"` (`installed_wheel_smoke.py:115-139`). No negative control is in the checkout. | [C] | Acceptance: the same lane run with `--worktree <source>` and a `"running"` manifest must fail. |
| CM-7 | **Medium [H]** | **Lanes can move shared refs.** The shared refs are writable from any lane, and the host builds the mutation facts *after* three lanes have run. A lane could move `cmru-v*` tags or `main`; the facts are partly cross-checked against the remote. | RW common directory (`run-gate.py:10418-10427`) | CMRU: digest `git for-each-ref` (excluding the sidecar branch) before and after each lane and fail on any change. Run-Gate: consider mounting `refs/tags` and `packed-refs` read-only. |
| CM-8 | **Low** | **Same-uid host writers are outside the threat model.** The sidecar is still writable by any uid-1003 process on the host, but such a process can read the root secret directly anyway. | design | State this limitation in the DESIGN-GUIDE. |
| H-B | **Medium (availability)** | **Mandatory 2 GiB caps vs the gates slice.** Observer lanes must declare memory (2g), any memory-`max` event marks a candidate crashed, and the example gates slice is 1.5G. | `resource_limits.py:206-216`; `host-setup.env.example:197` [C]; the live host value is [H] | Record the effective `dev-gates` `memory.max`, its ancestors and host headroom before the campaign. Run the headroom probe (baseline plus about 20 candidates, recording each candidate's `memory.events max` delta and the `memory.stat` file/anon split). |
| UNK-1 | **Low [H]** | **Unknown Assay outputs could fail the gate.** Any output Assay writes under `cmru/.assay` that isn't in `_LANE_EVIDENCE_FILES` (`gate_snapshot.py:23-31`) makes `validate_lane_outputs` fail a passing lane. | | This will only show up in the real CMRU gate (BL-2). |

## 4. Design verdicts
- **CMRU exact-HEAD gate snapshot (KI-74): right design.**
  - The caller's tree is never mounted, so the inventory race, the KI-73 class of ignored-file exposures and the KI-65 restore hazards are all gone.
  - It reuses the shared worktree lifecycle.
  - It refuses a tracked secret, git filters and tracked evidence paths, and requires a linked worktree.
  - It verifies the tree hash against the source (`gate_snapshot.py:129-199`, `:284-297`).
  - Copy-back uses `O_NOFOLLOW`, `nlink == 1`, size caps and atomic replacement (`:300-361`).
  - The gaps are the ones in BL-1, BL-3 and CM-1 to CM-6.
- **Run-Gate hooks overlay (RG-93): correct but incomplete.** It is correctly dual-mounted and fails closed when `hooks/` or `info/` is missing (both exist on this host [C]), and the live test checks hook and `info/exclude` writes. The rest of the class is BL-1.
- **Assay / cgprofile contract 2: unchanged by the dirty diff.** Committed contracts check out per the Sol report and my H-A recheck. Deferred limitations stay as before: H4 (embedding/threads), H5 (no retry for parent-cgroup events), M6 (no observer rate limit), CP-23, and the B110/B131 pilots as non-qualifying post-release measurements.

## 5. Required acceptance evidence before release
1. **Commits:** BL-1 and CM-1 to CM-5 fixed, committed per package, with RG-93 extended or a new RG entry filed.
2. **Exact-tip registered gates (BL-2):** each with the tester image ID, its build revision, and the HEAD it ran against.
3. **Real-container Run-Gate test:**
   - hooks, info, gitfile, admin `commondir` and `config.worktree` writes all fail;
   - a host `git status` afterwards doesn't run a planted `fsmonitor` program;
   - a negative control on rev 66 shows the writes succeeding.
4. **Live CMRU gate:**
   - the canary is absent in the `installed-wheel` lane, and the negative control (`--worktree <source>`) fails;
   - `docker inspect` shows no mount source under the source worktree;
   - a secret planted in the source mid-gate is invisible to the lane;
   - the tripwire fires on a secret planted in the sidecar, including under `.venv/`;
   - a symlinked evidence file is refused on copy-back;
   - a commit made in the source mid-gate fails the gate;
   - SIGTERM leads to a resumable re-run (BL-3);
   - `cmru cleanup` removes stale sidecars (CM-3).
5. **Host and daemon (R3):**
   - `stat /run/cgprofile/observer` shows `0751 root:cgprofile-observer`, and the observer GID is not the docker GID;
   - `version.resource_observer = {2, "/run/cgprofile/observer/resource-observer.sock", true}`;
   - the image's OCI revision label equals the commit of the `cgprofile-v1.0.0` tag;
   - live probes: daemon restart within and beyond 30 s, fork storm under `--pids-limit`, a sibling lane, SIGTERM during the baseline and during a candidate, and the H-B headroom probe with the effective slice and host capacity recorded.
6. **Release order:** merge, then release Run-Gate (rev 67 or later), cgprofile 1.0.0, Assay 10.0.0 and CMRU 6.2 with explicit versions. Install in the cockpit venv and notify dstdns.

## 6. Open hypotheses
- BL-1 can actually be exploited end to end.
- CM-7: a lane could move refs and that would affect the mutation facts.
- No other ignored Assay outputs exist under `cmru/.assay` beyond the allowlist.
- The live `dev-gates` memory cap and host headroom are adequate for 2g lanes.
- How often memory-`max` events with no OOM occur under the 2 GiB caps.
- Forkserver helpers don't outlive pytest and get counted as leaks.

The session stays open for follow-up.