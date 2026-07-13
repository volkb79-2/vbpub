# P54 Work Log

## Context

- Branch: feat/groop-p54-steady-state-report
- Worktree: .worktrees/-groop-p54-steady-state-report
- Base commit: main (after P53 merge)
- Package: P54 — Steady-State Report Command
- Current objective: Implement `groop report FILE [--window last:Ns|all] [--group-by slice|entity] --json`

## Timeline

```text
2026-07-14 UTC
- Action: Read P54 handoff, P53-REPORT.md, P53-REVIEW.md for context.
- Commands: read_file on handoff, reports, cli.py, model.py, reader.py, registry.py
- Files changed: (none yet)
- Result: Full understanding of the codebase and requirements.
- Follow-up: Create report.py, add CLI dispatch, write tests, update docs.

- Action: Created P54-LOG.md work log.
- Files changed: groop/handoff/reports/P54-LOG.md

- Action: Created groop/src/groop/report.py — core module.
- Files changed: groop/src/groop/report.py
- Result: compute_profile, compute_report, format_report, parse_window_spec implemented.

- Action: Added CLI dispatch in cli.py — parse_report_args, _main_report, dispatch line.
- Files changed: groop/src/groop/cli.py
- Result: `groop report FILE --json` works; all error cases handled.

- Action: Wrote 56 tests in groop/tests/test_report.py.
- Commands: PYTHONPATH=groop/src python3 -m pytest groop/tests/test_report.py -q -W error::RuntimeWarning
- Result: All 56 tests pass.

- Action: Updated documentation.
- Files changed: groop/README.md, groop/docs/ARCHITECTURE.md, groop/docs/OPERATIONS.md
- Result: README quickstart/docs/ARCHITECTURE dataflow/OPERATIONS runbook updated.

- Action: Ran quality gates.
- Commands: py_compile (3 files), full suite timeout 300, git diff --check
- Result: py_compile clean; 970 passed, 2 skipped (zstandard); git diff --check clean.

- Action: Created P54-REPORT.md.
- Files changed: groop/handoff/reports/P54-REPORT.md
- Result: Report written with all evidence.
```

## Decisions

- Decision: Report module goes in `groop/src/groop/report.py` (own file, parallel to other command modules like actions/daemon).
  Reason: Follows pattern of `groop action` / `groop daemon` — own parse_*_args and _main_* functions.
  Impact: Clean separation; no new subpackage needed for a single-file module.

- Decision: Rate metrics are detected by suffix heuristics: `_per_s`, `_bps`, `_pps`, `_iops`.
  Reason: The handoff names rf_z_per_s, rf_d_per_s, rf_f_per_s, mem_events_*_per_s, plus io/net rate metrics (io_r_bps, net_rx_pps, etc.).
  Impact: Correctness depends on the heuristic; all named rate metrics match one of the four suffixes.

- Decision: --json is required (exit 2 if omitted); --window defaults to "all"; --group-by defaults to "entity".
  Reason: Handoff explicitly requires --json; default window is all; entity is the safer default grouping.

- Decision: Slice ancestry uses the existing Entity.parent chain without reimplementing cgroup path parsing.
  Reason: Handoff says "reusing the existing parent/tree-ancestry logic (do not reimplement cgroup path parsing)."

- Decision: Nearest-rank percentile method per 2026-07-12 amendment (ceil(p/100 * N) - 1, 0-based).
  Reason: Amendment pins the method; test includes a fixture where nearest-rank and interpolation diverge.

- Decision: Float rounding to 6 decimal places at serialization per 2026-07-12 amendment.
  Reason: Ensures deterministic byte-identical output for identical inputs.

- Decision: .zst input without zstandard exits 2 with install hint, matching RecordReader behavior.
  Reason: Amendment requires citing existing behavior; RecordReader raises RuntimeError when zstandard is missing.

## Validation

```bash
$ cd /workspaces/vbpub/.worktrees/groop-p54-steady-state-report
$ PYTHONPATH=groop/src python3 -m py_compile groop/src/groop/report.py
$ PYTHONPATH=groop/src python3 -m py_compile groop/src/groop/cli.py
$ PYTHONPATH=groop/src python3 -m py_compile groop/tests/test_report.py
$ PYTHONPATH=groop/src python3 -m pytest groop/tests/test_report.py -q -W error::RuntimeWarning
56 passed in 0.99s
$ PYTHONPATH=groop/src timeout 300 python3 -m pytest groop/tests/ -q -p no:asyncio -p no:schemathesis -W error
970 passed, 2 skipped in 122.84s
$ git diff --check
# clean
```

## Handoff Checklist

- [x] Log file current.
- [x] Report file written.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.

## Decisions (continued)

- Decision: --group-by slice rolls entities up under their owning *.slice ancestor by following the parent chain.
  Reason: Entities with no *.slice ancestor (root, non-slice immediate children of root) are grouped under their direct parent or root.
  Impact: Matches the handoff requirement to reuse existing parent/tree-ancestry logic.

- Decision: window spec parsing rejects anything other than "all" or "last:Ns" where N is a positive integer.
  Reason: Handoff specifies "Reject malformed window specs with a clear message and exit 2."
```
