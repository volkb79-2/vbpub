# Contract-2 release review, pass 8 (focus: CMRU KI-72 follow-up)

**Recommendation: don't ship yet.** No code-level blocker is left: the KI-72 fix is correct in source and every earlier High finding is fixed in source. What still stands between this tree and a release is commit state, gates on the exact committed tip, and host acceptance.

I couldn't save this as a file because writing is disabled in this session; it's here as text only. Nothing was edited, tested, gated, released or deployed. The only command touching the host was a read-only `stat` of `/run/cgprofile`.

**Bound to:**
- HEAD `336336d81b9af9fd7d7a9ad3a4a272ca01c66071`
- Tracked-diff SHA-256 `f87033de1afdbfc5047d6c39de89676d7733e5284b82e15f65799b03416d9f0e` (137 files, +6410/−2625)
- Status SHA-256 `0ab33975c173f578acb8f398e4cab21f3f2965eeade60c5c0f18b20e1e3c7dc8`

## Blockers (process and state, not code)

1. **Files the code needs are still untracked.**
   - The files: `assay/src/assay/process_tree.py`, `assay/tests/core/test_process_tree.py`, `assay/tools/resource-observer-gid.sh`, `modern-debian-tools-python-debug/host-setup/units/mdt-cgprofile.sysusers`, `…/units/mdt-cgprofile-observer.conf`, and the CP-23 backlog file.
   - **Impact:** Assay's runner imports `process_tree`, and CMRU's `_run` (`cmru/tools/mutation_campaign.py:131-135`) stops with `CandidateIsolationError` without it. A build or gate from HEAD would fail.
   - **Fix:** commit them per project with scoped subjects, so CMRU's commit-subject version rule (KI-71) doesn't bump unrelated projects.
2. **No registered gate has passed on an exact committed tip.** This covers Assay, Run-Gate, cgprofile, and CMRU's full gate, including the schema-4 mutation lane.
3. **Host acceptance is still missing.**
   - `/run/cgprofile/observer` and its socket don't exist (checked this pass). `/run/cgprofile` is `0 994 770`.
   - No contract-2 daemon is deployed, so Run-Gate refuses every observer lane.

## KI-72 review

### Verified correct

- **History comparison across linked worktrees** (`mutation_campaign.py:584-599`):
  - The paths are now taken relative to Assay's own worktree top, from `assay_git.repo_top(assay_source_root)`.
  - The old bug built paths like `.worktrees/…/assay/src`, which don't exist in any commit, so Git reported "unchanged". The test at `test_mutation_campaign.py:282` would catch that.
  - An Assay source in a separate clone also works, because the comparison runs in Assay's own repository.
  - An unknown older commit raises an `AssayError`, which becomes a `RuntimeError` (`:286-293`). The campaign stops rather than reusing old results.
- **Ignored-file detection:**
  - `--ignored=matching` reports an ignored `core/` directory as one record, which is refused (test at `:183`).
  - The allowlist accepts only `__pycache__`, `.pyc` and `.egg-info`.
  - The current Assay tree shows only `__pycache__/` entries, and there's no setuptools_scm version file to trip it.
- **Required source root:** the argument is mandatory (`:495`), with a check for `None` (`:554`).
- **Clean check before the baseline:** it runs at `:689`, before the baseline. The test at `:223` fails if the baseline runs first.
- **Package roots for the current layout:** they resolve to `src`, `analysis/src` and `pyproject.toml`, and there is no `setup.py`, `setup.cfg` or `MANIFEST.in` to escape that.

### Findings (all Low; none blocks this release)

- **K-a: `py-modules` binds the whole Assay directory** (`:376-385`).
  - `package_dir.get("", ".")` makes `.` a source root.
  - Combined with the ignored-file check, `.venv`, `.pytest_cache` or `build/` would make every campaign refuse. This is latent, because Assay declares no `py-modules`.
  - **Fix:** bind only `<dir>/<module>.py` for each listed module, and test it with an ignored `.pytest_cache`.
- **K-b: unsupported setuptools settings are caught by a deny-list, not an allow-list** (`:387-391`).
  - Only `data-files` is refused. `ext-modules`, unknown future keys, or a non-setuptools `build-backend` with a leftover `[tool.setuptools]` table would pass unchecked.
  - **Fix:** accept only a closed list of `[tool.setuptools]` keys and require `build-backend == "setuptools.build_meta"`. This follows the "fail on what you can't derive" rule.
- **K-c: the `.pyc` allowlist is too wide** (`:396-406`).
  - It accepts any ignored `.pyc`, including a `foo.pyc` sitting directly in a package directory after `foo.py` was deleted. Python still imports that file.
  - A deleted module could therefore stay importable. That would also defeat `_run`'s missing-module guard (`except ImportError → CandidateIsolationError`).
  - **Fix:** allow `.pyc` only inside `__pycache__`, and add a test with `src/assay/x.pyc`.
- **K-d: where Assay is imported from isn't tied to what is checked.**
  - The import path `sys.path.insert(0, assay_source/"src")` (`:684`) is hardcoded, while the checked roots are read from `pyproject.toml`.
  - The clean check (`:689`) runs after importing the possibly-dirty Assay, using that same Assay's `git` module.
  - **Fix:** after importing, assert that `Path(assay.__file__).resolve()` lies under a bound root. Ideally take the import root from the same function that derives the roots.
- **K-e: test gaps.**
  - A list entry `where = ['../src']` is untested. The existing `'../src'` case is a plain string, so the type check rejects it before the `..` check is ever reached.
  - Passing `assay_source_root=None` explicitly is untested. The test at `:264` only checks Python's `TypeError` for a missing argument.
  - The test at `:223` leaves a temporary `src` directory on the test process's `sys.path`.
- **Nit:** `_resource_limit_evidence` has an unused `ProcessTreeEvidence` import (`:257`).

## Fixed since passes 6 and 7 and both Sol reviews

| Item | Evidence |
|---|---|
| H1: Assay version route | Decision A-496 (`assay/nyxloom-trove/decisions.md:1041`) records `cmru release assay --set-version 10.0.0`. The Assay ≥10.0.0 floor is in `run-gate-project/CONSUMERS.md:319` and `docs/DESIGN-GUIDE.md:254`. Both edits are still uncommitted. |
| Sol 1: daemon could report `listening` falsely | `serve.py:2510` sets the socket only after the permission check. On failure it closes the socket and removes the path only if it's still the one it created. |
| Sol 2: tester ran in the Docker group | The tester runs as uid and gid 1003, and the image build refuses a group named `docker` (`tester-unified/Dockerfile:211-214`). |
| Sol 3: resume ignored a changed timeout | The timeout is now part of the resume match (`mutation_campaign.py:607`); the test is at `test_mutation_campaign.py:676-691`. |
| Sol 5: installer continued after a setup failure | The installer now exits if `systemd-tmpfiles` fails (`install.sh:136-137`, `704-706`). |
| Sol: main checkout exposed sibling worktree secrets | The release gate requires `.git` to be a file, i.e. a linked worktree (`run_release_gate.py:501-513`). Narrowing the secret scan to `repo_root` matches Run-Gate, which mounts only the worktree and the shared git directory for a linked worktree (`run-gate.py` around 10392-10424). |
| KI-71: scoped breaking subjects | A recognised scope limits the bump to its own project (`version.py:279-320`). `90dc9078d feat:` and `2b1bd7f39 feat(rg55):` keep path-based minor bumps, which is why A-496's explicit version is needed. |
| B131 status | It now says it is not a release gate (`4-backlog.md:11874`). |
| B110 one worker | `--pilot-jobs` must be 1 (`cli.py:753-762`). Plan D3, D10, P4 and the P7 brief no longer claim shard or parallel results. |
| A-494 and RG-92 rollup, B163 tests, B164 | Unchanged since pass 6 and still correct. |

## Availability risks (none can cause a false pass)

- **Gate headroom may block the release gates themselves.** The last recorded `dev-gates.slice` headroom was about 1.05 GB against the 2 GiB needed. That stops the pilots, and probably also the exact-tip R2 and mutation gates. Free capacity first, or the "run the gates" step stalls.
- **Uniform 2 GiB caps with no baseline guard (H-B).** Any memory `max` event marks a candidate `crashed`. Measure headroom per lane, or add a guard that refuses R2 if the unmutated baseline already records a local event in its own cgroup.
- **Observer reconnect is a fixed 30 s (M-B).** The daemon-restart probe will show whether that's enough.
- **A process leak during the baseline destroys R0 (M-C).**

## Accepted limitations (deferred, documented)

- B165: embedding-safe and parallel isolation.
- CP-23: per-candidate cgroups, the route to parallel R2.
- B110 at one worker can only give a lower-bound NO-GO (about 9.8 h against the 5 h p90 threshold).
- B131 stage 2.
- CP-18 and CP-19.

## Release order

1. Fix K-c and K-d, which are cheap, or explicitly defer K-a through K-e.
2. Commit all tracked and untracked inputs.
3. Free gate capacity, then run the exact-tip registered gates.
4. Have the operator run `mdt host-setup`, then `stat` the observer directory and socket.
5. Release cgprofile 1.0.0 with `--set-version`, deploy it, and check that `version.resource_observer` reports `{contract 2, path, listening: true}` and the image revision is right.
6. Run the live probes: daemon restart, per-lane headroom, a fork storm, sibling memory pressure, and SIGTERM during a CMRU campaign.
7. Push `main`.
8. Release Assay 10.0.0 with `--set-version` (A-496).
9. Release Run-Gate 23.11, putting it after Assay so its documented Assay ≥10.0.0 floor already exists.
10. Release CMRU 6.2.
11. Install the new versions in the cockpit venv and notify dstdns.
12. Run the B105 and B131 pilots, as measurements only.
HEAD_BEFORE=336336d81b9af9fd7d7a9ad3a4a272ca01c66071
STATUS_BEFORE=0ab33975c173f578acb8f398e4cab21f3f2965eeade60c5c0f18b20e1e3c7dc8  -
DIFF_BEFORE=f87033de1afdbfc5047d6c39de89676d7733e5284b82e15f65799b03416d9f0e  -
336336d81b9af9fd7d7a9ad3a4a272ca01c66071
0ab33975c173f578acb8f398e4cab21f3f2965eeade60c5c0f18b20e1e3c7dc8  -
f87033de1afdbfc5047d6c39de89676d7733e5284b82e15f65799b03416d9f0e  -
REVIEW_EXIT=0
