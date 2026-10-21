# iobench IMPLEMENTATION-NOTES (IOB-03 P3)

Progress record and deviations for the controller. Not user documentation (README / DESIGN-GUIDE / CONSUMERS own that).

## Progress (update as work proceeds)

Done and tested on FakeHost (targeted pytest, branch coverage tracked per module):
`series`, `extents`, `host` (protocol + RealHost), `devio`, `testing` (FakeHost/FakeFio, content-based cache), `plan`, `cache`, `guard`,
`ladder`, `shape`, `sweep`, `derive`, `ceilings`, `restore`, `result`, `plausibility`, `proposals`, `stages`, `calibrate`, `matrix`, `dryrun`.

Remaining: see the last section.

## Deviations, assumptions and BLOCKED items

* BLOCKED sub-assertion IOB-CAL-01 (numbers): the oracle text gives the densification rungs for the 270 GiB cliff scenario as 299.3 and
  322.7 GiB "log-equispaced between 256 and 345 GiB". Log-equispacing 256..345.04 GiB with two interior points gives 282.8 and 312.4 GiB
  (299.3/322.7 correspond to log-thirds between about 277 and 348 GiB, not to the stated bounds). Implemented the stated RULE
  (`plan.densification_rungs`); the test asserts 282.8 / 312.4. Controller: either fix the numbers in SPEC or tell me the intended bounds.
* IOB-SHP-04 vs IOB-PLA-11 (poor fit): SHP-04 says model keys are `absent` (stage_failed-style) with `unproven: shape_fit_poor` for the
  dependent QoS; PLA-11 says "model keys unproven: shape_fit_poor". Implemented SHP-04 (model keys absent: stage_failed; QoS `max`
  unproven: shape_fit_poor); the PLA-11 check is recorded as `fail` with `degrades: [iocost_model, iocost_qos]`.
* IOB-CAL-06 says "sequential 1 MiB QD16 cell of S6", IOB-SHP-01 says every S6 cell runs at QD32. Implemented SHP-01 (QD32); the
  sequential 1 MiB QD32 rewrite cell is the BPS basis.
* IOB-SHP-01 anchors: "anchors repeated at start, middle and end" is costed in the section-15 table as 60 s of anchor repeats. Implemented:
  the four anchors are measured first (inside the 22 cells), the two WRITE anchors are repeated at the middle and at the end as 15 s cells
  (4 x 15 s = 60 s); the read anchors are measured once. The repeated write anchors also feed `windows.write_seq1m_bps` (best/worst for
  R6 `max` when there is no ceiling history).
* Observation runs log at 1 s (the other jobs at 2 s) so that a 2 s burst is visible to `reduce_series`; the median smoothing would hide a
  one-sample burst. SER-01 allows <= 2 s.
* The fio `--io_size` with `--time_based`: SPEC dictates io_size as the byte cap. Whether fio stops at the earlier of runtime and io_size
  when `time_based` is set must be confirmed in the r1002 acceptance probe (P4); FakeFio implements "earlier of the two".
* Dry run (`--dry-run`) cannot measure S1: it uses `--assume-r-est` or the R_est READ from an existing calibration.json, else refuses
  (never invents a rate).
* The sweep REF cell runs the formula triple (R2, R3 candidate, R1), so "chosen triple's REF probe" (R4) is evidence about the proposal itself.
* File targets: `free` for `WS_max = min(size - fresh - tail, 0.85 x free - margin, ...)` is the space that was free BEFORE the file was
  preallocated (`fs.free + size`). Where a file-target cache is hit-like without a cliff the reason is `device_smaller_than_cache_margin`
  (IOB-TGT-10 text) instead of `no_cliff_hit_like_mode`.
* `check` accepts a newer schema minor (unknown keys are ignored); only an unknown major is refused.
* Reporting threshold for `mean_overstates` (PLA-04, info only): 1.10 x steady.

## Remaining at the time of writing
CCH-08 fixtures (the E1-good-but-wrong fixture is not yet reproducible, see below), EVD replay fixtures, STA-05 audit test, DOC/GATE tests,
CLI + cli-extended surface/review artefacts, pyproject / cmru.toml / .coveragerc / probe_oracles.txt, SAF integration tests,
ARC-04/05 integration tests, LAT, CER, coverage to 100 %.

BLOCKED: IOB-REL-01 zipapp + sha256 -- "wheel plus zipapp plus sha256": cmru has no zipapp handler (`cmru handler` offers wheel-build and
wheel-publish only) and a zipapp cannot bundle the cli-extended wheel dependency without a project-owned build script (assay owns
`gate/distribution/build_release.py` for that). cmru.toml ships the wheel through `wheel-publish` (which publishes its sha256). Needs a decision:
project build script (like assay) or wheel only. Not invented here.
