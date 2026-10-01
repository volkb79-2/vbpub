# P91 -- Recoverable age-and-byte-capped daemon history -- REPORT

## Port-forward addendum (2026-09-08)

This document's body (below the divider) is the **original 2026-07-15
REPORT**, `groop`->`topos` substituted. Its contract-by-contract coverage,
oracle-to-test mapping, and recorded O10 measurements are still accurate --
the design and code are unchanged by this port, only their location and
package name. What changed in porting this forward:

- **Files, as ported** (see `P91-LOG.md`'s provenance section for the full
  merge-tree analysis): `config.py`, `query/__init__.py`, `query/source.py`,
  `MEASUREMENTS.md`, `docs/DAEMON.md` applied via git's own clean auto-merge;
  `daemon/persist.py`, `tests/test_persist_history.py`, and this file's
  siblings (`P91-LOG.md`, `P91-REVIEW.md`, the two `P91-measure-history*.py`
  scripts) recreated at their `topos/` paths with `groop`->`topos`
  substituted; `cli.py` and `daemon/__init__.py` hand-reconciled against
  real drift on `main` since the fork (detailed in `P91-LOG.md`).
- **One genuine bug found and fixed during the port** (not present in the
  original branch's own file list, and not touched by the original review):
  `daemon/component_health.py`'s `COMPONENT_NAMES` tuple never included
  `"persistent_history"`, so every health-registry call this package's CLI
  wiring makes was a silent no-op -- in the original 2026-07-15 branch too,
  since that file was last touched by P47 and neither P91 nor this port's
  reconciliation modified it until this fix. See `P91-LOG.md`.
- **The topos-suite gate now includes a 100% diff-coverage floor**
  (`topos/tools/coverage_gate.py`, bootstrapped by P96 -- after this
  package's original approval, so `groop-suite`'s 2026-07-15 green run never
  exercised it). Closing that gap added real tests for previously-untested
  functionality (documented in `P91-LOG.md`) and two `# pragma: no cover`
  exclusions for lines confirmed unreachable given the current call graph.

## Gate results (port-forward, 2026-09-08)

Dockerized `tester-unified` environment via `topos/run-gate.py --worktree`
(not a paste from the implementer's own claim -- the same declared lanes the
original review independently re-ran, `topos-suite` and `py-compile`, per
the handoff's `gates:` field), run against the final commit:

```
$ python3 topos/run-gate.py py-compile --worktree <worktree>
py-compile: OK
run-gate: lane 'py-compile' exit 0

$ python3 topos/run-gate.py topos-suite --worktree <worktree>
2986 passed in 78.84s (0:01:18)
diff-coverage OK: 528/528 changed executable lines covered (100.0% >= 100.0% floor)
run-gate: lane 'topos-suite' exit 0
```

Commit gated: `c9d70520` (the tip before this REPORT/LOG commit; report
artifacts are not gated code and do not change coverage). Full progression
across four gate rounds -- 82.7% -> 97.3% -> 99.4% -> 100.0% -- and what each
round closed is in `P91-LOG.md`'s "Closing the topos-suite diff-coverage
gate" section.

---

# P91 — Recoverable age-and-byte-capped daemon history — REPORT

**Date:** 2026-07-15
**Branch:** `feat/topos-P91-persistent-capped-history` (worktree
`/workspaces/vbpub/.worktrees/feat/topos-P91-persistent-capped-history`, based
on this branch's parent commit `133cd16`)
**Status:** Complete. All six Required Contracts implemented, all ten
Acceptance Oracles covered, focused + zero-skip full-suite gates green. No
`BLOCKED` trigger fired.

## What was built

D-005's persistent tier: canonical frames are batched into small,
atomically-published segment files under an operator-configured directory,
capped simultaneously at 24 hours and 256 MiB, recoverable from a crash at any
point, and queryable through P88's existing engine with no second aggregation
path.

| File | Role |
|------|------|
| `topos/src/topos/config.py` | `PersistConfig` (new) — `[history.daemon]` section matching TUI-SPEC.md's already-recorded schema (`enabled`, `dir`, `max_age_days`, `max_size_mb`, `compression`, `segment_frames`, `checkpoint_frames`, `fsync`, `dir_mode`, `file_mode`), validated in `__post_init__`. `HistoryConfig.full_resolution_seconds` default reconciled 14400→300 and `downsample_retention_hours` 4→24 per D-005/Contract 1. `to_primitive()`/`digest()`/`load()` updated. |
| `topos/src/topos/daemon/persist.py` | `PersistentHistoryStore` (new) — the store itself: segment writer/reader, atomic publish, index persistence, age+byte eviction, crash recovery with checksum re-verification, quarantine, disk-full/read-only degradation, `stats()`. |
| `topos/src/topos/query/source.py` | `PersistentHistoryFrameSource` (new) — thin `FrameSource` adapter over `PersistentHistoryStore.read_frames()`, reusing P88's existing gap/eviction semantics (`gap_before`, `evicted`) with zero changes to `engine.py`/`semantics.py`. |
| `topos/src/topos/cli.py` | `_persisting_frame_stream` (new) + `_main_daemon serve` wiring — the store is fed the exact same canonical frame stream the RAM-tier `FrameBroker` receives (tee, not a second collector), with component-health reporting and flush-on-shutdown. |
| `topos/src/topos/daemon/__init__.py` | Exports `PersistentHistoryStore`, `StoreStats`, `SegmentInfo`, `GapRange`, `PersistStoreError`. |
| `topos/src/topos/query/__init__.py` | Exports `PersistentHistoryFrameSource`. |
| `topos/tests/test_persist_history.py` | 31 tests: every numbered oracle (O1-O9, each with a positive and, where applicable, an explicit negative case) plus config validation/round-trip, file permissions, and RAM/disk dual-feed integration. |
| `topos/nyxloom-trove/reports/P91-measure-history.py`, `P91-measure-history-scaled.py` | O10's 24h synthetic workload measurement scripts (fixture scale and D-005 production scale), reproducible via `python3 nyxloom-trove/reports/P91-measure-history*.py` from `topos/`. |
| `topos/docs/DAEMON.md` | "Retention" section rewritten from D-005-aspirational to describe the implemented store. |
| `topos/MEASUREMENTS.md` | O10 measurement results and budget interpretation appended. |

## Contract-by-contract

1. **Explicit `HistoryConfig` reconciliation.** `full_resolution_seconds`
   default changed 14400→300 (D-005: five minutes at five-second resolution)
   and `downsample_retention_hours` 4→24, matching TUI-SPEC.md's already-
   recorded `[history]`/`[history.daemon]` schema exactly (field names:
   `max_size_mb`, `max_age_days`, `compression`). `PersistConfig` additionally
   exposes `segment_frames`/`checkpoint_frames` (segment/checkpoint settings)
   and `fsync`/`dir_mode`/`file_mode` (permissions/fsync policy). This is an
   explicit, documented default change, not a silent reinterpretation of the
   existing field — the one existing consumer of `full_resolution_seconds`
   (`HistoryRing.from_config`, the TUI sparkline ring) picks up the smaller
   default automatically and is covered by the unchanged full suite.
2. **Canonical frames, persisted once.** `_persisting_frame_stream` tees the
   *same* frame object the RAM-tier `FrameBroker` receives into
   `store.append()` — there is no second collector and no re-derivation.
   `PersistentHistoryFrameSource` hands the store's own `read_frames()`
   result to P88's `run_query` unchanged; no rollup, no aggregation, no second
   query engine (confirmed: `engine.py`/`semantics.py` have zero changes).
3. **Simultaneous age+byte caps, always.** `_evict_locked` sweeps
   oldest-segment-first while `(now - oldest.first_ts) > max_age_seconds OR
   total_bytes > max_size_bytes`, run both after every segment publish *and*
   again during startup recovery (so a cap that was violated across a
   restart, e.g. because the process was down past the age cap, is corrected
   immediately, not lazily on the next write). Eviction keys on each
   segment's *oldest* frame (`first_ts`), which guarantees the O1 invariant
   unconditionally regardless of segment size (see "Deviations" below).
   `StoreStats` reports actual bytes, oldest/newest ts, frame/segment counts,
   gaps, eviction counts, quarantine count, write errors, recovery state and
   compression ratio — everything Contract 3 lists.
4. **Daemon-owned files, explicit permissions.** `PersistConfig.dir` is
   operator-configured (default `/var/lib/topos/history`), never a
   client-supplied path — no protocol op accepts an arbitrary path. Segment
   and index files are `chmod`'d to `file_mode` (default `0o640`) and
   directories to `dir_mode` (default `0o750`) after every write, tested by
   `test_segment_and_index_files_use_configured_permissions`. fsync policy:
   `checkpoint_frames` controls periodic fsync of the in-progress segment;
   segment finalization always fsyncs before the atomic rename regardless of
   the `fsync` flag (durability of a *published* segment is not optional);
   `fsync` only gates the mid-segment checkpoint frequency/overhead tradeoff.
   Partial-write behavior is O5 (below).
5. **Corruption is quarantined with an explicit gap, not silently accepted.**
   Recovery re-hashes every segment file (bounded by the byte cap — at most
   256 MiB of hashing by default, not proportional to uptime) and compares
   against its recorded index checksum. A mismatch, a missing file, or an
   unparseable orphan is moved to `quarantine/` (never deleted) and produces
   a `GapRange` bounded by its two surviving neighbours' own timestamps — no
   precise timestamp is invented for the missing data itself. Disk-full/
   read-only conditions during any write are caught (`OSError`) and degrade
   the store (`stats().degraded`/`degraded_reason`) without raising, so
   `append()` never turns a disk problem into a collection outage; a 30s
   cooldown avoids retry storms against a persistently full disk.
6. **Measured before enabling by default.** `nyxloom-trove/reports/
   P91-measure-history*.py` simulate a full 24h at the default 5s interval at
   both fixture scale and D-005's stated ~447 KiB/frame production scale
   (details in MEASUREMENTS.md). Both stay comfortably within budget — see
   "Recorded measurements" below — but `PersistConfig.enabled` still defaults
   to `False` in this delivery; see "Deviations / decisions".

## Acceptance oracles → tests (all in `topos/tests/test_persist_history.py`)

| Oracle | Test(s) |
|---|---|
| O1 | `test_o1_frames_older_than_age_cap_are_evicted`, `test_o1_negative_frame_within_age_cap_is_retained` |
| O2 | `test_o2_byte_cap_evicts_oldest_segment_first` |
| O3 | `test_o3_byte_cap_alone_does_not_let_an_over_age_segment_survive`, `test_o3_age_cap_alone_does_not_let_an_over_byte_segment_survive` |
| O4 | `test_o4_restart_recovery_applies_current_caps`, `test_o4_recovery_is_bounded_by_directory_listing_not_full_history` |
| O5 | `test_o5_torn_segment_write_is_ignored_and_neighbors_survive`, `test_o5_torn_index_write_does_not_lose_prior_segments` |
| O6 | `test_o6_corrupt_middle_segment_is_quarantined_with_explicit_gap`, `test_o6_query_reports_gap_and_stays_queryable_not_crashed` |
| O7 | `test_o7_disk_full_on_publish_degrades_without_raising`, `test_o7_disk_full_recovers_once_space_returns`, `test_o7_readonly_directory_degrades_without_raising` |
| O8 | `test_o8_clean_store_reports_complete_continuous_series`, `test_o8_eviction_never_reported_as_continuous` |
| O9 | `test_o9_recovered_frames_are_byte_identical_to_originals`, `test_o9_compressed_round_trip_is_byte_deterministic` |
| O10 | `nyxloom-trove/reports/P91-measure-history.py` + `-scaled.py`; results in `MEASUREMENTS.md` — a one-shot recorded measurement, not a per-commit test (see "Deviations"). |

Plus: `PersistConfig` validation (4 tests), config TOML round-trip (4 tests),
`test_segment_and_index_files_use_configured_permissions` (Contract 4),
`test_read_segment_frames_raises_typed_error_on_corrupt_input`,
`test_disabled_store_touches_no_disk`,
`test_persisting_frame_stream_feeds_both_ram_and_disk_tiers`.

## Gate results (environment: agent worktree venv, Python 3.14.6, `topos[dev]`)

Focused:
```
$ /tmp/handoffctl-gate-manual/bin/python -m pytest topos/tests/test_persist_history.py -q
31 passed in ~1.2s
```

Full suite (zero-skip, correct CWD matching the declared gate exactly —
`cd {worktree} && pytest topos/tests`, not `cd {worktree}/topos`, since
several pre-existing tests use `cwd="topos/src"`):
```
$ cd /workspaces/vbpub/.worktrees/feat/topos-P91-persistent-capped-history
$ V=/tmp/handoffctl-gate-b1c4b05f
$ $V/bin/python -m pytest topos/tests -q
1697 passed in 186.82s (0:03:06)
```
Zero skips. 1697 vs the pre-P91 baseline (1666, inferred from 1697 − 31 new)
— no test count regression, no BLOCKED trigger.

Compile + hygiene:
```
$ python3 -m py_compile $(find src/topos tests -name '*.py') nyxloom-trove/reports/P91-measure-history*.py   # clean
$ git diff --check   # clean (no output)
```

## Recorded measurements (O10; full detail in `MEASUREMENTS.md`)

24h synthetic workload (17,280 frames @ 5s interval), default `PersistConfig`,
small per-metric jitter (fixed-seed) so frames aren't byte-identical:

| Scale | Wall | CPU (% of a real day) | Max RSS | Final disk bytes | Compression | Within 256 MiB cap |
|---|---|---|---|---|---|---|
| Fixture (8 entities, 42.7 KB/frame) | 22.4s | 0.024% | 28.8 MB | 18.5 MiB | 35.5x | Yes |
| D-005 production (88 entities, 449 KiB/frame) | 200.4s | 0.228% | 30.5 MB | 161.2 MiB | 43.9x | Yes (63% headroom) |

Segments are write-once (no rewrite amplification for frame data — the
byte-size actually written to disk equals the final on-disk footprint since
nothing was evicted in either 24h run). The only rewrite overhead is the
small `index.json`, republished after every segment publish: 0.19–1.6% of
segment bytes across both scales.

## Deviations / decisions

- **No rollup/downsampling.** Required Contract 2 explicitly forbids the
  store being "a second report/query engine." D-005's DECISION text commits
  only to "batched compressed disk segments... under simultaneous byte+age
  caps"; "older rollups" appears in the *options* discussion as a provisional
  recommendation, not the accepted contract. The existing
  `downsample_interval_seconds`/`downsample_retention_hours` `HistoryConfig`
  fields remain present (their default was reconciled to TUI-SPEC's recorded
  24h for consistency) but are still unused — no consumer beyond digest
  serialization existed before this package either.
- **Segment-granularity eviction, keyed on oldest-frame timestamp.** A
  segment is evicted once its *oldest* frame ages past the cap, which can
  evict some frames slightly before their individual cap would strictly
  require (never after). This unconditionally satisfies O1's "no over-age
  frame remains queryable" regardless of `segment_frames`; the default
  (360 frames = 30 min at 5s) bounds this early-eviction slack to ~2% of the
  default 24h age cap. Tests exercise `segment_frames=1` for exact
  frame-level assertions of the underlying invariant.
- **Checksum mismatch against a recorded index entry is always corruption**,
  never re-validated by content re-parse. Found via manual exploration before
  writing formal tests: a segment whose bytes were corrupted post-hoc but
  happened to remain syntactically valid JSON (mangled key name, `"type"`
  field untouched) would otherwise silently pass a naive "does it parse"
  check despite provably differing from what was durably published. Content
  re-parse is reserved for orphan segments with no recorded checksum (the
  index-write-lost-mid-crash path) — see P91-LOG.md "Decisions" for the full
  reasoning.
- **`PersistConfig.enabled` still defaults to `False`** despite the
  favorable O10 measurement. Required Contract 6's literal fallback text
  ("if the accepted budget is not met, ship it configured off...") implies
  the converse — budget met — is a genuine choice to make, not an automatic
  flip. Enabling disk writes by default for an already-distributed daemon is
  treated as a distinct, explicit product decision (the kind that warrants a
  human sign-off, analogous to D-005 itself), not one a resource measurement
  alone authorizes. `topos daemon serve` is therefore behaviorally identical
  to pre-P91 unless an operator opts in via `[history.daemon] enabled = true`.
- **No dedicated `topos daemon stats` CLI/protocol verb.** `StoreStats` (the
  full Contract-3 field set) is implemented, tested, and available to any
  Python caller (`PersistentHistoryStore.stats()`); the store's health is
  also surfaced generically through the existing `persistent_history`
  component in `topos daemon health` (disabled/degraded/healthy, whichever
  applies). A dedicated `topos daemon stats` command/protocol op — the full
  D-005 operator-facing surface — is deferred as explicit follow-up work
  rather than expanding this delivery's already-large surface area; it is
  additive over the existing `stats()` method and does not change the store's
  on-disk format or any decision recorded here.

## Proposed contract changes

None to `CONTRACTS.md`. The store is additive, daemon-internal code; its
on-disk segment/index format is explicitly not a client-facing contract
(Contract 4: "Files are daemon-owned and not exposed as arbitrary paths").

## Known gaps / follow-ups

- **`topos daemon stats` CLI/protocol verb** exposing the full `StoreStats`
  surface to operators (see "Deviations" above).
- **Leading-gap precision.** A hole at the very oldest edge of retained
  history (the oldest retained segment's id is not 0) is reported only as the
  coarse `evicted` boolean, not a precise range — this is indistinguishable
  from "cleanly aged out" without extra persisted bookkeeping the reduced
  design deliberately avoids (matches the existing
  `DaemonHistoryFrameSource.evicted` convention). Interior holes (both
  neighbouring segments retained) are always precise.
- **D-008 lifecycle facts** are intended to share this same store and caps
  rather than create a second persistence engine (per D-005's decision text)
  — not implemented here; P93/P95 own lifecycle-event work and should build
  on `PersistentHistoryStore` rather than adding a parallel store.
- **`quarantine/` is never auto-pruned.** Corrupted segments are preserved
  for forensics but accumulate indefinitely; an operator-facing cleanup path
  (or a bounded quarantine retention policy) is not implemented.
- **RSS measurement is burst-mode**, not steady-state: the O10 script runs
  17,280 appends back-to-back with no idle time between them, unlike real
  5s-paced collection. This is a conservative (over-stating) ceiling, not an
  under-estimate, but a live daemon measurement (blocked on this development
  host not being a deliberate performance-test host, same limitation as
  P17/P42/P44's BPF/DAMON measurements) would be more representative.
