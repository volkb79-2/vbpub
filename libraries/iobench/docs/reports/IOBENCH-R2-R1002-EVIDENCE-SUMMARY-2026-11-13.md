<!-- origin: 2026-11-02 to 2026-11-03 -->
<!-- status: summary of raw historical evidence retained outside Git -->
<!-- source: evidence/iobench-r2-r1002/ -->

# IOB-R2-01 raw evidence summary

## What and when

This is the raw evidence set for iobench's first full native Assay R2 campaign,
run 2026-11-02 to 2026-11-03 on r1002. The retained source directory contained
133 files totalling 31,058,728 bytes when inventoried on 2026-11-13. It
includes campaign/verdict JSON and text, progress, survivor and
budget-exceeded CSVs, serial replay results, run logs, and supporting archives.
The raw files remain at
`$CONTROLLER_HOME/evidence/iobench-r2-r1002/`.

## Run and key numbers

The judged source was commit
`8d913b637cf30a52eb5afd8df845283f31c5c735`. The campaign used Run-Gate 24.3.0,
Assay 11, the registered `r2` lane, `jobs = "auto"`, a 1,536 MiB lane memory
cap, and a prepared btrfs cheap-clone workspace. It selected 1,220 candidates:
1,215 reached terminal results, 897 were killed, 318 survived, and five
exceeded the candidate budget. There were no equivalent mutations, crashes or
hangs. The five timeouts were replayed serially and again exceeded their
budgets. The campaign remains incomplete; this summary does not change that
verdict.

## Regeneration

Use the source commit above and its `libraries/iobench/run-gate.toml` with the
historical Run-Gate 24.3.0 and Assay 11 environment, then run the registered
`r2` lane from the project checkout. Run-Gate adds the required Assay resume
and progress arguments for that lane; retain the verdict, progress, CSV/JSON
artifacts and full campaign output. Recreate the btrfs cheap-clone workspace
and the lane's 1,536 MiB cap before starting. A rerun creates new evidence and
may not be byte-identical; the report does not claim a replay can reproduce
the original raw files exactly.

The repository's [campaign report](IOBENCH-R2-R1002-2026-11-02.md) contains
the detailed configuration, candidate classifications and resource results.
