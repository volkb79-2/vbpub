# P88 Work Log

Keep this file current while working. It is a resumability artifact, not a
place for private reasoning.

## Context

- Branch: feat/groop-p88-unified-frame-query-core
- Worktree: /workspaces/vbpub/.worktrees/groop-p88-unified-frame-query-core
- Base commit: bf74607 (main)
- Package: P88 unified bounded frame query core
- Current objective: one bounded recording/daemon frame query engine behind CLI/TUI/HTTP/MCP.

## Timeline

Append newest entries at the bottom.

```text
2026-07-15
- Action: Read spec, README standing contracts, DECISIONS-INBOX D-003..D-019,
  report.py (P54/P70 math), record/reader.py + writer.py, daemon/client.py
  (P63 typed history), daemon/api.py history op, model.py, registry.py
  (branch_policy aggregation semantics), cli.py (report exemplar).
- Result: No BLOCKED trigger. Both RecordReader and DaemonClient.request_history
  already yield canonical `Frame` objects, so one FrameSource boundary wraps both
  with zero change to P2/P52 wire formats. Caps are enforceable before
  materialization. Design confirmed.
- Follow-up: implement groop.query package (errors, source, semantics, engine,
  serialize) + `groop query` CLI + tests.
```

```text
2026-07-15 (later)
- Action: implemented groop.query (errors, source, semantics, engine,
  __init__) + `groop query` CLI subcommand; wrote groop/tests/test_query.py
  (62 tests, all numbered oracles O1-O14 + contract oracles). Removed unused
  SelectorError (dead code). Added docs/BACKLOG.md B-039/B-040.
- Commands:
  PYTHONPATH=groop/src .venv/bin/python -m pytest groop/tests/test_query.py -q -W error -p no:schemathesis  -> 62 passed
  focused query+report+daemon(P63/P52)                                                                     -> 262 passed
  timeout 900 ... pytest groop/tests -q -W error -p no:schemathesis                                        -> 1512 passed (zero skips), then re-run after cleanups
  .venv/bin/python -m py_compile groop/src/groop/query/*.py groop/src/groop/cli.py groop/tests/test_query.py -> clean
  git diff --check                                                                                          -> clean
- Result: all gates green; no BLOCKED trigger. Recording vs daemon byte-identical
  apart from meta.source across summary/current/raw. Perf: 2008 entities x 30
  frames hierarchy summary = 0.27-0.34s wall, 819594 bytes encoded.
- Follow-up: REPORT written; commit.
```

## Decisions

- Decision: reuse `report._nearest_rank_percentile`, `report.WindowRange`,
  `report.parse_window_spec` rather than duplicating.
  Reason: spec Contract 3 "REUSE/generalize, do not duplicate"; test_report.py
  imports `_nearest_rank_percentile` by name, so it must not move.
  Impact: report.py public surface stays frozen.
- Decision: absolute source sequence numbers are internal to FrameSource and are
  NOT emitted in the deterministic payload; gaps/eviction are expressed
  structurally (positions/timestamps/flags).
  Reason: recording seq (0..N-1) and daemon seq differ; byte-identical payloads
  (apart from source provenance) over the same frames require source-independent
  encoding.
  Impact: satisfies Contract 7 byte-identity; seq still preserved internally for
  gap/eviction detection.

## Blockers

- none

## Validation

```bash
# Focused P88
PYTHONPATH=groop/src .venv/bin/python -m pytest groop/tests/test_query.py -q -W error -p no:schemathesis
# 62 passed

# Full suite (zero-skip P84 gate)
timeout 900 env PYTHONPATH=groop/src .venv/bin/python -m pytest groop/tests -q -W error -p no:schemathesis
# 1512 passed (zero skips)

.venv/bin/python -m py_compile groop/src/groop/query/*.py groop/src/groop/cli.py groop/tests/test_query.py  # clean
git diff --check  # clean
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented (BACKLOG B-039/B-040 + REPORT).
- [x] Feature branch committed.
