# Recorded R2 candidate inventories

One JSON file per recorded `assay plan r2 --json` measurement. `tests/test_r2_lane_contract.py`
uses these to check that `assay.toml`'s `max_mutants` sits above every recorded inventory with the
declared 10% headroom (PWMCP-59). Each file records where the number came from; a new release plan
adds a file, it never edits an old one (the record is evidence).

Fields: `recorded` (UTC date), `kind` (`measured` = an `assay plan r2 --json` run on a real commit;
`planned` = the release plan's own inventory), `commit` (the commit the plan was computed for, or
`null` when the plan names a release tag instead), `candidate_count`, `source` (the record that
states the number).

The cap-choice rule lives in `docs/CONSUMERS.md` "Release gates"; the obligation to refresh an
inventory before changing the cap is the same section.
