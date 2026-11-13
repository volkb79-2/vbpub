<!-- origin: 2026-11-02 Assay jobs pilot -->
<!-- status: summary of raw historical evidence retained outside Git -->
<!-- source: evidence/jobs-pilot3/ -->

# Assay jobs pilot 3 raw evidence summary

## What and when

This evidence set records the jobs=1/jobs=3 comparison reviewed on
2026-11-02. Its raw source contained 1,179 files totalling 23,655,197 bytes
when inventoried on 2026-11-13. It includes per-arm state records, progress,
verdicts, replay results and mutated-source details. The raw files remain at
`$CONTROLLER_HOME/evidence/jobs-pilot3/`.

## Run and key numbers

The analysis used the mutated source at `5fd78a8e4` and compared the
jobs=1 and jobs=3 arms with fresh isolated serial replays. Three of 56
candidates had bucket disagreements in the jobs=1 arm. All three agreed with
the isolated replay in jobs=3; the evidence tied two mismatches to an
unmutated run-gate test failure and treated the third attribution as an
inference. The measured speed-up was 2.61x, with a 3.49 GiB peak under the
5 GiB high-water mark. The analysis recommended Assay 11.0.0 and recorded the
flaky-test limitation; see the linked analysis for its full reasoning.

## Regeneration

A comparable rerun needs the pilot's mutated source revision, Assay 11
environment, original arm configuration and launcher, then fresh jobs=1 and
jobs=3 campaigns plus isolated serial replays. The original launcher and
per-arm inputs are in the retained raw directory; this summary does not
reconstruct that controller setup. Save each arm's state, progress, verdict
and replay outputs alongside the source revision. The campaign report is
[ASSAY-JOBS-PILOT-ANALYSIS](ASSAY-JOBS-PILOT-ANALYSIS-2026-11-02.md).
