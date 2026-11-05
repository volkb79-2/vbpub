# topos status

Current truth as of 2026-11-05, from reading callers and running the code. Latest
release: `topos-v0.5.0`. Target design: `TUI-SPEC.md`. Plan:
`docs/PLAN-2026-11-CONSOLE.md`. Evidence: `MEASUREMENTS.md`.

## Summary

`topos` is a usable cgroup-tree TUI and a strong CLI over recordings, but the
always-on and process-level parts of the product are not connected. Its best data
(effective memory.min, IOPS, throttling, memory events) is collected and never
shown in a column.

## Feature state

| Feature | State | Evidence |
|---|---|---|
| Collector, registry, `--once --json` | Works | `collect/`, `registry.py` |
| Tree, container and CIU-grouped TUI views; 8 profiles; drill-down; replay | Works | `ui/` |
| Record, replay, report, query, compare (recordings only) | Works in CLI | `cli.py` |
| Drift and origin detection | Works; effective protection is a simplification | `drift/origin.py` |
| Diagnostics rules and pressure score | Works | `diag/` |
| Daemon socket, `--attach`, MCP, HTTP gateway | Works; explicit attach only, gateway auth is provisional | `daemon/`, `mcp/` |
| Source auto-selection and backfill (F030) | Not started | zero-argument `topos` is local collection |
| Process sampler (F031) | Code exists, no caller anywhere | `procs/` |
| Persistent history (F032) | Writer only, off by default; nothing reads it | `daemon/persist.py` |
| Daemon history in `topos query` | Library only, no CLI binding | `query/source.py` |
| Owner-chain actions (F033) | Wired to CLI for Docker and systemd; Compose, CIU and Wings adapters refuse | `actions/` |
| Detail leases (F034), lifecycle identity and incidents (F036) | Not started | |
| Web UI (F035, F037, F038) | No code; parked | |
| Per-CPU, steal, iowait | Not collected | No `/proc/stat` reader |
| D-state, blocked processes, FD, ports, mounts, KSM | Not collected | |
| io.cost vrate and usage | Added on the review branch; not shown yet | |
| TUI cost | 624 ms CPU per frame with `DataTable`; line widget in progress | Plan, Evidence |

## Scenario coverage (`docs/OPERATOR-QUESTIONS.md`, 23 scenarios)

| Class | Scenarios |
|---|---|
| Answered in TUI | 1, 6, 8 |
| Partly | 4, 9, 12, 16, 18, 20, 23 |
| CLI only | 7, 11, 22 |
| Not answered | 2, 3, 5, 10, 13, 14, 15 (TUI bypasses the query core), 17, 19, 21 |

## Quality

- Registered gate: `topos-suite` lane (`run-gate.toml`), about 3,300 tests with a
  changed-line coverage gate.
- Tests run against fixtures. No lane checks kernel semantics on a real kernel;
  `TP-VMLANE` in the backlog proposes one.
- Live evidence for DAMON, daemon and BPF paths remains incomplete; see
  `docs/RELEASE-READINESS.md`.
