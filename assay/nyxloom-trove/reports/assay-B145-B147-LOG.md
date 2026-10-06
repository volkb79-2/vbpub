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
