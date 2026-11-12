# CGProfile independent review — Claude Opus 5.5 high — 2026-10-15

Review session: `810a8086-bbea-40e9-936f-9450666b214b`.

Candidate HEAD: `8f5e5eae24c47f4eb58aa39447726b8b2d67760b`.

**Verdict: READY** for the planned 1.0.0 release, with R2 explicitly deferred. I found no blockers.

One caveat on scope. I was read-only and had no git, so I reviewed the files on disk. The git status snapshot shows this worktree is dirty (cmru, ciu, assay and root files are modified), and the list was cut off before the `scripts/` entries. I therefore couldn't confirm that the CGProfile files I read match HEAD `8f5e5eae`. Before relying on this review, run `git diff --quiet 8f5e5eae -- scripts/cgroup-profiler scripts/damon-analysis`.

## 1. Blockers

None. Here is why each open CP item doesn't block:

- **CP-18 (DAMON start / EBUSY): not a blocker.**
  - **The original bug is fixed and tested.** The fake sysfs now rejects a commit while the monitor is off with `EINVAL` (`tests/test_damon.py:134-142`). The startup test asserts the last write is `state=on` and that no commit is issued (`test_damon.py:648-649`).
  - **The EBUSY degradation is truthful and safe.**
    - The start response reports `unavailable:OSError: [Errno 16] …` (`lib/serve.py:1143-1151`).
    - The summary marks DAMON unavailable and the stop response reports no DAMON series.
    - Ordinary samples continue.
    - The pool releases its slot and resets the shared `nr_kdamonds` count to 0 (`lib/damon.py:1013-1017`, then `release` and `_restore_baseline`).
  - **The live report matches that.** The session completed, no DAMON series was written, and `nr_kdamonds=0`.
  - **The limit is clearly documented:** `README.md:297-312`, `docs/DESIGN-GUIDE.md:390-423`, `docs/CONSUMERS.md:442-512`.
  - **What's still missing is evidence on the exact image, not correctness.** Getting it needs an operator-approved change to the host's `DAMON_STAT` setting.
- **CP-19 (placement refusal and restore): not a blocker.**
  - The one-process refusal happens before the recovery journal or systemd scope exists (`lib/placement.py:1226-1229`).
  - Each later move from the original unit is re-checked (`placement.py:1550-1552`).
  - Unknown, unreadable or symlinked membership refuses instead of guessing (`placement.py:1673-1709`).
  - Both live probes passed, and the remaining race is documented (`CONSUMERS.md:240-244`).
- **CP-20 (contract-2 observer): not a blocker.**
  - The `pids_localevents` refusal (`lib/resource_observer.py:73-77`) and the rule that hierarchical counters are never silently used as local ones (`resource_observer.py:509-533`) are both sound.
  - Every failure mode is a refusal. Assay strictly validates the response shape (`assay/src/assay/resource_limits.py:1121-1131`).
  - Nothing uses the observer until an R2 lane runs.
- **CP-21 (release build identity): not a blocker.**
  - What's left can only be checked at release time: the exact release build and the post-deploy revision-label check.
  - The code paths are correct:
    - the tag and commit checks plus clean-tree check (`lib/version.py:91-118`);
    - push refuses without CMRU's identity (`build-push.py:207-242`);
    - CMRU passes the protected values only to tagged build/push steps (`cmru/src/cmru/cli.py:371-379`, `2987-3023`).

The R2 deferral is stated honestly, and `tests/test_docs.py:139-148` checks that the "no completed full-source R2 verdict" wording appears on every release surface.

## 2. Non-blocking risks (most important first)

1. **The R3 canaries test none of the 1.0.0 features.** `tools/canary-run.sh:80-135` only breaks util, targets, store and logtail code. With R2 deferred, the daemon, DAMON pool, placement, observer and build-identity code have coverage evidence only, with nothing showing the tests would catch a regression. Cheap fix: add canaries that:
   - remove `placement.py:1736-1738`;
   - drop `resource_observer.py:73-77`;
   - substitute hierarchical counters at `resource_observer.py:533`;
   - add a commit before `state=on` at `damon.py:901`;
   - delete the guard at `Dockerfile:34-35`;
   - delete the HEAD check at `version.py:107-110`.
2. **A hard stop leaves DAMON stuck until someone fixes the host by hand.**
   - Compose sets no `stop_grace_period` (`ciu.compose.yml.j2:57-66`), so Docker gives 10 seconds.
   - Shutdown can take much longer:
     - sessions finalize one at a time with up to a 10-second join each (`serve.py:2097`);
     - the sampler's sleep isn't interruptible (`serve.py:1437`);
     - each restore can wait up to 5 seconds on systemd (`placement.py:128`).
   - The same happens on an OOM kill: the 1 GiB limit (`ciu.defaults.toml.j2:64`) is shared with the pandas-based `ctl report` subprocess.
   - After a SIGKILL, kernel monitors can stay on and `nr_kdamonds` stays at 16. Restart recovery handles placement only (`serve.py:481-573`), so the new daemon refuses DAMON indefinitely (`damon.py:584-588`).
   - **Fix:** set a grace period, use an interruptible wait in the sampler, and document the operator recovery.
3. **On hosts with `DAMON_STAT` on, DAMON can never work but still costs something.** Every DAMON-on start rewrites the shared monitor count twice, then fails. `ctl version` still reports `available` (`serve.py:759`), and the error message doesn't name `DAMON_STAT`. Consider detecting `/sys/module/damon_stat/parameters/enabled` and reporting it as the reason.
4. **One-shot DAMON cleanup can wrongly report success.** If resetting `nr_kdamonds` fails, the exception is swallowed, cleanup is still reported confirmed, and nothing is logged (`damon.py:1018-1027`, `1036`). Later daemon sessions then refuse DAMON.
5. **The observer readiness check is weak.**
   - `listening: true` (`serve.py:758`) only proves the socket bound, and Run-Gate's preflight relies on it (`run-gate-project/run_gate.py:7443-7466`). A host using `pids_localevents` passes the preflight and fails at the first real query.
   - The observer also serves one connection at a time with a 5-second deadline (`serve.py:3006-3106`), so one stalled client delays the others.
6. **Published bytes aren't tied to any accepted image.** The push step rebuilds the image from a floating `python:3.14-slim` base and unpinned apt/pip (`Dockerfile:31,47`), and no digest is recorded. The live acceptance used local image `sha256:1e91fd8a…`, which is not what gets published.
7. **A cleanup error can hide a successful push.** `build-push.py:152-159` raises from `finally` if removing the temporary worktree fails, so a successful push is reported as failed and the release notes step never runs.
8. **The first-release changelog has no size bound.** With no previous tag, `cmru/src/cmru/changelog.py:261` puts the entire project history into the GitHub Release body. CMRU has no check against GitHub's 125,000-character limit, and the notes step runs after the image push (`cmru.toml:71`). Measure the body from `cmru release --dry-run` first.
9. **Source checkouts report two different versions.** The CLI reports `0.1.0` from `pyproject.toml` (`cgprofile.py:46-53`), while the daemon reports `0.0.0-dev` (`serve.py:69`). The controller ruling RW-569 says untagged source must identify as `0.0.0-dev`. Built images are not affected.
10. **The Dockerfile's "fails before package installation" claim is only true for the builder stage.** The final stage's `apt-get install systemd` (`Dockerfile:58-60`) can run in parallel under BuildKit. No image is produced, so identity is still safe.
11. **First GHCR push visibility isn't covered.** New GHCR packages may default to private; the docs don't mention visibility or pull credentials on the deploy host.

## 3. Documentation and backlog drift

- `docs/CONSUMERS.md:210-215` still says a live start/stop probe is needed, which contradicts lines 503-507.
- **CP-18 understates its own evidence.** The backlog entry (lines 96-101) and the acceptance report (lines 19-22) say no DAMON sample has ever been collected. But the controller log shows:
  - RW-531 (`run-gate-WAVE-RG55-CONTROLLER-LOG.md:8514-8529`): a 2026-10-09 session collected 36 DAMON samples.
  - Lines 8939-8950: a 2026-10-10 on/off pair measured 0.000503 cores of overhead.

  Both ran on earlier images (one on main build `efaf1129`), not the candidate. The entry should cite them and say only that the exact-image re-run is pending.
- **CP-13 is listed as open but looks fixed:** `cgprofile.py:935` already uses `DEFAULT_OUT`.
- **Smaller wording mismatches:**
  - `README.md:206-208` says other users can traverse to the observer socket, but `/run/cgprofile` is `0770 root:docker` (`mdt-cgprofile.conf:11`).
  - `DESIGN-GUIDE.md:317-318` says Run-Gate mounts only the socket; it actually mounts the directory (`run_gate.py:7434-7436`).
  - `DESIGN-GUIDE.md:281` says ancestry stops at `dev-gates.slice`; it actually goes to the root (`resource_observer.py:86-102`).
  - The docstring at `placement.py:89-91` doesn't match the call at line 1563.
  - `CONSUMERS.md:474-475` refers to a "read-only sysfs bind" that doesn't exist.
  - `CHANGES.md:12-15` (the release-notes source) doesn't mention the `DAMON_STAT` limitation.

## 4. Evidence gaps and contradictions

- I couldn't independently check the r0-r1 numbers (2,590 tests, 100% coverage) or the r3 result.
- The live CP-18/CP-19 acceptance ran on an image built from `9943239c`, not HEAD `8f5e5eae`. Confirm with a diff that the HEAD commit only changed docs.
- CMRU will gate and tag its own generated child commit (it folds the changelog into the release section), not `8f5e5eae`. The release relies on CMRU re-running r0-r1 and r3 (`cli.py:2913-2930`).
- **No live contract-2 observer query has been recorded.** Contract-1 queries did succeed live during the RW-566/567 runs (`controller-log:9346-9366`). So peer binding and mount mapping are proven live, but the new `.local` reads and mount-option parsing are tested in unit tests only.
- **Cut the release from a clean checkout.** CGProfile's release uses the CMRU built from source in the repo (`cmru.orchestration.toml:203`). The uncommitted changes under `cmru/src` in this worktree would change how the release controller behaves.
