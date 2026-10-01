# P91 -- Recoverable age-and-byte-capped daemon history -- INDEPENDENT REVIEW

> **Port-forward note (2026-09-08):** this document is the ORIGINAL
> 2026-07-15 review, `groop`->`topos` substituted for consistency with the
> current tree (including the branch name/commit hash below, which actually
> read `feat/groop-P91-persistent-capped-history` @ `b720f4d9` at review
> time -- see `P91-LOG.md`'s provenance section for the real original
> identifiers and commit hashes, `b720f4d9`/`939b839d`). It reviewed the
> design, contracts, and O1-O10 test coverage that this port carries
> forward unchanged, and its **APPROVE** verdict stands for that scope. It
> did NOT and could not review this port's own reconciliation work: the
> cli.py/daemon/__init__.py merge, the `component_health.py`
> `COMPONENT_NAMES` fix, or the ~35 tests added afterward to close a
> diff-coverage floor that did not exist in 2026-07-15 (bootstrapped by
> P96, after this review). Those need their own adversarial pass before
> merge -- see `P91-LOG.md`'s Handoff Checklist for the specific open
> items.

# P91 — Recoverable age-and-byte-capped daemon history — INDEPENDENT REVIEW

**Reviewer role:** independent frontier merge-gate reviewer (did not author the
work).
**Date:** 2026-07-15
**Branch reviewed:** `feat/topos-P91-persistent-capped-history` @ `b720f4d`
(diff `main...HEAD`: 13 files, +2276 / −14).
**Verdict:** **APPROVE.** No blocking defects found; no code changes were
required.

## What I did

1. Read the handoff contract, the diff, and the implementer's LOG/REPORT.
2. Adversarially checked the tests against every acceptance oracle (O1–O10) for
   hollowness, overclaim, and edge-case gaps.
3. Re-ran BOTH declared gates myself from a fresh gate venv against the branch
   head — I did not trust the report's pasted output.

## Gate re-run (independent, not the implementer's paste)

Gate venv `/tmp/handoffctl-gate-b1c4b05f` (matching `project.toml`'s
`topos-suite` argv), Python venv freshly `pip install -e topos[dev]`:

- **topos-suite:** `1697 passed in 183.84s` — **zero skips**
  (`grep -c skipped` → 0). Exit 0.
- **py-compile:** `py_compile` of all changed `*.py` — clean.
- **`git diff --check main...HEAD`:** clean.

The reported 1697/zero-skip figure reproduced exactly. The Contract-1 default
change (`HistoryConfig.full_resolution_seconds` 14400→300,
`downsample_retention_hours` 4→24) did not regress any pre-existing test
(the only runtime consumer, `HistoryRing.from_config` via `capacity_for_interval`,
is exercised by `test_snapshot_bundle.py` and stays green).

## Oracle-by-oracle verification

| Oracle | Test(s) | Verified substantive? |
|---|---|---|
| O1 age eviction | `test_o1_frames_older_than_age_cap_are_evicted` (+neg) | Yes — asserts the over-age ts is absent from `read_frames()`, not merely a counter. |
| O2 byte eviction, oldest-first | `test_o2_byte_cap_evicts_oldest_segment_first` | Yes — asserts `byte_size <= cap`, newest survives, oldest gone. |
| O3 both caps together | two tests (byte-generous/age-tiny and vice-versa) | Yes — each isolates one cap and proves the other still fires. |
| O4 restart recovery within caps | recovery-evicts + bounded-scan tests | Yes — reopen with advanced clock + tighter cap evicts at recovery, not lazily. |
| O5 torn write | torn-segment + torn-index tests | Yes — atomic temp+rename design; leftover `.tmp` cleaned, neighbours survive. |
| O6 corrupt middle segment | quarantine-with-gap + query-stays-up tests | Yes — checksum mismatch → quarantined (file preserved), explicit gap, query does not raise. |
| O7 disk-full / read-only | 3 tests (publish OSError, recovery, mkdir PermissionError) | Yes — `append()`/construction never raise; `degraded`/`write_errors` set. |
| O8 truthful gap/eviction reporting | clean-continuous + eviction + O6 query tests | Yes — and NOT hollow: `engine.py:495/509` consumes the explicit `gap_before` flag, so the contiguous store-local `seq` cannot mask a hole. |
| O9 byte-deterministic recovery | plain + zstd round-trip tests | Yes — `frame_to_jsonable` equality across reopen. |
| O10 24h measurement | `MEASUREMENTS.md` + `P91-measure-history*.py` | Recorded artifact (see note 2). |

Adversarial probes that could have exposed a hollow pass but did NOT:
- Confirmed the query gap path is real by reading `engine.py` — `gap_before`
  drives `gap_count`/`complete`/`evicted`, so a persistent source with a
  contiguous `seq` still reports O6/O8 holes correctly.
- Confirmed every `ComponentHealthRegistry` method the new `daemon serve` wiring
  calls (`mark_disabled`, `record_degraded`, `record_success`,
  `mark_stopping`) exists — the enabled-path CLI wiring will not crash at
  runtime.
- Confirmed the new package imports resolve (`topos.daemon`, `topos.query`,
  `topos.cli._persisting_frame_stream`).

## Non-blocking observations (recorded, not fixed — none warrant a change)

1. **Byte-cap eviction counts only published segments, not the in-progress
   active segment.** Transient on-disk overshoot is bounded by one segment;
   O2's cap is on retained/published data. Acceptable.
2. **O10 is an artifact, not a per-commit regression test.** The oracle's
   negative ("enabled by default without the measurement recorded") cannot
   occur because `PersistConfig.enabled` defaults to `False` AND the
   measurement is recorded in `MEASUREMENTS.md`. Satisfied both ways.
3. **A segment corrupted *after* recovery but before a live `read_frames()`**
   is skipped (`PersistStoreError` → `continue`) without flagging a fresh gap,
   since it is still present in `_segments` metadata. Narrow in-process race;
   recovery on next restart quarantines it and reports the gap. Acceptable.
4. **Ships disabled-by-default despite the O10 budget being met.** Documented
   as a deliberate, conservative product decision (flipping disk-write-by-
   default warrants human sign-off). Contract 6 mandates *not enabling without
   measurement*; it does not force enabling. `daemon serve` is behaviourally
   identical to pre-P91 unless an operator opts in. Defensible.

## Escalation check

Neither `escalate_if` condition is present: both retention caps ARE enforced
after crashes (O4 recovery-eviction test proves it), and corruption recovery
quarantines (never deletes) rather than silently discarding acknowledged
history (O6). No escalation required.

## Verdict

**APPROVE** — contracts met, oracles covered by substantive tests, both
declared gates independently green with zero skips, no defects requiring a fix.
Not merged (manual merge lane, per role contract).
