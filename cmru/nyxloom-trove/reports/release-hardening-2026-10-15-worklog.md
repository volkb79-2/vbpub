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
