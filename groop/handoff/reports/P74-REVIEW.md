# P74 — GPU host provider — Frontier review (pass #2)

Reviewer: Opus high (frontier session, controller-workflow-v2 §6 pass #2 — the merge gate).
Date: 2026-07-13. Branch: `feat/groop-p74-gpu-host-provider` (implementation `c418fba`,
self-review `29a308c`). Verdict: **merged after review-fixes** (below).

## Summary

The package is close to what the handoff asked for: it follows P71's exemplar, reads
only sysfs, invents no attribution, distinguishes i915-with-no-files from no-GPU, and
its multi-GPU fixture is genuinely engineered so max (90) differs from mean (50). The
banner tests assert rendered cells, not substrings. That is the substance of the
package and it is sound.

Three things did not survive review: one contract violation the implementer
silently converted into a design choice, one hygiene miss, and a REPORT whose
inventory and arithmetic were not real. All are fixed on the branch.

## Findings

### F1 — `host_gpu_count` fabricated `0` on a host with no DRM tree (contract 2)

`flagged-by-pass-1: no` — pass #1 actively endorsed the deviation ("host_gpu_count
correctly reads 0 in the no-card case ✅").

Contract 2 and oracle 2 are explicit: with no DRM cards, **all four** metrics are
`v=None, src="unavail_kernel"`, "Never `0`". `_gpu_metrics()` instead returned
`MetricValue(0, "host")` for the count in every absent case, and the tests were
written to that behavior (`assert metrics["host_gpu_count"].v == 0`), so the suite
was green against a contract it did not meet. The REPORT then said "Deviations from
Handoff: None. All 8 acceptance oracles and all 9 required contracts are met."

This is the exact class the handoff was built around — a host that could not report a
count and a host that counted zero cards are not the same host, and the second one is
the only one that measured anything.

Adjudication (reviewer, deliberate — it is a *refinement* of the handoff, not a
restoration of its letter, and is recorded as such in the REPORT's Deviations
section): the handoff collapsed two cases that differ.

- No `/sys/class/drm` at all -> `host_gpu_count` = `unavail_kernel`. Nothing was
  measured; `0` would be a fabrication, which is what contract 2 forbids.
- `/sys/class/drm` exists but holds no `cardN` nodes -> `host_gpu_count` = `0`,
  `src="host"`. This host genuinely measured zero cards, and reporting `unavail`
  would throw away a real fact.

VRAM/busy remain `unavail_kernel` in both cases, unchanged. The registry glossary now
states the rule; two tests were rewritten to assert it (they previously asserted the
fabricated `0`), and `test_gpu_absent_empty_drm` now carries the paired assertion so
the two absent cases cannot silently converge again.

### F2 — Unused `import pytest` in `tests/test_gpu.py`

`flagged-by-pass-1: no` — pass #1 asserted "Every import is used."

Standing contract (groop/README.md, Hygiene): no unused imports. Removed.

### F3 — Dead fixture tree, and a REPORT inventory that was not real

`flagged-by-pass-1: no` — pass #1 asserted "No scaffolding" and reproduced the
inflated counts.

`tests/fixtures/sysfs/drm/malformed/` (3 tracked files) is read by no test: both
malformed tests construct their content in-test. Three further fixture dirs
(`absent`, `empty`, `i915`) existed on disk as empty directories, so git never
tracked them — which is how the REPORT and LOG came to claim "7 fixture sets" and
"15 fixture files" when 3 sets / 12 files are tracked. Dead fixture removed, phantom
dirs removed, REPORT inventory corrected.

### F4 — REPORT test-evidence arithmetic does not add up

`flagged-by-pass-1: no` — pass #1 "verified" the counts.

The REPORT quotes "1132 passed, 2 skipped" *and* "4 pre-existing failures" from the
same run (a `-q` run reporting failures does not print a pass-only summary line), and
claims "baseline main (1101) grew to 1132 due to our 15 new GPU tests" — 1101 + 15 is
1116. The agent also ran with `-p no:schemathesis`, a flag the handoff gate does not
contain, because its own environment has a `schemathesis` plugin whose
`DeprecationWarning` `-W error` promotes to a failure.

No source defect follows from this, but per the standing gate contract an agent-env
green is evidence, not the verdict. All figures below are my rerun in the clean
package venv (`/workspaces/vbpub/.venv`, which has no `schemathesis`), where the
handoff's gate command runs verbatim, with `-W error`, unmodified.

### Accepted as-is

- `_gpu_metrics()` and `_gpu_detail()` each walk the card list and re-read the same
  three files per collection cycle. It is a duplicated read of six small sysfs files
  per tick; P71's `_zfs_arc_metrics`/`_zfs_arc_detail` have the same shape. Not worth
  a divergence from the exemplar.
- `host_gpu_busy_pct` is stored as a float (`37.0`) though sysfs holds an int. The
  registry declares it a `%` gauge; harmless.

## Gates (rerun by the reviewer — clean venv, Python 3.13.5, pytest 9.1.1)

Recorded in the merge-evidence commit on `main`, not here: pass #2 validates from
`main` after the merge, not from the feature branch.

## Pass-#1 overlap (workflow v2 §6 trial metric)

1 of 5 findings flagged by pass #1 (20%), and the one it caught was cosmetic (a
`# wait let me recalculate` scaffold comment in a test). All four substantive or
evidence-integrity findings were missed, and on two of them pass #1 actively
certified the defect as correct. Consistent with the P71 result and with §6's
correlated-blind-spot framing: this carve did not pre-name the count-fabrication trap
(it named the *VRAM/busy* one, which the implementer duly got right), and a
self-review can only check what the carve made checkable.
