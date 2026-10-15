# CMRU release hardening — work log (2026-10-15)

Candidate reviewed: `14d5e653ebc4d77cbe904c99e243ce67f3e828e4`. Items 1–4 implemented; item 5 not encountered
(no CMRU-owned generated release-body path was touched).

## Residual contracts that CMRU does not define (bounded notes, not blockers)

1. **Probe placement for the wheel-build slice check (item 1).** `check_slice_unit(slice, image, probe_parent)`
   accepts a separate probe parent, so the probe is *not* started in the slice under test. The only input
   CMRU already declares for "where a host probe runs" is `CMRU_TESTER_CGROUP_PARENT` (the gates tier, itself
   verified by every `tester-gate` launch); it is required, never defaulted. If the operator wants a different
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
