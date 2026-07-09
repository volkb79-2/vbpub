# P28 Report — I/O Cap Saturation Metric

## What Was Built

- Added `read_io_max_caps()` parser to `groop/src/groop/collect/cgroup.py`:
  - Parses io.max per-device fields `rbps`, `wbps`, `riops`, `wiops`
  - Sums finite caps across devices; handles `max` values and missing fields
  - Adds `io.max:_available` sentinel + per-field entries to `raw_counters`
- Added `io_cap_saturation_pct` derivation in `groop/src/groop/collect/collector.py`:
  - Compares each I/O rate (r/w bytes/s, r/w IOPS) to its matching finite cap
  - Uses the highest ratio × 100; clamps lower bound at 0, allows >100% overshoot
  - Source: `derived` when computed, `unlimited` when io.max readable with all `max`, `unavail_kernel` when unreadable
- Added registry entry in `groop/src/groop/registry.py`:
  - `io_cap_saturation_pct` with unit `%`, branch_policy `local_only`, sources `io.max` + `io.stat`
- Added table support in `groop/src/groop/ui/table.py`:
  - Label: `IO_CAP%`
  - Width tier: 120 (alongside swap_disk, headroom_max_pct etc.)
  - Auto and wide profiles automatically include it
- Diagnostics integration already existed in `score.py`:
  - Default band warn=75%, crit=95%
  - Default weight 0.0 (opt-in)
  - Score contribution when saturation exceeds the configured band
- Added 16 focused tests in `tests/test_io_cap_saturation.py`:
  1-5: io.max caps parser (finite, max, multi-device, missing, empty)
  6: Malformed cap tokens are ignored gracefully
  7: Derivation uses highest ratio
  8: Reset handling/unavailable rate behavior
  9: Overshoot >100% preserved
  10: All-max caps -> unlimited source
  11: Header label `IO_CAP%`
  12: Value formatted as percentage
  13: Appears in auto profile at 120 width
  14: Appears in wide profile
  15: Diagnostics contribution when high
  16: Low saturation -> small contribution
- Updated golden fixture to include `io_cap_saturation_pct` for all entities
- Updated docs: `README.md` P28 → Done, `ROADMAP.md` P28 done, `STATUS.md` diagnostics gap narrowed + quality gate

## Deviations

- Added `io.max:_available` sentinel to raw_counters to correctly distinguish "io.max readable with all max caps" (→ `unlimited`) from "io.max unreadable" (→ `unavail_kernel`). Without this sentinel, the existing raw_counters dict is always truthy (has memory.stat etc.), so the fallback was always `unlimited`.
- The handoff suggested possibly adding a rule for high saturation. The existing `_io_cap_expected_throttle` rule already handles the case where PSI is high AND io.max is capped. Adding a duplicate rule would be noisy, so I left it as-is.

## Contract Changes

- None. The new metric is additive; no existing keys or behaviors changed.

## Test Evidence

```bash
python3 -m py_compile groop/src/groop/collect/cgroup.py groop/src/groop/collect/collector.py groop/src/groop/registry.py groop/src/groop/ui/table.py groop/tests/test_io_cap_saturation.py
# (no output — clean)

python3 -m pytest groop/tests/test_io_cap_saturation.py -v
# 15 passed in 0.10s

python3 -m pytest groop/tests -q
# 216 passed in 29.28s

# Controller review after malformed-token hardening
/tmp/p25-venv/bin/python -m pytest groop/tests/test_io_cap_saturation.py -q
# 16 passed in 0.06s

/tmp/p25-venv/bin/python -m pytest groop/tests -q
# 217 passed in 29.91s
```

## Known Gaps

- The `io_cap_saturation_pct` metric uses the highest single rate/cap ratio. If a cgroup has multiple device caps and some are saturated while others are idle, only the highest is reported. A future enhancement could expose per-device saturation.
- Asset attribution (e.g., which device is saturated) is not included in this metric.
- Network loss/retransmit diagnostics input remains the unimplemented diagnostics gap noted in STATUS.md.
- The existing `_io_cap_expected_throttle` rule (info-level) checks PSI + io_max_capped together, which is complementary but doesn't directly mention saturation percentage.

## Controller Merge Review

- Feature commit(s) on `feat/groop-p28-io-cap-saturation`.
- Pre-merge validation:
  - `python3 -m pytest groop/tests/test_io_cap_saturation.py -v` → `15 passed in 0.10s`
  - `python3 -m pytest groop/tests -q` → `216 passed in 29.28s`
  - `python3 -m py_compile ...` → clean
