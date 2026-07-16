# P28 Work Log

## Context

- Branch: feat/topos-p28-io-cap-saturation
- Worktree: .worktrees/-topos-p28-io-cap-saturation
- Base commit: 7af6a37 (docs(topos): carve P28 io cap saturation)
- Package: P28 - I/O cap saturation metric
- Current objective: Parse io.max finite caps, derive io_cap_saturation_pct, registry/table/diag support

## Timeline

```text
2026-07-09
- Action: Read required context: cgroup.py, collector.py, registry.py, table.py, score.py, rules.py, tests
- Result: Understood data flow for io.max parsing and rate derivation

- Action: Added read_io_max_caps() parser to cgroup.py; added sentinel recording in collect_cgroup()
- Files changed: topos/src/topos/collect/cgroup.py
- Result: io.max caps parser sums finite caps across devices, handles "max" gracefully

- Action: Added io_cap_saturation_pct derivation to collector.py _derived_rates()
- Files changed: topos/src/topos/collect/collector.py
- Result: Metric computed from highest rate/cap ratio; clamp at 0, allow overshoot

- Action: Added registry entry, table label/width tier, verified score.py integration
- Files changed: topos/src/topos/registry.py, topos/src/topos/ui/table.py
- Result: Registry entry, IO_CAP% label, 120-width tier, diagnostics already wired

- Action: Added 15 focused tests covering parser, derivation, table display, diagnostics
- Files changed: topos/tests/test_io_cap_saturation.py
- Result: 15/15 pass

- Action: Fixed golden fixture to include io_cap_saturation_pct; fixed sentinel-based src detection
- Files changed: topos/tests/fixtures/frames/gstammtisch-once.jsonl
- Result: 216/216 full suite pass

- Action: Updated docs
- Files changed: topos/README.md, topos/docs/ROADMAP.md, topos/docs/STATUS.md
- Result: P28 done, diagnostics gap narrowed

- Action: Ran full suite validation
- Commands: python3 -m pytest topos/tests -q
- Result: 216 passed in 29.28s
- Follow-up: Write P28-REPORT.md and commit

- Action: Controller review patched P28 before merge.
- Files changed: topos/src/topos/collect/cgroup.py,
  topos/tests/test_io_cap_saturation.py, topos/handoff/reports/P28-LOG.md,
  topos/handoff/reports/P28-REPORT.md.
- Result: Malformed `io.max` cap tokens are ignored instead of raising, reset
  behavior is asserted more tightly, stale report dates/details were corrected,
  and focused/full validation was rerun.
```

## Decisions

- Decision: Use sentinel io.max:_available in raw_counters to distinguish readable vs unavailable
  Reason: raw_counters is always populated with other files' data, so checking "if raw" is truthy even when io.max is unreadable; the sentinel fixes this
  Impact: Correct source labels ("unlimited" vs "unavail_kernel")
- Decision: Sum finite caps across devices rather than per-device comparison
  Reason: Subtree rows sum I/O rates; comparing total rate to total cap is the least surprising behavior
  Impact: Works for both per-cgroup and subtree views

## Blockers

- None.

## Validation

```bash
python3 -m py_compile topos/src/topos/collect/cgroup.py topos/src/topos/collect/collector.py topos/src/topos/registry.py topos/src/topos/ui/table.py topos/tests/test_io_cap_saturation.py
# (no output — clean)

python3 -m pytest topos/tests/test_io_cap_saturation.py -v
# 15 passed in 0.10s

python3 -m pytest topos/tests -q
# 216 passed in 29.28s

/tmp/p25-venv/bin/python -m pytest topos/tests/test_io_cap_saturation.py -q
# 16 passed in 0.06s after controller review

/tmp/p25-venv/bin/python -m pytest topos/tests -q
# 217 passed in 29.89s after controller review

PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest topos/tests/test_io_cap_saturation.py -q
# 16 passed in 0.11s on main after merge

PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m py_compile topos/src/topos/collect/cgroup.py topos/src/topos/collect/collector.py topos/src/topos/registry.py topos/src/topos/ui/table.py topos/tests/test_io_cap_saturation.py
# (no output - clean) on main after merge

PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest topos/tests -q
# 217 passed in 29.80s on main after merge
```

## Controller Merge

```bash
git merge --no-ff feat/topos-p28-io-cap-saturation
# Merge commit: 177c370 Merge topos P28 io cap saturation
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.
