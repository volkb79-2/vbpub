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
    title: "Operator-console frontier wave 3 — source, process, and history core"
    target_product_version: 1
    features: [F030, F031, F032]
    status: active
  - id: M8
    title: "Operator-console frontier wave 4 — lifecycle protocol and detail leases"
    target_product_version: 1
    features: [F033, F034]
    status: planned
  - id: M9
    title: "Operator-console frontier wave 5 — web transport and lifecycle incidents"
    target_product_version: 1
    features: [F035, F036]
    status: planned
  - id: M10
    title: "Operator-console frontier wave 6 — React Overview and Explore"
    target_product_version: 1
    features: [F037]
    status: planned
  - id: M11
    title: "Operator-console frontier wave 7 — React Entity, Incidents, and Compare"
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

Milestones preserve `docs/ROADMAP.md`'s own structure: the historical v0-v1-
v1.5-v2 capability-era track (M1-M4, all done — these are capability eras per
`README.md`, not package SemVer promises), then the **operator-console**
milestone's dependency-ordered "Executable frontier" waves (M5-M11, matching
the ROADMAP.md table: wave 1 = P81/P87/P66/P86 merged 2026-07-15, wave 2 = P88
merged 2026-07-15, wave 3 = P89/P90/P91 the current frontier, wave 4 =
P93/P94, wave 5 = P92/P95, wave 6 = P73, wave 7 = P77), and finally the
optional-plugins bucket (M12 done, M13 the scenario-gated remainder).

M7 is `active` because it is explicitly named "the current frontier" in
ROADMAP.md: P88 (wave 2) is merged and P89/P90/P91 are ready and dispatchable
in parallel. M8-M11 are `planned` rather than `active`: their declared
dependencies are satisfied but they are not yet dispatched (ROADMAP.md marks
P93 "Ready (P87 merged)" and P94/P95/P92/P73/P77 as blocked on siblings still
in M7/M8).

Lifecycle mutation (M8's F033) is intentionally a separate safety track: P87's
stopgap (M5/F024) already closes the urgent full-ID protected-service bypass
and refuses raw Docker mutation for recognized owner-managed workloads; P93
(F033) is the full owner-chain protocol and migration, not a redo of the
stopgap.

D-001 through D-019 in `docs/DECISIONS-INBOX.md` are all decided and underlie
this roadmap; the completed interview is retained as provenance in
`handoff/TOPOS-OBSERVABILITY-DISCUSSION.md`. P68, P80, and P82 were deleted
during the 2026-07-15 reconciliation (see `docs/BRANCH-DISPOSITION.md`) and are
not represented as milestones or features. P64 (informational baseline
comparison) and P65 (human-readable rendering) were revised as P88 consumers;
P65 shipped as F029 (M6); P64 remains the scenario-gated, non-release-blocking
F043 (M13) per D-007.
