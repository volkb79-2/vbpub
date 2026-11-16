# Recorded R2 candidate inventories

One JSON file per recorded `assay plan r2 --json` measurement. `tests/test_r2_lane_contract.py`
uses these to check that `assay.toml`'s `max_mutants` sits above every recorded inventory with the
declared 10% headroom (PWMCP-59). Each file records where the number came from; a new release plan
adds a file, it never edits an old one (the record is evidence).

Fields: `recorded` (UTC date), `kind` (`measured` = an `assay plan r2 --json` run on a real commit;
`planned` = the release plan's own inventory), `commit` (the commit the plan was computed for, or
`null` when the plan names a release tag instead), `candidate_count`, `source` (the record that
states the number), and `measured_with_max_mutants` when the reading was taken with a cap other
than the shipped one.

**A plan run AT the lane's cap is not an inventory.** Assay stops discovery at the cap and reports
`max_mutants + 1` candidates (its own filed item B526); that number measures the CAP, not the code. So a refresh either
records a reading taken with a cap comfortably above the expected count (a scratch worktree commit
raising `max_mutants` is the documented way, since a plan refuses an uncommitted lane file), or is
not recorded at all.

The cap-choice rule lives in `docs/CONSUMERS.md` "Release gates"; the obligation to refresh an
inventory before changing the cap is the same section.
