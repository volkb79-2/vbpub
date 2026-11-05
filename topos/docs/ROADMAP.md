# topos roadmap

The active plan is `docs/PLAN-2026-11-CONSOLE.md`, and the target design is
`TUI-SPEC.md`. The July package-by-package roadmap and its package history are in
`nyxloom-trove/archive/ROADMAP-2026-07.md`.

| Order | Work | State |
|---|---|---|
| 0 | Correctness fixes from the deep review | In review |
| A | Rust sampler PoC, then tiers, read path, unit, cockpit socket, release | PoC running |
| B | Line-rendered table, ratatui PoC, drawing PoC, then meters, process rows, pane, lenses | Started |
| C | eBPF block latency provider | Independent, not started |
| Parked | Web UI (former M9-M11: P92, P73, P77) | Revisit after the TUI meets its scenarios |
| Later | Gated TUI actions, remote hosts | After open safety items |

Milestones are not set; the operator defines them after using the PoCs.
