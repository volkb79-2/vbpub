# Estate design records

This directory holds cross-project designs, decisions, requirements and
research that do not belong to one repository project. Project-owned records
remain under that project's `docs/`, and acceptance evidence remains under
`docs/evidence/`.

Start with [RESOURCE-PROFILES.md](RESOURCE-PROFILES.md) for worker sizing and
the MDT capacity boundary.

| Topic | Design record |
|---|---|
| Controller status | [ctlstat requirements](ctlstat/REQUIREMENTS.md), [harness research](ctlstat/RESEARCH-harnesses.md), [compaction endpoints](ctlstat/RESEARCH-compaction-endpoints.md), [compaction experiment](ctlstat/EXPERIMENT-compaction-hooks.md), [OpenCode PoC](ctlstat/POC-opencode.md), [pi PoC](ctlstat/POC-pi.md) |
| dstdns adoption | [benefit analysis](dstdns/BENEFIT-ANALYSIS-2026-11-04.md), [development plan](dstdns/DEVELOPMENT-PLAN-2026-11-04.md), [CMRU adoption memo](dstdns/CMRU-ADOPTION-MEMO-2026-11-05.md), [R2 pilot](dstdns/R2-PILOT-2026-11-04.md), [0.1.0 release plan](dstdns/RELEASE-PLAN-0.1.0-2026-11-05.md) |
| Host endpoints | [phase-2 fast path](host-endpoints/DESIGN-phase2-fastpath-2026-11-07.md) |
| Host service sockets | [phase-1 release plan](host-service-sockets/RELEASE-PLAN-phase1-2026-11-07.md) |
| Host setup | [design rev 3](host-setup/DESIGN-rev3-2026-11-08.md) |
| Host configuration | [Augeas boundaries](hostcfg/DESIGN-AUGEAS-AND-BOUNDARIES-2026-11-07.md), [design rev 2](hostcfg/DESIGN-rev2-2026-11-08.md) |
| Host observation | [operator decisions](hostobs/DECISIONS-2026-11-07.md), [Astra opinion](hostobs/ASTRA-OPINION-2026-11-07.md), [plan v4](hostobs/PLAN-v4-2026-11-08.md) |
| IO governance monitor | [design rev 9](iogovmon/DESIGN-rev9-2026-11-06.md), [adversarial review](iogovmon/DESIGN-REVIEW-2026-11-05.md) |
| Lane audit | [2026-10-29 audit](lane-audit/LANES-AUDIT-2026-10-29.md) |
| MDT daemon | [design rev 4](mdtd/DESIGN-rev4-2026-11-07.md) |
| Parallelisation | [design memo](parallelisation/DESIGN-MEMO-2026-11-05.md), [addendum](parallelisation/DESIGN-MEMO-ADDENDUM-2026-11-04.md), [addendum 2](parallelisation/DESIGN-MEMO-ADDENDUM-2-2026-11-04.md) |
| R2 portfolio | [portfolio plan](r2-portfolio/R2-PORTFOLIO-PLAN-2026-10-29.md) |
| Resource-profile simulations | [budget model](resource-profiles-sim/sim-budget.md), [CLI-extended model](resource-profiles-sim/sim-cli-extended.md), [IO benchmark model](resource-profiles-sim/sim-iobench.md) |
| Requirements naming | [final rename decision](requirements/REQUIREMENTS-RENAMES-2026-11-03.md) |
| Telemetry platform | [design v2](telemetry-platform/DESIGN-v2-2026-11-07.md) |
| Test policy | [requirements](test-policy/REQUIREMENTS-2026-11-09.md) |

The provenance header at the top of each retained record names its origin,
status and controller-scratch source. Superseded versions remain controller
scratch; the retained record's header records the supersession.
