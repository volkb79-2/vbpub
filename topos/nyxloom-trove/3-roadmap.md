---
kind: roadmap
schema_version: 1
milestones:
  - id: M1
    title: "v0 — collector core proof"
    target_product_version: 1
    features: [F001]
    status: done
  - id: M2
    title: "v1 — read-only TUI"
    target_product_version: 1
    features: [F002, F003, F004, F005, F006, F007, F008]
    status: done
  - id: M3
    title: "v1.5 — DAMON, snapshots, backend awareness, recording ergonomics"
    target_product_version: 1
    features: [F009, F010, F011, F012, F013, F014]
    status: done
  - id: M4
    title: "v2 foundation — daemon, BPF, admin actions"
    target_product_version: 1
    features: [F015, F016, F017, F018, F019, F020, F021, F022, F023]
    status: done
  - id: M5
    title: "Operator-console frontier wave 1 — safety and rendering closures"
    target_product_version: 1
    features: [F024, F025, F026, F027]
    status: done
  - id: M6
    title: "Operator-console frontier wave 2 — unified frame query core"
    target_product_version: 1
    features: [F028, F029]
    status: done
  - id: M7
    title: "Console data layer — source selection, process model, tiered history"
    target_product_version: 1
    features: [F030, F031, F032]
    status: active
  - id: M8
    title: "Lifecycle protocol and detail leases"
    target_product_version: 1
    features: [F033, F034]
    status: planned
  - id: M9
    title: "PARKED (D-023) — web transport and lifecycle incidents"
    target_product_version: 1
    features: [F035, F036]
    status: planned
  - id: M10
    title: "PARKED (D-023) — React Overview and Explore"
    target_product_version: 1
    features: [F037]
    status: planned
  - id: M11
    title: "PARKED (D-023) — React Entity, Incidents, and Compare"
    target_product_version: 1
    features: [F038]
    status: planned
  - id: M12
    title: "Optional plugins — ZFS, GPU, and CIU metadata/grouping"
    target_product_version: 1
    features: [F039, F040, F041, F042]
    status: done
  - id: M13
    title: "Optional plugins — scenario-driven provider broadening"
    target_product_version: 1
    features: [F043]
    status: planned
---

# Roadmap

The active plan is `docs/PLAN-2026-11-CONSOLE.md`; target design is `TUI-SPEC.md`;
decisions are D-020 onward in `decisions.md`. Milestone lines are deliberately not
redrawn for the redesign yet (D-032): the operator sets them after using the PoCs.

- **M7** is the active data work: the Rust sampler PoC, then tiers, read path and
  source selection. Its process code exists with no caller, and its persistent
  history has a writer with no reader; the tiers replace that store (D-025).
- **M8:** F033 shipped for Docker and systemd through the CLI. F034 is not started.
- **M9 to M11** are parked (D-023). F036 (lifecycle identity) stays wanted for the
  TUI and moves out of M9 when the milestones are redrawn.
- The new main screen (D-020 to D-022) has no feature ID yet; it gets one with the
  milestone definition.

History of the M1 to M6 waves is in `archive/ROADMAP-2026-07.md`.
