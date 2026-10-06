# Assay B145/B147 implementation log

Date: 2026-10-06
Worktree: `/workspaces/vbpub/.worktrees/assay-b136-b141`
Branch: `assay-b136-b141`

## Scope

Finish Assay B145 and fold in B147. B146 remains deferred. The release is still
intended for the planned v14 / 8.0.0 combined wave.

## Work recorded

- Hardened the SQL qualification runner and PostgreSQL fixture lifecycle with
  per-run ownership labels, recorded Docker IDs, bounded cleanup, and recovery
  evidence for ambiguous launches.
- Fixed the log-follower exit-status check and derived the registered gate and
  controller timeout relationship from their bounded phases.
- Kept Assay's Git children independent of image and consumer configuration by
  pinning `maintenance.auto=false`, `maintenance.autoDetach=false`, and
  `gc.autoDetach=false` in its hermetic command argv (B147).
- Addressed the successive Sol xhigh findings on runner ownership, name-conflict
  classification, prelaunch scratch cleanup, and preserving ambiguous CID
  evidence across the outer EXIT cleanup. The focused follow-up review found no
  remaining issue in the tested ownership-marker handoff.
- Static checks passed: `git diff --check`, `bash -n`, Python source compilation,
  TOML parsing, and syntax compilation of the generated fake Docker helper.

## Remaining before delivery

- Integrate the nine newer commits on `main`, resolving overlapping Assay files.
- Review the resulting complete tip with Sol xhigh.
- Run the registered `tester-unified` gate on that fixed tip and record its
  receipt and result.
- Merge serially to `main` after the gate. Do not release until the planned
  v14 / 8.0.0 wave is complete.

No tests or registered gates have been run on the current dirty tree.

## Controller continuation (2026-10-06)

The branch already includes its integration merge from `main` at
`80138618f2ca06a5e51a5f9922704819337713b6`; the earlier remaining-work list
above is a prior snapshot. A Sol xhigh review of the branch found four issues
covering probe and tester ownership, B147 Git coverage, and preserving a
contradictory SQL runner CID. Those paths were fixed and regression cases were
added. The incremental Sol xhigh review found three test defects: assertions
were scoped to the wrong test, expected an old Git command shape, and expected
obsolete cleanup text. Those assertions are corrected.

Shell syntax, Python source compilation, and working-tree whitespace checks
pass. No tests or gates have run on the corrected tree yet. The next step is the
registered `tester-unified` gate on the final committed tree, then a serial
no-ff merge to `main`. B146 stays deferred; release remains grouped with the
planned v14/8.0.0 wave.

## Controller continuation: first registered gate result (2026-10-06)

The registered `tester-unified` gate ran on commit
`4c4aa3019f3a9f9d01002bb7e8e6672a16a23b83` and returned FAIL after 775.14s:
11 failed, 7,536 passed, and 11 skipped. The live B145 probes and packaging /
attestation steps passed. The failures were in stale test assertions and test
fixture wiring exposed by the B145 / SQL ownership changes, plus one ShellCheck
warning; no failure was waived. The focused regression set now passes all 11
cases, including exact physical-host, workspace, and socket mount checks.

The corrective delta received a Sol xhigh review. It found that the SQL mount
test did not bind its expected socket source; that assertion is now exact.
`bash -n`, ShellCheck, and `git diff --check` pass on the current working tree.
This corrected tree has not yet had a registered gate. Commit it, then rerun
`tester-unified`; merge only the final gate-green commit. Release remains
grouped with the planned v14 / 8.0.0 wave; B146 remains deferred.
