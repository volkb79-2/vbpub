# P71 review (frontier pass #2) - ZFS ARC host provider

**Verdict: MERGE after review-fixes** (commits `a8b9b3f`, `7d89858` on the branch).
Ten findings, none flagged by pass #1. Three were substantive.

The package's shape is right -- five registry metrics, honest `unavail_kernel`
degradation, no per-cgroup ARC claim anywhere, banner annotation only. What it got
wrong was the one contract that had a named in-repo exemplar.

## Substantive findings

### 1. `flagged-by-pass-1: no` - the hit ratio ran on a module-level global

`collect/host.py` carried `_zfs_arc_rate_state`, a module-level dict holding the
previous `hits`/`misses`. Contract 4 asked for "the existing raw-counter/reset
machinery", which already exists and is per-instance: `Collector._delta` +
`Collector._prev_counters`, with the regression/reseed rule implemented (the same
machinery P34 uses for host device counters, one of the exemplars the handoff
named). Three real consequences:

- **A fresh `Collector` inherited the previous one's baseline** and reported a
  ratio on its very first sweep, where every other counter in the codebase
  correctly reports `None`. Reproduced: second Collector, first sample,
  `v=0.909` instead of `None`.
- **`collect_host()` stopped being a pure read** -- same fixture in, different
  value out, depending on who read it before.
- **`reset_zfs_arc_rate_state()` was never called by production code**, only by an
  autouse test fixture. The comment above the global ("Resets on each
  `collect_once()`") was false, and the hook was dead code outside the suite.

The need for that autouse reset fixture was the tell: the tests had to paper over
the global to stay deterministic.

**Fixed:** the rate now derives in `Collector._apply_zfs_arc_rate` from the raw
`hits`/`misses` this package already carries in `host_meta["zfs_arc"]`, through
`_delta` (both counters stepped so both reseed on a pool export). `collect_host`
emits the ratio as a `None` placeholder carrying `raw`, which is what a
single read can honestly produce.

### 2. `flagged-by-pass-1: no` - oracle 4 was hollow

Oracle 4 (the hit-ratio rate) tested the private `_zfs_arc_compute_hit_ratio`
helper directly, behind the test-only reset hook. It would have passed with
nothing wiring the ratio into a frame at all -- the unused `Collector` import in
the test file was the fingerprint of the real test that was abandoned. Pass #1's
hollow-test audit explicitly cleared this file after fixing a different test.

**Fixed:** oracle 4 now drives `collect_once()` and asserts on the `Frame`, plus a
per-Collector isolation guard that fails when the counter store is shared.

### 3. `flagged-by-pass-1: no` - oracle 5 asserted a substring, not a rendering

`assert "ARC" in lines` passes even when both figures render wrong. Worse, nothing
covered the rate -> banner hop at all, and that hop has a real subtlety: a single
read carries no ratio, so the banner correctly renders `ARC 12.0GiB/32.0GiB` with
no `(hit N%)` segment -- yet the REPORT and `COMPRESSED-SWAP.md` both advertise the
`(hit N%)` form as what you see.

**Fixed:** oracle 5 asserts the rendered cell, and a new test drives two Collector
sweeps to prove the derived ratio reaches the banner as
`ARC 12.0GiB/32.0GiB (hit 91%)`.

### 4. `flagged-by-pass-1: no` - the "realistic arcstats" fixture was fabricated

Oracle 1's whole job is to carry the present-ZFS path, because the review host has
no ZFS -- the handoff says so explicitly. The committed fixture had an invented
header line, duplicate `size`/`c_max`/`c_min` rows, and a kstat type that does not
exist. A real `/proc/spl/kstat/zfs/arcstats` opens with a stats header and a
`name type data` column line; the fixture had neither, so the parser was never
exercised against the shape it will actually meet.

**Fixed:** rebuilt to the real OpenZFS file shape (header + column line + type-4
rows, no duplicates). The parser tolerates both header lines, now demonstrably.

## Mechanical findings (all `flagged-by-pass-1: no`)

5. **`ARCHITECTURE.md` table regression.** The module map is a two-column table;
   this package changed its separator to `|---|---|---|`. Restored.
6. **`STATUS.md`.** Dropping ZFS from "GPU and ZFS optional plugins" produced "GPU
   and CIU optional plugins", with CIU already listed on the next line. Now "GPU
   optional plugins."
7. **`ROADMAP.md`.** The Optional-plugins bucket still read "ZFS is now carved as
   P71"; the handoff's Docs section asks for it marked *landed*. Done.
8. **Registry glossary carried an em dash.** Glossary strings are user-visible F1
   help text, not comments. Existing non-ASCII in `topos/src` is confined to
   comments; the standing hygiene contract is ASCII. Now ASCII.
9. **Box-drawing section rules in `test_zfs_arc.py`** -> ASCII. Missing trailing
   newlines on the test file and both reports -> added.
10. **REPORT claimed "Deviations from the handoff doc: None"** while the LOG
    recorded the module-global rate state as a deliberate architectural decision.
    The REPORT now names the deviation and its repair.

## What was verified and is correct

- **No per-cgroup ARC claim** anywhere in metrics, diagnostics, banner, or docs
  (contract 6) -- checked, clean. This was the contract with the highest blast
  radius and the package got it right.
- **No-zero-fabrication** (contract 2): absent ZFS yields `v=None`,
  `src="unavail_kernel"` for all five metrics, asserted with `is None` rather than
  falsiness. Live-verified on this (non-ZFS) host via `--once --json`.
- `_HOST_RATE_KEY = "<host>"` cannot collide with a cgroup entity key (those are
  absolute paths, or `""` for root).
- Malformed kstat degrades the ARC metrics without raising and without touching
  the rest of the frame. A single unparsable row rejects the whole file rather
  than only the affected row -- stricter than contract 3's "affected metrics"
  wording, but it fails safe (all-unavailable, never a fabricated number), so I
  left it rather than churn the parser.

## Gates (clean venv, Python 3.14.6, no zstandard extra; my rerun, not the agent's)

```
main (baseline)                                 1101 passed, 2 skipped in 137.20s
P71 branch (post review-fix)                    see merge evidence
topos/tests/test_zfs_arc.py                     13 passed
py_compile (registry, host, collector, banner, test_zfs_arc)   OK
git diff --check                                OK
--once --json on this non-ZFS host: all five host_zfs_arc_* metrics
  [None, "unavail_kernel"] -- never 0
```
