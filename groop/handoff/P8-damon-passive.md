# P8 — DAMON passive: session detection, hot/warm/cold columns, detail panel

**Cut:** v1.5. **Depends:** P1, P5 merged (v1 shipped). Branch: `feat/groop-p8-damon-passive`.
Follow `groop/README.md` workflow protocol.

## Goal

Read-only DAMON awareness: detect sessions someone else (damo, sysadmin,
scripts) is running, attribute them to entities, and surface working-set
classification — WITHOUT ever mutating DAMON state. Passive means passive.

## Spec references

§3.6 (DAMON integration — the passive half; the section's control material is
P9), §0.1 (v1.5 cut), MEASUREMENTS.md M2/M7 context (what operators use DAMON
for on the reference host).

## Scope — in

1. `damon/passive.py`: enumerate /sys/kernel/mm/damon/admin/kdamonds/*;
   for each context: operations mode (vaddr/paddr), targets (pids → map to
   EntityKey via /proc/<pid>/cgroup), monitoring attrs (sample/aggr
   intervals), scheme count; read tried_regions/region stats where exposed
   WITHOUT writing any state file (no `state`, no `commit` — a test greps
   your code for write opens on damon sysfs paths).
2. Hot/warm/cold classification per entity from region access frequencies
   (thresholds in config `[damon]`), emitted as registry metrics
   (damon_hot_bytes, damon_warm_bytes, damon_cold_bytes, damon_sample_age_s,
   damon_mode) with `src="exact"` and honest unavailability when no session
   covers the entity.
3. UI: `damon` job profile columns activate when data present (P5 profile
   hook); drill-down DAMON panel: session parameters, region histogram
   (text bars), coverage note ("session covers 2/7 pids of this entity").
4. Degradation: no DAMON in kernel / no sessions / permission denied →
   unavail_kernel/unavail_perm; the banner NEVER nags about DAMON absence.
5. Tests: fixture sysfs trees for one vaddr and one paddr session; pid→entity
   attribution; no-write guarantee test; stale-session aging (sample_age).

## Scope — out

Starting/stopping/committing ANY DAMON state (P9), paddr auto-start (v2),
DAMON-based automation.

## Acceptance

- With a damo-started vaddr session on the reference host, the target entity
  shows hot/warm/cold within one sample and the panel renders; with no
  session, columns show `–` and nothing is written anywhere under damon
  sysfs (verified by test + strace spot-check noted in report).
- pytest green; report per README protocol.
