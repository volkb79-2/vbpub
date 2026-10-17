# CMRU release hardening — work log (2026-10-15)

Candidate reviewed: `14d5e653ebc4d77cbe904c99e243ce67f3e828e4`. Items 1–4 implemented; item 5 not encountered
(no CMRU-owned generated release-body path was touched).

## Residual contracts that CMRU does not define (bounded notes, not blockers)

1. **Probe placement for the wheel-build slice check (item 1).** `check_slice_unit(slice, image, probe_parent)`
   accepts a separate probe parent, so the probe is *not* started in the slice under test. The only input
   CMRU already declares for "where a host probe runs" is `CMRU_TESTER_CGROUP_PARENT` (the gates tier); it is
   required, never defaulted. **Corrected in the follow-up (below):** this entry first claimed that tier was
   "itself verified by every `tester-gate` launch" — that was an assumption about another command, not a check
   `wheel-build` made. It now verifies the probe parent itself with `tester-gate`'s self-check first. If the operator wants a different
   trust root for the first probe (e.g. the cockpit's own `$CGROUP_PARENT_DEV_INTERACTIVE`), that is a new
   public input and an operator decision; none was invented.
2. **"D-4 capacity evidence" (item 4).** The only D-4 in the repository is RG-55 wave-plan D-4 (lane model),
   owned by Run-Gate; no CMRU file defines which artifact constitutes the capacity evidence a multi-hour R2
   follow-up requires. CMRU records `d4-capacity-evidence` as a *named prerequisite* in the marker, manifest,
   KI-79 and the public docs, with the wording "defined by the Run-Gate RG-55 program". The exact artifact
   (file, lane output or report) needs a Run-Gate-side definition before CMRU can check for it mechanically.
3. **Pinned tester-image digest (item 2).** `tester-unified/Dockerfile` (out of scope) embeds the pinned
   `cli-extended` URL/digest and `build-image.sh` has no flag to override the pin, so CMRU carries the verified
   released digest into the build as a *resolve-mode decision* (`pinned` only when the pin equals the released
   asset, else `latest`, whose fetcher sha256-verifies the pointer's asset) plus a before/after pointer check.
   Updating the three Dockerfile defaults to the released asset belongs to the tester-unified owner.

## Not run

No tests, gates, mutation runs, releases, publishes, merges or pushes were executed; the controller runs the
registered gates in `tester-unified`.


## Follow-up (2026-10-15, after the Opus high review `release-hardening-followup-claude-opus-high-2026-10-15.md`)

Implemented in the same worktree/branch; nothing was released, merged, pushed, run or gated.

1. **M-1 — cleanup failure leaves no success-shaped evidence.** `_finish_gate_snapshot` runs both cleanup steps
   (canary removal, lease close), then persists the final manifest. A cleanup failure after a claimed
   `passed`/`provisional` becomes `failed` with `deferred_lanes: []`, the marker is deleted and the error is
   re-raised (so `main` returns 1). Tests: `test_release_gate_provisional.py` (canary removal and snapshot close,
   provisional and full, plus `main`).
2. **L-1 — `follow_up_lane` is `gate`** (first release: `assay-first-release-r2`). Asserted in marker and manifest.
3. **Probe-parent self-check.** `require_installed_cgroup_parent` first requires `check_slice_unit(parent, image,
   parent)` to answer `True` (tester-gate's own contract), then probes the build slice from that parent. Refusal
   names `CMRU_TESTER_CGROUP_PARENT`, starts no builder, preserves existing wheels. No new public input.
4. **L-3 — cli-extended byte identity.** Every image labels `org.vbpub.cli-extended.wheel-sha256`; the Dockerfile
   verifies the installed wheel file against it in all modes; `latest` mode now needs the published digest
   (`--expect-sha256` in the fetcher, `--cli-extended-wheel-sha256` in `build-image.sh`,
   `RUN_GATE_TESTER_CLI_EXTENDED_SHA256` in Run-Gate's `release_candidate.py`); `build-image.sh` and `prepare`
   re-read the label. The stale Dockerfile pin is still tester-unified's to update; no unverified mode remains.
5. **L-4 — bootstrap doc/script text.** Published identity is read from `cmru.release.log`; exact cgroup-parent
   inputs per command; per-command image pinning for CGProfile; the script reports config failures as themselves.
6. **L-2 — early refusal preserves evidence.** Input validation was moved ahead of snapshot open / manifest rewrite
   (`_plan_gate`); the stale marker is cleared only once a valid gate starts. Tests: refused unauthorized id and
   refused Run-Gate-identity resume leave marker and manifest byte-identical; a valid rerun shows no stale marker
   at its first lane.

Still open (unchanged): the "D-4 capacity evidence" artifact definition (Run-Gate RG-55); the review's live checks
(real wheel-build placement, in-transaction `gate-provisional`, a from-zero rehearsal) belong to the controller.


## Gate triage (2026-10-15, KI-78 review fold-in + KI-81 candidate; HEAD `4fbf4beb8`, tree dirty)

Evidence directory (outside the repo): `~/gate-evidence/ki81-2026-10-15/`. Every run is the registered
`run-gate coverage --allow-dirty` from `cmru/`; exit status is read from the run's own `EXIT=` marker.

| run | window (UTC) | EXIT | result |
|---|---|---|---|
| 1 `coverage-dirty.log` | 15:16:32–15:25:51 | 1 | 5050 passed, **18 failed**, 6 skipped, coverage 99.89% |
| 3 `coverage-run3.log` | 15:39:07–15:50:12 | 1 | 5084 passed, **1 failed**, 6 skipped, coverage 99.99% (`junit-coverage-run3.xml`, `coverage-run3.json`) |
| 4 `coverage-run4.log` | 15:51:43–15:51:54 | 1 | no test ran: `tools/check_assay_source.py` refused, `assay/` was dirty (another session's edits) |

The 18 run-1 failures and their causes (all fixed in the working tree; none waived):

- **9 wheel-build tests** and the config-resolution test: product bug. `handlers._configured_env_value` used its
  `name` parameter (the env key) as a loop variable, so every config-declared value (`CMRU_WHEEL_BUILDER_IMAGE`,
  `CMRU_TESTER_CGROUP_PROBE_IMAGE`, `CMRU_TESTER_CGROUP_PARENT`) read as `""`. Fixed (`project_id`); the tests that
  reach `cmd_wheel_build` now stub the host-slice probe like their siblings (covered by `test_wheel_build_placement.py`).
- **`test_host_git_hooks_disabled_pins_git_to_the_verified_worktree_and_restores`**: the policy appends to a caller's
  `GIT_CONFIG_COUNT`; the lane injects `safe.directory` at index 0. The test now starts from an empty count.
- **Two bootstrap tests** (`test_fresh_checkout_bootstrap_is_the_only_source_build_launcher`,
  `test_bootstrap_names_the_library_checkout_only_as_the_source_mode_build_input`): the zero-release `source` mode
  legitimately names the library source twice. They now require exactly those two uses, inside the source-mode
  branch (`tests/_cx_paths.library_source_uses`).
- **`test_dockerfile_pins_the_released_asset_and_digest_by_default_and_latest_is_opt_in`**: its regex assumed one
  space in `echo "<digest>  $1"` after whitespace was collapsed; matched loosely, with the two-space form pinned on
  the raw text.
- **Three OCI `...any-command-after-release` cases** and **`test_release_child_accepts_candidate_source_loaded_by_transaction_pythonpath`**:
  refusal wording; and `_validate_release_body_handlers` was given `configs` (empty in that path) instead of the
  ordered configs (`KeyError: 'cmru'`).
- **`test_dockerfile_copies_every_cmru_wheel_input_before_build`**: a stray ignored `cmru/logs/cmru/run-tests.log`
  (archived to the evidence directory, not deleted).

Run 3's remaining failure and the coverage gap:

- **`test_dockerfile_copies_every_cmru_wheel_input_before_build`**: the persisted review report
  `cmru/nyxloom-trove/reports/cmru-KI78-codex-sol-xhigh-2026-10-15.md` was in the build context, so it entered
  `/src/cmru` (`unaccounted COPY output ... enters a wheel package tree`). The report stays in the repository; the
  root `.dockerignore` now drops `cmru/nyxloom-trove` and `scripts/cgroup-profiler/nyxloom-trove` from the
  `tester-unified` context (nothing in the image, wheel metadata or runtime code reads either). Oracle:
  `test_dockerignore_keeps_repository_only_trove_reports_out_of_the_wheel_source_tree`.
- **Missing 0.01%** (`coverage-run3.json`): `src/cmru/handlers.py` line 411, branch 409→411 in `_declared_input` —
  the env var absent, value read from the enclosing config. Run 1 could not reach it for the bug above. Covered by
  `test_probe_inputs_declared_only_in_the_enclosing_config_are_found_and_used` (`test_wheel_build_placement.py`).

Not verified: no gate has run since these fixes. The coverage lane cannot start while `assay/` is dirty. Not run:
`gate-provisional`, CGProfile `r0-r1`/`r3`; never to be run from this session: CMRU `gate`/`mutation`/
`assay-first-release-r2`, CGProfile `gate`/`r2` (R2 lanes of 7–24 h).


## Zero-release bootstrap docs/tests (2026-10-15, same dirty tree)

Scope: `docs/BOOTSTRAP-FROM-ZERO.md`, the bootstrap script's final hint, the CMRU/root README, SPEC, DESIGN-GUIDE,
CONSUMERS and RELEASE-TRANSACTIONS wording, and three test files (`test_bootstrap_from_zero_sequence.py`,
`test_installed_wheel_subprocess.py`, `test_wheel_build_placement.py`). The slice verification itself
(`handlers.require_installed_cgroup_parent`, the script's call before Buildx) already existed; new oracles run the
script through the REAL verification against a fake Docker host (typo'd build slice, unloaded gates slice, missing
gates slice refuse before Buildx and the handler; loaded slices proceed) and pin the unset-`${CGROUP_PARENT_DEV_GATES}`
config refusal. The guide is now contract-tested (fence parse, no doubled/dangling continuation or fill-in placeholder,
install and PATH before the first `cmru`, setup block run, release order and versions against the declared graph).

Gate: `run-gate coverage --allow-dirty` from `cmru/` (evidence `~/gate-evidence/bootstrap-zero-2026-10-15/coverage.log`)
ended `EXIT=1` with **no test run**: `tools/check_assay_source.py` refused because another session's `assay/` edits
are uncommitted. The new tests are therefore **unexecuted**. Re-run once `assay/` is committed or clean.

## Release-wave blockers (2026-10-15, Sol xhigh + Opus high release-wave reviews)

Source: `run-gate-project/nyxloom-trove/reports/release-wave-{sol-xhigh,claude-opus-high}-review-2026-10-15.md`.
Nothing was staged, committed, run, gated or released in this pass; the controller owns the gates (`run-gate
coverage` from `cmru`, then `run-gate gate-provisional` from `cmru`) and none of them has passed on this change.
No KI ID is allocated: the candidate must first contain the latest local `main`.

1. **Gate controller checks the declared floor (Sol blocker 3, Opus 12).** `_ensure_gate_environment` now reads
   each declared dependency's installed distribution version and tests it against the whole declared requirement
   (stdlib ordering for `>=,>,<=,<,==,!=`; an unsupported specifier refuses; an importable module with no
   distribution metadata does not count). An authoritative interpreter is probed for the installed version before
   the switch; below-floor, no version, or probe failure refuses with the remedy. `-I -B` and the order (nothing
   from the worktree before the interpreter is chosen) are unchanged.
   Oracles: host venv with a real `cli_extended` 0.2.0 dist-info + authoritative 0.3.0 launcher switches, probe and
   exec logged; below-floor authoritative launcher refused before the controller starts; below-floor
   runtime-reported interpreter refused with no PATH fallback; a host at exactly 0.3.0 does not switch; the version
   table and specifier errors; in-process fakes for no-metadata, no-reported-version, probe failure, one-shot marker.
2. **Bootstrap provenance (Sol blocker 2).** Chosen contract: REFUSE (not an exact-OID snapshot, which would need a
   second source path through `wheel-build`). Before any Docker command, the host probe included,
   `build-initial-standalone.sh` requires no tracked edit, untracked file or Git-ignored-but-packaged file under
   `cmru/pyproject.toml`, `cmru/src`, `libraries/worktree/src` (+ `cmru/setup.py|setup.cfg|MANIFEST.in` if present)
   and, in source mode only, `libraries/cli-extended/{pyproject.toml,README.md,src}`; `__pycache__`, `*.pyc/*.pyo`
   and `*.egg-info` are ignored; an unreadable Git state refuses. `handlers.py`/`wheel-build` untouched.
   Residual: the check and the mount are not atomic (a file can change after the check); the wheel's filename and
   version still come from HEAD. `docs/BOOTSTRAP-FROM-ZERO.md` (outside this pass's scope) already promises
   committed source; the script now enforces it and that guide was not edited.
3. **Unknown writes (Sol blocker 4) and 4. visibility/target (Opus B2, Sol non-blocking).** `release.py`:
   `publish_release_body` sends each POST/PATCH once through `_send_write`, which never raises and never resends.
   Every post-send HTTP error, transport error, undecodable success body or malformed record becomes a `cause`;
   `_confirm_write` reads back (create: by returned ID, else by tag; update: by captured ID), retries a create
   `READBACK_ATTEMPTS` (5) times with injectable `_pause`, reports "not yet visible" apart from "tag now owned by
   release N", verifies id/tag/body/full-SHA `target_commitish`/remote tag, and returns success only when all hold
   (warning that the answer was unusable); otherwise `has an UNKNOWN outcome ... MAY OR MAY NOT have been written
   and nothing was rolled back ... inspect the release and the tag before any retry`. Known writes keep the earlier
   `was written, but ...` / `WAS WRITTEN, but reading it back ... failed` wording. `target_commitish` that is not a
   full SHA (a branch name) is not a commit claim, as before. Body comparison stays byte-exact: **no normalization
   is applied and whether real GitHub preserves every body byte is unproven** (open live check: create and update on
   a scratch repository, compare stored bytes with sent bytes).
   Oracles (deterministic injected sequences, no sleeps): 9 unusable answers x create/update applied-then-lost ->
   success, one mutation, WARN; same answers not applied -> UNKNOWN, one mutation; lost answer read by tag (changed
   body, unreadable, not yet visible, never visible); by-ID not-yet-visible then visible; five 404s -> bounded
   (5 reads, 4 pauses), not "replaced"; replacement -> "release ID is now"; update reads once; retarget after
   create/PATCH; branch/empty/ref targets accepted; exact commit accepted; PATCH 500 never a clean refusal.

### Test disposition
- Changed: `test_release_gate_interpreter.py` (probe string, the old "no switch on mere importability" test
  rewritten to the floor, in-process tests now fake `_installed_version`), `test_installed_wheel_subprocess.py`
  (fixture commits its project files and re-tags; helper `_recommit_tree`), `test_oci_image_release.py` (by-ID
  routes in `FakeGitHub`, `respond_after`, pause recorder; post-write keys now `GET /releases/<id>`; the old
  "malformed create answer is written, not refused" test is replaced because an exactly verified read-back is now
  success; the 404 read-back row moved to the dedicated visibility tests).
- Added: version-floor tests (subprocess and in-process); source-binding tests (dirty tracked/untracked/ignored,
  cli-extended only in source mode, unreadable Git, cap of 20 paths, noise that must not block, refusal before any
  Docker via the order log); unknown-write, visibility and target tests above.
- Not run here (forbidden in this pass); `python3 -m py_compile` and `bash -n` pass; the version-ordering table and
  the Git pathspec behavior the script relies on were checked by hand outside pytest.
